"""Renders the directional (buy calls/puts) signal into a Markdown report."""

from __future__ import annotations

import datetime as dt

from .analysis import MarketSnapshot
from .direction import DirectionalSignal, SuggestedContract
from .intraday import IntradaySnapshot
from .news import CatalystHit


def _fmt(x, decimals: int = 2) -> str:
    if x is None or x != x:
        return "n/a"
    return f"{x:.{decimals}f}"


def _bias_header(signal: DirectionalSignal) -> str:
    if signal.bias == "CALL":
        return f"## \U0001F7E2 Signal: BUY CALLS  (score {signal.score:+.2f}, confidence {signal.confidence})"
    if signal.bias == "PUT":
        return f"## \U0001F534 Signal: BUY PUTS  (score {signal.score:+.2f}, confidence {signal.confidence})"
    return f"## ⚪ Signal: NO TRADE  (score {signal.score:+.2f})"


def _components_table(signal: DirectionalSignal) -> str:
    lines = ["| Signal | Weight | Contribution | Read |", "|---|---|---|---|"]
    for c in signal.components:
        lines.append(f"| {c.name} | {c.weight:.2f} | {c.contribution:+.3f} | {c.detail} |")
    return "\n".join(lines)


def _contract_section(label: str, contract: SuggestedContract) -> str:
    if contract is None:
        return f"_No {label} contract could be selected (missing expiration or illiquid chain)._\n"
    lines = [
        f"### {label} -- {contract.option_type.upper()} expiring {contract.expiration} ({contract.dte} DTE)",
        f"- **Strike:** {_fmt(contract.strike, 0)}  |  **Mid price:** ${_fmt(contract.mid_price)}  |  "
        f"**Delta:** {_fmt(contract.delta, 3)}  |  **IV:** {_fmt(contract.implied_vol, 1)}%",
    ]
    if contract.warning:
        lines.append(f"- ⚠️ {contract.warning}")
    lines.append("")
    return "\n".join(lines)


def render_signal_report(
    symbol: str,
    daily: MarketSnapshot,
    intraday: IntradaySnapshot,
    signal: DirectionalSignal,
    headlines: list,
    catalyst_hits: list,
    gate,
    generated_at: dt.datetime = None,
) -> str:
    generated_at = generated_at or dt.datetime.now()

    parts = []
    parts.append(f"# {symbol} Directional Signal (Calls/Puts) -- {generated_at.strftime('%Y-%m-%d %H:%M %Z').strip()}")
    parts.append("")
    parts.append(
        "> Automated decision-support, not an auto-trader and not financial advice. No signal here "
        "is close to \"99% accurate\" -- no legitimate day-trading system is. Treat this as one "
        "structured opinion among several inputs, size small, and confirm live quotes/greeks against "
        "your broker before trading. Buying calls/puts is a defined-risk but theta-negative position: "
        "you can lose the full premium if the move doesn't happen fast enough."
    )
    parts.append("")

    parts.append(_bias_header(signal))
    parts.append("")

    if signal.forced_no_trade_reasons:
        parts.append("**Hard gate triggered -- overrides the technical score:**")
        for r in signal.forced_no_trade_reasons:
            parts.append(f"- ⛔ {r}")
        parts.append("")

    parts.append("### Score breakdown")
    parts.append("")
    parts.append(_components_table(signal))
    parts.append("")

    if signal.advisory_notes:
        parts.append("**Advisory flags** (don't change the score, but worth weighing):")
        for note in signal.advisory_notes:
            parts.append(f"- ⚠️ {note}")
        parts.append("")

    parts.append("## Market Snapshot")
    parts.append("")
    parts.append(f"- **{symbol} last:** ${_fmt(daily.spot)} ({_fmt(daily.day_change_pct)}% vs prior close)")
    parts.append(f"- **Daily trend:** {daily.trend_label}  |  **VIX:** {_fmt(daily.vix_level,1)} ({_fmt(daily.vix_percentile_1y,0)}th pct of trailing 1y)")
    parts.append(
        f"- **Intraday VWAP:** ${_fmt(intraday.vwap)}  |  **Opening range:** "
        f"${_fmt(intraday.opening_range_low)} - ${_fmt(intraday.opening_range_high)}  |  **ORB status:** {intraday.orb_status}"
    )
    parts.append(f"- **RSI(14) daily / intraday:** {_fmt(daily.rsi14,1)} / {_fmt(intraday.rsi14,1)}")
    parts.append(f"- **ATR(14) daily:** ${_fmt(daily.atr14)}")
    parts.append("")

    if signal.bias in ("CALL", "PUT"):
        move_pct = (signal.target_underlying / daily.spot - 1.0) * 100.0 if signal.target_underlying else float("nan")
        parts.append("## Trade Plan (if taken)")
        parts.append("")
        parts.append(f"- **Direction:** {signal.bias}")
        parts.append(f"- **Underlying stop-loss level:** ${_fmt(signal.stop_loss_underlying)}")
        parts.append(f"- **Underlying target level:** ${_fmt(signal.target_underlying)}  (~{_fmt(abs(move_pct),2)}% move)")
        parts.append("")
        parts.append("### Suggested contracts (near-ATM, for max responsiveness to the move)")
        parts.append("")
        for label, contract in signal.contracts.items():
            parts.append(_contract_section(label, contract))
        parts.append(
            "**Position sizing:** risk no more than 1-2% of account equity on the premium paid. A "
            "long call/put's max loss is the premium itself, so that cap alone can satisfy the 1-2% "
            "risk limit -- there's no separate stop order required on the option -- but exiting at "
            "the underlying stop level above (rather than holding to expiration hoping it comes back) "
            "is still the discipline that keeps a wrong idea from decaying to zero.\n"
        )

    parts.append("## News & Catalysts")
    parts.append("")
    if catalyst_hits:
        parts.append("**Potential catalysts flagged in today's headlines:**\n")
        for h in catalyst_hits[:10]:
            kws = ", ".join(sorted(set(h.matched_keywords)))
            parts.append(f"- ⚠️ [{h.headline.source}] {h.headline.title}  _(matched: {kws})_")
        parts.append("")
    else:
        parts.append("_No obvious high-impact keywords detected in the pulled headlines._\n")

    parts.append("## Limitations")
    parts.append("")
    parts.append(
        "- This is a rules-based blend of daily trend/momentum + intraday VWAP/EMA/opening-range "
        "signals + QQQ-vs-SPY relative strength -- a structured checklist, not a backtested or "
        "machine-learned edge, and not a probability of profit.\n"
        "- The hard gate (scheduled FOMC/CPI, a large gap, or a VIX spike) forces NO TRADE regardless "
        "of the technical score, using the same skip-day rules as the iron condor scanner -- see "
        "`Config.macro_event_dates` and keep it current.\n"
        "- Same-day (0DTE) option prices decay and swing fast; if you're reading this more than a "
        "few minutes after it ran, re-pull the underlying price, VWAP, and option quotes before acting.\n"
        "- No day-trading system is close to \"99% profitable\" -- professional traders manage a "
        "modest statistical edge with strict risk control, not a near-certain win rate."
    )
    parts.append("")

    return "\n".join(parts)
