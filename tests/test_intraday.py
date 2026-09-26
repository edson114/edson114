"""Unit tests for the intraday snapshot (VWAP, opening range, EMA/RSI)."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from qqq_iron_condor.intraday import (
    build_intraday_snapshot,
    compute_relative_strength,
    compute_relative_volume,
    interval_minutes,
)


def _bars(closes, volume=100_000):
    n = len(closes)
    closes = np.array(closes, dtype=float)
    idx = pd.date_range(start=dt.datetime(2026, 1, 2, 9, 30), periods=n, freq="5min")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes + 0.1,
            "Low": closes - 0.1,
            "Close": closes,
            "Volume": [volume] * n,
        },
        index=idx,
    )


def test_interval_minutes_known_and_unknown():
    assert interval_minutes("5m") == 5
    assert interval_minutes("15m") == 15
    assert interval_minutes("bogus") == 5  # falls back to a sane default


def test_orb_breakout_above_range():
    # First 3 bars (15 min at 5m bars) set the opening range 100-101; then a clean breakout up.
    closes = [100, 101, 100.5] + [102, 103, 104]
    snap = build_intraday_snapshot(_bars(closes), opening_range_minutes=15, bar_minutes=5)
    assert snap.orb_status == "above_range"
    assert snap.opening_range_high == pytest.approx(101.1)
    assert snap.last_price == pytest.approx(104)


def test_orb_breakdown_below_range():
    closes = [100, 101, 100.5] + [99, 98, 97]
    snap = build_intraday_snapshot(_bars(closes), opening_range_minutes=15, bar_minutes=5)
    assert snap.orb_status == "below_range"


def test_orb_inside_range_when_no_breakout():
    closes = [100, 101, 100.5] + [100.2, 100.8, 100.4]
    snap = build_intraday_snapshot(_bars(closes), opening_range_minutes=15, bar_minutes=5)
    assert snap.orb_status == "inside_range"


def test_orb_unknown_while_still_inside_opening_window():
    closes = [100, 101]  # fewer bars than the 3-bar opening range window
    snap = build_intraday_snapshot(_bars(closes), opening_range_minutes=15, bar_minutes=5)
    assert snap.orb_status == "unknown"


def test_vwap_above_price_trending_up_gives_positive_deviation():
    closes = [100] * 3 + list(np.linspace(100, 110, 20))
    snap = build_intraday_snapshot(_bars(closes), opening_range_minutes=15, bar_minutes=5)
    assert snap.price_vs_vwap_pct > 0


def test_ema_trend_bullish_on_sustained_uptrend():
    closes = list(np.linspace(100, 130, 40))
    snap = build_intraday_snapshot(_bars(closes), opening_range_minutes=15, bar_minutes=5)
    assert snap.ema_trend == "bullish"
    assert snap.ema9 > snap.ema21


def test_ema_trend_bearish_on_sustained_downtrend():
    closes = list(np.linspace(130, 100, 40))
    snap = build_intraday_snapshot(_bars(closes), opening_range_minutes=15, bar_minutes=5)
    assert snap.ema_trend == "bearish"


def test_empty_intraday_raises():
    with pytest.raises(RuntimeError):
        build_intraday_snapshot(pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"]), 15, 5)


def test_relative_strength_positive_when_qqq_outperforms():
    qqq = _bars([100, 102, 104])  # +4%
    spy = _bars([500, 502, 503])  # +0.6%
    rel = compute_relative_strength(qqq, spy)
    assert rel > 0


def test_relative_strength_negative_when_qqq_underperforms():
    qqq = _bars([100, 99, 98])
    spy = _bars([500, 502, 504])
    rel = compute_relative_strength(qqq, spy)
    assert rel < 0


def _multi_day_bars(day_offsets_and_volumes, n_bars_per_day=10, volume=100_000):
    """day_offsets_and_volumes: list of (day_offset, volume) -- distinct
    calendar dates so compute_relative_volume can group by day."""
    frames = []
    for offset, vol in day_offsets_and_volumes:
        start = dt.datetime(2026, 1, 2, 9, 30) + dt.timedelta(days=offset)
        idx = pd.date_range(start=start, periods=n_bars_per_day, freq="5min")
        frames.append(
            pd.DataFrame(
                {"Open": 100.0, "High": 100.1, "Low": 99.9, "Close": 100.0, "Volume": vol},
                index=idx,
            )
        )
    return pd.concat(frames)


def test_relative_volume_above_one_when_today_busier_than_average():
    historical = _multi_day_bars([(0, 1000), (1, 1000), (2, 1000)])
    today_so_far = _multi_day_bars([(3, 2000)])
    rel_vol = compute_relative_volume(today_so_far, historical)
    assert rel_vol == pytest.approx(2.0)


def test_relative_volume_below_one_when_today_quieter_than_average():
    historical = _multi_day_bars([(0, 1000), (1, 1000)])
    today_so_far = _multi_day_bars([(3, 500)])
    rel_vol = compute_relative_volume(today_so_far, historical)
    assert rel_vol == pytest.approx(0.5)


def test_relative_volume_only_compares_same_number_of_bars_elapsed():
    # Historical days have 20 bars; "today so far" only has 5 -- comparison
    # must use each historical day's first 5 bars' volume, not its full-day total.
    historical = _multi_day_bars([(0, 100), (1, 100)], n_bars_per_day=20)
    today_so_far = _multi_day_bars([(3, 100)], n_bars_per_day=5)
    rel_vol = compute_relative_volume(today_so_far, historical)
    assert rel_vol == pytest.approx(1.0)


def test_relative_volume_defaults_to_neutral_with_no_history():
    today_so_far = _multi_day_bars([(0, 1000)])
    rel_vol = compute_relative_volume(today_so_far, pd.DataFrame(columns=today_so_far.columns))
    assert rel_vol == pytest.approx(1.0)


def test_relative_volume_defaults_to_neutral_with_empty_today():
    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    historical = _multi_day_bars([(0, 1000)])
    assert compute_relative_volume(empty, historical) == pytest.approx(1.0)
