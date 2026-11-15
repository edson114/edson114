"""Backtest a small fixed $ target at your own size: 5 contracts, +$500.

Two rule sets, both on real QQQ hourly bars, with options priced like the
other daily-target backtests (Black-Scholes at the prior day's VIX x
`daily_target_iv_vix_multiple`, `daily_target_slippage` per share per fill):

1. **RSI calls** (the rules as first proposed, Nov 2026):
   - Buy `contracts` calls at `delta`, `dte` days to expiration, at the
     session open, only if daily RSI(14) at the prior close is below `rsi_max`.
   - Sell with a limit at fill + target / (contracts x 100) per share.
   - Optional $ stop on any touch (`stop_usd`, 0 = none). A same-hour tie
     goes by the bar's path: an up bar went low first, a down bar high first.
   - One position at a time; after any exit the next one opens the next
     session. A later session opening beyond the target or stop exits at the open.
   - Not in the original rules, so assumed here: a position still open with
     `exit_dte` days left to expiration is sold at that session's close.

2. **The daily plan's rules at that size**: daily_target_backtest.simulate()
   with first-hour direction (calls and puts) at 10:30, the skip-day rules,
   the level stop on an hourly close and a `backstop_usd` hard stop.

Usage:
    python -m qqq_iron_condor.fixed_target_backtest            # report to reports/backtests/
    python -m qqq_iron_condor.fixed_target_backtest --contracts 5 --target 500 --backstop 2000
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from .config import Config
from .daily_target import plan_config, strike_for_delta
from .options_math import bs_price


@dataclass
class CallTrade:
    entry_date: dt.date
    exit_date: dt.date
    strike: float
    entry_fill: float
    exit_fill: float
    pnl_usd: float
    outcome: str  # "target", "stop", "expiry"
    sessions: int
    worst_usd: float  # worst unrealized P&L while open (bar lows)
    capital_usd: float


@dataclass
class OpenCall:
    entry_date: dt.date
    strike: float
    entry_fill: float
    mark_usd: float  # P&L if sold at the last session's close
    worst_usd: float


def _prior(series: pd.Series, d: dt.date) -> float:
    x = series[series.index < d]
    return float(x.iloc[-1]) if len(x) else float("nan")


def simulate_rsi_calls(
    daily: pd.DataFrame,
    vix: pd.DataFrame,
    hourly: pd.DataFrame,
    cfg: Config,
    contracts: int = 5,
    target_usd: float = 500.0,
    delta: float = 0.85,
    dte: int = 60,
    exit_dte: int = 5,
    rsi_max: float = 70.0,
    stop_usd: float = 0.0,
    start: Optional[dt.date] = None,
) -> tuple[list[CallTrade], Optional[OpenCall], int]:
    """(closed trades, the position still open at the end or None, days
    skipped for RSI). `hourly`: bars with an ET wall-time index."""
    r, mult, slip = cfg.risk_free_rate, cfg.daily_target_iv_vix_multiple, cfg.daily_target_slippage
    dates = pd.to_datetime(daily.index).date
    rsi = pd.Series(ind.rsi(daily["Close"], 14).to_numpy(float), index=dates)
    vixc = pd.Series(vix["Close"].to_numpy(float), index=pd.to_datetime(vix.index).date)
    by_day = {d: g for d, g in hourly.groupby(hourly.index.date)}
    days = sorted(d for d in by_day if start is None or d >= start)
    per_share = target_usd / (contracts * 100)
    stop_share = stop_usd / (contracts * 100) if stop_usd > 0 else None

    trades: list[CallTrade] = []
    pos: Optional[dict] = None
    skipped = 0
    for d in days:
        bars = by_day[d]
        iv = _prior(vixc, d) / 100.0 * mult
        if iv != iv:
            continue
        if pos is None:
            if trades and trades[-1].exit_date == d:
                continue  # exited today: the next one opens the next session
            if not _prior(rsi, d) < rsi_max:
                skipped += 1
                continue
            spot = float(bars["Open"].iloc[0])
            k = strike_for_delta(spot, dte / 365.0, r, iv, "call", delta)
            fill = bs_price(spot, k, dte / 365.0, r, iv, "call") + slip
            pos = dict(entry=d, k=k, fill=fill, expiry=d + dt.timedelta(days=dte), worst=0.0, sessions=0)
        pos["sessions"] += 1
        k, fill = pos["k"], pos["fill"]
        tgt = fill + per_share
        stp = fill - stop_share if stop_share is not None else None
        t_years = max((pos["expiry"] - d).days, 0.5) / 365.0

        def value(px: float) -> float:
            return bs_price(px, k, t_years, r, iv, "call") - slip

        exit_px = outcome = None
        for i in range(len(bars)):
            b = bars.iloc[i]
            if i == 0 and d != pos["entry"]:
                v_open = value(float(b["Open"]))
                if v_open >= tgt:
                    exit_px, outcome = v_open, "target"
                    break
                if stp is not None and v_open <= stp:
                    exit_px, outcome = v_open, "stop"
                    break
            v_hi, v_lo = value(float(b["High"])), value(float(b["Low"]))
            pos["worst"] = min(pos["worst"], (v_lo - fill) * contracts * 100)
            hit_t = v_hi >= tgt
            hit_s = stp is not None and v_lo <= stp
            if hit_t and hit_s:
                if float(b["Close"]) >= float(b["Open"]):
                    hit_t = False
                else:
                    hit_s = False
            if hit_s:
                exit_px, outcome = stp, "stop"
                break
            if hit_t:
                exit_px, outcome = tgt, "target"
                break
        if exit_px is None and (pos["expiry"] - d).days <= exit_dte:
            exit_px, outcome = value(float(bars["Close"].iloc[-1])), "expiry"
        if exit_px is not None:
            trades.append(CallTrade(
                pos["entry"], d, k, round(fill, 2), round(exit_px, 2),
                round((exit_px - fill) * contracts * 100, 2), outcome, pos["sessions"],
                round(pos["worst"], 2), round(fill * contracts * 100, 2),
            ))
            pos = None

    open_call = None
    if pos is not None:
        last = days[-1]
        t_years = max((pos["expiry"] - last).days, 0.5) / 365.0
        iv = _prior(vixc, last + dt.timedelta(days=1)) / 100.0 * mult
        mark = bs_price(float(by_day[last]["Close"].iloc[-1]), pos["k"], t_years, r, iv, "call") - slip
        open_call = OpenCall(pos["entry"], pos["k"], round(pos["fill"], 2),
                             round((mark - pos["fill"]) * contracts * 100, 2), round(pos["worst"], 2))
    return trades, open_call, skipped


def plan_size_config(cfg: Config, contracts: int, target_usd: float, backstop_usd: float, delta: Optional[float] = None) -> Config:
    """The daily plan's config at your size. The options backtest's hard
    stop is `daily_target_stop_usd` when `backstop=True`."""
    cfg = dataclasses.replace(plan_config(cfg), daily_target_contracts=contracts,
                              daily_target_profit_usd=target_usd, daily_target_stop_usd=backstop_usd)
    return dataclasses.replace(cfg, daily_target_delta=delta) if delta is not None else cfg


def first_hour_direction(daily: pd.DataFrame, rsi_max: Optional[float] = None, calls_only: bool = False):
    """simulate(direction=...): the first hour's side, optionally skipping
    calls when RSI(14) at the prior close is >= rsi_max, or skipping puts."""
    rsi = ind.rsi(daily["Close"], 14).to_numpy(float)

    def direction(i, bars):
        move = float(bars["Close"].iloc[-1] - bars["Open"].iloc[0]) if len(bars) else 0.0
        side = "call" if move > 0 else ("put" if move < 0 else None)
        if side is None or (calls_only and side != "call"):
            return None
        if rsi_max is not None and side == "call" and not rsi[i - 1] < rsi_max:
            return None
        return side

    return direction


def run_plan(daily, vix, hourly, cfg: Config, direction=None, tie_rule: str = "path"):
    from .daily_target_backtest import simulate

    return simulate(daily, vix, cfg, intraday=hourly, direction=direction or first_hour_direction(daily),
                    entry_bar=1, structure_stop=True, stop_on_close=True, backstop=True, tie_rule=tie_rule)


def summarize(trades, start: dt.date, end: dt.date, open_usd: float = 0.0) -> dict:
    t = [x for x in trades if start <= x.entry_date <= end]
    pnl = pd.Series([x.pnl_usd for x in t], dtype=float)
    equity = pnl.cumsum()
    mid = start + (end - start) / 2
    wins = int((pnl > 0).sum())
    return {
        "trades": len(t),
        "wins": wins,
        "win_rate": wins / len(t) if t else 0.0,
        "total": float(pnl.sum()),
        "open": float(open_usd),
        "net": float(pnl.sum() + open_usd),
        "worst": float(pnl.min()) if len(pnl) else 0.0,
        "avg_loss": float(pnl[pnl <= 0].mean()) if (pnl <= 0).any() else 0.0,
        "max_dd": float((equity.cummax().clip(lower=0) - equity).max()) if len(equity) else 0.0,
        "first_half": float(sum(x.pnl_usd for x in t if x.entry_date < mid)),
        "second_half": float(sum(x.pnl_usd for x in t if x.entry_date >= mid)),
        "capital": float(np.mean([x.capital_usd for x in t])) if t else 0.0,
    }


STOPS = (0.0, 500.0, 1000.0, 2000.0, 3000.0)


def run_all(daily, vix, hourly, cfg: Config, contracts=5, target=500.0, backstop=2000.0, delta=0.85, rsi_max=70.0):
    days = sorted(set(hourly.index.date))
    end = days[-1]
    windows = (("Last year", end - dt.timedelta(days=365)), ("Two years", max(days[0], end - dt.timedelta(days=730))))
    rsi_rows = []
    for stop in STOPS:
        cells = []
        for _, start in windows:
            trades, open_call, skipped = simulate_rsi_calls(daily, vix, hourly, cfg, contracts, target, delta,
                                                            rsi_max=rsi_max, stop_usd=stop, start=start)
            cells.append((summarize(trades, start, end, open_call.mark_usd if open_call else 0.0), open_call))
        rsi_rows.append((stop, cells))

    base = plan_size_config(cfg, contracts, target, backstop)
    variants = (
        ("Daily plan rules (calls + puts, 0.80 delta)", base, None, "path"),
        (f"...with {delta:.2f} delta", plan_size_config(cfg, contracts, target, backstop, delta), None, "path"),
        ("...same-hour ties scored as stops", base, None, "stop"),
        ("...2x slippage", dataclasses.replace(base, daily_target_slippage=2 * base.daily_target_slippage), None, "path"),
        ("...3x slippage", dataclasses.replace(base, daily_target_slippage=3 * base.daily_target_slippage), None, "path"),
        (f"...plus RSI < {rsi_max:.0f} for calls", base, first_hour_direction(daily, rsi_max=rsi_max), "path"),
        ("...calls only", base, first_hour_direction(daily, calls_only=True), "path"),
    )
    plan_rows = []
    for name, c, direction, tie in variants:
        trades = run_plan(daily, vix, hourly, c, direction, tie)
        plan_rows.append((name, [summarize(trades, start, end) for _, start in windows]))
    return windows, end, rsi_rows, plan_rows


def _money(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def render_report(windows, end, rsi_rows, plan_rows, contracts=5, target=500.0, backstop=2000.0, delta=0.85, rsi_max=70.0) -> str:
    (l1, s1), (l2, s2) = windows
    lines = [
        f"# Fixed ${target:,.0f} Target at {contracts} Contracts -- {dt.date.today():%Y-%m-%d}",
        "",
        f"QQQ hourly bars; {l1.lower()} = {s1} to {end}, {l2.lower()} = {s2} to {end}. Options are Black-Scholes "
        "at the prior day's VIX x 1.15 with $0.05/share slippage per fill, not real quotes.",
        "",
        f"## 1. RSI calls: buy {contracts} calls at {delta:.2f} delta, ~60 DTE, at the open if RSI(14) < {rsi_max:.0f}; "
        f"sell at +{_money(target)}",
        "",
        "One position at a time; the next opens the session after an exit. A position still open with 5 days to "
        "expiration is sold at that close (an assumption: the original rules had none). \"Open\" is the position "
        "still held at the end, marked at the last close.",
        "",
        f"| Stop | {l1}: trades | Win rate | Closed P&L | Still open | Net | Worst trade | "
        f"{l2}: trades | Win rate | Closed P&L | Net |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for stop, ((a, _), (b, _)) in rsi_rows:
        lines.append(
            f"| {'none' if not stop else '-' + _money(stop)} | {a['trades']} | {a['win_rate']:.0%} | {_money(a['total'])} | "
            f"{_money(a['open'])} | **{_money(a['net'])}** | {_money(a['worst'])} | {b['trades']} | {b['win_rate']:.0%} | "
            f"{_money(b['total'])} | **{_money(b['net'])}** |"
        )
    lines += [
        "",
        f"## 2. The daily plan's rules at {contracts} contracts: +{_money(target)} target, -{_money(backstop)} hard stop",
        "",
        "Skip-day rules (gap >= 0.8%, prior VIX >= 20, FOMC/CPI), first-hour direction at 10:30 (calls and puts), "
        "level stop on an hourly close past the first hour's low/high, 5-session time stop.",
        "",
        f"| Variant | {l1}: trades | Win rate | Total | Worst | Max DD | 1st half | 2nd half | "
        f"{l2}: trades | Total | 1st half | 2nd half |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, (a, b) in plan_rows:
        lines.append(
            f"| {name} | {a['trades']} | {a['win_rate']:.0%} | **{_money(a['total'])}** | {_money(a['worst'])} | "
            f"{_money(a['max_dd'])} | {_money(a['first_half'])} | {_money(a['second_half'])} | {b['trades']} | "
            f"**{_money(b['total'])}** | {_money(b['first_half'])} | {_money(b['second_half'])} |"
        )
    lines += [
        "",
        "**Caveats:** option prices are modeled, not quoted; real spreads on deep in-the-money QQQ options are "
        "often wider than the $0.05-$0.15/share tested here, and the daily plan's rules turn negative at 3x "
        "slippage. About two years of hourly data only. Simulated history, not a prediction.",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backtest a fixed $ target at your own size")
    parser.add_argument("--contracts", type=int, default=5)
    parser.add_argument("--target", type=float, default=500.0, help="Take profit in $ for the whole position")
    parser.add_argument("--backstop", type=float, default=2000.0, help="Daily plan rules: hard stop in $")
    parser.add_argument("--delta", type=float, default=0.85, help="RSI calls (and a plan variant): option delta")
    parser.add_argument("--rsi-max", type=float, default=70.0)
    parser.add_argument("--output-dir", default="reports/backtests")
    args = parser.parse_args(argv)

    from .data import get_intraday_history, get_price_history, get_vix_history

    cfg = plan_config(Config())
    try:
        daily = get_price_history(cfg.daily_target_symbol, "5y")
        vix = get_vix_history("5y")
        hourly = get_intraday_history(cfg.daily_target_symbol, "1h", "730d")[["Open", "High", "Low", "Close"]]
        windows, end, rsi_rows, plan_rows = run_all(daily, vix, hourly, cfg, args.contracts, args.target,
                                                    args.backstop, args.delta, args.rsi_max)
    except Exception as exc:
        print(f"Fixed-target backtest failed: {exc}", file=sys.stderr)
        return 1
    report = render_report(windows, end, rsi_rows, plan_rows, args.contracts, args.target, args.backstop,
                           args.delta, args.rsi_max)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"fixed-target-{dt.date.today():%Y-%m-%d}.md"
    out.write_text(report)
    print(report)
    print(f"Saved report to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
