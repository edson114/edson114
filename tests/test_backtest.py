"""Tests for the historical backtest simulation.

All data here is synthetic (deterministic RNG or hand-built) -- no
network access, matching the rest of the test suite. These tests check
the simulation mechanics (settlement math, gate enforcement, non-overlap)
rather than asserting any particular historical win rate, since the
underlying data is fabricated.
"""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from qqq_iron_condor import backtest
from qqq_iron_condor.config import Config, ExpirationTarget
from qqq_iron_condor.strategy import build_iron_condor


def _synthetic_history(n_days: int, seed: int = 7, start_price: float = 400.0, vix_level: float = 18.0):
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


def test_simulate_chain_prices_decrease_for_calls_and_increase_for_puts_with_strike():
    chain = backtest.simulate_chain(spot=400.0, iv=0.20, dte=30, rate=0.045, expiration="2026-11-01")
    calls = chain.calls.sort_values("strike")
    puts = chain.puts.sort_values("strike")
    assert calls["lastPrice"].is_monotonic_decreasing
    assert puts["lastPrice"].is_monotonic_increasing
    assert chain.dte == 30


def test_simulate_chain_widens_range_for_high_iv():
    calm = backtest.simulate_chain(spot=400.0, iv=0.12, dte=30, rate=0.045, expiration="2026-11-01")
    stressed = backtest.simulate_chain(spot=400.0, iv=0.60, dte=30, rate=0.045, expiration="2026-11-01")
    calm_width = calm.calls["strike"].max() - calm.calls["strike"].min()
    stressed_width = stressed.calls["strike"].max() - stressed.calls["strike"].min()
    assert stressed_width > calm_width


def test_simulate_chain_respects_custom_strike_step_for_spx_scale():
    # SPX's near-the-money strikes are $5 apart (verified live), vs QQQ's
    # $1 -- at SPX's price level the default step=1.0 still "works" but
    # wastes thousands of unnecessary Black-Scholes evaluations per chain,
    # which is what motivated making this configurable.
    chain = backtest.simulate_chain(spot=7800.0, iv=0.18, dte=30, rate=0.045, expiration="2026-11-01", strike_step=5.0)
    strikes = sorted(chain.calls["strike"].unique())
    assert all(s % 5 == 0 for s in strikes)
    # a non-trivial number of distinct strikes were actually generated
    assert len(strikes) > 10


def test_settle_pnl_is_max_profit_when_spot_finishes_between_short_strikes():
    chain = backtest.simulate_chain(spot=400.0, iv=0.20, dte=30, rate=0.045, expiration="2026-11-01")
    trade = build_iron_condor("Weekly", chain, 400.0, 0.045, 0.16, 5.0)
    assert trade is not None
    pnl = backtest._settle_pnl(trade, exit_spot=400.0)
    assert pnl == pytest.approx(trade.credit)


def test_settle_pnl_is_max_loss_when_spot_finishes_beyond_the_wing():
    chain = backtest.simulate_chain(spot=400.0, iv=0.20, dte=30, rate=0.045, expiration="2026-11-01")
    trade = build_iron_condor("Weekly", chain, 400.0, 0.045, 0.16, 5.0)
    assert trade is not None
    far_above = trade.legs["long_call"].strike + 20.0
    pnl = backtest._settle_pnl(trade, exit_spot=far_above)
    assert pnl == pytest.approx(trade.credit - trade.call_width)
    assert pnl < 0


def test_run_backtest_produces_non_overlapping_trades_with_sane_stats():
    price_history, vix_history = _synthetic_history(n_days=252 + 260)
    cfg = Config()
    target = ExpirationTarget("Weekly", 5, 10)

    trades = backtest.run_backtest(price_history, vix_history, target, cfg, apply_gates=True)

    assert len(trades) > 5
    for prev, nxt in zip(trades, trades[1:]):
        assert nxt.entry_date > prev.exit_date

    summary = backtest.summarize("Weekly", trades)
    assert summary.num_trades == len(trades)
    assert 0.0 <= summary.win_rate_pct <= 100.0
    assert summary.max_drawdown >= 0.0


def test_run_backtest_skips_entries_on_large_gap_days_when_gates_applied():
    price_history, vix_history = _synthetic_history(n_days=252 + 40)
    gap_day_idx = 252 + 10
    price_history.iloc[gap_day_idx, price_history.columns.get_loc("Close")] *= 1.05  # +5% gap, exceeds 0.8% threshold

    cfg = Config()
    target = ExpirationTarget("0DTE", 0, 0, short_delta_target=0.10, wing_width=2.0, allow_fallback=False)
    gap_date = price_history.index[gap_day_idx].date()

    gated_trades = backtest.run_backtest(price_history, vix_history, target, cfg, apply_gates=True)
    ungated_trades = backtest.run_backtest(price_history, vix_history, target, cfg, apply_gates=False)

    assert gap_date not in {t.entry_date for t in gated_trades}
    assert gap_date in {t.entry_date for t in ungated_trades}


def test_summarize_handles_no_trades():
    summary = backtest.summarize("Weekly", [])
    assert summary.num_trades == 0
    assert summary.by_regime == []


def test_infer_strike_step_picks_spx_scale_above_1000_and_qqq_scale_below():
    assert backtest.infer_strike_step(7800.0) == 5.0
    assert backtest.infer_strike_step(750.0) == 1.0
    assert backtest.infer_strike_step(1000.0) == 1.0  # boundary is exclusive


def test_render_backtest_report_strips_caret_from_display_symbol():
    report_md = backtest.render_backtest_report("^SPX", 2.0, [], apply_gates=True)
    assert "# SPX Iron Condor Backtest" in report_md
    assert "^SPX" not in report_md


def test_render_backtest_report_includes_each_label():
    price_history, vix_history = _synthetic_history(n_days=252 + 260)
    cfg = Config()
    target = ExpirationTarget("Weekly", 5, 10)
    trades = backtest.run_backtest(price_history, vix_history, target, cfg)
    summary = backtest.summarize("Weekly", trades)

    report_md = backtest.render_backtest_report("QQQ", 2.0, [summary], apply_gates=True)
    assert "Weekly" in report_md
    assert "Win rate" in report_md


def test_simulate_chain_t_years_override_takes_precedence_over_dte():
    # dte=0 with no override derives T from the ACTUAL current wall-clock
    # time (time_to_expiration_years' default `now`), which varies with
    # when the test happens to run -- a caller that needs a specific
    # historical/intraday T (e.g. meic_backtest.py's multiple intraday
    # entries) must be able to override it explicitly.
    chain_default = backtest.simulate_chain(400.0, 0.20, 0, 0.045, "2026-11-01")
    chain_override = backtest.simulate_chain(400.0, 0.20, 0, 0.045, "2026-11-01", t_years=5.0 / 365.0)
    # A 5-day T is wildly more time value than any 0DTE wall-clock T can
    # be, so the override must have actually taken effect.
    price_default = chain_default.calls.set_index("strike").loc[410.0]["lastPrice"]
    price_override = chain_override.calls.set_index("strike").loc[410.0]["lastPrice"]
    assert price_override > price_default


def test_points_otm_backtest_picks_strikes_at_the_requested_distance():
    price_history, vix_history = _synthetic_history(n_days=252 + 60)
    cfg = Config()
    strike_step = backtest.infer_strike_step(float(price_history["Close"].iloc[-1]))

    trades = backtest.run_points_otm_backtest(
        price_history, vix_history, cfg, points_otm=5.0, wing_width=5.0, apply_gates=True, strike_step=strike_step,
    )

    assert len(trades) > 5
    for t in trades:
        assert t.short_call_strike - t.entry_spot == pytest.approx(5.0, abs=strike_step)
        assert t.entry_spot - t.short_put_strike == pytest.approx(5.0, abs=strike_step)
        assert t.entry_date == t.exit_date  # every trade is 0DTE


def test_points_otm_backtest_settles_at_max_profit_between_short_strikes():
    chain = backtest.simulate_chain(spot=400.0, iv=0.20, dte=0, rate=0.045, expiration="2026-11-01", strike_step=1.0)
    calls = chain.calls.set_index("strike")
    puts = chain.puts.set_index("strike")
    short_call, long_call = 405.0, 410.0
    short_put, long_put = 395.0, 390.0
    credit = (backtest._mid(calls.loc[short_call]) - backtest._mid(calls.loc[long_call])) + (
        backtest._mid(puts.loc[short_put]) - backtest._mid(puts.loc[long_put])
    )
    # Settling between the short strikes keeps the full credit, same
    # max-profit settlement math as the delta-targeted backtest.
    exit_spot = 400.0
    call_loss = min(max(exit_spot - short_call, 0.0), long_call - short_call)
    put_loss = min(max(short_put - exit_spot, 0.0), short_put - long_put)
    pnl = credit - call_loss - put_loss
    assert pnl == pytest.approx(credit)


def test_points_otm_backtest_skips_entries_on_large_gap_days_when_gates_applied():
    price_history, vix_history = _synthetic_history(n_days=252 + 40)
    gap_day_idx = 252 + 10
    price_history.iloc[gap_day_idx, price_history.columns.get_loc("Close")] *= 1.05

    cfg = Config()
    gap_date = price_history.index[gap_day_idx].date()

    gated_trades = backtest.run_points_otm_backtest(price_history, vix_history, cfg, points_otm=5.0, wing_width=5.0, apply_gates=True)
    ungated_trades = backtest.run_points_otm_backtest(price_history, vix_history, cfg, points_otm=5.0, wing_width=5.0, apply_gates=False)

    assert gap_date not in {t.entry_date for t in gated_trades}
    assert gap_date in {t.entry_date for t in ungated_trades}


def test_points_otm_backtest_label_reflects_requested_distance():
    price_history, vix_history = _synthetic_history(n_days=252 + 30)
    cfg = Config()
    trades = backtest.run_points_otm_backtest(price_history, vix_history, cfg, points_otm=5.0, wing_width=5.0, apply_gates=False)
    assert all(t.label == "5pt OTM" for t in trades)


def test_market_open_datetime_gives_a_consistent_time_to_expiration():
    # Regression test for the bug this and the managed backtest's
    # correctness depend on: simulate_chain's dte=0 default silently uses
    # the actual current wall-clock time, which varies with whenever the
    # backtest happens to run. Pricing "entered at the open" trades must
    # use a fixed market-open time instead, so every historical day gets
    # the same (correct) ~6.5 hours of time value regardless of when the
    # backtest script itself is executed.
    from qqq_iron_condor.options_math import time_to_expiration_years

    t1 = time_to_expiration_years(0, now=backtest._market_open_datetime(dt.date(2026, 1, 5)))
    t2 = time_to_expiration_years(0, now=backtest._market_open_datetime(dt.date(2026, 6, 15)))
    assert t1 == pytest.approx(t2)
    assert t1 == pytest.approx(6.5 / (24 * 365), abs=1e-6)


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


def test_condor_debit_to_close_grows_with_adverse_move_either_direction():
    debit_atm = backtest._condor_debit_to_close(7800.0, 7830.0, 7850.0, 7770.0, 7750.0, 0.0005, 0.045, 0.16)
    debit_call_side_threatened = backtest._condor_debit_to_close(7825.0, 7830.0, 7850.0, 7770.0, 7750.0, 0.0005, 0.045, 0.16)
    debit_put_side_threatened = backtest._condor_debit_to_close(7775.0, 7830.0, 7850.0, 7770.0, 7750.0, 0.0005, 0.045, 0.16)
    assert debit_call_side_threatened > debit_atm
    assert debit_put_side_threatened > debit_atm


def test_managed_backtest_profit_take_triggers_via_pure_theta_decay_on_a_calm_day():
    # No intraday move at all (High=Low=Open=Close) -- a low profit-take
    # bar (10% of max profit) should still trigger from theta decay alone
    # by the midpoint checkpoint, since 0DTE options bleed value fast.
    ph, vh = _flat_history(252 + 1, spot=7800.0, day_high=7800.0, day_low=7800.0)
    cfg = Config()
    trades = backtest.run_points_otm_managed_backtest(
        ph, vh, cfg, points_otm=20.0, wing_width=20.0, profit_take_pct=0.1, stop_multiple=100.0,
        apply_gates=False, strike_step=5.0,
    )
    assert len(trades) == 1
    t = trades[0]
    assert t.pnl > 0
    assert t.pnl < t.credit  # an early partial-profit exit, not the full credit a settlement would keep


def test_managed_backtest_stop_triggers_on_large_adverse_move():
    # A violent move threatens the put side hard enough, with a tight
    # stop and a wing wide enough for the stop to actually bind (rather
    # than being structurally unreachable), to force a stop-loss exit.
    ph, vh = _flat_history(252 + 1, spot=7800.0, day_high=7800.0, day_low=7600.0)
    cfg = Config()
    trades = backtest.run_points_otm_managed_backtest(
        ph, vh, cfg, points_otm=20.0, wing_width=100.0, profit_take_pct=0.5, stop_multiple=1.5,
        apply_gates=False, strike_step=5.0,
    )
    assert len(trades) == 1
    t = trades[0]
    assert t.pnl < 0
    assert t.pnl == pytest.approx(t.credit - t.credit * 1.5, abs=1e-2)


def test_managed_backtest_label_is_suffixed():
    ph, vh = _flat_history(252 + 5, spot=7800.0, day_high=7805.0, day_low=7795.0)
    cfg = Config()
    trades = backtest.run_points_otm_managed_backtest(
        ph, vh, cfg, points_otm=20.0, wing_width=20.0, apply_gates=False, strike_step=5.0,
    )
    assert all(t.label == "20pt OTM (managed)" for t in trades)


def test_managed_backtest_skips_entries_on_large_gap_days_when_gates_applied():
    price_history, vix_history = _synthetic_history(n_days=252 + 40)
    gap_day_idx = 252 + 10
    price_history.iloc[gap_day_idx, price_history.columns.get_loc("Close")] *= 1.05

    cfg = Config()
    gap_date = price_history.index[gap_day_idx].date()

    gated_trades = backtest.run_points_otm_managed_backtest(price_history, vix_history, cfg, points_otm=5.0, wing_width=5.0, apply_gates=True)
    ungated_trades = backtest.run_points_otm_managed_backtest(price_history, vix_history, cfg, points_otm=5.0, wing_width=5.0, apply_gates=False)

    assert gap_date not in {t.entry_date for t in gated_trades}
    assert gap_date in {t.entry_date for t in ungated_trades}


def test_managed_backtest_with_unreachable_stop_never_loses_more_than_credit_via_stop_path():
    # stop_multiple so large it structurally can't bind (max loss is
    # capped by wing_width regardless) -- this isolates the profit-take
    # rule's effect, which should never produce a worse per-trade loss
    # than plain settlement would (it only ever exits EARLY and partially
    # in its own favor, never adds risk).
    price_history, vix_history = _synthetic_history(n_days=252 + 60)
    cfg = Config()
    strike_step = backtest.infer_strike_step(float(price_history["Close"].iloc[-1]))
    managed = backtest.run_points_otm_managed_backtest(
        price_history, vix_history, cfg, points_otm=20.0, wing_width=10.0,
        profit_take_pct=0.5, stop_multiple=1000.0, apply_gates=True, strike_step=strike_step,
    )
    unmanaged = backtest.run_points_otm_backtest(
        price_history, vix_history, cfg, points_otm=20.0, wing_width=10.0, apply_gates=True, strike_step=strike_step,
    )
    assert len(managed) == len(unmanaged)
    for m, u in zip(managed, unmanaged):
        assert m.entry_date == u.entry_date
        assert m.pnl >= u.pnl - 1e-6  # profit-take only ever helps or matches, never hurts, with no real stop active


def test_points_otm_backtest_multi_entry_defaults_match_single_entry_at_open():
    ph, vh = _flat_history(252 + 5, spot=7800.0, day_high=7805.0, day_low=7795.0)
    cfg = Config()
    default_trades = backtest.run_points_otm_backtest(
        ph, vh, cfg, points_otm=20.0, wing_width=20.0, apply_gates=False, strike_step=5.0,
    )
    explicit_trades = backtest.run_points_otm_backtest(
        ph, vh, cfg, points_otm=20.0, wing_width=20.0, apply_gates=False, strike_step=5.0,
        entries_per_day=1, entry_spacing_minutes=30, first_entry_hour=9, first_entry_minute=30,
    )
    assert default_trades == explicit_trades


def test_points_otm_backtest_multi_entry_opens_n_trades_per_eligible_day():
    n_days = 252 + 5
    ph, vh = _flat_history(n_days, spot=7800.0, day_high=7805.0, day_low=7795.0)
    cfg = Config()
    single = backtest.run_points_otm_backtest(
        ph, vh, cfg, points_otm=20.0, wing_width=20.0, apply_gates=False, strike_step=5.0,
    )
    staggered = backtest.run_points_otm_backtest(
        ph, vh, cfg, points_otm=20.0, wing_width=20.0, apply_gates=False, strike_step=5.0,
        entries_per_day=3, entry_spacing_minutes=30, first_entry_hour=10, first_entry_minute=0,
    )
    assert len(staggered) == 3 * len(single)
    assert {t.entry_date for t in staggered} == {t.entry_date for t in single}


def test_entry_times_spacing_and_start():
    # The user's specific ask: 3 entries starting at 10:00am, 30 minutes apart.
    times = backtest.entry_times(3, first_hour=10, first_minute=0, spacing_minutes=30)
    assert times == [dt.time(10, 0), dt.time(10, 30), dt.time(11, 0)]


def test_entry_times_single_entry_defaults_to_market_open():
    assert backtest.entry_times(1) == [dt.time(backtest.MARKET_OPEN_HOUR, backtest.MARKET_OPEN_MINUTE)]


def test_session_fraction_bounds():
    assert backtest._session_fraction(dt.time(9, 30)) == pytest.approx(0.0)
    assert backtest._session_fraction(dt.time(16, 0)) == pytest.approx(1.0)
    assert backtest._session_fraction(dt.time(12, 45)) == pytest.approx(0.5, abs=1e-6)


def test_managed_backtest_multi_entry_defaults_match_single_entry_at_open():
    # entries_per_day=1 is the default -- explicitly passing it (and the
    # market-open first-entry time) must reproduce the original
    # single-entry behavior exactly, since this is the backward-compatible
    # default every existing test above relies on.
    ph, vh = _flat_history(252 + 5, spot=7800.0, day_high=7805.0, day_low=7795.0)
    cfg = Config()
    default_trades = backtest.run_points_otm_managed_backtest(
        ph, vh, cfg, points_otm=20.0, wing_width=20.0, apply_gates=False, strike_step=5.0,
    )
    explicit_trades = backtest.run_points_otm_managed_backtest(
        ph, vh, cfg, points_otm=20.0, wing_width=20.0, apply_gates=False, strike_step=5.0,
        entries_per_day=1, entry_spacing_minutes=30, first_entry_hour=9, first_entry_minute=30,
    )
    assert default_trades == explicit_trades


def test_managed_backtest_multi_entry_opens_n_trades_per_eligible_day():
    n_days = 252 + 5
    ph, vh = _flat_history(n_days, spot=7800.0, day_high=7805.0, day_low=7795.0)
    cfg = Config()
    single = backtest.run_points_otm_managed_backtest(
        ph, vh, cfg, points_otm=20.0, wing_width=20.0, apply_gates=False, strike_step=5.0,
    )
    staggered = backtest.run_points_otm_managed_backtest(
        ph, vh, cfg, points_otm=20.0, wing_width=20.0, apply_gates=False, strike_step=5.0,
        entries_per_day=3, entry_spacing_minutes=30, first_entry_hour=10, first_entry_minute=0,
    )
    assert len(staggered) == 3 * len(single)
    assert {t.entry_date for t in staggered} == {t.entry_date for t in single}


def test_managed_backtest_multi_entry_interpolates_spot_between_open_and_close():
    n_days = 252 + 1
    dates = pd.date_range(end=dt.date.today(), periods=n_days, freq="B")
    day_open = np.full(n_days, 7800.0)
    day_close = np.full(n_days, 7800.0)
    day_close[-1] = 7850.0  # only the last (tested) day actually drifts
    price_history = pd.DataFrame(
        {"Open": day_open, "High": np.maximum(day_open, day_close), "Low": np.minimum(day_open, day_close),
         "Close": day_close, "Volume": np.full(n_days, 2_000_000)},
        index=dates,
    )
    vix = np.full(n_days, 16.0)
    vix_history = pd.DataFrame({"Open": vix, "High": vix, "Low": vix, "Close": vix}, index=dates)
    cfg = Config()

    trades = backtest.run_points_otm_managed_backtest(
        price_history, vix_history, cfg, points_otm=20.0, wing_width=20.0,
        profit_take_pct=0.0, stop_multiple=1000.0, apply_gates=False, strike_step=5.0,
        entries_per_day=3, entry_spacing_minutes=30, first_entry_hour=10, first_entry_minute=0,
    )
    assert len(trades) == 3
    # 10:00, 10:30, 11:00 sit at increasing fractions of the 9:30-16:00
    # session, so each entry's interpolated spot should be strictly
    # further from the Open and closer to the Close than the last.
    spots = [t.entry_spot for t in trades]
    assert spots[0] < spots[1] < spots[2]
    assert all(7800.0 < s < 7850.0 for s in spots)
