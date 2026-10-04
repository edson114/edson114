"""Experiments on the daily $ target rules: entry timing, direction rules,
stop/target sizes, ATR-scaled exits, and overnight vs. intraday holds.

Every variant runs through `daily_target_backtest.simulate` /
`simulate_hold` over the same window, with the same option model and
slippage, so they're comparable with the baseline.

**Guarding against luck.** Trying ~20 variants and keeping the best is
how backtests lie: one of them will look good by chance. So each variant
is also scored separately on the first and second half of the window. A
variant only counts as a candidate if it made money in *both* halves --
and even then it's a candidate to paper-trade, not proof.

Usage:
    python -m qqq_iron_condor.daily_target_experiments
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

from .config import Config
from .daily_target_backtest import (
    MIN_DAILY_HISTORY,
    TargetTrade,
    daily_score,
    simulate,
    simulate_hold,
)

# Half of the daily score's +/-0.4 range (raw weights 0.2 + 0.1 + 0.1).
STRONG_SCORE = 0.2


@dataclass
class VariantResult:
    group: str
    name: str
    trades: int
    win_rate: float
    total_pnl: float
    avg_pnl: float
    max_drawdown: float
    first_half_pnl: float
    second_half_pnl: float
    goal_days: Optional[int]  # sessions closed at the full $ target on entry day

    @property
    def both_halves_positive(self) -> bool:
        return self.first_half_pnl > 0 and self.second_half_pnl > 0


def _drawdown(pnls: pd.Series) -> float:
    equity = pnls.cumsum()
    return float((equity.cummax().clip(lower=0) - equity).max()) if len(equity) else 0.0


def score_variant(group: str, name: str, trades: list[TargetTrade], midpoint: dt.date, count_goal_days: bool) -> VariantResult:
    pnls = pd.Series([t.pnl_usd for t in trades], dtype=float)
    first = sum(t.pnl_usd for t in trades if t.entry_date < midpoint)
    second = sum(t.pnl_usd for t in trades if t.entry_date >= midpoint)
    return VariantResult(
        group=group,
        name=name,
        trades=len(trades),
        win_rate=float((pnls > 0).mean()) if len(pnls) else 0.0,
        total_pnl=float(pnls.sum()),
        avg_pnl=float(pnls.mean()) if len(pnls) else 0.0,
        max_drawdown=_drawdown(pnls),
        first_half_pnl=float(first),
        second_half_pnl=float(second),
        goal_days=(sum(t.outcome == "target" and t.sessions == 1 for t in trades) if count_goal_days else None),
    )


def _score_cache(price_history: pd.DataFrame, vix_history: pd.DataFrame, cfg: Config) -> dict:
    return {
        i: daily_score(price_history.iloc[:i], vix_history.loc[: price_history.index[i - 1]], cfg)
        for i in range(MIN_DAILY_HISTORY, len(price_history))
    }


def _sign(x: float) -> Optional[str]:
    return "call" if x > 0 else ("put" if x < 0 else None)


def build_directions(scores: dict) -> dict[str, Callable]:
    """Direction rules, each `f(day_index, bars_before_entry) -> side`.
    The first-hour rules need entry_bar >= 1 (they read the bars before entry)."""

    def first_hour_move(bars: pd.DataFrame) -> float:
        return float(bars["Close"].iloc[-1] - bars["Open"].iloc[0]) if len(bars) else 0.0

    def agree(i, bars):
        daily, hour = _sign(scores.get(i, 0.0)), _sign(first_hour_move(bars))
        return daily if daily == hour else None

    return {
        "daily signal": lambda i, bars: _sign(scores.get(i, 0.0)),
        "strong daily signal only": lambda i, bars: _sign(scores[i]) if abs(scores.get(i, 0.0)) >= STRONG_SCORE else None,
        "first-hour momentum": lambda i, bars: _sign(first_hour_move(bars)),
        "first-hour fade": lambda i, bars: _sign(-first_hour_move(bars)),
        "daily + first hour agree": agree,
    }


def run_experiments(
    price_history: pd.DataFrame, vix_history: pd.DataFrame, intraday: pd.DataFrame, cfg: Config
) -> tuple[list[VariantResult], dt.date, dt.date, dt.date]:
    covered = set(intraday.index.date)
    window = [d.date() for d in price_history.index[MIN_DAILY_HISTORY:] if d.date() in covered]
    if not window:
        raise RuntimeError("No trading days to test after the warm-up period.")
    midpoint = window[len(window) // 2]
    # simulate_hold has no intraday filter, so start it at the same first day.
    hold_start = int(price_history.index.get_indexer([pd.Timestamp(window[0])])[0])

    scores = _score_cache(price_history, vix_history, cfg)
    dirs = build_directions(scores)

    def stops(target: float, stop: float) -> Config:
        return dataclasses.replace(cfg, daily_target_profit_usd=target, daily_target_stop_usd=stop)

    results: list[VariantResult] = []
    recipes: dict = {}  # variant name -> simulate kwargs, for the robustness re-runs

    def add(group, name, count_goal_days=True, **kwargs):
        recipes[name] = dict(kwargs)
        trades = simulate(price_history, vix_history, kwargs.pop("cfg", cfg), intraday=intraday, **kwargs)
        results.append(score_variant(group, name, trades, midpoint, count_goal_days))

    # 1. Baseline (the live rules) and entry timing.
    add("Baseline", "Live rules: daily signal, 9:30 entry, +$1,000 / -$1,000", direction=dirs["daily signal"])
    add("Entry timing", "Daily signal, 10:30 entry", direction=dirs["daily signal"], entry_bar=1)

    # 2. Direction rules (10:30 entry so the first-hour rules have a bar to read).
    for name in ("strong daily signal only", "first-hour momentum", "first-hour fade", "daily + first hour agree"):
        add("Direction", f"{name[0].upper()}{name[1:]}, 10:30 entry", direction=dirs[name], entry_bar=1)

    # 3. Target / stop sizes (daily signal, 9:30 entry).
    for target, stop in ((1000, 500), (1000, 600), (1000, 750), (1500, 750), (500, 500)):
        add("Target / stop", f"+${target:,} / -${stop:,}", direction=dirs["daily signal"], cfg=stops(target, stop))

    # 4. Exits scaled to the day's expected range instead of fixed dollars.
    for ta, sa in ((0.15, 0.10), (0.25, 0.15), (0.30, 0.30), (0.50, 0.25)):
        add(
            "ATR-scaled exits",
            f"Target {ta:.2f} x ATR / stop {sa:.2f} x ATR",
            count_goal_days=False,
            direction=dirs["daily signal"],
            atr_exits=(ta, sa),
        )

    # 5. Hold through a fixed half of the day, no target/stop.
    for side in ("call", "put"):
        for hold, label in (("overnight", "close -> next open"), ("intraday", "open -> close")):
            trades = simulate_hold(price_history, vix_history, cfg, side, hold, start=hold_start)
            trades = [t for t in trades if t.entry_date in covered]
            results.append(score_variant("Hold period", f"Always {side}s, {label}", trades, midpoint, False))

    # 6. Stress-test every candidate: would it survive pessimistic tie
    # scoring or wider real-world spreads?
    for r in [r for r in results if r.both_halves_positive and r.name in recipes]:
        base = recipes[r.name]
        base_cfg = base.get("cfg", cfg)
        others = {k: v for k, v in base.items() if k != "cfg"}
        checks = (
            ("same-hour ties scored as stops", dict(others, cfg=base_cfg, tie_rule="stop")),
            ("2x slippage", dict(others, cfg=dataclasses.replace(base_cfg, daily_target_slippage=2 * base_cfg.daily_target_slippage))),
            ("3x slippage", dict(others, cfg=dataclasses.replace(base_cfg, daily_target_slippage=3 * base_cfg.daily_target_slippage))),
        )
        for label, kwargs in checks:
            add(f"Stress test: {r.name}", f"{r.name} -- {label}", **kwargs)

    return results, window[0], midpoint, window[-1]


def render_report(results: list[VariantResult], first: dt.date, midpoint: dt.date, last: dt.date, cfg: Config) -> str:
    money = lambda x: f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"  # noqa: E731
    lines = [
        f"# QQQ Daily $ Target Experiments -- {dt.date.today():%Y-%m-%d}",
        "",
        f"Window: {first} to {last} (first half ends {midpoint}). All variants: "
        f"{cfg.daily_target_contracts} contracts, {cfg.daily_target_delta:.2f} delta, 60 DTE, Black-Scholes "
        f"prices at VIX x {cfg.daily_target_iv_vix_multiple}, "
        f"{money(cfg.daily_target_slippage * 100 * cfg.daily_target_contracts)} slippage per side, "
        "same skip-day gate, one position at a time.",
        "",
        "A variant is a **candidate** (✅) only if it made money in **both** halves. With this many "
        "variants, one will look good by chance, so a single good total isn't enough.",
        "",
    ]
    group = None
    for r in results:
        if r.group != group:
            group = r.group
            lines += [
                "",
                f"### {group}",
                "",
                "| Variant | Trades | Win rate | Total P&L | Avg/trade | Max DD | 1st half | 2nd half | $ goal hit same day | |",
                "|---|---|---|---|---|---|---|---|---|---|",
            ]
        goal = str(r.goal_days) if r.goal_days is not None else "--"
        lines.append(
            f"| {r.name} | {r.trades} | {r.win_rate:.0%} | {money(r.total_pnl)} | {money(r.avg_pnl)} | "
            f"{money(r.max_drawdown)} | {money(r.first_half_pnl)} | {money(r.second_half_pnl)} | {goal} | "
            f"{'✅' if r.both_halves_positive else ''} |"
        )

    stress = [r for r in results if r.group.startswith("Stress test")]
    candidates = [r for r in results if r.both_halves_positive and not r.group.startswith("Stress test")]
    lines += ["", "## Verdict", ""]
    if not candidates:
        lines.append("No variant made money in both halves of the window.")
    for r in sorted(candidates, key=lambda r: r.total_pnl, reverse=True):
        checks = [x for x in stress if x.group == f"Stress test: {r.name}"]
        failed = [x.name.split(" -- ", 1)[1] for x in checks if x.total_pnl <= 0]
        if r.group == "Hold period" or not checks:
            status = "positive in both halves (no stress test for this group)"
        elif failed:
            status = "**fragile** -- turns negative with " + ", ".join(failed)
        else:
            status = "**survives the stress tests** -- worth paper-trading, still not proof"
        lines.append(
            f"- **{r.group}: {r.name}** -- {money(r.total_pnl)} over {r.trades} trades "
            f"({money(r.avg_pnl)}/trade): {status}."
        )
    lines += [
        "",
        "**Not captured:** real option quotes and IV changes, the order of moves inside one hourly bar "
        "(the OHLC path convention decides), news, and fills worse than the modelled slippage. "
        "Past results don't predict future ones.",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the daily $ target backtest experiments")
    parser.add_argument("--period", default="5y", help="yfinance daily history period (default 5y)")
    parser.add_argument("--output-dir", default="reports/backtests")
    args = parser.parse_args(argv)

    from . import providers
    from .data import get_intraday_history

    cfg = Config()
    try:
        price_history = providers.get_price_history(cfg.symbol, args.period)
        vix_history = providers.get_vix_history(args.period, cfg.vix_symbol, cfg.tradier_vix_symbol)
        intraday = get_intraday_history(cfg.symbol, "1h", "730d")
        results, first, mid, last = run_experiments(price_history, vix_history, intraday, cfg)
    except Exception as exc:
        print(f"Experiments failed: {exc}", file=sys.stderr)
        return 1

    report = render_report(results, first, mid, last, cfg)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"daily-target-experiments-{dt.date.today():%Y-%m-%d}.md"
    out_path.write_text(report)
    print(report)
    print(f"Saved report to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
