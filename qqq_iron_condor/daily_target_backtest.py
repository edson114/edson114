"""Historical backtest of the daily $ target rules (daily_target.py).

Replays: open a ~0.80-delta, ~60 DTE QQQ call or put (10 contracts) at the
session open whenever no position is open, then exit on whichever comes
first -- +$1,000, -$1,000, or the end of the Nth session -- over years of
real daily QQQ bars.

What's real vs. modelled:
  - Real: QQQ daily open/high/low/close, VIX closes, the hard skip-day
    gate's scheduled macro dates and opening-gap rule.
  - Modelled: option prices. There is no free source of historical QQQ
    option chains, so every option is priced with Black-Scholes at IV =
    prior-day VIX x `daily_target_iv_vix_multiple`, and every fill pays
    `daily_target_slippage` per share vs. that model mid on each side.
  - Direction: the live signal is half intraday (VWAP, opening range,
    5-min EMAs) and free intraday history only goes back ~60 days, so the
    "signal" mode here uses only the daily half of the score (trend, MACD,
    RSI) from prior completed sessions. "call" / "put" modes are
    always-long / always-short baselines -- if "signal" can't beat both,
    the direction call isn't adding anything.
  - Intraday path: with +/-$1.25 levels and a ~$8-10 daily QQQ range,
    most sessions touch *both* levels, so which came first decides the
    trade. Daily bars can't tell, so the CLI walks real hourly bars
    (yfinance keeps ~730 days of them) and only a bar that spans both
    levels is scored as the stop (conservative). `--daily-only` falls
    back to daily bars, where any two-sided day is a stop -- a floor,
    not an estimate. A gap through a level on a later day exits at the
    open's value, which can be better or worse than the level.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import pandas as pd

from . import indicators as ind
from .analysis import build_snapshot
from .config import Config
from .daily_target import per_share, strike_for_delta, underlying_for_option_price
from .direction import _macd_component, _rsi_component, _trend_component
from .options_math import bs_price

MIN_DAILY_HISTORY = 200
DTE = 60
# Stop sizes compared in the report's sensitivity table; None = no stop,
# only the target or the time stop closes the trade.
STOP_SWEEP_USD = (500.0, 1000.0, 2000.0, 3000.0, None)
NO_STOP_USD = 1e12


@dataclass
class TargetTrade:
    entry_date: dt.date
    exit_date: dt.date
    side: str
    strike: float
    entry_fill: float
    exit_fill: float
    pnl_usd: float
    outcome: str  # "target", "stop", "time"
    sessions: int  # sessions the position was open, entry day = 1
    capital_usd: float


@dataclass
class TargetSummary:
    mode: str
    trading_days: int
    trades: int
    targets: int
    stops: int
    time_exits: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    avg_win: float
    avg_loss: float
    max_drawdown: float
    avg_capital: float
    same_day_targets: int
    worst_case_win_rate: Optional[float] = None


def daily_score(history: pd.DataFrame, vix: pd.DataFrame, cfg: Config) -> float:
    """Daily-only half of the directional score (trend/MACD/RSI) from
    completed sessions only. Raw (not renormalized) weights, so with the
    defaults it spans -0.4..+0.4."""
    snap = build_snapshot(history.tail(260), vix.tail(260), cfg.adx_trend_threshold)
    w = cfg.component_weights
    return (
        w["daily_trend"] * _trend_component(snap)[0]
        + w["macd"] * _macd_component(snap)[0]
        + w["rsi"] * _rsi_component(snap)[0]
    )


def daily_bias(history: pd.DataFrame, vix: pd.DataFrame, cfg: Config) -> Optional[str]:
    score = daily_score(history, vix, cfg)
    if score > 0:
        return "call"
    if score < 0:
        return "put"
    return None


def _skip_day(date: dt.date, open_px: float, prev_close: float, cfg: Config) -> bool:
    if date.isoformat() in cfg.macro_event_dates:
        return True
    return abs(open_px / prev_close - 1.0) * 100.0 >= cfg.gap_threshold_pct


def _bars_by_day(intraday: Optional[pd.DataFrame]) -> dict:
    if intraday is None or intraday.empty:
        return {}
    bars = intraday[["Open", "High", "Low", "Close"]]
    return {d: g for d, g in bars.groupby(bars.index.date)}


def simulate(
    price_history: pd.DataFrame,
    vix_history: pd.DataFrame,
    cfg: Config,
    mode: str = "signal",
    intraday: Optional[pd.DataFrame] = None,
    tie_rule: str = "path",
    bias_cache: Optional[dict] = None,
    entry_bar: int = 0,
    direction: Optional[Callable[[int, pd.DataFrame], Optional[str]]] = None,
    atr_exits: Optional[tuple] = None,
) -> list[TargetTrade]:
    """Replay the rules. With `intraday` bars (e.g. hourly), each session
    is walked bar by bar so the order of the target and stop touches is
    known down to one bar, and only days covered by those bars are
    traded; without it, each session is a single daily OHLC bar.

    `tie_rule` decides a bar that spans both levels: "stop" always assumes
    the stop came first (worst case); "path" uses the usual OHLC path
    convention -- an up bar (close >= open) went open->low->high->close, a
    down bar open->high->low->close -- which favours neither side.

    Experiment knobs (defaults reproduce the live rules):
      - `entry_bar`: enter at the open of this bar of the session (0 = the
        open, 1 = after the first hourly bar). Bars before it are skipped
        on the entry day.
      - `direction`: `f(day_index, bars_before_entry) -> "call"/"put"/None`
        replaces `mode`'s direction (None = no trade that day).
      - `atr_exits`: `(target_atr, stop_atr)` fixes the exits as QQQ levels
        at entry +/- that many ATR14s instead of the $ target/stop."""
    df = price_history[["Open", "High", "Low", "Close", "Volume"]].copy()
    vix = vix_history["Close"].reindex(df.index).ffill()
    atr = ind.atr(df["High"], df["Low"], df["Close"], 14)
    day_bars = _bars_by_day(intraday)
    n_contracts = cfg.daily_target_contracts
    gain = per_share(cfg.daily_target_profit_usd, n_contracts)
    loss = per_share(cfg.daily_target_stop_usd, n_contracts)
    slip = cfg.daily_target_slippage
    r = cfg.risk_free_rate

    trades: list[TargetTrade] = []
    pos: Optional[dict] = None

    for i in range(MIN_DAILY_HISTORY, len(df)):
        date = df.index[i].date()
        if day_bars:
            if date not in day_bars:
                continue
            bars = day_bars[date]
        else:
            bars = df.iloc[[i]][["Open", "High", "Low", "Close"]]
        prev_vix = float(vix.iloc[i - 1])
        if prev_vix != prev_vix:  # NaN
            continue
        iv = prev_vix / 100.0 * cfg.daily_target_iv_vix_multiple
        day_open = float(bars["Open"].iloc[0])
        entry_day = False

        if pos is None:
            if _skip_day(date, day_open, float(df["Close"].iloc[i - 1]), cfg) or prev_vix >= cfg.vix_spike_threshold:
                continue
            if len(bars) <= entry_bar:
                continue
            if direction is not None:
                side = direction(i, bars.iloc[:entry_bar])
            elif mode in ("call", "put"):
                side = mode
            elif bias_cache is not None and date in bias_cache:
                side = bias_cache[date]
            else:
                side = daily_bias(df.iloc[:i], vix_history.loc[: df.index[i - 1]], cfg)
                if bias_cache is not None:
                    bias_cache[date] = side
            if side is None:
                continue
            entry_px = float(bars["Open"].iloc[entry_bar])
            bars = bars.iloc[entry_bar:]
            expiry = date + dt.timedelta(days=DTE)
            t = DTE / 365.0
            strike = strike_for_delta(entry_px, t, r, iv, side, cfg.daily_target_delta)
            fill = bs_price(entry_px, strike, t, r, iv, side) + slip
            pos = {"side": side, "strike": strike, "expiry": expiry, "fill": fill, "date": date, "sessions": 0}
            if atr_exits is not None:
                sign = 1.0 if side == "call" else -1.0
                prev_atr = float(atr.iloc[i - 1])
                pos["target_u"] = entry_px + sign * atr_exits[0] * prev_atr
                pos["stop_u"] = entry_px - sign * atr_exits[1] * prev_atr
            entry_day = True

        pos["sessions"] += 1
        side, strike, fill = pos["side"], pos["strike"], pos["fill"]
        t = max((pos["expiry"] - date).days, 1) / 365.0
        value = lambda s: bs_price(s, strike, t, r, iv, side)  # noqa: E731
        # Exits sell at model mid - slip, so the mid has to clear the level by `slip`.
        fixed_levels = "target_u" in pos
        if fixed_levels:
            target_u, stop_u = pos["target_u"], pos["stop_u"]
        else:
            target_u = underlying_for_option_price(fill + gain + slip, strike, t, r, iv, side)
            stop_u = underlying_for_option_price(max(fill - loss + slip, 0.01), strike, t, r, iv, side)

        up = side == "call"
        beyond = lambda px, lvl, favourable: lvl is not None and ((px >= lvl) == (up == favourable))  # noqa: E731

        outcome = exit_fill = None
        if not entry_day and (beyond(day_open, target_u, True) or beyond(day_open, stop_u, False)):
            # Gapped through a level overnight: exit at the open's value.
            exit_fill = value(day_open) - slip
            outcome = "target" if exit_fill >= fill else "stop"
        else:
            for _, bar in bars.iterrows():
                hit_target = beyond(bar["High"] if up else bar["Low"], target_u, True)
                hit_stop = beyond(bar["Low"] if up else bar["High"], stop_u, False)
                if hit_stop and hit_target:
                    low_first = bar["Close"] >= bar["Open"]
                    stop_first = tie_rule == "stop" or (low_first == up)
                    hit_target, hit_stop = not stop_first, stop_first
                if hit_stop:
                    outcome, exit_fill = "stop", (value(stop_u) - slip) if fixed_levels else fill - loss
                    break
                if hit_target:
                    outcome, exit_fill = "target", (value(target_u) - slip) if fixed_levels else fill + gain
                    break
            if outcome is None and pos["sessions"] >= cfg.daily_target_max_hold_days:
                outcome, exit_fill = "time", value(float(bars["Close"].iloc[-1])) - slip

        if outcome is not None:
            trades.append(
                TargetTrade(
                    entry_date=pos["date"],
                    exit_date=date,
                    side=side,
                    strike=strike,
                    entry_fill=round(fill, 2),
                    exit_fill=round(exit_fill, 2),
                    pnl_usd=round((exit_fill - fill) * 100 * n_contracts, 2),
                    outcome=outcome,
                    sessions=pos["sessions"],
                    capital_usd=round(fill * 100 * n_contracts, 2),
                )
            )
            pos = None

    return trades


def simulate_hold(
    price_history: pd.DataFrame,
    vix_history: pd.DataFrame,
    cfg: Config,
    side: str,
    hold: str,
    start: int = MIN_DAILY_HISTORY,
) -> list[TargetTrade]:
    """No target/stop: buy the same 0.80-delta ~60 DTE option and sell at a
    fixed time, every day. `hold="overnight"` buys at the close and sells
    at the next session's open; `hold="intraday"` buys at the open and
    sells at the close. Splits QQQ's move into the two halves of the day.
    IV is held at the entry day's VIX on both legs so the result is the
    price move and theta, not a vega bet."""
    df = price_history[["Open", "Close"]]
    vix = vix_history["Close"].reindex(df.index).ffill()
    n = cfg.daily_target_contracts
    slip, r = cfg.daily_target_slippage, cfg.risk_free_rate
    trades: list[TargetTrade] = []
    last = len(df) - (1 if hold == "overnight" else 0)
    for i in range(start, last):
        iv = float(vix.iloc[i - 1 if hold == "intraday" else i]) / 100.0 * cfg.daily_target_iv_vix_multiple
        if iv != iv:
            continue
        date = df.index[i].date()
        if hold == "overnight":
            entry_px, exit_px = float(df["Close"].iloc[i]), float(df["Open"].iloc[i + 1])
            exit_date = df.index[i + 1].date()
        else:
            entry_px, exit_px = float(df["Open"].iloc[i]), float(df["Close"].iloc[i])
            exit_date = date
        t_entry = DTE / 365.0
        t_exit = max(DTE - (exit_date - date).days, 1) / 365.0
        strike = strike_for_delta(entry_px, t_entry, r, iv, side, cfg.daily_target_delta)
        fill = bs_price(entry_px, strike, t_entry, r, iv, side) + slip
        exit_fill = bs_price(exit_px, strike, t_exit, r, iv, side) - slip
        pnl = (exit_fill - fill) * 100 * n
        trades.append(
            TargetTrade(
                entry_date=date,
                exit_date=exit_date,
                side=side,
                strike=strike,
                entry_fill=round(fill, 2),
                exit_fill=round(exit_fill, 2),
                pnl_usd=round(pnl, 2),
                outcome="time",
                sessions=1,
                capital_usd=round(fill * 100 * n, 2),
            )
        )
    return trades


def summarize(trades: list[TargetTrade], mode: str, trading_days: int) -> TargetSummary:
    pnls = pd.Series([t.pnl_usd for t in trades], dtype=float)
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    equity = pnls.cumsum()
    drawdown = float((equity.cummax().clip(lower=0) - equity).max()) if len(equity) else 0.0
    return TargetSummary(
        mode=mode,
        trading_days=trading_days,
        trades=len(trades),
        targets=sum(t.outcome == "target" for t in trades),
        stops=sum(t.outcome == "stop" for t in trades),
        time_exits=sum(t.outcome == "time" for t in trades),
        win_rate=float(len(wins) / len(trades)) if trades else 0.0,
        total_pnl=float(pnls.sum()),
        avg_pnl=float(pnls.mean()) if trades else 0.0,
        avg_win=float(wins.mean()) if len(wins) else 0.0,
        avg_loss=float(losses.mean()) if len(losses) else 0.0,
        max_drawdown=drawdown,
        avg_capital=float(pd.Series([t.capital_usd for t in trades]).mean()) if trades else 0.0,
        same_day_targets=sum(t.outcome == "target" and t.sessions == 1 for t in trades),
    )


def render_report(
    summaries: list[TargetSummary],
    first: dt.date,
    last: dt.date,
    cfg: Config,
    resolution: str,
    stop_sweep: Optional[list[TargetSummary]] = None,
) -> str:
    money = lambda x: f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"  # noqa: E731
    lines = [
        f"# QQQ Daily $ Target Backtest -- {dt.date.today():%Y-%m-%d}",
        "",
        f"Window: {first} to {last}. Rules: {cfg.daily_target_contracts} contracts, "
        f"{cfg.daily_target_delta:.2f} delta, {DTE} DTE, +{money(cfg.daily_target_profit_usd)} target / "
        f"-{money(cfg.daily_target_stop_usd)} stop / {cfg.daily_target_max_hold_days}-session time stop, "
        f"one position at a time, {money(cfg.daily_target_slippage * 100 * cfg.daily_target_contracts)} "
        "slippage per side.",
        f"Intraday path: {resolution}.",
        "",
        "| Direction | Trades | Target | Stop | Time | Win rate | Worst-case win rate | Total P&L | Avg/trade | Avg win | Avg loss | Max DD | Target same day | Avg capital |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for s in summaries:
        lines.append(
            f"| {s.mode} | {s.trades} | {s.targets} | {s.stops} | {s.time_exits} | {s.win_rate:.0%} | "
            f"{s.worst_case_win_rate:.0%} | "
            f"{money(s.total_pnl)} | {money(s.avg_pnl)} | {money(s.avg_win)} | {money(s.avg_loss)} | "
            f"{money(s.max_drawdown)} | {s.same_day_targets} of {s.trading_days} days | {money(s.avg_capital)} |"
        )
    if stop_sweep:
        lines += [
            "",
            "### Stop size (signal direction, same +$1,000 target)",
            "",
            "| Stop | Trades | Target | Stop | Time | Win rate | Total P&L | Avg/trade | Avg loss | Max DD |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        for s in stop_sweep:
            lines.append(
                f"| {s.mode} | {s.trades} | {s.targets} | {s.stops} | {s.time_exits} | {s.win_rate:.0%} | "
                f"{money(s.total_pnl)} | {money(s.avg_pnl)} | {money(s.avg_loss)} | {money(s.max_drawdown)} |"
            )
        lines += [
            "",
            "A wider stop (or none) raises the win rate because QQQ usually comes back far enough to "
            "tag +$1,000 -- but the losses that do happen get bigger. The total P&L column is what "
            "decides whether that trade-off pays.",
        ]
    lines += [
        "",
        "**How to read this:** `signal` uses the daily half of the directional score; `call` / `put` "
        "are always-long / always-short baselines. Over a strong uptrend, `call` will look good "
        "simply because QQQ went up -- the question is whether `signal` holds up in both directions. "
        "\"Target same day\" counts sessions that actually closed at +$1,000 out of all trading days "
        "in the window -- that's the honest answer to how often the daily goal was met.",
        "",
        "**Not captured:** real option quotes/IV (Black-Scholes at VIX x "
        f"{cfg.daily_target_iv_vix_multiple}), the intraday half of the live signal, news, and the "
        "true intraday order of highs and lows (a day spanning both levels counts as a stop). "
        "Past results don't predict future ones.",
    ]
    return "\n".join(lines) + "\n"


def run(price_history: pd.DataFrame, vix_history: pd.DataFrame, cfg: Config, intraday: Optional[pd.DataFrame] = None) -> str:
    window = price_history.index[MIN_DAILY_HISTORY:]
    if intraday is not None and not intraday.empty:
        covered = set(intraday.index.date)
        window = window[[d.date() in covered for d in window]]
        resolution = (
            "hourly bars; when one hour touched both levels, the OHLC path convention decides "
            "(up bar: low first, down bar: high first). \"Worst-case win rate\" instead scores every "
            "such tie as a stop"
        )
    else:
        resolution = (
            "daily bars only -- a day that touched both levels is decided by the OHLC path "
            "convention, which is a coarse guess at this level spacing"
        )
    if len(window) == 0:
        raise RuntimeError("No trading days to backtest after the warm-up period.")
    cache: dict = {}
    summaries = []
    for m in ("signal", "call", "put"):
        s = summarize(simulate(price_history, vix_history, cfg, m, intraday, bias_cache=cache), m, len(window))
        s.worst_case_win_rate = summarize(
            simulate(price_history, vix_history, cfg, m, intraday, tie_rule="stop", bias_cache=cache), m, len(window)
        ).win_rate
        summaries.append(s)

    sweep = []
    for stop in STOP_SWEEP_USD:
        swept = dataclasses.replace(cfg, daily_target_stop_usd=stop if stop is not None else NO_STOP_USD)
        label = f"-${stop:,.0f}" if stop is not None else f"none ({cfg.daily_target_max_hold_days}-session time stop only)"
        sweep.append(summarize(simulate(price_history, vix_history, swept, "signal", intraday, bias_cache=cache), label, len(window)))
    return render_report(summaries, window[0].date(), window[-1].date(), cfg, resolution, sweep)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the QQQ daily $ target rules")
    parser.add_argument("--period", default="5y", help="yfinance daily history period (default 5y)")
    parser.add_argument("--output-dir", default="reports/backtests")
    parser.add_argument("--daily-only", action="store_true", help="Skip the hourly-bar path (daily OHLC only)")
    args = parser.parse_args(argv)

    from . import providers

    cfg = Config()
    try:
        price_history = providers.get_price_history(cfg.symbol, args.period)
        vix_history = providers.get_vix_history(args.period, cfg.vix_symbol, cfg.tradier_vix_symbol)
        intraday = None
        if not args.daily_only:
            from .data import get_intraday_history

            # yfinance serves hourly bars for the last ~730 days only.
            intraday = get_intraday_history(cfg.symbol, "1h", "730d")
        report = run(price_history, vix_history, cfg, intraday)
    except Exception as exc:
        print(f"Backtest failed: {exc}", file=sys.stderr)
        return 1

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"daily-target-{dt.date.today():%Y-%m-%d}.md"
    out_path.write_text(report)
    print(report)
    print(f"Saved report to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
