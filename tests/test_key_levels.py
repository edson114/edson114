"""Tests for the key-levels reaction setups and the simulator hooks they use."""

import datetime as dt

import pandas as pd
import pytest

from qqq_iron_condor import daily_target_backtest as bt
from qqq_iron_condor import key_levels as kl
from qqq_iron_condor.config import Config

P = kl.LevelParams(lookback_bars=60, atr_bars=10, use_context=False)


def _hist(level=100.0, n=60):
    """Hourly bars oscillating 98-100 so $100 is a multi-touch resistance."""
    rows, idx, t = [], [], pd.Timestamp("2026-09-01 09:30")
    for i in range(n):
        top = level if i % 6 == 3 else level - 1.0
        rows.append((level - 1.5, top, level - 2.0, level - 1.2, 1000))
        idx.append(t)
        t += pd.Timedelta(hours=1)
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close", "Volume"])


def _today(rows, start="2026-10-01 09:30"):
    idx = pd.date_range(start, periods=len(rows), freq="h")
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close", "Volume"])


def test_find_levels_counts_touches():
    h = _hist()
    levels = kl.find_levels(h, atr=1.0, p=P)
    top = max(levels, key=lambda lv: lv.price)
    assert top.price == pytest.approx(100.0, abs=0.05)
    assert top.touches >= 3


def test_rejection_at_resistance_is_put():
    today = _today([(99.0, 99.4, 98.8, 99.2, 1000), (99.2, 100.05, 99.0, 99.1, 1000)])  # tag $100, close back under, long wick
    s = kl.detect_setup(_hist(), today, None, P)
    assert s is not None and s.side == "put" and "rejection" in s.kind
    assert s.stop >= 100.0


def test_reclaim_is_call_with_level_stop():
    hist = _hist()
    today = _today([(100.2, 100.6, 100.0, 100.4, 1000), (100.4, 100.5, 99.3, 99.5, 1000), (99.5, 100.9, 99.4, 100.8, 1000)])
    s = kl.detect_setup(hist, today, None, kl.LevelParams(lookback_bars=60, atr_bars=10, use_context=False, setups="three"))
    assert s is not None and s.side == "call" and s.kind == "reclaim"
    assert s.stop == pytest.approx(s.level.price)


def test_no_setup_when_price_does_nothing_at_a_level():
    # Mid-range between the $98 support and $100 resistance, small wicks: no reaction at either level.
    today = _today([(99.0, 99.15, 98.95, 99.1, 1000)])
    assert kl.detect_setup(_hist(), today, None, P) is None


def test_context_sizing():
    idx = pd.bdate_range(end="2026-09-30", periods=40)
    up = pd.DataFrame({"Open": range(100, 140), "High": range(101, 141), "Low": range(99, 139), "Close": range(100, 140)},
                      index=idx).astype(float)
    p = kl.LevelParams(use_context=True)
    assert kl._context_size("call", 139.5, up, p)[0] == 1.0  # trend agrees, not extended
    assert kl._context_size("put", 139.0, up, p)[0] == 0.5  # trend disagrees
    assert kl._context_size("call", 145.0, up, p)[0] == 0.5  # extended (>1 daily ATR above prior close)
    assert kl._context_size("put", 133.0, up, p)[0] == 0.0  # trend disagrees and extended -> pass


# --- simulator hooks: entry_window, custom stop, size ---------------------------------

START = dt.date(2025, 1, 6)
PRICE = 400.0


def _daily(n_extra):
    dates = pd.bdate_range(end=START - dt.timedelta(days=1), periods=bt.MIN_DAILY_HISTORY).append(pd.bdate_range(start=START, periods=n_extra))
    df = pd.DataFrame({"Open": PRICE, "High": PRICE + 0.5, "Low": PRICE - 0.5, "Close": PRICE, "Volume": 1_000_000}, index=dates)
    vix = pd.DataFrame({"Open": 15.0, "High": 15.0, "Low": 15.0, "Close": 15.0}, index=dates)
    return df, vix


def _intraday(bars):
    idx = [pd.Timestamp(START) + pd.Timedelta(hours=9.5 + h) for h in range(len(bars))]
    return pd.DataFrame(bars, index=idx, columns=["Open", "High", "Low", "Close"])


def test_entry_window_enters_after_first_signal_bar():
    daily, vix = _daily(1)
    bars = [(PRICE, PRICE + 0.2, PRICE - 0.2, PRICE)] * 3 + [(PRICE, PRICE + 3.0, PRICE - 0.2, PRICE + 2.9)]
    seen = []

    def direction(i, b):
        seen.append(len(b))
        return "call" if len(b) == 3 else None

    trades = bt.simulate(daily, vix, Config(), intraday=_intraday(bars), entry_bar=1, direction=direction, entry_window=(1, 5))
    assert seen == [1, 2, 3]
    assert [t.outcome for t in trades] == ["target"]


def test_custom_stop_and_half_size():
    daily, vix = _daily(1)
    bars = [(PRICE, PRICE + 0.2, PRICE - 0.2, PRICE), (PRICE, PRICE + 0.2, PRICE - 2.5, PRICE - 2.4)]
    trades = bt.simulate(daily, vix, Config(), intraday=_intraday(bars), entry_bar=1,
                         direction=lambda i, b: ("call", PRICE - 1.0, 0.5), stop_on_close=True)
    assert [t.outcome for t in trades] == ["stop"]
    assert trades[0].size == 0.5
    full = bt.simulate(daily, vix, Config(), intraday=_intraday(bars), entry_bar=1,
                       direction=lambda i, b: ("call", PRICE - 1.0), stop_on_close=True)
    assert trades[0].pnl_usd == pytest.approx(full[0].pnl_usd * 0.5, abs=0.02)
