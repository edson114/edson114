"""Backtest of the MEIC (Multiple-Entry Iron Condor) 0DTE strategy: several
independently-managed credit spreads legged into the market through the
day on SPX 0DTE options, each sized to a target *credit* rather than a
target *delta*, each stopped out at a fixed loss multiple of its own
credit rather than held unconditionally to expiration like
`backtest.run_backtest`'s single delta-targeted condor.

This reuses `backtest.py`'s synthetic-chain machinery (Black-Scholes
pricing, VIX as a flat no-skew/no-term-structure IV proxy, no free
historical option-chain data -- see that module's docstring for the
baseline simulation caveats, which all still apply here) but adds two
things a single entry held to expiration doesn't need:

1. **Multiple entries per day, each at its own synthetic spot level.**
   Free daily bars give Open/High/Low/Close, not a real intraday path, so
   each entry's spot is linearly interpolated between the day's Open and
   Close by how far its entry time sits between the regular 9:30am-4:00pm
   ET session -- matching the rule's "late morning through afternoon"
   window. This captures the day's net intraday *drift* across entries
   but NOT real intraday noise or reversals between them: a day that
   round-trips (e.g. down then back up) looks smoother here than it
   actually traded, which understates how differently two same-day
   entries could really have fared.
2. **A per-side stop-loss**, checked against a worst-case adverse spot
   level derived from the day's realized High (for the call side) and Low
   (for the put side). Free daily bars carry no timestamp for *when* the
   High/Low printed, so assuming every entry sees the full day's eventual
   extreme overstates stop risk for entries placed after it already
   happened -- early tests against this exact assumption stopped out
   nearly every single leg, even on calm days, which isn't a credible
   stand-in for real MEIC performance. Instead, the room between each
   entry's spot and the day's extreme is scaled down by the square root of
   the fraction of the session remaining after that entry (a standard
   volatility-scaling heuristic: an entry at 10:30am, with most of the day
   still ahead, is assumed capable of seeing close to the full extreme;
   one at 2:15pm, with little time left, only a small fraction of it).
   This is still an approximation standing in for a real intraday path,
   not a replay of one. The stop check itself re-prices the spread at the
   SAME time-to-expiration as entry (no intraday theta decay credited), a
   deliberately conservative assumption -- a real position would have
   picked up some time-decay cushion between entry and whenever that
   worst level printed. The stop is also assumed to fill exactly at its
   target debit (2x credit by default) with no slippage, which cuts the
   other way -- optimistic, since a fast 0DTE move can blow through a
   stop order in reality.

Net effect: this is a cruder approximation than `backtest.py`'s
single-entry simulation, specifically around intraday path and stop
timing. Use it to gauge the STRUCTURE of the MEIC approach (entry-time
diversification vs. one delta-targeted condor, a stop-loss vs.
hold-to-expiration) against real historical SPX/VIX regimes, not as a
precise estimate of its historical Sharpe ratio.

One specific bias worth flagging for a CREDIT-targeted strategy in
particular: flat VIX-as-IV, with no skew and no term structure, likely
UNDERSTATES how far OTM a real market would place a given credit level --
real SPX 0DTE options carry put skew and their own intraday IV dynamics
that a 30-day constant-maturity VIX read doesn't capture, so a real
$1.00-$1.75 credit strike often sits further from spot than this
backtest's does. That makes this simulation's stop-out rate run
noticeably higher than live MEIC trackers typically report -- read a high
stop rate here as a signal of this specific modeling gap more than a
verdict on the strategy itself.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from .backtest import MIN_HISTORY_DAYS, infer_strike_step, simulate_chain
from .config import Config
from .options_math import MARKET_TIMEZONE, ZoneInfo, bs_price, time_to_expiration_years

MARKET_OPEN_HOUR, MARKET_OPEN_MINUTE = 9, 30
MARKET_CLOSE_HOUR = 16

# Defaults from the MEIC rules this backtest mirrors: 6 entries/day, 45
# minutes apart, starting mid-morning; $1.00-$1.75 credit target per side;
# 50-60-wide spreads (55 as a single representative value); stop at 2x the
# initial credit (a 1x net loss).
DEFAULT_ENTRIES_PER_DAY = 6
DEFAULT_ENTRY_SPACING_MINUTES = 45
DEFAULT_FIRST_ENTRY_HOUR, DEFAULT_FIRST_ENTRY_MINUTE = 10, 30
DEFAULT_CREDIT_LOW = 1.00
DEFAULT_CREDIT_HIGH = 1.75
DEFAULT_SPREAD_WIDTH = 55.0
DEFAULT_STOP_MULTIPLE = 2.0


def _mid(row: pd.Series) -> float:
    bid = float(row.get("bid", 0.0) or 0.0)
    ask = float(row.get("ask", 0.0) or 0.0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return float(row.get("lastPrice", 0.0) or 0.0)


def entry_times(
    n: int,
    first_hour: int = DEFAULT_FIRST_ENTRY_HOUR,
    first_minute: int = DEFAULT_FIRST_ENTRY_MINUTE,
    spacing_minutes: int = DEFAULT_ENTRY_SPACING_MINUTES,
) -> list[dt.time]:
    """N entry clock times, `spacing_minutes` apart starting at
    first_hour:first_minute -- e.g. the default 6 entries land at 10:30,
    11:15, 12:00, 12:45, 13:30, 14:15 ET."""
    start_minutes = first_hour * 60 + first_minute
    return [dt.time(hour=(start_minutes + i * spacing_minutes) // 60, minute=(start_minutes + i * spacing_minutes) % 60) for i in range(n)]


def _session_fraction(t: dt.time) -> float:
    """How far a clock time sits through the regular 9:30-16:00 ET
    session, as a 0-1 fraction -- used to interpolate that entry's
    synthetic spot between the day's Open and Close."""
    open_minutes = MARKET_OPEN_HOUR * 60 + MARKET_OPEN_MINUTE
    close_minutes = MARKET_CLOSE_HOUR * 60
    t_minutes = t.hour * 60 + t.minute
    return max(0.0, min(1.0, (t_minutes - open_minutes) / (close_minutes - open_minutes)))


def _entry_datetime(trade_date: dt.date, t: dt.time) -> dt.datetime:
    tzinfo = ZoneInfo(MARKET_TIMEZONE) if ZoneInfo else None
    return dt.datetime.combine(trade_date, t, tzinfo=tzinfo)


def _credit_targeted_strike(
    df: pd.DataFrame, spot: float, width: float, credit_low: float, credit_high: float, side: str,
) -> Optional[tuple[float, float, float]]:
    """Search OTM strikes on `side` ("call" or "put") for the vertical
    spread (short strike, short +/- width as the long strike) whose mid
    credit lands closest to [credit_low, credit_high] -- 0 distance (and
    ties broken toward the range midpoint) for any strike inside the
    range, otherwise whichever strike's credit comes closest to the
    nearest edge (e.g. every strike overshoots the range on a high-IV day,
    or undershoots it on a dead-calm one).

    Returns (short_strike, long_strike, credit) or None if no strike on
    this side has a usable (bid>0 or ask>0) quote.
    """
    if side == "call":
        candidates = df[df["strike"] > spot]
    else:
        candidates = df[df["strike"] < spot]
    if candidates.empty:
        return None

    strikes = df.set_index("strike")
    best = None
    best_score = None
    midpoint = (credit_low + credit_high) / 2.0
    for _, row in candidates.iterrows():
        short_strike = float(row["strike"])
        long_strike = short_strike + width if side == "call" else short_strike - width
        if long_strike not in strikes.index:
            # snap to the nearest listed strike (chain is evenly stepped,
            # but the exact target may fall outside the simulated range)
            nearest_pos = np.abs(strikes.index.to_numpy() - long_strike).argmin()
            long_strike = float(strikes.index[nearest_pos])
        long_row = strikes.loc[long_strike]
        if isinstance(long_row, pd.DataFrame):  # duplicate strikes shouldn't happen, but don't crash if they do
            long_row = long_row.iloc[0]

        credit = _mid(row) - _mid(long_row)
        if credit <= 0:
            continue

        if credit_low <= credit <= credit_high:
            score = abs(credit - midpoint)
        else:
            score = (credit_low - credit) if credit < credit_low else (credit - credit_high)
            score += 1000.0  # always worse than any in-range candidate

        if best_score is None or score < best_score:
            best_score = score
            best = (short_strike, long_strike, credit)

    return best


@dataclass
class MeicLegResult:
    trade_date: dt.date
    entry_index: int
    entry_time: str
    side: str  # "call" or "put"
    short_strike: float
    long_strike: float
    width: float
    credit: float
    stopped: bool
    exit_value: float  # debit paid (or owed at settlement) to close this side
    pnl: float


@dataclass
class MeicDaySummary:
    trade_date: dt.date
    legs: list  # list[MeicLegResult]
    net_pnl: float
    vix_level: float
    iv_regime: str


@dataclass
class MeicRegimeStats:
    regime: str
    num_days: int
    win_rate_days_pct: float
    avg_daily_pnl: float
    total_pnl: float


@dataclass
class MeicBacktestSummary:
    num_days: int
    num_legs: int
    total_pnl: float
    avg_daily_pnl: float
    win_rate_days_pct: float
    leg_win_rate_pct: float
    stop_rate_pct: float
    avg_credit_per_leg: float
    profit_factor: float
    max_drawdown: float
    by_regime: list = field(default_factory=list)  # list[MeicRegimeStats]


def _simulate_leg(
    side: str, chain_calls: pd.DataFrame, chain_puts: pd.DataFrame, spot: float, width: float,
    credit_low: float, credit_high: float, t_years: float, rate: float, iv: float,
    adverse_spot: float, close_spot: float, stop_multiple: float,
) -> Optional[tuple[float, float, float, bool, float, float]]:
    """Returns (short_strike, long_strike, credit, stopped, exit_value, pnl) for one side.

    adverse_spot is the worst-case spot level this leg is checked against
    for its stop -- already scaled down from the day's raw High/Low by how
    much session time remained after entry (see run_meic_backtest)."""
    df = chain_calls if side == "call" else chain_puts
    picked = _credit_targeted_strike(df, spot, width, credit_low, credit_high, side)
    if picked is None:
        return None
    short_strike, long_strike, credit = picked

    extreme_spot = adverse_spot
    short_price_extreme = bs_price(extreme_spot, short_strike, t_years, rate, iv, side)
    long_price_extreme = bs_price(extreme_spot, long_strike, t_years, rate, iv, side)
    worst_debit = short_price_extreme - long_price_extreme

    stop_target = credit * stop_multiple
    if worst_debit >= stop_target:
        exit_value = stop_target
        pnl = credit - exit_value
        return short_strike, long_strike, credit, True, round(exit_value, 4), round(pnl, 4)

    # Never stopped -- settle at the day's close via intrinsic value,
    # capped at the spread's own width (standard vertical-spread math,
    # same as backtest._settle_pnl/journal._settlement_value).
    if side == "call":
        loss = min(max(close_spot - short_strike, 0.0), long_strike - short_strike)
    else:
        loss = min(max(short_strike - close_spot, 0.0), short_strike - long_strike)
    exit_value = loss
    pnl = credit - exit_value
    return short_strike, long_strike, credit, False, round(exit_value, 4), round(pnl, 4)


def run_meic_backtest(
    price_history: pd.DataFrame,
    vix_history: pd.DataFrame,
    cfg: Config,
    apply_gates: bool = True,
    strike_step: Optional[float] = None,
    entries_per_day: int = DEFAULT_ENTRIES_PER_DAY,
    entry_spacing_minutes: int = DEFAULT_ENTRY_SPACING_MINUTES,
    credit_low: float = DEFAULT_CREDIT_LOW,
    credit_high: float = DEFAULT_CREDIT_HIGH,
    spread_width: float = DEFAULT_SPREAD_WIDTH,
    stop_multiple: float = DEFAULT_STOP_MULTIPLE,
) -> list[MeicDaySummary]:
    dates = price_history.index
    open_ = price_history["Open"]
    high = price_history["High"]
    low = price_history["Low"]
    close = price_history["Close"]

    vix_close = vix_history["Close"].reindex(dates, method="ffill").ffill()

    if strike_step is None:
        strike_step = infer_strike_step(float(close.iloc[-1]))

    times = entry_times(entries_per_day, spacing_minutes=entry_spacing_minutes)

    days: list[MeicDaySummary] = []
    for i in range(MIN_HISTORY_DAYS, len(dates)):
        trade_date = dates[i].date()

        if apply_gates:
            event_label = cfg.macro_event_dates.get(trade_date.isoformat())
            gap_pct = (float(close.iloc[i]) / float(close.iloc[i - 1]) - 1.0) * 100.0
            vix_level = float(vix_close.iloc[i])
            if event_label or abs(gap_pct) >= cfg.gap_threshold_pct or vix_level >= cfg.vix_spike_threshold:
                continue

        day_open = float(open_.iloc[i])
        day_close = float(close.iloc[i])
        day_high = float(high.iloc[i])
        day_low = float(low.iloc[i])
        iv = float(vix_close.iloc[i]) / 100.0

        legs: list[MeicLegResult] = []
        for idx, t in enumerate(times):
            frac = _session_fraction(t)
            entry_spot = day_open + (day_close - day_open) * frac
            t_years = time_to_expiration_years(0, now=_entry_datetime(trade_date, t))

            # Scale the room between this entry and the day's eventual
            # extreme by sqrt(fraction of the session remaining after
            # entry) -- an entry early in the day is assumed capable of
            # seeing close to the full extreme, one near the close only a
            # small fraction of it. Never worse than the day's actual
            # High/Low, never better than the entry spot itself.
            remaining_frac = max(0.0, 1.0 - frac)
            scale = remaining_frac ** 0.5
            adverse_call_spot = entry_spot + max(0.0, day_high - entry_spot) * scale
            adverse_put_spot = entry_spot - max(0.0, entry_spot - day_low) * scale

            chain = simulate_chain(
                entry_spot, iv, 0, cfg.risk_free_rate, trade_date.isoformat(),
                strike_step=strike_step, t_years=t_years,
            )

            for side in ("call", "put"):
                adverse_spot = adverse_call_spot if side == "call" else adverse_put_spot
                result = _simulate_leg(
                    side, chain.calls, chain.puts, entry_spot, spread_width, credit_low, credit_high,
                    t_years, cfg.risk_free_rate, iv, adverse_spot, day_close, stop_multiple,
                )
                if result is None:
                    continue
                short_strike, long_strike, credit, stopped, exit_value, pnl = result
                legs.append(MeicLegResult(
                    trade_date=trade_date,
                    entry_index=idx,
                    entry_time=t.strftime("%H:%M"),
                    side=side,
                    short_strike=short_strike,
                    long_strike=long_strike,
                    width=spread_width,
                    credit=round(credit, 4),
                    stopped=stopped,
                    exit_value=exit_value,
                    pnl=pnl,
                ))

        if not legs:
            continue

        vix_pctile = ind.percentile_rank(vix_close.iloc[max(0, i - MIN_HISTORY_DAYS):i + 1], float(vix_close.iloc[i]))
        regime = "High IV" if vix_pctile >= 70 else ("Low IV" if vix_pctile <= 20 else "Normal IV")

        days.append(MeicDaySummary(
            trade_date=trade_date,
            legs=legs,
            net_pnl=round(sum(leg.pnl for leg in legs), 4),
            vix_level=float(vix_close.iloc[i]),
            iv_regime=regime,
        ))

    return days


def _regime_stats(regime: str, days: list[MeicDaySummary]) -> MeicRegimeStats:
    if not days:
        return MeicRegimeStats(regime=regime, num_days=0, win_rate_days_pct=float("nan"), avg_daily_pnl=float("nan"), total_pnl=0.0)
    pnls = [d.net_pnl for d in days]
    wins = [p for p in pnls if p > 0]
    return MeicRegimeStats(
        regime=regime,
        num_days=len(days),
        win_rate_days_pct=len(wins) / len(days) * 100.0,
        avg_daily_pnl=sum(pnls) / len(days),
        total_pnl=sum(pnls),
    )


def summarize(days: list[MeicDaySummary]) -> MeicBacktestSummary:
    if not days:
        return MeicBacktestSummary(
            num_days=0, num_legs=0, total_pnl=0.0, avg_daily_pnl=float("nan"), win_rate_days_pct=float("nan"),
            leg_win_rate_pct=float("nan"), stop_rate_pct=float("nan"), avg_credit_per_leg=float("nan"),
            profit_factor=float("nan"), max_drawdown=0.0, by_regime=[],
        )

    all_legs = [leg for d in days for leg in d.legs]
    daily_pnls = [d.net_pnl for d in days]
    leg_wins = [leg for leg in all_legs if leg.pnl > 0]
    stopped_legs = [leg for leg in all_legs if leg.stopped]

    equity = np.cumsum(daily_pnls)
    running_peak = np.maximum.accumulate(equity)
    max_drawdown = float((running_peak - equity).max()) if len(equity) else 0.0

    daily_wins = [p for p in daily_pnls if p > 0]
    daily_losses = [p for p in daily_pnls if p <= 0]
    gross_win = sum(daily_wins)
    gross_loss = abs(sum(daily_losses))

    by_regime = [
        _regime_stats(regime, [d for d in days if d.iv_regime == regime])
        for regime in ("Low IV", "Normal IV", "High IV")
    ]
    by_regime = [r for r in by_regime if r.num_days > 0]

    return MeicBacktestSummary(
        num_days=len(days),
        num_legs=len(all_legs),
        total_pnl=sum(daily_pnls),
        avg_daily_pnl=sum(daily_pnls) / len(days),
        win_rate_days_pct=len(daily_wins) / len(days) * 100.0,
        leg_win_rate_pct=(len(leg_wins) / len(all_legs) * 100.0) if all_legs else float("nan"),
        stop_rate_pct=(len(stopped_legs) / len(all_legs) * 100.0) if all_legs else float("nan"),
        avg_credit_per_leg=(sum(leg.credit for leg in all_legs) / len(all_legs)) if all_legs else float("nan"),
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else float("nan"),
        max_drawdown=max_drawdown,
        by_regime=by_regime,
    )


def _fmt(x: float, decimals: int = 2) -> str:
    if x is None or x != x:
        return "n/a"
    return f"{x:.{decimals}f}"


def render_meic_backtest_report(
    symbol: str, years: float, summary: MeicBacktestSummary, apply_gates: bool,
    entries_per_day: int, credit_low: float, credit_high: float, spread_width: float, stop_multiple: float,
) -> str:
    generated_at = dt.datetime.now()
    symbol = symbol.lstrip("^")
    parts = [
        f"# {symbol} MEIC (Multiple-Entry Iron Condor) Backtest -- {generated_at.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"> Simulated over ~{years:.1f} years of history. Per-leg/per-day P&L (multiply by 100 for "
        "a standard contract). **This is a Black-Scholes simulation with linearly-interpolated "
        "intraday spot and a worst-case-at-the-day's-High/Low stop check, driven by real "
        "historical underlying/VIX data -- NOT a replay of real historical option quotes or a "
        "real intraday price path.** See the module docstring in "
        "`qqq_iron_condor/meic_backtest.py` for exactly what this can and can't capture. Not "
        "financial advice.",
        "",
        f"**Rules simulated:** {entries_per_day} entries/day, {spread_width:.0f}-wide spreads, "
        f"${credit_low:.2f}-${credit_high:.2f} credit target per side, stop at {stop_multiple:.1f}x "
        f"initial credit (a {stop_multiple - 1:.1f}x net loss), each side managed independently.",
        "",
        f"_Hard skip-day gates {'applied' if apply_gates else 'NOT applied'} (gap and VIX-spike "
        "gates are backtested for real; the scheduled-macro-event gate only fires for dates "
        "present in `Config.macro_event_dates`, currently populated for 2026 only)._",
        "",
        "_Flat VIX-as-IV with no skew/term structure likely understates how far OTM a real market "
        "would place a given credit level, since real SPX 0DTE options carry put skew and their own "
        "intraday IV dynamics a 30-day VIX read doesn't capture -- expect this backtest's stop-out "
        "rate to run higher than live MEIC trackers typically report as a result. Read a high stop "
        "rate below as a signal of that modeling gap more than a verdict on the strategy itself._",
        "",
    ]

    if summary.num_days == 0:
        parts.append("_No trading days produced any legs over the backtest period._")
        return "\n".join(parts)

    parts.append(f"- **Trading days:** {summary.num_days}")
    parts.append(f"- **Legs entered:** {summary.num_legs} ({summary.num_legs / summary.num_days:.1f}/day on average)")
    parts.append(f"- **Day win rate (net P&L > 0):** {_fmt(summary.win_rate_days_pct, 1)}%")
    parts.append(f"- **Per-leg win rate:** {_fmt(summary.leg_win_rate_pct, 1)}%")
    parts.append(f"- **Stop-out rate (legs that hit their stop):** {_fmt(summary.stop_rate_pct, 1)}%")
    parts.append(f"- **Avg credit collected per leg:** ${_fmt(summary.avg_credit_per_leg)}")
    parts.append(f"- **Avg daily P&L:** ${_fmt(summary.avg_daily_pnl)}")
    parts.append(f"- **Total P&L (sum over all days):** ${_fmt(summary.total_pnl)}")
    parts.append(f"- **Profit factor (gross winning days / gross losing days):** {_fmt(summary.profit_factor, 2)}")
    parts.append(f"- **Max drawdown (daily equity curve):** ${_fmt(summary.max_drawdown)}")
    parts.append("")

    if summary.by_regime:
        parts.append("| IV Regime (VIX percentile at entry) | Days | Day win rate | Avg daily P&L | Total P&L |")
        parts.append("|---|---|---|---|---|")
        for r in summary.by_regime:
            parts.append(f"| {r.regime} | {r.num_days} | {_fmt(r.win_rate_days_pct, 1)}% | ${_fmt(r.avg_daily_pnl)} | ${_fmt(r.total_pnl)} |")
        parts.append("")

    return "\n".join(parts)


def main(argv=None) -> int:
    import argparse
    import sys
    from pathlib import Path

    from . import data

    parser = argparse.ArgumentParser(description="Backtest the MEIC (Multiple-Entry Iron Condor) 0DTE strategy")
    parser.add_argument(
        "--symbol", default="^SPX",
        help="Underlying to backtest (default ^SPX -- MEIC is defined on full-size SPX 0DTE options; "
        "the default credit target/spread width are calibrated to SPX's price scale, not Config.symbol's "
        "QQQ default, so this does NOT follow Config.symbol the way the other backtests do)",
    )
    parser.add_argument("--years", type=float, default=3.0, help="Years of history to backtest (default 3)")
    parser.add_argument("--no-gates", action="store_true", help="Disable the hard skip-day gates (gap/VIX-spike/macro)")
    parser.add_argument("--output-dir", default="reports/backtests", help="Directory to save the report")
    parser.add_argument("--entries-per-day", type=int, default=DEFAULT_ENTRIES_PER_DAY)
    parser.add_argument("--entry-spacing-minutes", type=int, default=DEFAULT_ENTRY_SPACING_MINUTES)
    parser.add_argument("--credit-low", type=float, default=DEFAULT_CREDIT_LOW)
    parser.add_argument("--credit-high", type=float, default=DEFAULT_CREDIT_HIGH)
    parser.add_argument("--spread-width", type=float, default=DEFAULT_SPREAD_WIDTH)
    parser.add_argument("--stop-multiple", type=float, default=DEFAULT_STOP_MULTIPLE)
    parser.add_argument(
        "--strike-step", type=float, default=None,
        help="Synthetic chain strike spacing (default: inferred from the underlying's price level -- "
        "$5 above $1000/share, $1 below, matching SPX vs. QQQ-style strike spacing)",
    )
    args = parser.parse_args(argv)

    cfg = Config()
    period = f"{max(1, round(args.years))}y"

    print(f"Fetching {period} of {args.symbol}/VIX history from yfinance for the MEIC backtest...", file=sys.stderr)
    price_history = data.get_price_history(args.symbol, period)
    vix_history = data.get_vix_history(period)

    days = run_meic_backtest(
        price_history, vix_history, cfg, apply_gates=not args.no_gates, strike_step=args.strike_step,
        entries_per_day=args.entries_per_day, entry_spacing_minutes=args.entry_spacing_minutes,
        credit_low=args.credit_low, credit_high=args.credit_high, spread_width=args.spread_width,
        stop_multiple=args.stop_multiple,
    )
    summary = summarize(days)

    report_md = render_meic_backtest_report(
        args.symbol, args.years, summary, apply_gates=not args.no_gates,
        entries_per_day=args.entries_per_day, credit_low=args.credit_low, credit_high=args.credit_high,
        spread_width=args.spread_width, stop_multiple=args.stop_multiple,
    )
    print(report_md)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"meic-{dt.date.today().isoformat()}.md"
    out_path.write_text(report_md)
    print(f"\nSaved MEIC backtest report to {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
