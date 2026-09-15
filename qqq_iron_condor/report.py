"""Renders the daily scan into a Markdown report."""

from __future__ import annotations

import datetime as dt

from .analysis import MarketSnapshot
from .news import CatalystHit
from .data import Headline
from .strategy import IronCondorTrade


def _fmt(x: float, decimals: int = 2) -> str:
    if x is None or x != x:  # NaN check
        return "n/a"
    return f"{x:.{decimals}f}"


def _condor_section(trade: IronCondorTrade) -> str:
    if trade is None:
        return (
            "_No valid iron condor could be constructed for this expiration -- "
            "either QQQ has no matching listed expiration today (e.g. no same-day "
            "0DTE listing), or the chain data was illiquid/missing quotes._\n"
        )

    legs = trade.legs
    lines = [
        f"### {trade.label} Iron Condor -- expires {trade.expiration} ({trade.dte} DTE)",
        "",
        f"- **Spot (QQQ):** ${_fmt(trade.spot)}",
        f"- **ATM IV (approx):** {_fmt(trade.atm_iv, 1)}%",
        f"- **IV-implied expected move to expiration:** ±${_fmt(trade.expected_move_iv)}",
        "",
        "| Leg | Action | Strike | Mid Price | Delta |",
        "|---|---|---|---|---|",
        f"| Call | **SELL** | {_fmt(legs['short_call'].strike, 0)} | ${_fmt(legs['short_call'].mid_price)} | {_fmt(legs['short_call'].delta, 3)} |",
        f"| Call | BUY (protection) | {_fmt(legs['long_call'].strike, 0)} | ${_fmt(legs['long_call'].mid_price)} | {_fmt(legs['long_call'].delta, 3)} |",
        f"| Put | **SELL** | {_fmt(legs['short_put'].strike, 0)} | ${_fmt(legs['short_put'].mid_price)} | {_fmt(legs['short_put'].delta, 3)} |",
        f"| Put | BUY (protection) | {_fmt(legs['long_put'].strike, 0)} | ${_fmt(legs['long_put'].mid_price)} | {_fmt(legs['long_put'].delta, 3)} |",
        "",
        f"- **Net credit (per contract, x100):** ${_fmt(trade.credit)}  →  **${_fmt(trade.credit * 100)}** per contract",
        f"- **Max profit:** ${_fmt(trade.max_profit * 100)} per contract (credit received)",
        f"- **Max loss:** ${_fmt(trade.max_loss * 100)} per contract (call wing ${_fmt(trade.call_width,0)} wide, put wing ${_fmt(trade.put_width,0)} wide)",
        f"- **Return on risk:** {_fmt(trade.return_on_risk() * 100, 1)}%",
        f"- **Breakevens:** ${_fmt(trade.breakeven_lower)} / ${_fmt(trade.breakeven_upper)}",
        f"- **Approx. probability of profit (finishing between short strikes):** {_fmt(trade.probability_of_profit, 1)}%",
        f"- **Position delta (approx, per contract):** {_fmt(trade.position_delta, 3)}",
    ]
    if trade.warning:
        lines.append(f"- ⚠️ {trade.warning}")
    lines.append("")
    return "\n".join(lines)


def _catalyst_section(hits: list[CatalystHit], headlines: list[Headline]) -> str:
    lines = []
    if hits:
        lines.append("**Potential catalysts flagged in today's headlines:**\n")
        for h in hits[:10]:
            kws = ", ".join(sorted(set(h.matched_keywords)))
            lines.append(f"- ⚠️ [{h.headline.source}] {h.headline.title}  _(matched: {kws})_")
        lines.append("")
    else:
        lines.append("_No obvious high-impact keywords detected in the pulled headlines._\n")

    if headlines:
        lines.append("<details><summary>All pulled headlines</summary>\n")
        for h in headlines[:40]:
            lines.append(f"- [{h.source}] {h.title}")
        lines.append("\n</details>\n")
    return "\n".join(lines)


def render_report(
    symbol: str,
    snapshot: MarketSnapshot,
    condors: dict,
    headlines: list,
    catalyst_hits: list,
    generated_at: dt.datetime = None,
) -> str:
    generated_at = generated_at or dt.datetime.now()

    parts = []
    parts.append(f"# {symbol} Iron Condor Daily Scan -- {generated_at.strftime('%Y-%m-%d %H:%M %Z').strip()}")
    parts.append("")
    parts.append(
        "> Automated analysis for informational purposes only. **Not financial advice.** "
        "Verify all prices/strikes against a live broker quote before placing any trade."
    )
    parts.append("")

    parts.append("## Market Snapshot")
    parts.append("")
    parts.append(f"- **{symbol} last close:** ${_fmt(snapshot.spot)} ({_fmt(snapshot.day_change_pct)}% vs prior close of ${_fmt(snapshot.prev_close)})")
    parts.append(f"- **Trend:** {snapshot.trend_label}")
    parts.append(f"- **Volatility regime:** {snapshot.regime_label} (VIX {_fmt(snapshot.vix_level,1)}, {_fmt(snapshot.vix_percentile_1y,0)}th percentile of trailing 1y)")
    parts.append(f"- **20-day range:** ${_fmt(snapshot.low_20d)} - ${_fmt(snapshot.high_20d)}")
    parts.append(f"- **50-day range:** ${_fmt(snapshot.low_50d)} - ${_fmt(snapshot.high_50d)}")
    parts.append("")

    parts.append("### Technical Indicators")
    parts.append("")
    parts.append("| Indicator | Value |")
    parts.append("|---|---|")
    parts.append(f"| RSI(14) | {_fmt(snapshot.rsi14,1)} |")
    parts.append(f"| SMA20 / SMA50 / SMA200 | {_fmt(snapshot.sma20)} / {_fmt(snapshot.sma50)} / {_fmt(snapshot.sma200)} |")
    parts.append(f"| EMA9 / EMA21 | {_fmt(snapshot.ema9)} / {_fmt(snapshot.ema21)} |")
    parts.append(f"| MACD (line / signal / hist) | {_fmt(snapshot.macd_line,3)} / {_fmt(snapshot.macd_signal,3)} / {_fmt(snapshot.macd_hist,3)} |")
    parts.append(f"| Bollinger Bands (20,2) | {_fmt(snapshot.bb_lower)} / {_fmt(snapshot.bb_mid)} / {_fmt(snapshot.bb_upper)} (width {_fmt(snapshot.bb_width_pct,2)}%) |")
    parts.append(f"| ATR(14) | {_fmt(snapshot.atr14)} |")
    parts.append(f"| ADX(14) | {_fmt(snapshot.adx14,1)} |")
    parts.append(f"| 20-day Historical Volatility (annualized) | {_fmt(snapshot.hv20_pct,1)}% |")
    parts.append("")

    if snapshot.regime_notes:
        parts.append("**Regime notes:**")
        for note in snapshot.regime_notes:
            parts.append(f"- {note}")
        parts.append("")

    parts.append("## News & Catalysts")
    parts.append("")
    parts.append(_catalyst_section(catalyst_hits, headlines))

    parts.append("## Recommended Iron Condor Structures")
    parts.append("")
    for label, trade in condors.items():
        parts.append(_condor_section(trade))

    parts.append("## Risk Management")
    parts.append("")
    if "0DTE" in condors:
        parts.append(
            "**0DTE note:** a same-day iron condor has almost no time value cushion -- gamma "
            "is extreme near the short strikes and a fast intraday move can go from 'near max "
            "profit' to 'near max loss' within minutes, especially in the final 1-2 hours. "
            "0DTE strike/price data reflects the moment the scan ran; if you're checking this "
            "later in the session, re-pull quotes before acting. Consider closing well before "
            "the close rather than letting contracts expire, and size these smaller than "
            "weekly/monthly positions.\n"
        )
    parts.append(
        "- Size each trade so **max loss ≤ 1-3% of account equity**; this is a defined-risk "
        "structure but max loss can still be substantial relative to credit received.\n"
        "- Consider taking profit at **50-75% of max profit** rather than holding to expiration.\n"
        "- If price approaches a short strike (delta rises toward ~0.30-0.35), consider closing, "
        "rolling the tested side out/away, or converting to a defined adjustment -- don't let a "
        "defined-risk trade turn into a hope-and-pray hold.\n"
        "- Avoid opening new positions within 1-2 days of major catalysts flagged above "
        "(FOMC/CPI/NFP/mega-cap earnings) unless the position is explicitly sized for the "
        "expected volatility expansion.\n"
        "- This scan uses a Black-Scholes delta approximation from option-chain implied "
        "volatility, not live broker greeks -- always confirm strikes/greeks/prices in your "
        "broker platform before submitting an order."
    )
    parts.append("")

    return "\n".join(parts)
