"""The opening-range setup, both ways.

trader-entry's rule, long side: the first closed bar after the opening range
that CLOSES above the range high. Here the mirror image counts too: the first
close below the range low is a put setup. Whichever comes first is the day's
one trigger. trader-exit's default stop: the range midpoint, rounded to the
cent away from the entry.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np


@dataclass
class OrbSetup:
    status: str  # forming | waiting | triggered | faded
    or_high: float = math.nan
    or_low: float = math.nan
    option_type: Optional[str] = None  # "call" or "put"
    trigger_slot: Optional[int] = None
    trigger_price: float = math.nan
    last_slot: Optional[int] = None
    last_close: float = math.nan
    stop: float = math.nan
    note: str = ""

    @property
    def side(self) -> str:
        return "buy" if self.option_type == "call" else "sell"


def cents_away(x: float, long: bool) -> float:
    c = round(x * 100, 4)
    return (math.floor(c) if long else math.ceil(c)) / 100.0


def find_orb(today: np.ndarray, or_bars: int, last_entry_slot: int) -> OrbSetup:
    """today: (n, 4) closed bars from 9:30. last_entry_slot: the last bar
    whose close may still trigger (no_entries_after)."""
    if len(today) < or_bars:
        return OrbSetup(status="forming", note=f"{len(today)} of {or_bars} opening-range bars closed")

    or_high = float(today[:or_bars, 1].max())
    or_low = float(today[:or_bars, 2].min())
    last_slot = len(today) - 1
    last_close = float(today[-1, 3])
    base = dict(or_high=or_high, or_low=or_low, last_slot=last_slot, last_close=last_close)

    for i in range(or_bars, min(len(today), last_entry_slot + 1)):
        close = float(today[i, 3])
        if close > or_high or close < or_low:
            option_type = "call" if close > or_high else "put"
            long = option_type == "call"
            stop = cents_away((or_high + or_low) / 2.0, long)
            setup = OrbSetup(
                status="triggered", option_type=option_type, trigger_slot=i,
                trigger_price=round(close, 2), stop=stop, **base,
            )
            back_inside = last_close <= or_high if long else last_close >= or_low
            if back_inside:
                setup.status = "faded"
                setup.note = "the price is back inside the opening range since the trigger"
            return setup

    away = min(or_high - last_close, last_close - or_low)
    return OrbSetup(status="waiting", note=f"last close {last_close:.2f}, {away:.2f} from the range", **base)
