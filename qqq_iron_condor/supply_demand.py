"""Supply/demand read for the daily $ target plan, from hourly OHLCV bars.

Two parts, combined into one -1..+1 score:

1. **Supply/demand zones.** A zone is the quiet "base" (one or two small-
   bodied bars) right before an impulse bar -- a bar whose body is at least
   `zone_impulse_atr` x the hourly ATR. An impulse up leaves a **demand**
   zone (buyers overwhelmed sellers there); an impulse down leaves a
   **supply** zone. A zone stays live until a later bar *closes* through
   its far side. At the entry price:
     - within `zone_proximity_atr` x ATR of a live demand zone -> +1
       (buying into support), of a live supply zone -> -1;
     - a demand zone broken by today's bars -> -1 (support failed), a
       supply zone broken today -> +1 (resistance gave way);
     - if several apply, the closest zone wins; nothing nearby -> 0.

2. **Buying vs selling pressure.** Chaikin Money Flow over the last
   `pressure_bars` hourly bars (about one session, ending with today's
   first hour): volume-weighted close-location value, in -1..+1. Positive
   when volume came in on bars closing near their highs.

score = zone_weight * zone + (1 - zone_weight) * pressure. At or above
+threshold -> "call", at or below -threshold -> "put", otherwise None.

This uses only bars completed before the entry time, so the backtest and
the live plan see the same information. It is a rules-based reading of
price and volume, not order-book data: free sources don't publish actual
resting bids and offers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SDParams:
    atr_bars: int = 50  # hourly bars in the ATR used to size impulses and proximity
    lookback_bars: int = 140  # ~20 sessions of hourly bars searched for zones
    zone_impulse_atr: float = 1.2  # impulse bar body >= this x ATR
    zone_base_atr: float = 0.5  # base bar bodies <= this x ATR
    zone_proximity_atr: float = 1.0  # "at" a zone = within this x ATR of it
    pressure_bars: int = 7  # Chaikin Money Flow window (~one session)
    zone_weight: float = 0.6
    threshold: float = 0.3


@dataclass
class Zone:
    kind: str  # "demand" or "supply"
    low: float
    high: float
    formed: pd.Timestamp
    broken_at: Optional[pd.Timestamp] = None


@dataclass
class SupplyDemandRead:
    ready: bool
    side: Optional[str]  # "call", "put" or None
    score: float = 0.0
    zone_score: float = 0.0
    pressure: float = 0.0
    zone_detail: str = ""
    zones: list = field(default_factory=list)  # live zones near price, for the report
    detail: str = ""


def _atr(bars: pd.DataFrame, n: int) -> float:
    high, low, close = bars["High"], bars["Low"], bars["Close"]
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return float(tr.tail(n).mean())


def find_zones(bars: pd.DataFrame, atr: float, p: SDParams) -> list[Zone]:
    """Zones formed in `bars`, with `broken_at` set if a later bar closed
    through the zone's far side."""
    o = bars["Open"].to_numpy(float)
    c = bars["Close"].to_numpy(float)
    h = bars["High"].to_numpy(float)
    l = bars["Low"].to_numpy(float)
    body = np.abs(c - o)
    zones: list[Zone] = []
    for i in range(1, len(bars)):
        if body[i] < p.zone_impulse_atr * atr:
            continue
        base = [j for j in (i - 1, i - 2) if j >= 0 and body[j] <= p.zone_base_atr * atr]
        # The base has to sit directly before the impulse.
        if not base or base[0] != i - 1:
            continue
        if len(base) == 2 and base[1] != i - 2:
            base = base[:1]
        kind = "demand" if c[i] > o[i] else "supply"
        z = Zone(kind, float(min(l[j] for j in base)), float(max(h[j] for j in base)), bars.index[i])
        later = c[i + 1 :]
        broken = np.nonzero(later < z.low)[0] if kind == "demand" else np.nonzero(later > z.high)[0]
        if len(broken):
            z.broken_at = bars.index[i + 1 + broken[0]]
        zones.append(z)
    return zones


def chaikin_money_flow(bars: pd.DataFrame) -> float:
    rng = (bars["High"] - bars["Low"]).replace(0, np.nan)
    clv = (((bars["Close"] - bars["Low"]) - (bars["High"] - bars["Close"])) / rng).fillna(0.0)
    vol = bars["Volume"].astype(float)
    total = float(vol.sum())
    return float((clv * vol).sum() / total) if total > 0 else 0.0


def supply_demand_read(history: pd.DataFrame, today_start: pd.Timestamp, p: SDParams = SDParams()) -> SupplyDemandRead:
    """Read from hourly bars `history` (index = ET wall time), using only
    bars that are complete -- the caller passes everything up to and
    including today's first hour. `today_start` is today's 9:30 bar time,
    used to tell zones broken *today* from ones broken earlier."""
    if history is None or len(history) < p.atr_bars + 2 or "Volume" not in history:
        return SupplyDemandRead(False, None, detail="Not enough hourly bars for a supply/demand read.")
    bars = history.tail(p.lookback_bars + p.atr_bars)
    atr = _atr(bars, p.atr_bars)
    if not atr > 0:
        return SupplyDemandRead(False, None, detail="Hourly ATR unavailable.")
    price = float(bars["Close"].iloc[-1])
    zones = find_zones(bars.tail(p.lookback_bars), atr, p)

    candidates = []  # (distance, score, description)
    near = []
    for z in zones:
        if z.broken_at is not None:
            if z.broken_at >= today_start:
                flip = -1.0 if z.kind == "demand" else 1.0
                dist = abs(price - (z.low if z.kind == "demand" else z.high))
                candidates.append((dist, flip, f"broke {'below' if z.kind == 'demand' else 'above'} the "
                                   f"{z.kind} zone ${z.low:,.2f}-${z.high:,.2f} today"))
            continue
        if z.kind == "demand":
            dist = max(price - z.high, z.low - price, 0.0)
            if price >= z.low - p.zone_proximity_atr * atr and dist <= p.zone_proximity_atr * atr:
                candidates.append((dist, 1.0, f"at the demand zone ${z.low:,.2f}-${z.high:,.2f}"))
        else:
            dist = max(z.low - price, price - z.high, 0.0)
            if price <= z.high + p.zone_proximity_atr * atr and dist <= p.zone_proximity_atr * atr:
                candidates.append((dist, -1.0, f"at the supply zone ${z.low:,.2f}-${z.high:,.2f}"))
        if abs(price - (z.low + z.high) / 2) <= 3 * atr:
            near.append(z)

    if candidates:
        _, zone_score, zone_detail = min(candidates, key=lambda x: x[0])
    else:
        zone_score, zone_detail = 0.0, "no live supply/demand zone near the price"

    pressure = chaikin_money_flow(bars.tail(p.pressure_bars))
    score = round(p.zone_weight * zone_score + (1 - p.zone_weight) * pressure, 3)
    side = "call" if score >= p.threshold else ("put" if score <= -p.threshold else None)
    flow = "buying" if pressure > 0.05 else ("selling" if pressure < -0.05 else "balanced")
    detail = (
        f"QQQ ${price:,.2f} is {zone_detail}; volume pressure {pressure:+.2f} ({flow}) "
        f"over the last {p.pressure_bars} hourly bars -> score {score:+.2f}."
    )
    near.sort(key=lambda z: abs(price - (z.low + z.high) / 2))
    return SupplyDemandRead(True, side, score, zone_score, round(pressure, 3), zone_detail, near[:4], detail)


def hourly_bars_before(intraday: pd.DataFrame, when: pd.Timestamp) -> pd.DataFrame:
    """Hourly bars that *started* before `when` -- i.e. completed by then
    for bars that start on the hour grid (9:30, 10:30, ...)."""
    return intraday[intraday.index < when]


def make_backtest_direction(intraday: pd.DataFrame, p: SDParams = SDParams(), require_first_hour: bool = False):
    """Direction callable for daily_target_backtest.simulate(direction=...).
    Uses hourly bars up to the entry bar (entry_bar >= 1); with
    `require_first_hour`, trades only when the first hour moved the same way."""
    intraday = intraday.sort_index()

    def direction(i, bars_before_entry: pd.DataFrame) -> Optional[str]:
        if bars_before_entry is None or len(bars_before_entry) == 0:
            return None
        entry_time = bars_before_entry.index[-1] + pd.Timedelta(hours=1)
        hist = hourly_bars_before(intraday, entry_time)
        read = supply_demand_read(hist, bars_before_entry.index[0], p)
        if not read.ready or read.side is None:
            return None
        if require_first_hour:
            move = float(bars_before_entry["Close"].iloc[-1] - bars_before_entry["Open"].iloc[0])
            if (move > 0) != (read.side == "call") or move == 0:
                return None
        return read.side

    return direction
