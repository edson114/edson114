"""Markdown rendering for the daily scan and the backtest."""

from __future__ import annotations

import datetime as dt

from .backtest import BacktestResult
from .config import Config
from .signals import SymbolSignal

DISCLAIMER = (
    "> **Not financial advice.** A rules-based reconstruction of a publicly "
    "described trading *style* -- not Takashi Kotegawa's actual trades, and "
    "not a verified edge. This tool never places orders. Buying sharp "
    "sell-offs loses money when the drop is the start of a real decline; "
    "the stop loss is not optional.\n"
)


def _pct(x: float) -> str:
    return "n/a" if x is None or x != x else f"{x:+.1%}"


def render_scan(signals: list[SymbolSignal], cfg: Config, as_of: dt.date, universe_size: int) -> str:
    ranked = sorted(signals, key=lambda s: s.deviation)
    entries = [s for s in ranked if s.is_entry]
    oversold = [s for s in ranked if s.deviation <= -cfg.entry_deviation]
    breadth = len(oversold) / len(signals) if signals else 0.0
    regime = (
        "**PANIC** -- many names stretched below their MA at once. Historically "
        "the best environment for this style."
        if breadth >= cfg.panic_breadth_threshold
        else "Normal -- few names stretched; expect isolated setups only."
    )

    lines = [
        f"# Kotegawa-Style 25-Day MA Reversion Scan -- {as_of.isoformat()}",
        "",
        DISCLAIMER,
        f"Universe: {universe_size} symbols ({len(signals)} with enough data). "
        f"Entry rule: close ≤ {-cfg.entry_deviation:+.0%} vs the {cfg.ma_window}-day MA"
        f"{', green candle' if cfg.require_green_close else ''}; buy next open.",
        "",
        f"**Market breadth:** {len(oversold)}/{len(signals)} ({breadth:.0%}) at/below the entry deviation. {regime}",
        "",
        f"## Buy signals for next open ({len(entries)})",
        "",
    ]
    if entries:
        lines += [
            "| Symbol | Close | 25d MA | Deviation | Stop | Target | Reward:Risk |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for s in entries[: cfg.max_candidates_in_report]:
            lines.append(
                f"| **{s.symbol}** | {s.close:.2f} | {s.ma:.2f} | {_pct(s.deviation)} | "
                f"{s.stop_price:.2f} | {s.target_price:.2f} | {s.reward_risk:.1f} |"
            )
        lines += [
            "",
            f"Plan per position: size at ~{cfg.position_fraction:.0%} of equity (max {cfg.max_positions} open), "
            f"stop {cfg.stop_loss_pct:.0%} below your fill, sell when the close is back within "
            f"{cfg.exit_deviation:.0%} of the MA, and exit at the close after {cfg.max_hold_days} trading days "
            "if neither has happened.",
        ]
    else:
        lines.append("_No setups today. Sitting in cash is a position -- Kotegawa waited for panics._")

    lines += ["", "## Most stretched below the MA (watchlist)", "",
              "| Symbol | Close | Deviation | Status |", "|---|---:|---:|---|"]
    for s in ranked[: cfg.max_candidates_in_report]:
        lines.append(f"| {s.symbol} | {s.close:.2f} | {_pct(s.deviation)} | {s.reason} |")
    return "\n".join(lines) + "\n"


def render_backtest(result: BacktestResult, cfg: Config, universe_size: int, period: str) -> str:
    st = result.stats
    lines = [
        f"# Kotegawa-Style Reversion Backtest -- {dt.date.today().isoformat()}",
        "",
        DISCLAIMER,
        f"Universe: {universe_size} symbols, period `{period}` ({st.get('start')} → {st.get('end')}). "
        f"Entry {-cfg.entry_deviation:+.0%} vs {cfg.ma_window}d MA, exit within {cfg.exit_deviation:.0%} of MA, "
        f"stop {cfg.stop_loss_pct:.0%}, time stop {cfg.max_hold_days}d, {cfg.max_positions} slots × "
        f"{cfg.position_fraction:.0%}, {cfg.cost_bps:.0f} bps/side.",
        "",
        "> Survivorship bias: the default universe is *today's* large caps, which "
        "by definition recovered from every past drop. Real-time results will be worse.",
        "",
        "| Metric | Strategy | Benchmark (buy & hold) |",
        "|---|---:|---:|",
        f"| Total return | {_pct(st['total_return'])} | {_pct(st.get('benchmark_return'))} |",
        f"| Max drawdown | {_pct(st['max_drawdown'])} | {_pct(st.get('benchmark_max_drawdown'))} |",
        f"| CAGR | {_pct(st['cagr'])} | |",
        f"| Final equity | ${st['final_equity']:,.0f} | |",
        "",
        "| Trade stat | Value |",
        "|---|---:|",
        f"| Trades | {st['trades']} |",
        f"| Win rate | {st['win_rate']:.0%} |" if st["trades"] else "| Win rate | n/a |",
        f"| Avg trade | {_pct(st['avg_trade_return'])} |",
        f"| Avg win / avg loss | {_pct(st['avg_win'])} / {_pct(st['avg_loss'])} |",
        f"| Profit factor | {st['profit_factor']:.2f} |",
        f"| Avg days held | {st['avg_bars_held']:.1f} |",
        "",
        "**Exits:** " + (", ".join(f"{k}: {v}" for k, v in st["exit_reasons"].items()) or "none"),
        "",
        "## Last 25 trades",
        "",
        "| Symbol | Entry | Exit | Entry dev. | Return | Days | Exit reason |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for t in result.trades[-25:]:
        lines.append(
            f"| {t.symbol} | {t.entry_date.date()} @ {t.entry_price:.2f} | {t.exit_date.date()} @ {t.exit_price:.2f} | "
            f"{_pct(t.entry_deviation)} | {_pct(t.return_pct)} | {t.bars_held} | {t.exit_reason} |"
        )
    return "\n".join(lines) + "\n"
