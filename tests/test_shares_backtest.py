"""Tests for the QQQ shares backtest (signal only, no option modeling).

Hand-built hourly bars so each trade's outcome and P&L are exact.
"""

import datetime as dt
from dataclasses import replace

import pandas as pd
import pytest

from qqq_iron_condor import shares_backtest as sb
from qqq_iron_condor.config import Config

CFG = replace(Config(), macro_event_dates={})
D0 = dt.date(2025, 3, 3)  # a Monday
PREV = 400.0


def _daily(extra_days=6):
    idx = pd.bdate_range(end=D0 - dt.timedelta(days=1), periods=5).append(pd.bdate_range(start=D0, periods=extra_days))
    daily = pd.DataFrame({"Open": PREV, "High": PREV, "Low": PREV, "Close": PREV}, index=idx)
    vix = pd.DataFrame({"Close": 15.0}, index=idx)
    return daily, vix


def _intraday(sessions):
    """sessions: list of lists of (open, high, low, close) hourly bars, one list per business day from D0."""
    rows, idx = [], []
    for day, bars in zip(pd.bdate_range(start=D0, periods=len(sessions)), sessions):
        for h, bar in enumerate(bars):
            idx.append(day + pd.Timedelta(hours=9.5 + h))
            rows.append(bar)
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close"])


FIRST_UP = (PREV, PREV + 1.0, PREV - 0.5, PREV + 0.8)  # first hour up, low $399.50
FLAT = (PREV + 0.8, PREV + 0.9, PREV + 0.7, PREV + 0.8)


def run(sessions, **kw):
    daily, vix = _daily()
    return sb.simulate_shares(daily, vix, _intraday(sessions), CFG, shares=100, cost=0.0, **kw)


def test_long_hits_target():
    t = run([[FIRST_UP, (PREV + 0.8, PREV + 2.2, PREV + 0.6, PREV + 2.0), FLAT]])
    assert [x.outcome for x in t] == ["target"]
    assert t[0].side == 1 and t[0].pnl_usd == pytest.approx(125.0)  # +$1.25 x 100 shares


def test_close_stop_ignores_wick_but_exits_on_close():
    wick = run([[FIRST_UP, (PREV + 0.8, PREV + 0.9, PREV - 1.0, PREV + 0.6), (PREV + 0.6, PREV + 2.2, PREV + 0.5, PREV + 2.0)]])
    assert [x.outcome for x in wick] == ["target"]
    closed = run([[FIRST_UP, (PREV + 0.8, PREV + 0.9, PREV - 1.0, PREV - 0.8)]])
    assert [x.outcome for x in closed] == ["level stop"]
    assert closed[0].exit == pytest.approx(PREV - 0.8) and closed[0].pnl_usd == pytest.approx(-160.0)


def test_touch_stop_exits_at_level():
    t = run([[FIRST_UP, (PREV + 0.8, PREV + 0.9, PREV - 1.0, PREV + 0.6)]], stop_mode="touch")
    assert [x.outcome for x in t] == ["level stop"]
    assert t[0].exit == pytest.approx(PREV - 0.5)


def test_hard_stop_and_costs():
    daily, vix = _daily()
    bars = _intraday([[FIRST_UP, (PREV + 0.8, PREV + 0.9, PREV - 5.0, PREV - 4.5)]])
    t = sb.simulate_shares(daily, vix, bars, CFG, shares=100, cost=0.03, hard_stop=2.0, stop_mode="none")
    assert [x.outcome for x in t] == ["hard stop"]
    assert t[0].pnl_usd == pytest.approx((-2.0 - 0.06) * 100)


def test_short_on_down_first_hour():
    first_down = (PREV, PREV + 0.5, PREV - 1.0, PREV - 0.8)
    t = run([[first_down, (PREV - 0.8, PREV - 0.6, PREV - 2.2, PREV - 2.0)]])
    assert t[0].side == -1 and t[0].outcome == "target"


def test_skips_big_gap_and_flat_first_hour():
    gap_open = (PREV * 1.01, PREV * 1.01 + 1, PREV * 1.01 - 0.5, PREV * 1.01 + 0.8)
    up = (gap_open[3], gap_open[3] + 2.0, gap_open[3] - 0.1, gap_open[3] + 1.9)
    assert run([[gap_open, up]]) == []
    assert [x.outcome for x in run([[gap_open, up]], skip_rules=False)] == ["target"]
    flat_first = (PREV, PREV + 0.5, PREV - 0.5, PREV)
    assert run([[flat_first, FLAT, FLAT]]) == []


def test_time_stop_after_max_hold():
    quiet = (PREV + 0.8, PREV + 0.9, PREV + 0.7, PREV + 0.85)
    t = run([[FIRST_UP, quiet]] + [[quiet, quiet] for _ in range(4)], max_hold=5)
    assert [x.outcome for x in t] == ["time"]
    assert t[0].sessions == 5


def test_summary_and_report():
    t = run([[FIRST_UP, (PREV + 0.8, PREV + 2.2, PREV + 0.6, PREV + 2.0)]])
    s = sb.summarize(t, D0 + dt.timedelta(days=30))
    assert s["trades"] == 1 and s["win_rate"] == 1.0 and s["first_half"] == pytest.approx(125.0)
    report = sb.render_report([("Live rules", s)], D0, D0, D0, shares=100)
    assert "| Live rules | 1 | 100% | $125 |" in report
