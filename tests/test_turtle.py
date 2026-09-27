"""Tests for the Turtle Trading backtest.

Bars are hand-built (not random) so every fill, stop, and pyramid add
can be asserted exactly. The flat warm-up has a constant 2-point range,
so N settles at exactly 2.0 and a 1%-risk unit on $100k is 500 shares.
"""

import numpy as np
import pandas as pd
import pytest

from qqq_iron_condor import turtle
from qqq_iron_condor.turtle import TurtleConfig, prepare_indicators, run_backtest

FLAT = (100.0, 101.0, 99.0, 100.0)  # open, high, low, close


def _frame(bars):
    dates = pd.bdate_range("2024-01-01", periods=len(bars))
    return pd.DataFrame(bars, columns=["Open", "High", "Low", "Close"], index=dates)


def _cfg(**kw):
    return TurtleConfig(**{"cost_bps": 0.0, **kw})


def test_indicators_only_use_prior_bars():
    rng = np.random.default_rng(1)
    close = 100 + np.cumsum(rng.normal(0, 1, 120))
    df = _frame([(c, c + 1, c - 1, c) for c in close])
    ind = prepare_indicators(df, _cfg())
    t = 80
    assert ind["entry_hi"].iloc[t] == df["High"].iloc[t - 20:t].max()
    assert ind["exit_lo"].iloc[t] == df["Low"].iloc[t - 10:t].min()
    assert ind["fs_hi"].iloc[t] == df["High"].iloc[t - 55:t].max()


def test_breakout_entry_pyramid_and_channel_exit():
    bars = [FLAT] * 60 + [
        (100.0, 103.0, 100.0, 102.5),  # breaks 101: enter at 101, add at 102
        (99.0, 99.5, 97.0, 97.0),  # gaps below the 10-day low (99) -> exit at the open
    ]
    result = run_backtest({"X": _frame(bars)}, _cfg())
    assert len(result.trades) == 1
    t = result.trades[0]
    assert (t.direction, t.units, t.shares) == ("LONG", 2, 1000)
    assert t.avg_entry == pytest.approx(101.5)
    assert (t.exit_price, t.exit_reason) == (99.0, "exit")
    assert t.pnl == pytest.approx(500 * (99 - 101) + 500 * (99 - 102))


def test_gap_through_breakout_fills_at_open_and_sets_2n_stop():
    bars = [FLAT] * 60 + [(104.0, 104.8, 103.5, 104.5)]
    result = run_backtest({"X": _frame(bars)}, _cfg())
    pos = result.open_positions["X"]
    assert pos.fills == [(500, 104.0)]
    assert pos.stop == pytest.approx(100.0)
    assert pos.next_add == pytest.approx(105.0)


def test_stop_is_hit_before_channel_exit_when_nearer():
    bars = [FLAT] * 60 + [
        (100.0, 101.5, 100.5, 101.5),  # enter at 101, stop 97
        (101.0, 101.2, 96.0, 96.5),  # 10-day low is 99 -> hit first (above the stop)
    ]
    t = run_backtest({"X": _frame(bars)}, _cfg()).trades[0]
    assert (t.exit_reason, t.exit_price) == ("exit", 99.0)

    # Tighten the stop so it sits above the channel exit.
    t = run_backtest({"X": _frame(bars)}, _cfg(stop_n=0.5)).trades[0]
    assert (t.exit_reason, t.exit_price) == ("stop", 100.0)


def test_short_breakout():
    bars = [FLAT] * 60 + [(100.0, 100.0, 98.2, 98.5)]
    pos = run_backtest({"X": _frame(bars)}, _cfg()).open_positions["X"]
    assert pos.direction == -1
    assert pos.fills[0] == (500, 99.0)
    assert pos.stop == pytest.approx(103.0)
    assert run_backtest({"X": _frame(bars)}, _cfg(long_only=True)).open_positions == {}


def test_leverage_cap_limits_unit_size():
    bars = [FLAT] * 60 + [(100.0, 101.5, 100.5, 101.5)]
    pos = run_backtest({"X": _frame(bars)}, _cfg(max_leverage=0.5)).open_positions["X"]
    assert pos.fills == [(int(50_000 // 101), 101.0)]


def _winner_then_new_breakout():
    rally = [(100 + k - 0.5, 101 + k, 100 + k - 1, 100 + k + 0.5) for k in range(1, 26)]
    drop = [(124.0, 124.0, 110.0, 112.0)]  # 10-day low is 115 -> profitable exit
    base = [(112.0, 113.0, 111.0, 112.0)] * 25
    breakout = [(112.5, 114.5, 112.0, 114.0)]  # new 20-day high, well below the 55-day high
    return [FLAT] * 60 + rally + drop + base + breakout


def test_s1_skips_breakout_after_a_winning_breakout():
    df = _frame(_winner_then_new_breakout())
    filtered = run_backtest({"X": df}, _cfg(system=1))
    assert len(filtered.trades) == 1 and filtered.trades[0].pnl > 0
    assert filtered.last_breakout_winner["X"] is True
    assert "X" not in filtered.open_positions

    unfiltered = run_backtest({"X": df}, _cfg(system=1, use_s1_filter=False))
    assert "X" in unfiltered.open_positions


def test_s1_failsafe_enters_skipped_breakout_at_55_day_level():
    bars = _winner_then_new_breakout()[:-1] + [(125.5, 127.0, 125.0, 126.5)]  # clears the 55-day high too
    result = run_backtest({"X": _frame(bars)}, _cfg(system=1))
    pos = result.open_positions["X"]
    assert pos.failsafe
    assert pos.fills[0][1] == pytest.approx(_frame(bars)["High"].iloc[-56:-1].max())


def test_portfolio_direction_limit():
    bars = [FLAT] * 60 + [(100.0, 103.0, 100.0, 102.5)]  # 2 units per market
    prices = {s: _frame(bars) for s in ("A", "B", "C")}
    result = run_backtest(prices, _cfg(max_units_per_direction=3, max_leverage=10))
    assert sum(p.units for p in result.open_positions.values()) == 3


def test_self_test_report_renders():
    prices = turtle.synthetic_prices()
    results = [run_backtest(prices, TurtleConfig(system=s)) for s in (1, 2)]
    report = turtle.render_report(prices, results, 100_000)
    assert "Turtle Trading Report" in report
    assert "System 1 (20-day entry / 10-day exit)" in report
    assert "System 2 backtest" in report
    assert "Profit factor" in report
