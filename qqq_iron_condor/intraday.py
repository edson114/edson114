"""Intraday (same-session) technical read: VWAP, opening-range breakout,
and short-window EMA/RSI on intraday bars -- built for the directional
(buy calls/puts) signal, distinct from the daily-bar MarketSnapshot used
by the iron condor scan.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ind

_INTERVAL_MINUTES = {"1m": 1, "2m": 2, "5m": 5, "15m": 15, "30m": 30, "60m": 60, "90m": 90}


def interval_minutes(interval: str) -> int:
    return _INTERVAL_MINUTES.get(interval, 5)


@dataclass
class IntradaySnapshot:
    last_price: float
    vwap: float
    price_vs_vwap_pct: float
    opening_range_high: float
    opening_range_low: float
    orb_status: str  # "above_range", "below_range", "inside_range", "unknown"
    ema9: float
    ema21: float
    ema_trend: str  # "bullish", "bearish", "flat"
    rsi14: float
    bars_used: int


def build_intraday_snapshot(intraday: pd.DataFrame, opening_range_minutes: int, bar_minutes: int) -> IntradaySnapshot:
    if intraday.empty:
        raise RuntimeError("No intraday bars available -- market may be closed or data unavailable.")

    close = intraday["Close"]
    high = intraday["High"]
    low = intraday["Low"]
    volume = intraday["Volume"]

    last_price = float(close.iloc[-1])

    typical_price = (high + low + close) / 3.0
    cum_vol = volume.cumsum()
    vwap_series = (typical_price * volume).cumsum() / cum_vol.replace(0, np.nan)
    vwap = float(vwap_series.iloc[-1])
    price_vs_vwap_pct = (last_price / vwap - 1.0) * 100.0 if vwap == vwap and vwap != 0 else float("nan")

    range_bars = max(1, opening_range_minutes // bar_minutes)
    orb_bars = intraday.iloc[:range_bars]
    orb_high = float(orb_bars["High"].max())
    orb_low = float(orb_bars["Low"].min())

    if len(intraday) <= range_bars:
        orb_status = "unknown"
    elif last_price > orb_high:
        orb_status = "above_range"
    elif last_price < orb_low:
        orb_status = "below_range"
    else:
        orb_status = "inside_range"

    ema9_series = ind.ema(close, 9)
    ema21_series = ind.ema(close, 21)
    ema9 = float(ema9_series.iloc[-1]) if not ema9_series.dropna().empty else float("nan")
    ema21 = float(ema21_series.iloc[-1]) if not ema21_series.dropna().empty else float("nan")
    if ema9 == ema9 and ema21 == ema21:
        ema_trend = "bullish" if ema9 > ema21 else ("bearish" if ema9 < ema21 else "flat")
    else:
        ema_trend = "flat"

    rsi_series = ind.rsi(close, 14)
    rsi14 = float(rsi_series.iloc[-1]) if not rsi_series.dropna().empty else float("nan")

    return IntradaySnapshot(
        last_price=last_price,
        vwap=vwap,
        price_vs_vwap_pct=price_vs_vwap_pct,
        opening_range_high=orb_high,
        opening_range_low=orb_low,
        orb_status=orb_status,
        ema9=ema9,
        ema21=ema21,
        ema_trend=ema_trend,
        rsi14=rsi14,
        bars_used=len(intraday),
    )


def compute_relative_strength(qqq_intraday: pd.DataFrame, spy_intraday: pd.DataFrame) -> float:
    """QQQ's %-change-so-far today minus SPY's -- outperformance points to
    tech-specific strength (or weakness) rather than a broad market move."""

    def _pct_change(df: pd.DataFrame) -> float:
        open_ = float(df["Open"].iloc[0])
        last = float(df["Close"].iloc[-1])
        return (last / open_ - 1.0) * 100.0 if open_ else 0.0

    return _pct_change(qqq_intraday) - _pct_change(spy_intraday)
