"""Turns raw price history (QQQ + VIX) into the technical snapshot used
in the daily report: trend, momentum, volatility regime, support/resistance.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from . import indicators as ind


@dataclass
class MarketSnapshot:
    spot: float
    prev_close: float
    day_change_pct: float

    rsi14: float
    sma20: float
    sma50: float
    sma200: float
    ema9: float
    ema21: float
    macd_line: float
    macd_signal: float
    macd_hist: float
    bb_upper: float
    bb_mid: float
    bb_lower: float
    bb_width_pct: float
    atr14: float
    adx14: float
    hv20_pct: float

    high_20d: float
    low_20d: float
    high_50d: float
    low_50d: float

    vix_level: float
    vix_percentile_1y: float

    trend_label: str
    regime_label: str
    regime_notes: list


def _trend_label(spot: float, sma50: float, sma200: float, macd_hist: float, adx: float, adx_threshold: float) -> str:
    if adx < adx_threshold:
        return "Range-bound / low trend strength"
    if spot > sma50 > sma200 and macd_hist > 0:
        return "Uptrend"
    if spot < sma50 < sma200 and macd_hist < 0:
        return "Downtrend"
    return "Mixed / transitional trend"


def build_snapshot(price_history: pd.DataFrame, vix_history: pd.DataFrame, adx_threshold: float) -> MarketSnapshot:
    close = price_history["Close"]
    high = price_history["High"]
    low = price_history["Low"]

    spot = float(close.iloc[-1])
    prev_close = float(close.iloc[-2])
    day_change_pct = (spot / prev_close - 1.0) * 100.0

    rsi14 = float(ind.rsi(close, 14).iloc[-1])
    sma20 = float(ind.sma(close, 20).iloc[-1])
    sma50 = float(ind.sma(close, 50).iloc[-1])
    sma200 = float(ind.sma(close, 200).iloc[-1]) if len(close) >= 200 else float("nan")
    ema9 = float(ind.ema(close, 9).iloc[-1])
    ema21 = float(ind.ema(close, 21).iloc[-1])

    macd_line, macd_signal, macd_hist = ind.macd(close)
    bb_upper, bb_mid, bb_lower, bb_width_pct = ind.bollinger_bands(close)
    atr14 = float(ind.atr(high, low, close, 14).iloc[-1])
    adx14 = float(ind.adx(high, low, close, 14).iloc[-1])
    hv20 = float(ind.historical_volatility(close, 20).iloc[-1]) * 100.0

    high_20d = float(high.tail(20).max())
    low_20d = float(low.tail(20).min())
    high_50d = float(high.tail(50).max())
    low_50d = float(low.tail(50).min())

    vix_close = vix_history["Close"]
    vix_level = float(vix_close.iloc[-1])
    vix_percentile_1y = ind.percentile_rank(vix_close, vix_level)

    trend_label = _trend_label(spot, sma50, sma200, float(macd_hist.iloc[-1]), adx14, adx_threshold)

    regime_notes = []
    if adx14 >= adx_threshold:
        regime_notes.append(
            f"ADX({adx14:.1f}) is above the {adx_threshold:.0f} trend threshold -- "
            "the market is trending, which raises directional risk for a range-bound "
            "iron condor. Consider skewing strikes, reducing size, or skipping today."
        )
    if vix_percentile_1y >= 70:
        regime_notes.append(
            f"VIX ({vix_level:.1f}) is in the {vix_percentile_1y:.0f}th percentile of its "
            "1-year range -- elevated/rich premium environment, but also higher gap risk."
        )
    elif vix_percentile_1y <= 20:
        regime_notes.append(
            f"VIX ({vix_level:.1f}) is in the {vix_percentile_1y:.0f}th percentile of its "
            "1-year range -- premium is cheap; credit received may not justify the risk."
        )
    if rsi14 >= 70:
        regime_notes.append(f"RSI14 ({rsi14:.1f}) is overbought -- watch for mean reversion or a pullback.")
    elif rsi14 <= 30:
        regime_notes.append(f"RSI14 ({rsi14:.1f}) is oversold -- watch for a bounce.")

    regime_label = "High IV" if vix_percentile_1y >= 70 else ("Low IV" if vix_percentile_1y <= 20 else "Normal IV")

    return MarketSnapshot(
        spot=spot,
        prev_close=prev_close,
        day_change_pct=day_change_pct,
        rsi14=rsi14,
        sma20=sma20,
        sma50=sma50,
        sma200=sma200,
        ema9=ema9,
        ema21=ema21,
        macd_line=float(macd_line.iloc[-1]),
        macd_signal=float(macd_signal.iloc[-1]),
        macd_hist=float(macd_hist.iloc[-1]),
        bb_upper=float(bb_upper.iloc[-1]),
        bb_mid=float(bb_mid.iloc[-1]),
        bb_lower=float(bb_lower.iloc[-1]),
        bb_width_pct=float(bb_width_pct.iloc[-1]) * 100.0,
        atr14=atr14,
        adx14=adx14,
        hv20_pct=hv20,
        high_20d=high_20d,
        low_20d=low_20d,
        high_50d=high_50d,
        low_50d=low_50d,
        vix_level=vix_level,
        vix_percentile_1y=vix_percentile_1y,
        trend_label=trend_label,
        regime_label=regime_label,
        regime_notes=regime_notes,
    )
