"""Day gates and per-trade sizing.

Day gates reuse the repo's own skip-day calendar (qqq_iron_condor.config's
macro_event_dates: FOMC decisions, CPI prints) and its VIX spike threshold.
Per-candidate: an earnings date today is a skip (the stock can gap and the
implied volatility collapses after the print); one before the option expires
is a flag.

Sizing follows trader-budget, in contracts: the smallest of the max loss per
trade (the loss per contract if the stop fills at the stop), the position cap,
the max order and the cash after the buffer, all from trader/config.json.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

TRADER_DEFAULTS = {
    "budget": 5000.0,
    "cash_buffer_pct": 10.0,
    "max_position_pct": 75.0,
    "max_order_usd": 4000.0,
    "max_loss_per_trade_usd": 50.0,
    "no_entries_before": "09:45",
    "no_entries_after": "15:30",
    "flat_by": "15:55",
    "allow_options": False,
    "paper": True,
}


def load_trader_config(path: str) -> dict:
    cfg = dict(TRADER_DEFAULTS)
    try:
        cfg.update(json.loads(Path(path).read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def day_gates(today: dt.date, macro_event_dates: dict, vix_level: Optional[float], vix_threshold: float) -> list[str]:
    reasons = []
    event = macro_event_dates.get(today.isoformat())
    if event:
        reasons.append(f"Scheduled macro event today: {event}.")
    if vix_level is not None and vix_level >= vix_threshold:
        reasons.append(f"VIX at {vix_level:.1f} is at/above the {vix_threshold:.0f} spike threshold.")
    return reasons


def earnings_gate(earnings: Optional[dt.date], today: dt.date, expiration: str) -> tuple[Optional[str], list[str]]:
    """(skip_reason, flags)."""
    if earnings is None:
        return None, ["No earnings date found; check before trading."]
    if earnings == today:
        return f"Earnings today ({earnings.isoformat()}).", []
    exp = dt.datetime.strptime(expiration, "%Y-%m-%d").date()
    if today < earnings <= exp:
        return None, [f"Earnings {earnings.isoformat()} before this option expires: implied volatility drops after the report."]
    return None, []


@dataclass
class Sizing:
    contracts: int
    binding: str
    loss_per_contract: float
    cost_per_contract: float
    limits: dict = field(default_factory=dict)
    note: str = ""


def size_contracts(cost: float, value_at_stop: float, trader: dict) -> Sizing:
    """cost: the ask per share of the option; value_at_stop: what it would
    sell for if the stop filled at the stop, right now."""
    per_contract = cost * 100.0
    loss = max(cost - value_at_stop, 0.01) * 100.0
    budget = float(trader["budget"])
    limits = {
        "max loss": math.floor(float(trader["max_loss_per_trade_usd"]) / loss),
        "position cap": math.floor(budget * float(trader["max_position_pct"]) / 100.0 / per_contract),
        "max order": math.floor(float(trader["max_order_usd"]) / per_contract),
        "cash": math.floor(budget * (1 - float(trader["cash_buffer_pct"]) / 100.0) / per_contract),
    }
    binding = min(limits, key=limits.get)
    contracts = max(limits[binding], 0)
    note = ""
    if contracts == 0:
        if binding == "max loss":
            note = (f"1 contract would lose about ${loss:,.0f} at the stop, over your "
                    f"${float(trader['max_loss_per_trade_usd']):,.0f} max loss per trade.")
        else:
            note = f"1 contract costs ${per_contract:,.0f}, over your {binding} limit."
    return Sizing(contracts, binding, round(loss, 2), round(per_contract, 2), limits, note)
