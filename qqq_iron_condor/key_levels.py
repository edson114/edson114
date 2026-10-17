"""Key levels + reaction setups: "predictions cost, reactions pay".

A rules-based version of a four-pillar discretionary framework, run on
hourly QQQ bars:

1. **The map.** Swing highs/lows (a bar whose high/low beats `pivot_bars`
   bars on each side) from the last `lookback_bars` hourly bars, clustered
   within `cluster_atr` x hourly ATR. A cluster with at least `min_touches`
   swings is a level; more touches = stronger.
2. **Confirmation (a reaction, not a prediction).** A setup only counts
   once an hourly bar has *closed*:
     - reclaim:   price closed below a level, then a later bar closes back
                  above it -> call (mirror: a failed breakout -> put)
     - flip:      price broke out above a level, a later bar retests it
                  (low within tolerance) and closes above -> call
                  (mirror: support turned resistance -> put)
     - rejection: a bar tags a level from below, closes back under it with
                  a long upper wick -> put (mirror: hammer at support -> call)
   `setups="three"` keeps only the original three (flip and reclaim long,
   rejection short); `"all"` adds the mirrors.
3. **Invalidation.** The level itself: an hourly close back through it
   (the backtest's `stop_on_close`), sized up front.
4. **Context -> size.** QQQ's daily trend (prior close vs a rising/falling
   20-day average) and extension (today's move so far vs the daily ATR):
   both agree -> full size, one against -> half size, both against ->
   pass. Scheduled catalysts (FOMC/CPI) are already skipped by the plan's
   hard gate.

Only bars completed before the decision are used.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class LevelParams:
    lookback_bars: int = 140  # ~20 sessions of hourly bars
    pivot_bars: int = 3
    atr_bars: int = 50
    cluster_atr: float = 0.35  # swings within this x hourly ATR form one level
    min_touches: int = 2
    tolerance_atr: float = 0.25  # "at the level" / retest tolerance
    wick_ratio: float = 0.5  # rejection: wick >= this share of the bar's range
    breakout_lookback: int = 14  # flip: breakout within this many bars (~2 sessions)
    setups: str = "all"  # "three" (original) or "all" (with mirrors)
    use_context: bool = True
    extended_atr: float = 1.0  # move from prior close >= this x daily ATR = extended


@dataclass
class Level:
    price: float
    touches: int


@dataclass
class Setup:
    side: str  # "call" / "put"
    kind: str  # "reclaim", "flip", "rejection" (+ "failed breakout", etc.)
    level: Level
    stop: float  # QQQ level whose hourly close invalidates the trade
    size: float = 1.0
    context: str = ""


def _atr(bars: pd.DataFrame, n: int) -> float:
    prev = bars["Close"].shift(1)
    tr = pd.concat([bars["High"] - bars["Low"], (bars["High"] - prev).abs(), (bars["Low"] - prev).abs()], axis=1).max(axis=1)
    return float(tr.tail(n).mean())


def find_levels(bars: pd.DataFrame, atr: float, p: LevelParams) -> list[Level]:
    h = bars["High"].to_numpy(float)
    l = bars["Low"].to_numpy(float)
    k = p.pivot_bars
    swings = []
    for i in range(k, len(bars) - k):
        if h[i] == max(h[i - k : i + k + 1]):
            swings.append(h[i])
        if l[i] == min(l[i - k : i + k + 1]):
            swings.append(l[i])
    swings.sort()
    levels: list[Level] = []
    cluster: list[float] = []
    for px in swings:
        if cluster and px - cluster[-1] > p.cluster_atr * atr:
            if len(cluster) >= p.min_touches:
                levels.append(Level(float(np.mean(cluster)), len(cluster)))
            cluster = []
        cluster.append(px)
    if len(cluster) >= p.min_touches:
        levels.append(Level(float(np.mean(cluster)), len(cluster)))
    return levels


def _context_size(side: str, price: float, daily: pd.DataFrame, p: LevelParams) -> tuple[float, str]:
    """daily: completed daily bars (Open/High/Low/Close) before today."""
    if not p.use_context or daily is None or len(daily) < 25:
        return 1.0, "context not checked"
    close = daily["Close"]
    sma = close.rolling(20).mean()
    up_trend = close.iloc[-1] > sma.iloc[-1] and sma.iloc[-1] > sma.iloc[-6]
    down_trend = close.iloc[-1] < sma.iloc[-1] and sma.iloc[-1] < sma.iloc[-6]
    trend_ok = up_trend if side == "call" else down_trend
    prev = daily.copy()
    tr = pd.concat([prev["High"] - prev["Low"], (prev["High"] - close.shift()).abs(), (prev["Low"] - close.shift()).abs()], axis=1).max(axis=1)
    datr = float(tr.tail(14).mean())
    move = price - float(close.iloc[-1])
    extended = datr > 0 and (move if side == "call" else -move) >= p.extended_atr * datr
    ok = int(trend_ok) + int(not extended)
    size = {2: 1.0, 1: 0.5, 0: 0.0}[ok]
    trend_txt = "daily trend agrees" if trend_ok else "daily trend doesn't agree"
    ext_txt = "move already extended" if extended else "move not extended"
    return size, f"{trend_txt}, {ext_txt} -> {'full size' if size == 1 else 'half size' if size else 'pass'}"


def detect_setup(history: pd.DataFrame, today: pd.DataFrame, daily: Optional[pd.DataFrame], p: LevelParams = LevelParams()) -> Optional[Setup]:
    """history: hourly bars before today's session (for the map).
    today: today's completed hourly bars so far (the last one is the
    trigger bar). daily: completed daily bars before today (context)."""
    if history is None or len(history) < p.atr_bars or today is None or len(today) == 0:
        return None
    hist = history.tail(p.lookback_bars)
    atr = _atr(hist, p.atr_bars)
    if not atr > 0:
        return None
    levels = find_levels(hist, atr, p)
    if not levels:
        return None
    tol = p.tolerance_atr * atr
    recent = pd.concat([hist, today]).tail(p.breakout_lookback + 1)
    c = today.iloc[-1]
    o, h, lo, cl = float(c["Open"]), float(c["High"]), float(c["Low"]), float(c["Close"])
    rng = max(h - lo, 1e-9)
    prev_closes = recent["Close"].iloc[:-1].to_numpy(float)
    found: list[Setup] = []
    for lv in sorted(levels, key=lambda x: abs(x.price - cl)):
        L = lv.price
        if abs(cl - L) > 2 * atr:
            continue
        prior_today = today["Close"].iloc[:-1].to_numpy(float)
        # Reclaim: closed below L earlier (today or the last bars), now closes back above it.
        if cl > L + 0.1 * tol and len(prev_closes) and (prev_closes[-3:] < L).any():
            found.append(Setup("call", "reclaim", lv, L))
        # Failed breakout (mirror): closed above L, now closes back below.
        if p.setups == "all" and cl < L - 0.1 * tol and len(prev_closes) and (prev_closes[-3:] > L).any():
            found.append(Setup("put", "failed breakout", lv, L))
        # Flip: a breakout close above L within the window, closes stayed above, this bar retests and holds.
        above = prev_closes > L
        if cl > L and lo <= L + tol and len(prev_closes) >= 2:
            crosses = np.nonzero(above[1:] & ~above[:-1])[0]
            if len(crosses) and above[crosses[-1] + 1 :].all():
                found.append(Setup("call", "flip (old resistance held as support)", lv, L))
        below = prev_closes < L
        if p.setups == "all" and cl < L and h >= L - tol and len(prev_closes) >= 2:
            crosses = np.nonzero(below[1:] & ~below[:-1])[0]
            if len(crosses) and below[crosses[-1] + 1 :].all():
                found.append(Setup("put", "flip (old support held as resistance)", lv, L))
        # Rejection at resistance: tagged L from below, closed back under it, long upper wick.
        upper_wick = h - max(o, cl)
        if h >= L - tol and cl < L and cl < o and upper_wick >= p.wick_ratio * rng and (len(prior_today) == 0 or prior_today[-1] < L + tol):
            found.append(Setup("put", "rejection at resistance", lv, max(L, h)))
        lower_wick = min(o, cl) - lo
        if p.setups == "all" and lo <= L + tol and cl > L and cl > o and lower_wick >= p.wick_ratio * rng and (len(prior_today) == 0 or prior_today[-1] > L - tol):
            found.append(Setup("call", "rejection at support", lv, min(L, lo)))
        if found:
            break  # nearest level with a setup wins
    if not found:
        return None
    s = max(found, key=lambda x: x.level.touches)
    s.size, s.context = _context_size(s.side, cl, daily, p)
    return s


def make_backtest_direction(intraday: pd.DataFrame, daily: pd.DataFrame, p: LevelParams = LevelParams()):
    """Direction callable for simulate(direction=..., entry_window=(1, 5),
    stop_on_close=True): returns (side, stop_level, size) or None."""
    intraday = intraday.sort_index()
    daily = daily.sort_index()

    def direction(i, bars_before_entry: pd.DataFrame):
        if bars_before_entry is None or len(bars_before_entry) == 0:
            return None
        start = bars_before_entry.index[0]
        history = intraday[intraday.index < start]
        prior_daily = daily[daily.index < pd.Timestamp(start.date())]
        s = detect_setup(history, bars_before_entry, prior_daily, p)
        if s is None or s.size == 0:
            return None
        return s.side, s.stop, s.size

    return direction
