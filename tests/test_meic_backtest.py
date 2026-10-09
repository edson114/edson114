"""Tests for the MEIC (Multiple-Entry Iron Condor) backtest.

All data here is synthetic (deterministic RNG or hand-built) -- no network
access, matching the rest of the test suite. These tests check the
simulation mechanics (credit-targeted strike selection, stop-loss vs.
settlement math, entry scheduling, gate enforcement) rather than any
particular historical win rate, since the underlying data is fabricated.
"""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from qqq_iron_condor import meic_backtest as meic
from qqq_iron_condor.backtest import simulate_chain
from qqq_iron_condor.config import Config
from qqq_iron_condor.options_math import time_to_expiration_years


def _flat_history(n_days: int, spot: float = 7800.0, vix_level: float = 16.0, day_high=None, day_low=None):
    """n_days of a completely flat market (Open=Close=spot every day), with
    the LAST day's High/Low overridden if given -- lets a test control
    exactly one day's intraday range while keeping the warmup period inert."""
    dates = pd.date_range(end=dt.date.today(), periods=n_days, freq="B")
    price = np.full(n_days, spot)
    high = price.copy()
    low = price.copy()
    if day_high is not None:
        high[-1] = day_high
    if day_low is not None:
        low[-1] = day_low
    price_history = pd.DataFrame(
        {"Open": price, "High": high, "Low": low, "Close": price, "Volume": np.full(n_days, 2_000_000)},
        index=dates,
    )
    vix = np.full(n_days, vix_level)
    vix_history = pd.DataFrame({"Open": vix, "High": vix, "Low": vix, "Close": vix}, index=dates)
    return price_history, vix_history


def _synthetic_history(n_days: int, seed: int = 11, start_price: float = 7800.0, vix_level: float = 17.0):
    rng = np.random.default_rng(seed)
    dates = pd.date_range(end=dt.date.today(), periods=n_days, freq="B")
    log_returns = rng.normal(0.0002, 0.011, n_days)
    price = start_price * np.cumprod(1.0 + log_returns)
    high = price * (1 + rng.uniform(0.001, 0.01, n_days))
    low = price * (1 - rng.uniform(0.001, 0.01, n_days))
    open_ = price * (1 + rng.normal(0, 0.002, n_days))
    price_history = pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": price, "Volume": rng.integers(1_000_000, 5_000_000, n_days)},
        index=dates,
    )
    vix = vix_level + np.cumsum(rng.normal(0, 0.3, n_days))
    vix = np.clip(vix, 10.0, 45.0)
    vix_history = pd.DataFrame({"Open": vix, "High": vix, "Low": vix, "Close": vix}, index=dates)
    return price_history, vix_history


def test_entry_times_default_schedule_matches_the_meic_rule():
    # 6 entries, 45 minutes apart, starting 10:30 -- "late morning through
    # afternoon" per the rule.
    times = meic.entry_times(6)
    assert [t.strftime("%H:%M") for t in times] == ["10:30", "11:15", "12:00", "12:45", "13:30", "14:15"]


def test_session_fraction_clamps_outside_regular_hours():
    assert meic._session_fraction(dt.time(9, 30)) == 0.0
    assert meic._session_fraction(dt.time(16, 0)) == 1.0
    assert meic._session_fraction(dt.time(8, 0)) == 0.0  # before open
    assert meic._session_fraction(dt.time(18, 0)) == 1.0  # after close
    mid = meic._session_fraction(dt.time(12, 45))
    assert 0.0 < mid < 1.0


def test_credit_targeted_strike_picks_strike_with_credit_inside_range():
    now = dt.datetime.combine(dt.date.today(), dt.time(10, 30))
    t_years = time_to_expiration_years(0, now=now)
    chain = simulate_chain(7800.0, 0.16, 0, 0.045, "2026-10-09", strike_step=5.0, t_years=t_years)

    picked = meic._credit_targeted_strike(chain.calls, 7800.0, 55.0, 1.00, 1.75, "call")
    assert picked is not None
    short_strike, long_strike, credit = picked
    assert short_strike > 7800.0
    assert long_strike == short_strike + 55.0
    assert 1.00 <= credit <= 1.75


def test_credit_targeted_strike_picks_nearest_edge_when_none_in_range():
    now = dt.datetime.combine(dt.date.today(), dt.time(10, 30))
    t_years = time_to_expiration_years(0, now=now)
    # An absurdly high credit target that no real 0DTE spread will reach --
    # every candidate undershoots, so the closest (highest-credit, nearest
    # the money) strike should still be returned rather than None.
    chain = simulate_chain(7800.0, 0.16, 0, 0.045, "2026-10-09", strike_step=5.0, t_years=t_years)
    picked = meic._credit_targeted_strike(chain.calls, 7800.0, 55.0, 50.0, 60.0, "call")
    assert picked is not None
    short_strike, _, credit = picked
    assert credit < 50.0
    # closest-to-the-money OTM strike available
    assert short_strike == pytest.approx(7805.0)


def test_calm_day_with_no_adverse_move_never_stops_out():
    ph, vh = _flat_history(252 + 1, spot=7800.0, day_high=7810.0, day_low=7790.0)
    cfg = Config()
    days = meic.run_meic_backtest(ph, vh, cfg, apply_gates=False, strike_step=5.0)
    assert len(days) == 1
    day = days[0]
    assert len(day.legs) == meic.DEFAULT_ENTRIES_PER_DAY * 2
    assert all(not leg.stopped for leg in day.legs)
    assert day.net_pnl > 0  # every leg kept its full credit (spot never moved)


def test_large_adverse_move_stops_out_the_threatened_side_only():
    # A violent down move threatens the put side hard, the call side not at
    # all (day_high == day_low == spot, so the call side sees zero adverse
    # room and should never stop).
    ph, vh = _flat_history(252 + 1, spot=7800.0, day_high=7800.0, day_low=7600.0)
    cfg = Config()
    days = meic.run_meic_backtest(ph, vh, cfg, apply_gates=False, strike_step=5.0)
    day = days[0]
    calls = [leg for leg in day.legs if leg.side == "call"]
    puts = [leg for leg in day.legs if leg.side == "put"]
    assert all(not leg.stopped for leg in calls)
    assert any(leg.stopped for leg in puts)
    for leg in puts:
        if leg.stopped:
            # stop target is exactly stop_multiple x credit, so pnl is
            # exactly -(stop_multiple - 1) x credit (a "1x net loss")
            assert leg.exit_value == pytest.approx(leg.credit * meic.DEFAULT_STOP_MULTIPLE, abs=1e-3)
            assert leg.pnl == pytest.approx(leg.credit - leg.credit * meic.DEFAULT_STOP_MULTIPLE, abs=1e-3)


def test_stop_check_is_scaled_down_for_later_entries():
    # A move that's severe enough to stop out an early entry (lots of
    # session time left, assumed to see most of the day's eventual range)
    # should matter less -- or not stop at all -- for a later entry with
    # little time left, since the adverse-room scaling shrinks toward 0.
    ph, vh = _flat_history(252 + 1, spot=7800.0, day_high=7800.0, day_low=7650.0)
    cfg = Config()
    days = meic.run_meic_backtest(ph, vh, cfg, apply_gates=False, strike_step=5.0, entries_per_day=6)
    puts = [leg for leg in days[0].legs if leg.side == "put"]
    first_entry_stopped = next(leg.stopped for leg in puts if leg.entry_index == 0)
    last_entry_stopped = next(leg.stopped for leg in puts if leg.entry_index == 5)
    assert first_entry_stopped or not last_entry_stopped  # never stricter for the later entry


def test_entries_per_day_controls_leg_count():
    ph, vh = _flat_history(252 + 1, spot=7800.0, day_high=7805.0, day_low=7795.0)
    cfg = Config()
    days = meic.run_meic_backtest(ph, vh, cfg, apply_gates=False, strike_step=5.0, entries_per_day=3)
    assert len(days[0].legs) == 3 * 2


def test_run_meic_backtest_skips_entries_on_large_gap_days_when_gates_applied():
    ph, vh = _synthetic_history(252 + 40)
    gap_day_idx = 252 + 10
    ph.iloc[gap_day_idx, ph.columns.get_loc("Close")] *= 1.05
    ph.iloc[gap_day_idx, ph.columns.get_loc("Open")] *= 1.05
    cfg = Config()
    gap_date = ph.index[gap_day_idx].date()

    gated_days = meic.run_meic_backtest(ph, vh, cfg, apply_gates=True, strike_step=5.0)
    ungated_days = meic.run_meic_backtest(ph, vh, cfg, apply_gates=False, strike_step=5.0)

    assert gap_date not in {d.trade_date for d in gated_days}
    assert gap_date in {d.trade_date for d in ungated_days}


def test_summarize_handles_no_days():
    summary = meic.summarize([])
    assert summary.num_days == 0
    assert summary.num_legs == 0
    assert summary.by_regime == []


def test_summarize_aggregates_hand_built_days():
    leg_win = meic.MeicLegResult(
        trade_date=dt.date(2026, 1, 2), entry_index=0, entry_time="10:30", side="call",
        short_strike=7850.0, long_strike=7905.0, width=55.0, credit=1.5, stopped=False, exit_value=0.0, pnl=1.5,
    )
    leg_loss = meic.MeicLegResult(
        trade_date=dt.date(2026, 1, 2), entry_index=0, entry_time="10:30", side="put",
        short_strike=7750.0, long_strike=7695.0, width=55.0, credit=1.5, stopped=True, exit_value=3.0, pnl=-1.5,
    )
    day1 = meic.MeicDaySummary(trade_date=dt.date(2026, 1, 2), legs=[leg_win, leg_loss], net_pnl=0.0, vix_level=16.0, iv_regime="Normal IV")
    day2 = meic.MeicDaySummary(trade_date=dt.date(2026, 1, 3), legs=[leg_win, leg_win], net_pnl=3.0, vix_level=16.0, iv_regime="Normal IV")

    summary = meic.summarize([day1, day2])
    assert summary.num_days == 2
    assert summary.num_legs == 4
    assert summary.total_pnl == pytest.approx(3.0)
    assert summary.leg_win_rate_pct == pytest.approx(75.0)  # 3 of 4 legs profitable
    assert summary.stop_rate_pct == pytest.approx(25.0)  # 1 of 4 legs stopped
    assert summary.win_rate_days_pct == pytest.approx(50.0)  # only day2 net positive


def test_render_meic_backtest_report_handles_no_days():
    summary = meic.summarize([])
    report_md = meic.render_meic_backtest_report(
        "^SPX", 3.0, summary, apply_gates=True, entries_per_day=6, credit_low=1.0, credit_high=1.75,
        spread_width=55.0, stop_multiple=2.0,
    )
    assert "MEIC" in report_md
    assert "No trading days" in report_md
    assert "^SPX" not in report_md  # caret stripped for display


def test_render_meic_backtest_report_includes_key_stats():
    ph, vh = _synthetic_history(252 + 60)
    cfg = Config()
    days = meic.run_meic_backtest(ph, vh, cfg, apply_gates=True, strike_step=5.0)
    summary = meic.summarize(days)
    report_md = meic.render_meic_backtest_report(
        "^SPX", 1.0, summary, apply_gates=True, entries_per_day=6, credit_low=1.0, credit_high=1.75,
        spread_width=55.0, stop_multiple=2.0,
    )
    assert "Stop-out rate" in report_md
    assert "Day win rate" in report_md
    assert "Per-leg win rate" in report_md


def test_simulate_chain_t_years_override_changes_pricing_vs_default():
    # Regression test for the bug this module's correctness depends on:
    # simulate_chain's dte=0 default silently uses the actual current
    # wall-clock time, not any particular historical/intraday moment.
    # Passing t_years explicitly must override that.
    morning = time_to_expiration_years(0, now=dt.datetime.combine(dt.date.today(), dt.time(10, 0)))
    afternoon = time_to_expiration_years(0, now=dt.datetime.combine(dt.date.today(), dt.time(15, 45)))
    assert morning > afternoon  # more time left earlier in the day

    chain_morning = simulate_chain(7800.0, 0.16, 0, 0.045, "2026-10-09", strike_step=5.0, t_years=morning)
    chain_afternoon = simulate_chain(7800.0, 0.16, 0, 0.045, "2026-10-09", strike_step=5.0, t_years=afternoon)

    price_morning = chain_morning.calls.set_index("strike").loc[7850.0]["lastPrice"]
    price_afternoon = chain_afternoon.calls.set_index("strike").loc[7850.0]["lastPrice"]
    assert price_morning > price_afternoon  # more time value earlier in the day
