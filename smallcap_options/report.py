"""Renders the small-cap momentum options scan into a Markdown report."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from .data import CandidateQuote
from .direction import DirectionalSignal
from .gates import GateResult
from .intraday import IntradaySnapshot
from .news import CatalystHit
from .strategy import SuggestedContract


@dataclass
class CandidateReport:
    quote: CandidateQuote
    float_shares: Optional[float]
    avg_dollar_volume: float
    intraday: Optional[IntradaySnapshot]
    signal: Optional[DirectionalSignal]
    catalyst_hits: list = field(default_factory=list)
    contract: Optional[SuggestedContract] = None
    gate: Optional[GateResult] = None
    skip_reason: Optional[str] = None  # set when the candidate couldn't even be scored (no intraday data, etc.)


def _fmt(x, decimals: int = 2) -> str:
    if x is None or x != x:
        return "n/a"
    return f"{x:.{decimals}f}"


def _float_fmt(shares: Optional[float]) -> str:
    if shares is None:
        return "unknown"
    if shares >= 1_000_000:
        return f"{shares / 1_000_000:.1f}M"
    return f"{shares:,.0f}"


def _bias_emoji(bias: str) -> str:
    return {"CALL": "\U0001F7E2", "PUT": "\U0001F534"}.get(bias, "⚪")


def _components_table(signal: DirectionalSignal) -> str:
    lines = ["| Signal | Weight | Contribution | Read |", "|---|---|---|---|"]
    for c in signal.components:
        lines.append(f"| {c.name} | {c.weight:.2f} | {c.contribution:+.3f} | {c.detail} |")
    return "\n".join(lines)


def _contract_section(contract: Optional[SuggestedContract]) -> str:
    if contract is None:
        return "_No contract could be selected -- see gate reasons above._\n"
    lines = [
        f"**{contract.option_type.upper()}** expiring {contract.expiration} ({contract.dte} DTE), "
        f"strike **{_fmt(contract.strike, 2)}**",
        f"- Mid price: ${_fmt(contract.mid_price)}  |  Delta: {_fmt(contract.delta, 3)}  |  "
        f"IV: {_fmt(contract.implied_vol, 1)}%  |  Bid/ask spread: {_fmt(contract.bid_ask_spread_pct, 1)}%  |  "
        f"Open interest: {contract.open_interest if contract.open_interest is not None else 'n/a'}",
    ]
    if contract.warning:
        lines.append(f"- ⚠️ {contract.warning}")
    return "\n".join(lines)


def _summary_row(c: CandidateReport) -> str:
    q = c.quote
    bias = c.signal.bias if c.signal else "SKIPPED"
    score = f"{c.signal.score:+.2f}" if c.signal else "n/a"
    gated = " ⛔" if (c.gate and c.gate.skip) else ""
    contract_txt = (
        f"{c.contract.option_type.upper()} ${_fmt(c.contract.strike, 2)} {c.contract.expiration}"
        if c.contract
        else "-"
    )
    return (
        f"| {q.symbol} | {q.name[:28]} | ${q.price:.2f} | {q.change_pct:+.1f}% | {q.relative_volume:.1f}x | "
        f"{_float_fmt(c.float_shares)} | {_bias_emoji(bias)} {bias}{gated} | {score} | {contract_txt} |"
    )


def render_report(
    candidates: list,
    universe_stats: dict,
    cfg,
    generated_at: dt.datetime = None,
) -> str:
    generated_at = generated_at or dt.datetime.now()

    scored = [c for c in candidates if c.signal is not None]
    tradable = [c for c in scored if c.signal.bias in ("CALL", "PUT") and not (c.gate and c.gate.skip)]
    tradable.sort(key=lambda c: abs(c.signal.score), reverse=True)
    gated_out = [c for c in scored if c.signal.bias in ("CALL", "PUT") and c.gate and c.gate.skip]

    top = tradable[: cfg.max_candidates_in_report]

    parts = []
    parts.append(f"# Small-Cap Momentum Options Scan -- {generated_at.strftime('%Y-%m-%d %H:%M %Z').strip()}")
    parts.append("")
    parts.append(
        "> **Not financial advice, and not a reproduction of any specific trader's actual rules or "
        "track record.** This formalizes a publicly-associated *style* of small-cap momentum trading "
        "(low float, high relative volume, a gap or spike with a news catalyst, traded with cheap "
        "short-dated OTM calls/puts) into an objective checklist over free data -- it is not "
        "backtested, not verified against real fills, and no signal here is close to \"reliably "
        "profitable.\" Low-float small caps are extremely volatile and often illiquid; short-dated "
        "OTM options on them can lose their entire premium in minutes, routinely do, and can also be "
        "impossible to exit at a fair price when the bid/ask spread blows out. This tool never places "
        "orders -- verify every price, strike, and spread against your broker's live quotes, and size "
        "positions as pure risk capital you can afford to lose completely."
    )
    parts.append("")

    parts.append("## Universe")
    parts.append("")
    parts.append(
        f"- Screener quotes pulled: **{universe_stats.get('screened', 0)}**  |  "
        f"After cheap prefilter (price/cap/volume/gap thresholds): **{universe_stats.get('prefiltered', 0)}**  |  "
        f"Deep-scanned (float + $ liquidity check): **{universe_stats.get('deep_scanned', 0)}**  |  "
        f"Scored: **{len(scored)}**"
    )
    if universe_stats.get("manual_symbols"):
        parts.append(f"- Manual watchlist symbols included: {', '.join(universe_stats['manual_symbols'])}")
    parts.append("")

    parts.append("## Candidates")
    parts.append("")
    if scored:
        parts.append("| Symbol | Name | Price | Chg% | RVOL | Float | Bias | Score | Contract |")
        parts.append("|---|---|---|---|---|---|---|---|---|")
        for c in sorted(scored, key=lambda c: abs(c.signal.score), reverse=True)[: cfg.max_candidates_deep_scanned]:
            parts.append(_summary_row(c))
        parts.append("")
        parts.append("_⛔ = gated out (no tradable contract despite a CALL/PUT score -- see detail below)._")
    else:
        parts.append("_No candidates survived the universe filters and scoring this run._")
    parts.append("")

    if top:
        parts.append("## Top Setups (detail)")
        parts.append("")
        for c in top:
            q = c.quote
            parts.append(
                f"### {_bias_emoji(c.signal.bias)} {q.symbol} -- {c.signal.bias}  "
                f"(score {c.signal.score:+.2f}, confidence {c.signal.confidence})"
            )
            parts.append("")
            parts.append(
                f"{q.name} -- ${q.price:.2f} ({q.change_pct:+.1f}% today), relative volume "
                f"{q.relative_volume:.1f}x, float {_float_fmt(c.float_shares)}, "
                f"~${c.avg_dollar_volume / 1_000_000:.1f}M avg daily $ volume."
            )
            parts.append("")
            parts.append(_components_table(c.signal))
            parts.append("")
            parts.append("**Suggested contract:**")
            parts.append("")
            parts.append(_contract_section(c.contract))
            parts.append("")
            if c.catalyst_hits:
                parts.append("**Catalyst headlines:**")
                for h in c.catalyst_hits[:3]:
                    parts.append(f"- [{h.headline.source}] {h.headline.title}")
                parts.append("")
            if c.gate and c.gate.soft_reasons:
                parts.append("**Advisory flags:**")
                for r in c.gate.soft_reasons:
                    parts.append(f"- ⚠️ {r}")
                parts.append("")

    if gated_out:
        parts.append("## Scored CALL/PUT but gated out")
        parts.append("")
        for c in gated_out:
            reasons = "; ".join(c.gate.hard_reasons)
            parts.append(f"- **{c.quote.symbol}** ({c.signal.bias}, score {c.signal.score:+.2f}): {reasons}")
        parts.append("")

    parts.append("## Risk management notes")
    parts.append("")
    parts.append(
        "- **Position size as a lottery ticket, not a core position.** This style is publicly "
        "associated with small, defined-risk bets (the premium paid) sized so a full, fast loss on "
        "any single name doesn't matter to the account -- commonly cited as a small fraction (well "
        "under 1-2%) of account equity per trade, spread across several names rather than concentrated "
        "in one.\n"
        "- **Plan the exit before entry.** The associated style is known for taking profit quickly and "
        "scaling out into strength (e.g. selling a portion at a 50-100%+ gain) rather than holding for a "
        "home run, precisely because these contracts round-trip to worthless fast when momentum stalls.\n"
        "- **A wide bid/ask spread is a real cost, not a rounding error**, on illiquid small-cap option "
        "chains -- the mid price shown is not a guaranteed fill; check the live spread before sizing.\n"
        "- **A low float that squeezes in your favor can also gap against you overnight or on a halt** "
        "(circuit breakers, trading halts pending news, and reverse splits are common in this universe) "
        "-- only ever risk the premium you'd be fully fine losing, and expect volatility exceeding a "
        "typical large-cap options trade by a wide margin."
    )
    parts.append("")

    parts.append("## Limitations")
    parts.append("")
    parts.append(
        "- The universe comes from Yahoo Finance's free, unofficial screener endpoint -- it can "
        "rate-limit, change shape, or simply miss a name that a paid real-time scanner (e.g. Trade "
        "Ideas, Benzinga Pro) would have caught; this is not a substitute for a real-time Level 2/scanner "
        "feed.\n"
        "- Float shares and news catalysts come from free, best-effort sources and are frequently "
        "stale, missing, or wrong for thinly-covered small caps -- always verify manually before "
        "trading, especially float.\n"
        "- The directional score is a rules-based checklist (gap, VWAP, extension off session "
        "high/low, relative volume, catalyst presence) -- it is not backtested against real historical "
        "fills and does not claim a win rate.\n"
        "- This tool never places orders and is not a reproduction of any specific trader's real rules, "
        "risk management, or track record -- it structures the same *kind* of inputs a discretionary "
        "small-cap momentum trader would look at, consistently, nothing more."
    )
    parts.append("")

    return "\n".join(parts)
