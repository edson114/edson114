"""Per-candidate trade gate: reasons a candidate is skipped regardless of
how strong its directional score is, plus advisory (soft) flags worth
reading before trading.

Hard gates (any one forces NO TRADE for this candidate):
  - No option contract clears the liquidity/spread bar (strategy.py).

Soft flags (shown, don't force a skip):
  - Float shares unknown (data provider gap -- verify manually).
  - Earnings reported within the next 5 calendar days (IV crush / gap risk
    on the very short-dated contracts this strategy uses).
  - No news catalyst found for the move.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GateResult:
    skip: bool
    hard_reasons: list = field(default_factory=list)
    soft_reasons: list = field(default_factory=list)


def evaluate_candidate_gates(
    contract_skip_reason: Optional[str],
    float_shares: Optional[float],
    next_earnings_date: Optional[dt.date],
    catalyst_found: bool,
    today: Optional[dt.date] = None,
) -> GateResult:
    today = today or dt.date.today()
    hard_reasons: list = []
    soft_reasons: list = []

    if contract_skip_reason:
        hard_reasons.append(contract_skip_reason)

    if float_shares is None:
        soft_reasons.append("Float shares unavailable from the data provider -- verify low float manually before trading.")

    if next_earnings_date is not None:
        days_out = (next_earnings_date - today).days
        if 0 <= days_out <= 5:
            soft_reasons.append(
                f"Earnings expected around {next_earnings_date.isoformat()} ({days_out}d out) -- elevated IV crush/"
                "gap risk for a short-dated contract held through that date."
            )

    if not catalyst_found:
        soft_reasons.append("No news catalyst found in free feeds for this move -- see the catalyst component detail.")

    return GateResult(skip=bool(hard_reasons), hard_reasons=hard_reasons, soft_reasons=soft_reasons)
