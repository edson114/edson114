"""Directional (buy calls/puts) scoring for a single small-cap candidate.

Blends today's gap/move, position vs intraday VWAP, whether the move is
still extending or fading off its session high/low, relative volume, and
a news catalyst hit into a single -1..+1 score, mapped to a CALL / PUT /
NO TRADE bias. This formalizes the *shape* of a small-cap momentum
options trade (buy calls on a low-float breakout that's still extending
with volume and a catalyst behind it; buy puts on a big gainer that's
failing and rolling over, or a heavily-shorted name breaking down) -- it
is a rules-based checklist over free data, not a backtested or verified
edge, and not a reproduction of any specific trader's actual rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .config import Config
from .data import CandidateQuote
from .intraday import IntradaySnapshot
from .news import CatalystHit


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _sign(x: float) -> float:
    return 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)


@dataclass
class ScoreComponent:
    name: str
    weight: float
    contribution: float
    detail: str


@dataclass
class DirectionalSignal:
    bias: str  # "CALL", "PUT", "NO TRADE"
    score: float
    confidence: str
    components: list = field(default_factory=list)
    advisory_notes: list = field(default_factory=list)


def _normalized_weights(weights: dict) -> dict:
    total = sum(weights.values())
    if total <= 0:
        return {name: 0.0 for name in weights}
    return {name: w / total for name, w in weights.items()}


def _gap_component(quote: CandidateQuote, lean: float) -> tuple[float, str]:
    """Today's move is what put this name "in play" -- but its *sign* here
    follows the current price-action lean (vwap + extension), not the raw
    day's %change. That's what lets this correctly score a PUT on a name
    that's still up on the day but is failing/rolling over right now (the
    classic small-cap "faded gapper" short setup), instead of the day's
    original gap direction fighting the actual current signal."""
    magnitude = _clip(abs(quote.change_pct) / 20.0, 0.0, 1.0)
    return lean * magnitude, f"{quote.change_pct:+.1f}% move today vs prior close (in-play magnitude {magnitude:.2f})."


def _vwap_component(intraday: IntradaySnapshot) -> tuple[float, str]:
    pct = intraday.price_vs_vwap_pct
    if pct != pct:  # NaN
        return 0.0, "VWAP unavailable yet (too early in the session)."
    val = _clip(pct / 3.0)
    side = "above" if pct >= 0 else "below"
    return val, f"Price {side} VWAP by {abs(pct):.2f}% (${intraday.last_price:.2f} vs VWAP ${intraday.vwap:.2f})."


def _extension_component(quote: CandidateQuote, intraday: IntradaySnapshot) -> tuple[float, str]:
    """Distinguishes "still running" from "fading" on the same-direction
    day -- the difference between a continuation CALL and a
    failed-breakout PUT (or, on a red day, a breakdown PUT vs. a
    short-squeeze-bounce CALL)."""
    near_high = intraday.pct_off_high <= 2.0
    near_low = intraday.pct_off_low <= 2.0
    faded_hard = intraday.pct_off_high >= 8.0
    bounced_hard = intraday.pct_off_low >= 8.0

    if near_high and intraday.price_vs_vwap_pct == intraday.price_vs_vwap_pct and intraday.price_vs_vwap_pct >= 0:
        return 1.0, f"Trading within 2% of the session high (${intraday.session_high:.2f}) and holding above VWAP -- still extending."
    if near_low and intraday.price_vs_vwap_pct == intraday.price_vs_vwap_pct and intraday.price_vs_vwap_pct <= 0:
        return -1.0, f"Trading within 2% of the session low (${intraday.session_low:.2f}) and holding below VWAP -- still breaking down."
    if faded_hard and quote.change_pct > 0:
        return -1.0, f"Up {quote.change_pct:.1f}% on the day but {intraday.pct_off_high:.1f}% off the session high -- a green day that's rolling over."
    if bounced_hard and quote.change_pct < 0:
        return 1.0, f"Down {quote.change_pct:.1f}% on the day but {intraday.pct_off_low:.1f}% above the session low -- a red day that's bouncing/squeezing."
    return 0.0, "Neither clearly extending nor clearly fading -- mid-range of today's session."


def _relative_volume_component(quote: CandidateQuote, lean: float) -> tuple[float, str]:
    rvol = quote.relative_volume
    val = lean * _clip((rvol - 1.0) / 10.0, 0.0, 1.0)
    return val, f"Relative volume {rvol:.1f}x the 3-month average -- {'confirms' if rvol >= 3 else 'thin support for'} the current move."


def _catalyst_component(hits: list[CatalystHit], lean: float) -> tuple[float, str]:
    if not hits:
        return 0.0, "No news catalyst found in free feeds -- move may be technical/flow-driven, or the catalyst just isn't covered by free sources."
    top = hits[0]
    return lean, f"Catalyst: \"{top.headline.title}\" ({top.headline.source})."


def build_directional_signal(
    quote: CandidateQuote,
    intraday: IntradaySnapshot,
    catalyst_hits: list[CatalystHit],
    cfg: Config,
) -> DirectionalSignal:
    vwap_val, vwap_detail = _vwap_component(intraday)
    extension_val, extension_detail = _extension_component(quote, intraday)

    # The current price-action lean: extension (still running vs. fading)
    # takes priority since it's the more direct read of "right now"; VWAP
    # position breaks ties when extension is flat; the raw day's %change
    # sign is the last-resort fallback.
    if extension_val != 0:
        lean = _sign(extension_val)
    elif vwap_val != 0:
        lean = _sign(vwap_val)
    else:
        lean = _sign(quote.change_pct)

    raw = {
        "gap": _gap_component(quote, lean),
        "vwap": (vwap_val, vwap_detail),
        "extension": (extension_val, extension_detail),
        "relative_volume": _relative_volume_component(quote, lean),
        "catalyst": _catalyst_component(catalyst_hits, lean),
    }
    weights = _normalized_weights(cfg.component_weights)
    components = [
        ScoreComponent(name=name, weight=weights[name], contribution=round(weights[name] * val, 4), detail=detail)
        for name, (val, detail) in raw.items()
    ]
    score = round(sum(c.contribution for c in components), 3)

    advisory_notes = []
    if not catalyst_hits:
        advisory_notes.append("No catalyst found -- a move without a known reason can reverse without warning.")

    if score >= cfg.signal_score_threshold:
        bias, confidence = "CALL", ("High" if score >= 0.65 else "Medium")
    elif score <= -cfg.signal_score_threshold:
        bias, confidence = "PUT", ("High" if score <= -0.65 else "Medium")
    else:
        bias, confidence = "NO TRADE", "N/A (no directional edge / choppy)"

    return DirectionalSignal(bias=bias, score=score, confidence=confidence, components=components, advisory_notes=advisory_notes)
