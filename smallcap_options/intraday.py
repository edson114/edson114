"""Intraday (same-session) read used by the directional signal: VWAP and
how extended price is off the session high/low -- the "still running" vs
"fading" distinction that separates a continuation CALL setup from a
failed-breakout PUT setup on the same green (or red) day.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class IntradaySnapshot:
    last_price: float
    vwap: float
    price_vs_vwap_pct: float
    session_high: float
    session_low: float
    pct_off_high: float  # how far below the session high, in %, always >= 0
    pct_off_low: float  # how far above the session low, in %, always >= 0
    bars_used: int


def build_intraday_snapshot(intraday: pd.DataFrame) -> IntradaySnapshot:
    if intraday.empty:
        raise RuntimeError("No intraday bars available -- market may be closed or data unavailable.")

    close = intraday["Close"]
    high = intraday["High"]
    low = intraday["Low"]
    volume = intraday["Volume"]

    last_price = float(close.iloc[-1])
    session_high = float(high.max())
    session_low = float(low.min())

    typical_price = (high + low + close) / 3.0
    cum_vol = volume.cumsum()
    vwap_series = (typical_price * volume).cumsum() / cum_vol.replace(0, np.nan)
    vwap = float(vwap_series.iloc[-1])
    price_vs_vwap_pct = (last_price / vwap - 1.0) * 100.0 if vwap == vwap and vwap != 0 else float("nan")

    pct_off_high = (session_high / last_price - 1.0) * 100.0 if last_price > 0 else 0.0
    pct_off_low = (last_price / session_low - 1.0) * 100.0 if session_low > 0 else 0.0

    return IntradaySnapshot(
        last_price=last_price,
        vwap=vwap,
        price_vs_vwap_pct=price_vs_vwap_pct,
        session_high=session_high,
        session_low=session_low,
        pct_off_high=max(0.0, pct_off_high),
        pct_off_low=max(0.0, pct_off_low),
        bars_used=len(intraday),
    )
