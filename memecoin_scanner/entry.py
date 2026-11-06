"""Entry point (where to buy) for each pick: buy zone, action, stop, take-profits.

Meme coins that pass the scan are usually mid-run, and buying the top of a
green candle is how most people lose. So the plan buys a pullback: the
38.2%-61.8% retracement of the last up-leg (3h of 5-minute candles from
GeckoTerminal). When candles aren't available the leg is estimated from
DexScreener's 5m/1h/6h % changes and the plan says so.

Actions:
  BUY NOW  price is in the buy zone (or just above it)
  WAIT     above the zone: set a limit buy in it, or let the coin go;
           below the zone: buy only if it climbs back above the zone
  SKIP     price broke below the leg's swing low: the setup failed
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from . import data
from .config import Config
from .data import PairSnapshot


@dataclass(frozen=True)
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float  # USD


def parse_ohlcv(payload) -> List[Candle]:
    """GeckoTerminal ohlcv_list (newest first) → candles, oldest first."""
    try:
        rows = payload["data"]["attributes"]["ohlcv_list"]
    except (KeyError, TypeError):
        return []
    out = []
    for row in rows or []:
        try:
            ts, o, h, l, c, v = row[:6]
            out.append(Candle(int(ts), float(o), float(h), float(l), float(c), float(v or 0)))
        except (TypeError, ValueError):
            continue
    out.sort(key=lambda c: c.ts)
    return [c for c in out if c.high > 0 and c.low > 0]


def fetch_candles(pair: PairSnapshot, cfg: Config) -> Optional[List[Candle]]:
    """5-minute candles for the pick's pool, or None if GeckoTerminal won't answer."""
    if not pair.pair_address:
        return None
    payload = data._get_json(
        f"{data.GECKOTERMINAL}/networks/{pair.chain}/pools/{pair.pair_address}/ohlcv/minute",
        cfg.request_timeout_s,
        params={"aggregate": cfg.candle_aggregate_min, "limit": cfg.candle_limit},
        retries=cfg.candle_retries,
        backoff_s=cfg.candle_backoff_s,
    )
    candles = parse_ohlcv(payload)
    return candles or None


def vwap(candles: List[Candle]) -> Optional[float]:
    total_v = sum(c.volume for c in candles)
    if total_v <= 0:
        return None
    return sum((c.high + c.low + c.close) / 3 * c.volume for c in candles) / total_v


def swing_from_candles(candles: List[Candle], lookback: int) -> Tuple[float, float]:
    """(swing low, swing high) of the last up-leg inside the lookback window.

    The high is the window's highest high; the low is the lowest low before
    it. If the high is the window's first candle (falling the whole time),
    the low is simply the window's lowest low.
    """
    window = candles[-lookback:]
    hi_idx = max(range(len(window)), key=lambda i: window[i].high)
    high = window[hi_idx].high
    before = window[: hi_idx + 1] if hi_idx > 0 else window
    low = min(c.low for c in before)
    return low, high


def swing_from_changes(p: PairSnapshot) -> Tuple[float, float]:
    """Rough (low, high) of the last hour, from DexScreener's % changes."""
    price = p.price_usd

    def ago(window: str) -> float:
        change = p.price_change.get(window, 0.0)
        return price / (1 + change / 100) if change > -100 else price

    anchors = [price, ago("m5"), ago("h1")]
    low, high = min(anchors), max(anchors)
    if high < low * 1.08:  # flat hour: widen to 6h
        anchors.append(ago("h6"))
        low, high = min(anchors), max(anchors)
    return low, high


@dataclass
class EntryPlan:
    action: str  # BUY NOW / WAIT / SKIP
    price: float
    zone_low: float
    zone_high: float
    entry: float
    stop: float
    swing_low: float
    swing_high: float
    method: str  # where the levels came from
    note: str
    vwap: Optional[float] = None

    @property
    def stop_pct(self) -> float:
        return (1 - self.stop / self.entry) * 100 if self.entry > 0 else 0.0

    def take_profits(self, cfg: Config) -> List[Tuple[float, float, str]]:
        return [(m, self.entry * m, what) for m, what in cfg.take_profit_ladder]


def plan_entry(p: PairSnapshot, candles: Optional[List[Candle]], cfg: Config) -> EntryPlan:
    price = p.price_usd
    if candles and len(candles) >= 6:
        low, high = swing_from_candles(candles, cfg.swing_lookback_candles)
        hours = cfg.swing_lookback_candles * cfg.candle_aggregate_min / 60
        method = f"{cfg.candle_aggregate_min}m candles, {hours:g}h swing"
        vw = vwap(candles)
    else:
        low, high = swing_from_changes(p)
        method = "estimated from the 5m/1h % changes (candles unavailable), 1h swing"
        vw = None
    high = max(high, price)  # the live price can be above the last closed candle
    low = min(low, high)

    leg = high - low
    if leg < high * 0.03:  # no real leg: buy near the range low
        zone_low, zone_high = low * 0.98, low + 0.5 * max(leg, low * 0.04)
    else:
        zone_high = high - cfg.entry_fib_shallow * leg
        zone_low = high - cfg.entry_fib_deep * leg

    tol = 1 + cfg.in_zone_tolerance_pct / 100
    if price < low:
        action, entry = "SKIP", zone_low
        note = f"broke below the swing low ({_fmt(low)}): the setup failed, don't buy"
    elif zone_low <= price <= zone_high * tol:
        action, entry = "BUY NOW", price
        note = "price is in the buy zone"
    elif price > zone_high * tol:
        action, entry = "WAIT", (zone_low + zone_high) / 2
        note = (f"price is {(price / zone_high - 1) * 100:.0f}% above the zone: set a limit buy near "
                f"{_fmt(entry)}; if it never pulls back, let it go")
    else:
        action, entry = "WAIT", zone_low
        note = f"price dipped under the zone: buy only if it climbs back above {_fmt(zone_low)}"

    structural = low * (1 - cfg.stop_below_swing_low_pct / 100)
    stop = min(entry * (1 - cfg.min_stop_pct / 100), max(entry * (1 - cfg.stop_loss_pct / 100), structural))
    return EntryPlan(
        action=action, price=price, zone_low=zone_low, zone_high=zone_high, entry=entry, stop=stop,
        swing_low=low, swing_high=high, method=method, note=note, vwap=vw,
    )


def fmt_price(x: float) -> str:
    if x <= 0:
        return "?"
    if x >= 1:
        return f"${x:,.4f}"
    # keep 4 significant digits for sub-cent meme prices
    return f"${x:.{max(4, 3 - int(f'{x:e}'.split('e')[1]))}f}"


_fmt = fmt_price
