"""Trade gate: the user's stated skip-day rules, applied automatically.

Skip trading on: FOMC days, CPI prints, surprise macro news, a strong
pre-market/opening gap, a trending day with above-average volume, or a
VIX spike. Not all of these are fully checkable from a single point-in-time
morning scan -- this module is explicit about which conditions are hard
gates (objective, checkable now) versus soft flags (best-effort proxies
that need intraday reconfirmation):

Hard gates (any one triggers a SKIP verdict):
  - Today matches a known scheduled macro event (user-maintained calendar).
  - Opening/indicated gap vs. prior close exceeds the configured threshold.
  - VIX is at/above the spike threshold *at scan time*.

Soft flags (shown, but don't by themselves force a skip):
  - ADX trending + last session's volume well above its 20-day average --
    a leading-indicator proxy from the most recently *completed* session,
    since a morning scan can't yet know today's full-day volume or trend.
  - Any catalyst keyword hit in today's headlines (see News & Catalysts) --
    "surprise macro news" is by definition not on a calendar; this is the
    best available real-time proxy, not a guarantee of detection.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

from .analysis import MarketSnapshot
from .news import CatalystHit


@dataclass
class GateResult:
    skip: bool
    hard_reasons: list = field(default_factory=list)
    soft_reasons: list = field(default_factory=list)
    gap_pct: Optional[float] = None
    gap_confirmed: bool = False


def evaluate_gates(
    today: dt.date,
    macro_event_dates: dict,
    gap_pct: Optional[float],
    gap_confirmed: bool,
    gap_threshold_pct: float,
    vix_level: float,
    vix_spike_threshold: float,
    snapshot: MarketSnapshot,
    adx_trend_threshold: float,
    volume_ratio_threshold: float,
    catalyst_hits: list[CatalystHit],
) -> GateResult:
    hard_reasons: list[str] = []
    soft_reasons: list[str] = []

    event_label = macro_event_dates.get(today.isoformat())
    if event_label:
        hard_reasons.append(f"Scheduled macro event today: {event_label}.")

    if gap_pct is None:
        soft_reasons.append("Could not determine the pre-market/opening gap -- check manually before trading.")
    elif abs(gap_pct) >= gap_threshold_pct:
        kind = "Opening" if gap_confirmed else "Indicated pre-market"
        hard_reasons.append(
            f"{kind} gap of {gap_pct:+.2f}% exceeds the {gap_threshold_pct:.1f}% threshold."
        )

    if vix_level >= vix_spike_threshold:
        hard_reasons.append(
            f"VIX at {vix_level:.1f} is at/above the {vix_spike_threshold:.0f} spike threshold "
            "(checked at scan time -- a later intraday spike won't be caught until the next scan)."
        )

    if snapshot.adx14 >= adx_trend_threshold and snapshot.volume_ratio >= volume_ratio_threshold:
        soft_reasons.append(
            f"Last session was trending (ADX {snapshot.adx14:.1f}) on {snapshot.volume_ratio:.2f}x "
            "its 20-day average volume -- if today extends that (confirm intraday), treat this as "
            "a skip-worthy trending/high-volume day even though the morning scan can't see today's "
            "volume yet."
        )

    if catalyst_hits:
        soft_reasons.append(
            f"{len(catalyst_hits)} headline(s) flagged as potential macro catalysts (see News & "
            "Catalysts) -- not a scheduled event, but worth reading before trading."
        )

    return GateResult(
        skip=bool(hard_reasons),
        hard_reasons=hard_reasons,
        soft_reasons=soft_reasons,
        gap_pct=gap_pct,
        gap_confirmed=gap_confirmed,
    )
