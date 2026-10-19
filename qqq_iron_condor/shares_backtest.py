"""Backtest the daily $ target's signal on QQQ *shares* -- no option modeling.

The options backtest (daily_target_backtest.py) has to price 60 DTE options
with Black-Scholes at a VIX-based IV, because free historical option quotes
don't exist. This replays the same rules on real QQQ hourly prices instead,
so it answers a narrower, cleaner question: does the *signal* (first-hour
direction + a stop on an hourly close past the first hour's low/high) have
an edge before any option effects (spread, theta, IV changes)?

Rules (defaults sized so 800 shares ~ 10 contracts at 0.80 delta):
  - Skip the day if the opening gap vs the prior close is >= the config's
    gap threshold (0.8%), the prior VIX close is >= its spike threshold
    (20), or it's a scheduled FOMC/CPI day.
  - Direction: the 9:30-10:30 hourly bar's close vs its open. Up -> long,
    down -> short, flat -> no trade. Enter at the 10:30 bar's open.
  - Exit on the first of: +`target` per share (touch); `stop_mode`
    "close" -- an hourly *close* past the first hour's low (long) / high
    (short), exiting at that close; "touch" -- any touch of that level;
    "none" -- no level stop; a hard stop at -`hard_stop` per share (touch);
    or the close of session `max_hold`.
  - A later day that opens beyond a level exits at the open.
  - A bar touching both the target and a touch-stop is resolved by
    `tie_rule`: "path" (up bar = low first, down bar = high first) or
    "stop" (worst case).
  - Costs: `cost` per share each way, deducted from every trade.

Usage:
    python -m qqq_iron_condor.shares_backtest              # report to reports/backtests/
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from .config import Config


@dataclass
class ShareTrade:
    entry_date: dt.date
    exit_date: dt.date
    side: int  # +1 long, -1 short
    entry: float
    exit: float
    pnl_usd: float
    outcome: str  # "target", "level stop", "hard stop", "gap", "time"
    sessions: int


def simulate_shares(
    daily: pd.DataFrame,
    vix: pd.DataFrame,
    intraday: pd.DataFrame,
    cfg: Config,
    shares: int = 800,
    target: float = 1.25,
    hard_stop: float = 5.0,
    cost: float = 0.03,
    stop_mode: str = "close",
    skip_rules: bool = True,
    tie_rule: str = "path",
    max_hold: int = 5,
    skip_day: Optional[Callable[[dt.date, int], bool]] = None,
) -> list[ShareTrade]:
    """daily / vix: daily OHLC with a date-like index; intraday: hourly bars
    (index = ET wall time, first bar of each session = 9:30). `skip_day(date,
    side)` -> True skips that day's entry (side +1 long, -1 short); it must
    only use information known by the entry."""
    closes = pd.Series(daily["Close"].to_numpy(float), index=pd.to_datetime(daily.index).date)
    vix_closes = pd.Series(vix["Close"].to_numpy(float), index=pd.to_datetime(vix.index).date)
    close_dates = np.array(closes.index)
    vix_dates = np.array(vix_closes.index)

    def prior(series, dates, d):
        k = np.searchsorted(dates, d) - 1
        return float(series.iloc[k]) if k >= 0 else None

    trades: list[ShareTrade] = []
    pos: Optional[dict] = None
    for d, bars in intraday.groupby(intraday.index.date):
        start = 0
        if pos is None:
            prev_close = prior(closes, close_dates, d)
            if prev_close is None or len(bars) < 2:
                continue
            if skip_rules:
                prev_vix = prior(vix_closes, vix_dates, d)
                gap = abs(float(bars["Open"].iloc[0]) / prev_close - 1.0) * 100.0
                if (
                    gap >= cfg.gap_threshold_pct
                    or (prev_vix is not None and prev_vix >= cfg.vix_spike_threshold)
                    or d.isoformat() in cfg.macro_event_dates
                ):
                    continue
            first = bars.iloc[0]
            move = float(first["Close"] - first["Open"])
            if move == 0:
                continue
            side = 1 if move > 0 else -1
            if skip_day is not None and skip_day(d, side):
                continue
            entry = float(bars["Open"].iloc[1])
            level = float(first["Low"]) if side > 0 else float(first["High"])
            if (side > 0 and level >= entry) or (side < 0 and level <= entry):
                level = None  # already on the wrong side: no level stop
            pos = {"side": side, "entry": entry, "level": level, "date": d, "sessions": 0}
            start = 1

        pos["sessions"] += 1
        s, e, level = pos["side"], pos["entry"], pos["level"]
        tgt, hard = e + s * target, e - s * hard_stop
        exit_px = outcome = None

        if start == 0:  # a later session: gap through a level at the open
            o = float(bars["Open"].iloc[0])
            if (o - tgt) * s >= 0:
                exit_px, outcome = o, "target"
            elif (o - hard) * s <= 0 or (stop_mode != "none" and level is not None and (o - level) * s < 0):
                exit_px, outcome = o, "gap"

        if exit_px is None:
            for k in range(start, len(bars)):
                b = bars.iloc[k]
                hi, lo, cl, op = float(b["High"]), float(b["Low"]), float(b["Close"]), float(b["Open"])
                hit_t = hi >= tgt if s > 0 else lo <= tgt
                hit_h = lo <= hard if s > 0 else hi >= hard
                hit_touch = stop_mode == "touch" and level is not None and (lo <= level if s > 0 else hi >= level)
                hit_close = stop_mode == "close" and level is not None and (cl < level if s > 0 else cl > level)
                if hit_t and (hit_h or hit_touch):
                    stop_first = tie_rule == "stop" or ((cl >= op) == (s > 0))
                    if stop_first:
                        hit_t = False
                    else:
                        hit_h = hit_touch = False
                if hit_h and not hit_t:
                    exit_px, outcome = hard, "hard stop"
                    break
                if hit_touch and not hit_t:
                    exit_px, outcome = level, "level stop"
                    break
                if hit_t:
                    exit_px, outcome = tgt, "target"
                    break
                if hit_close:
                    exit_px, outcome = cl, "level stop"
                    break
            if exit_px is None and pos["sessions"] >= max_hold:
                exit_px, outcome = float(bars["Close"].iloc[-1]), "time"

        if exit_px is not None:
            pnl = (s * (exit_px - e) - 2 * cost) * shares
            trades.append(ShareTrade(pos["date"], d, s, round(e, 2), round(exit_px, 2), round(pnl, 2), outcome, pos["sessions"]))
            pos = None
    return trades


def summarize(trades: list[ShareTrade], midpoint: dt.date) -> dict:
    pnl = pd.Series([t.pnl_usd for t in trades], dtype=float)
    losses = pnl[pnl <= 0]
    equity = pnl.cumsum()
    years = pd.Series([t.entry_date.year for t in trades])
    return {
        "trades": len(trades),
        "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
        "total": float(pnl.sum()),
        "avg": float(pnl.mean()) if len(pnl) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "worst": float(losses.min()) if len(losses) else 0.0,
        "max_dd": float((equity.cummax().clip(lower=0) - equity).max()) if len(equity) else 0.0,
        "first_half": float(sum(t.pnl_usd for t in trades if t.entry_date < midpoint)),
        "second_half": float(sum(t.pnl_usd for t in trades if t.entry_date >= midpoint)),
        "by_year": pnl.groupby(years).sum().round(0).to_dict() if len(pnl) else {},
        "longs": float(sum(t.pnl_usd for t in trades if t.side > 0)),
        "shorts": float(sum(t.pnl_usd for t in trades if t.side < 0)),
    }


VARIANTS = (
    ("Level stop on hourly close + $5 hard stop (live rules)", {}),
    ("...same, ties scored as stops", {"tie_rule": "stop"}),
    ("...same, 2x costs", {"cost_mult": 2}),
    ("...same, 3x costs", {"cost_mult": 3}),
    ("Level stop on any touch", {"stop_mode": "touch"}),
    ("Fixed -$1.25/share stop (the old -$1,000)", {"stop_mode": "none", "hard_stop": 1.25}),
    ("Live rules without the skip-day rules", {"skip_rules": False}),
)


def run_variants(daily, vix, intraday, cfg, shares=800, cost=0.03) -> tuple[list, dt.date, dt.date, dt.date]:
    days = sorted(set(intraday.index.date))
    midpoint = days[len(days) // 2]
    rows = []
    for name, kw in VARIANTS:
        kw = dict(kw)
        c = cost * kw.pop("cost_mult", 1)
        rows.append((name, summarize(simulate_shares(daily, vix, intraday, cfg, shares=shares, cost=c, **kw), midpoint)))
    return rows, days[0], midpoint, days[-1]


def render_report(rows, first, midpoint, last, shares=800, cost=0.03) -> str:
    money = lambda x: f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"  # noqa: E731
    lines = [
        f"# QQQ Shares Backtest (signal only) -- {dt.date.today():%Y-%m-%d}",
        "",
        f"Window: {first} to {last} (first half ends {midpoint}), real QQQ hourly bars. {shares} shares "
        f"(~10 contracts at 0.80 delta), ${cost:.2f}/share costs each way, +$1.25/share target, "
        "first-hour direction, entry at 10:30. No option modeling: this tests the signal and the "
        "stop rule, not the option spread/theta the live plan pays.",
        "",
        "| Variant | Trades | Win rate | Total | Avg/trade | Avg loss | Worst | Max DD | 1st half | 2nd half |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, r in rows:
        lines.append(
            f"| {name} | {r['trades']} | {r['win_rate']:.0%} | {money(r['total'])} | {money(r['avg'])} | "
            f"{money(r['avg_loss'])} | {money(r['worst'])} | {money(r['max_dd'])} | {money(r['first_half'])} | "
            f"{money(r['second_half'])} |"
        )
    live = rows[0][1]
    years = ", ".join(f"{y}: {money(v)}" for y, v in live["by_year"].items())
    lines += [
        "",
        f"Live rules by year: {years}. Longs {money(live['longs'])}, shorts {money(live['shorts'])}.",
        "",
        "**Caveats:** the level-stop rule was chosen after testing on this same window, so these "
        "results are partly in-sample -- live paper trading is the out-of-sample test. Hourly bars "
        "hide the order of moves inside each hour, fills are assumed at exact levels, and ~2 years "
        "is a short, mostly rising history.",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the daily $ target signal on QQQ shares")
    parser.add_argument("--shares", type=int, default=800)
    parser.add_argument("--cost", type=float, default=0.03, help="Cost per share each way")
    parser.add_argument("--output-dir", default="reports/backtests")
    args = parser.parse_args(argv)

    from .data import get_intraday_history, get_price_history, get_vix_history

    cfg = Config()
    try:
        daily = get_price_history(cfg.daily_target_symbol, "5y")
        vix = get_vix_history("5y")
        intraday = get_intraday_history(cfg.daily_target_symbol, "1h", "730d")
        rows, first, mid, last = run_variants(daily, vix, intraday, cfg, args.shares, args.cost)
    except Exception as exc:
        print(f"Shares backtest failed: {exc}", file=sys.stderr)
        return 1
    report = render_report(rows, first, mid, last, args.shares, args.cost)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"shares-{dt.date.today():%Y-%m-%d}.md"
    out.write_text(report)
    print(report)
    print(f"Saved report to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
