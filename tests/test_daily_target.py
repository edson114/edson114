"""Tests for the daily $ target plan and its backtest.

Backtest fixtures are hand-built (flat daily history, scripted intraday
bars) so each trade's outcome is known exactly.
"""

import dataclasses
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from qqq_iron_condor import daily_target as dtg
from qqq_iron_condor import daily_target_backtest as bt
from qqq_iron_condor.config import Config
from qqq_iron_condor.options_math import bs_delta, bs_price

CFG = Config()


# --- daily_target.py ------------------------------------------------------

def test_per_share_target_is_one_dollar_for_ten_contracts():
    assert dtg.per_share(1000.0, 10) == pytest.approx(1.00)


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_underlying_for_option_price_round_trips(option_type):
    t, iv = 60 / 365, 0.2
    price = bs_price(751.0, 710.0, t, 0.045, iv, option_type)
    assert dtg.underlying_for_option_price(price, 710.0, t, 0.045, iv, option_type) == pytest.approx(751.0, abs=1e-4)


def test_underlying_for_option_price_unreachable_put_returns_none():
    assert dtg.underlying_for_option_price(10_000.0, 700.0, 60 / 365, 0.045, 0.2, "put") is None


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_strike_for_delta_lands_near_target(option_type):
    t = 60 / 365
    strike = dtg.strike_for_delta(750.0, t, 0.045, 0.2, option_type, 0.80)
    assert strike == round(strike)
    assert abs(bs_delta(750.0, strike, t, 0.045, 0.2, option_type)) == pytest.approx(0.80, abs=0.01)


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_leg_plan_levels(option_type):
    chain = dtg._synthetic_chain(750.0, 60, CFG)
    leg = dtg.build_leg_plan(chain, 750.0, 9.5, option_type, CFG)
    assert leg is not None
    assert abs(leg.contract.delta) == pytest.approx(0.80, abs=0.02)
    assert leg.target_option_price == pytest.approx(leg.entry_price + 1.00)
    assert leg.stop_option_price == pytest.approx(leg.entry_price - 1.00)
    assert leg.cost_usd == pytest.approx(leg.entry_price * 1000)
    # ~$1 / 0.80 delta = ~$1.25 QQQ move, in the right direction.
    assert 1.1 < leg.move_to_target < 1.4
    assert 1.1 < leg.move_to_stop < 1.4
    if option_type == "call":
        assert leg.stop_underlying < 750.0 < leg.target_underlying
    else:
        assert leg.target_underlying < 750.0 < leg.stop_underlying
    assert leg.theta_per_day_usd < 0


def test_plan_picks_leg_matching_bias():
    chain = dtg._synthetic_chain(750.0, 60, CFG)
    assert dtg.build_plan(750.0, 9.5, chain, "PUT", "test", CFG).chosen.contract.option_type == "put"
    assert dtg.build_plan(750.0, 9.5, chain, "NO TRADE", "test", CFG).chosen is None


def test_plan_without_chain_notes_it():
    plan = dtg.build_plan(750.0, 9.5, None, "CALL", "test", CFG)
    assert plan.chosen is None
    assert any("No QQQ expiration" in n for n in plan.notes)


@pytest.mark.parametrize(
    "current, days, action",
    [
        (51.00, 0, "CLOSE -- TARGET HIT"),
        (51.50, 0, "CLOSE -- TARGET HIT"),
        (49.00, 0, "CLOSE -- STOP HIT"),
        (50.40, 2, "HOLD"),
        (50.40, 5, "CLOSE -- TIME STOP"),
    ],
)
def test_evaluate_position(current, days, action):
    status = dtg.evaluate_position(50.00, current, days, CFG)
    assert status.action == action
    assert status.pnl_usd == pytest.approx((current - 50.00) * 1000)
    assert status.target_option_price == 51.00
    assert status.stop_option_price == 49.00


def test_sessions_held_counts_business_days():
    assert dtg.sessions_held(dt.date(2026, 10, 2), dt.date(2026, 10, 2)) == 0
    assert dtg.sessions_held(dt.date(2026, 10, 2), dt.date(2026, 10, 5)) == 1  # Fri -> Mon


def test_self_test_report_sections():
    report, plan = dtg._self_test_report()
    for section in ("Goal math", "Order ticket", "Rules", "Limitations", "+$1,000.00"):
        assert section in report
    assert plan.bias == "CALL"


def test_cli_check_requires_position_fields():
    with pytest.raises(SystemExit):
        dtg.main(["--check", "--side", "call"])


# --- daily_target_backtest.py ---------------------------------------------

START = dt.date(2025, 1, 6)  # outside the 2026-only macro calendar
PRICE = 400.0


def _daily(n_extra: int):
    dates = pd.bdate_range(end=START - dt.timedelta(days=1), periods=bt.MIN_DAILY_HISTORY).append(
        pd.bdate_range(start=START, periods=n_extra)
    )
    df = pd.DataFrame({"Open": PRICE, "High": PRICE + 0.5, "Low": PRICE - 0.5, "Close": PRICE, "Volume": 1_000_000}, index=dates)
    vix = pd.DataFrame({"Open": 15.0, "High": 15.0, "Low": 15.0, "Close": 15.0}, index=dates)
    return df, vix


def _intraday(days: list, paths: list) -> pd.DataFrame:
    """One day per `paths` entry: a list of (open, high, low, close) hourly bars."""
    rows, idx = [], []
    for day, bars in zip(days, paths):
        for h, bar in enumerate(bars):
            idx.append(pd.Timestamp(day) + pd.Timedelta(hours=9.5 + h))
            rows.append(bar)
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close"])


def _flat(n=7):
    return [(PRICE, PRICE + 0.2, PRICE - 0.2, PRICE)] * n


def test_backtest_target_hit_first():
    daily, vix = _daily(1)
    intra = _intraday([START], [[(PRICE, PRICE + 3, PRICE - 0.2, PRICE + 2.5)] + _flat()])
    trades = bt.simulate(daily, vix, CFG, "call", intra)
    assert len(trades) == 1
    assert trades[0].outcome == "target"
    assert trades[0].pnl_usd == pytest.approx(1000.0)
    assert trades[0].sessions == 1


def test_backtest_stop_hit_first():
    daily, vix = _daily(1)
    intra = _intraday([START], [[(PRICE, PRICE + 0.2, PRICE - 3, PRICE - 2.5), (PRICE - 2.5, PRICE + 3, PRICE - 2.5, PRICE + 3)]])
    trades = bt.simulate(daily, vix, CFG, "call", intra)
    assert [t.outcome for t in trades] == ["stop"]
    assert trades[0].pnl_usd == pytest.approx(-1000.0)


@pytest.mark.parametrize(
    "bar, tie_rule, expected",
    [
        ((PRICE, PRICE + 3, PRICE - 3, PRICE + 1), "path", "stop"),  # up bar: low first -> call stopped
        ((PRICE, PRICE + 3, PRICE - 3, PRICE - 1), "path", "target"),  # down bar: high first -> call target
        ((PRICE, PRICE + 3, PRICE - 3, PRICE - 1), "stop", "stop"),  # worst case
    ],
)
def test_backtest_tie_rule(bar, tie_rule, expected):
    daily, vix = _daily(1)
    trades = bt.simulate(daily, vix, CFG, "call", _intraday([START], [[bar]]), tie_rule=tie_rule)
    assert [t.outcome for t in trades] == [expected]


def test_backtest_time_stop_and_one_position_at_a_time():
    daily, vix = _daily(7)
    days = [d.date() for d in daily.index[bt.MIN_DAILY_HISTORY:]]
    trades = bt.simulate(daily, vix, CFG, "put", _intraday(days, [_flat()] * len(days)))
    assert trades[0].outcome == "time"
    assert trades[0].sessions == CFG.daily_target_max_hold_days
    assert trades[0].exit_date == days[CFG.daily_target_max_hold_days - 1]
    assert trades[0].pnl_usd < 0  # spread paid, nothing gained
    # The next position opens only after the first one closed.
    assert len(trades) == 1 or trades[1].entry_date > trades[0].exit_date


def test_backtest_skips_macro_event_day():
    daily, vix = _daily(1)
    cfg = dataclasses.replace(CFG, macro_event_dates={START.isoformat(): "test event"})
    intra = _intraday([START], [[(PRICE, PRICE + 3, PRICE - 0.2, PRICE + 2.5)]])
    assert bt.simulate(daily, vix, cfg, "call", intra) == []


def test_backtest_report_renders():
    rng = np.random.default_rng(1)
    daily, vix = _daily(30)
    walk = PRICE + np.cumsum(rng.normal(0, 2, len(daily)))
    daily = daily.assign(Open=walk, Close=walk, High=walk + 3, Low=walk - 3)
    report = bt.run(daily, vix, CFG)
    for text in ("| signal |", "| call |", "| put |", "Stop size", "Not captured"):
        assert text in report


# --- experiment knobs -------------------------------------------------------

def test_backtest_entry_bar_skips_first_hour():
    daily, vix = _daily(1)
    # The first hour spikes through both levels; entering at bar 1 must ignore it.
    bars = [(PRICE, PRICE + 5, PRICE - 5, PRICE), (PRICE, PRICE + 3, PRICE - 0.2, PRICE + 2.5)]
    trades = bt.simulate(daily, vix, CFG, "call", _intraday([START], [bars]), entry_bar=1)
    assert [t.outcome for t in trades] == ["target"]


def test_backtest_direction_callable_sees_only_bars_before_entry():
    daily, vix = _daily(1)
    seen = []

    def direction(i, bars):
        seen.append(len(bars))
        return "put" if bars["Close"].iloc[-1] < bars["Open"].iloc[0] else "call"

    bars = [(PRICE, PRICE + 0.2, PRICE - 1, PRICE - 1), (PRICE - 1, PRICE - 1, PRICE - 4, PRICE - 4)]
    trades = bt.simulate(daily, vix, CFG, intraday=_intraday([START], [bars]), entry_bar=1, direction=direction)
    assert seen == [1]
    assert trades[0].side == "put" and trades[0].outcome == "target"


def test_backtest_atr_exits_use_fixed_levels():
    daily, vix = _daily(1)  # flat daily bars: ATR14 = 1.0
    # Target 2 x ATR = +$2.00 QQQ: a +$1.50 bar must not hit it, +$2.50 must.
    path = [(PRICE, PRICE + 1.5, PRICE - 0.2, PRICE + 1.0), (PRICE + 1.0, PRICE + 2.5, PRICE + 0.9, PRICE + 2.4)]
    trades = bt.simulate(daily, vix, CFG, "call", _intraday([START], [path]), atr_exits=(2.0, 1.0))
    assert [t.outcome for t in trades] == ["target"]
    # ~0.8+ delta x $2 x 1,000 shares, minus slippage both sides.
    assert 1400 < trades[0].pnl_usd < 2100


@pytest.mark.parametrize("hold", ["overnight", "intraday"])
def test_simulate_hold_flat_market_loses_costs(hold):
    daily, vix = _daily(5)
    trades = bt.simulate_hold(daily, vix, CFG, "call", hold)
    assert len(trades) == (4 if hold == "overnight" else 5)
    assert all(t.pnl_usd < 0 for t in trades)  # no move: only slippage (and overnight theta)


def test_simulate_hold_overnight_gain():
    daily, vix = _daily(2)
    i = bt.MIN_DAILY_HISTORY
    daily.iloc[i + 1, daily.columns.get_loc("Open")] = PRICE + 2.0
    trades = bt.simulate_hold(daily, vix, CFG, "call", "overnight")
    assert trades[0].pnl_usd > 1000  # +$2 gap x ~0.8 delta x 1,000 shares, less costs


def test_score_variant_splits_halves():
    from qqq_iron_condor.daily_target_experiments import score_variant

    mk = lambda d, pnl: bt.TargetTrade(d, d, "call", 1.0, 1.0, 1.0, pnl, "target" if pnl > 0 else "stop", 1, 1.0)  # noqa: E731
    trades = [mk(dt.date(2025, 1, 2), 1000.0), mk(dt.date(2025, 1, 3), -500.0), mk(dt.date(2025, 6, 2), 1000.0)]
    r = score_variant("g", "n", trades, dt.date(2025, 3, 1), True)
    assert (r.first_half_pnl, r.second_half_pnl, r.total_pnl) == (500.0, 1000.0, 1500.0)
    assert r.both_halves_positive and r.goal_days == 2
    assert r.max_drawdown == 500.0


def test_experiments_report_renders():
    from qqq_iron_condor import daily_target_experiments as ex

    rng = np.random.default_rng(2)
    daily, vix = _daily(40)
    walk = PRICE + np.cumsum(rng.normal(0, 2, len(daily)))
    daily = daily.assign(Open=walk, Close=walk, High=walk + 3, Low=walk - 3)
    days = daily.index[bt.MIN_DAILY_HISTORY:]
    paths = [[(w, w + 1.5, w - 1.5, w + rng.normal(0, 1)) for _ in range(7)] for w in walk[bt.MIN_DAILY_HISTORY:]]
    results, first, mid, last = ex.run_experiments(daily, vix, _intraday([d.date() for d in days], paths), CFG)
    report = ex.render_report(results, first, mid, last, CFG)
    for text in ("### Baseline", "### Direction", "### Hold period", "## Verdict"):
        assert text in report


# --- first-hour momentum direction ------------------------------------------

def _session_bars(n_bars: int, start_px: float = 750.0, step: float = 0.1):
    idx = pd.date_range("2026-10-05 09:30", periods=n_bars, freq="5min")
    close = start_px + step * np.arange(1, n_bars + 1)
    return pd.DataFrame({"Open": close - step, "High": close + 0.05, "Low": close - step - 0.05, "Close": close}, index=idx)


def test_first_hour_read_waits_until_hour_is_complete():
    # 12 bars = 9:30..10:25; the 10:25 bar may still be in progress until a 10:30 bar exists.
    fh = dtg.first_hour_read(_session_bars(12))
    assert not fh.ready and fh.side is None
    assert "10:30" in fh.detail


@pytest.mark.parametrize("step, side", [(0.1, "call"), (-0.1, "put"), (0.0, None)])
def test_first_hour_read_direction(step, side):
    bars = _session_bars(20, step=step)
    fh = dtg.first_hour_read(bars)
    assert fh.ready and fh.side == side
    # Move is the 10:25 bar's close vs. the 9:30 open -- later bars are ignored.
    assert fh.move == pytest.approx(12 * step)
    assert fh.open_price == pytest.approx(750.0)


def test_first_hour_read_no_bars():
    assert not dtg.first_hour_read(_session_bars(0)).ready


READY_UP = dtg.FirstHourRead(True, "call", 750.0, 751.0, 1.0, "up")
NOT_READY = dtg.FirstHourRead(False, None, None, None, None, "not yet")
FLAT = dtg.FirstHourRead(True, None, 750.0, 750.0, 0.0, "flat")


@pytest.mark.parametrize(
    "source, override, forced, signal_bias, fh, expected",
    [
        ("first_hour", None, [], "PUT", READY_UP, "CALL"),  # first hour wins over the signal
        ("signal", None, [], "PUT", READY_UP, "PUT"),
        ("first_hour", None, ["FOMC day"], "CALL", READY_UP, "NO TRADE"),  # gates apply to both
        ("signal", None, ["gap"], "CALL", READY_UP, "NO TRADE"),
        ("first_hour", None, [], None, NOT_READY, "WAIT"),
        ("first_hour", None, [], None, FLAT, "NO TRADE"),
        ("first_hour", None, [], None, None, "UNKNOWN"),
        ("signal", None, [], None, READY_UP, "UNKNOWN"),
        ("first_hour", "put", ["FOMC day"], "CALL", READY_UP, "PUT"),  # manual override is final
    ],
)
def test_choose_bias(source, override, forced, signal_bias, fh, expected):
    assert dtg.choose_bias(source, override, forced, signal_bias, fh)[0] == expected


def test_report_flags_disagreement_and_paper_trade_note():
    chain = dtg._synthetic_chain(750.0, 60, CFG)
    plan = dtg.build_plan(750.0, 9.5, chain, "CALL", "first-hour momentum", CFG, signal_bias="PUT", first_hour=READY_UP)
    report = dtg.render_plan(plan, CFG)
    assert "disagree" in report
    assert "paper-trade-only" in report


def test_report_wait_message():
    plan = dtg.build_plan(750.0, 9.5, dtg._synthetic_chain(750.0, 60, CFG), "WAIT", "first-hour momentum (not ready yet)", CFG, first_hour=NOT_READY)
    assert plan.chosen is None
    assert "Re-run after the first hour" in dtg.render_plan(plan, CFG)


def test_cli_rejects_unknown_direction_source():
    with pytest.raises(SystemExit):
        dtg.main(["--direction-source", "magic"])
