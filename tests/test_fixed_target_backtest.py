"""Tests for the fixed $ target backtest (RSI calls, and the daily plan at your size).

Hand-built daily and hourly bars, so each exit is known.
"""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from qqq_iron_condor import fixed_target_backtest as ft
from qqq_iron_condor.config import Config

CFG = Config()
FLAT = (500.0, 500.3, 499.7, 500.0)


def _data(sessions, trend=0.0):
    """Daily history (alternating +-1, RSI ~50; `trend` adds a steady rise
    to the last 20 days) followed by the test sessions, hourly bars from 9:30."""
    hist = pd.bdate_range("2025-01-01", periods=260)
    test_days = pd.bdate_range(hist[-1] + pd.Timedelta(days=1), periods=len(sessions))
    closes = [500.0 + (1 if i % 2 else -1) for i in range(len(hist))]
    if trend:
        closes[-20:] = [500.0 + trend * k for k in range(20)]
    closes += [float(s[-1][3]) for s in sessions]
    idx = hist.append(test_days)
    daily = pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes}, index=idx)
    vix = pd.DataFrame({"Close": 15.0}, index=idx)
    rows, times = [], []
    for day, bars in zip(test_days, sessions):
        for h, bar in enumerate(bars):
            times.append(day + pd.Timedelta(hours=9.5 + h))
            rows.append(bar)
    hourly = pd.DataFrame(rows, index=pd.DatetimeIndex(times), columns=["Open", "High", "Low", "Close"])
    return daily, vix, hourly


def run(sessions, trend=0.0, **kw):
    daily, vix, hourly = _data(sessions, trend)
    return ft.simulate_rsi_calls(daily, vix, hourly, CFG, **kw)


def test_target_exits_at_fill_plus_a_dollar():
    trades, open_call, _ = run([[FLAT, (500.0, 504.0, 499.9, 503.5)]])
    assert [t.outcome for t in trades] == ["target"] and open_call is None
    assert trades[0].pnl_usd == pytest.approx(500.0)  # 5 contracts x 100 x $1.00
    assert trades[0].exit_fill == pytest.approx(trades[0].entry_fill + 1.0, abs=0.01)


def test_stop_on_touch():
    trades, _, _ = run([[FLAT, (500.0, 500.1, 496.0, 496.5)]], stop_usd=1000.0)
    assert [t.outcome for t in trades] == ["stop"]
    assert trades[0].pnl_usd == pytest.approx(-1000.0)


def test_no_stop_holds_and_records_the_drawdown():
    trades, open_call, _ = run([[FLAT, (500.0, 500.1, 490.0, 491.0)]] + [[FLAT, FLAT]] * 2)
    assert trades == [] and open_call is not None
    assert open_call.worst_usd < -3000 and open_call.mark_usd < 0


def test_sold_at_the_close_with_five_days_left():
    # 8 days to expiration: entered on a Wednesday, sold at Friday's close (5 days left).
    trades, _, _ = run([[FLAT, FLAT]] * 4, dte=8, exit_dte=5)
    assert [t.outcome for t in trades] == ["expiry"]
    assert (dt.date.fromisoformat(str(trades[0].entry_date)) + dt.timedelta(days=8) - trades[0].exit_date).days <= 5


def test_next_position_opens_the_session_after_an_exit():
    win = [FLAT, (500.0, 504.0, 499.9, 503.5)]
    trades, _, _ = run([win, win])
    assert len(trades) == 2
    assert trades[1].entry_date > trades[0].exit_date


def test_rsi_filter_skips_overbought_days():
    trades, open_call, skipped = run([[FLAT, (500.0, 504.0, 499.9, 503.5)]], trend=2.0)
    assert trades == [] and open_call is None and skipped == 1


def test_plan_size_config_and_direction():
    c = ft.plan_size_config(CFG, 5, 500.0, 2000.0, delta=0.85)
    assert (c.symbol, c.daily_target_contracts, c.daily_target_profit_usd, c.daily_target_stop_usd, c.daily_target_delta) == (
        "QQQ", 5, 500.0, 2000.0, 0.85)
    daily, _, _ = _data([[FLAT]])
    up = pd.DataFrame({"Open": [500.0], "Close": [501.0]})
    down = pd.DataFrame({"Open": [500.0], "Close": [499.0]})
    i = len(daily) - 1
    assert ft.first_hour_direction(daily)(i, up) == "call"
    assert ft.first_hour_direction(daily)(i, down) == "put"
    assert ft.first_hour_direction(daily, calls_only=True)(i, down) is None
    assert ft.first_hour_direction(daily, rsi_max=1.0)(i, up) is None  # RSI ~50 is not below 1


def test_summary_and_report():
    trades, open_call, _ = run([[FLAT, (500.0, 504.0, 499.9, 503.5)]])
    d = trades[0].entry_date
    s = ft.summarize(trades, d, d, open_usd=-250.0)
    assert s["trades"] == 1 and s["win_rate"] == 1.0 and s["net"] == pytest.approx(250.0)
    windows = (("Last year", d), ("Two years", d))
    report = ft.render_report(windows, d, [(0.0, [(s, None), (s, None)])], [("Daily plan rules", [s, s])])
    assert "| none | 1 | 100% | $500 | -$250 | **$250** |" in report
    assert "| Daily plan rules | 1 | 100% | **$500** |" in report
