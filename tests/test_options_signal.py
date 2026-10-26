"""Unit tests for the daily options signal (options_signal/)."""

import datetime as dt
import math
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from options_signal.bars import SESSION_BARS, bar_close_time, clock_to_slot_close, split_sessions
from options_signal.config import Config
from options_signal.options import (
    Contract,
    bs_price_vec,
    implied_vol,
    norm_cdf,
    pick_expiration,
    select_contract,
    years_until,
)
from options_signal.pipeline import choose_pick, rank, run
from options_signal.report import render_markdown, verdict
from options_signal.rules import day_gates, earnings_gate, size_contracts, TRADER_DEFAULTS
from options_signal.scan import SyntheticMarket
from options_signal.setup import find_orb
from options_signal.simulate import OptionOdds, build_pool, history_replay, simulate_option, spot_needed_now
from qqq_iron_condor.options_math import bs_price

DAY = dt.date(2026, 10, 26)


def _bars(closes, spread=0.05):
    """(n, 4) bars from closes: open = previous close, high/low around."""
    rows, prev = [], closes[0]
    for c in closes:
        rows.append((prev, max(prev, c) + spread, min(prev, c) - spread, c))
        prev = c
    return np.array(rows, dtype=float)


def _contract(option_type="call", strike=100.0, bid=2.0, ask=2.1, iv=0.3, dte=4):
    exp = (DAY + dt.timedelta(days=dte)).isoformat()
    return Contract(option_type, exp, dte, strike, bid, ask, 1000, iv, 0.5)


def _past(n=30, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    for j in range(n):
        closes = 100 * np.cumprod(1 + rng.normal(0, 0.002, SESSION_BARS))
        out.append((DAY - dt.timedelta(days=n - j), _bars(list(closes), spread=0.03)))
    return out


# --- math -----------------------------------------------------------------


def test_norm_cdf_matches_erf():
    xs = np.linspace(-6, 6, 241)
    exact = np.array([0.5 * (1 + math.erf(x / math.sqrt(2))) for x in xs])
    assert np.max(np.abs(norm_cdf(xs) - exact)) < 2e-7


@pytest.mark.parametrize("option_type", ["call", "put"])
def test_vector_price_matches_scalar(option_type):
    spots = np.array([90.0, 100.0, 110.0])
    vec = bs_price_vec(spots, 100.0, 7 / 365, 0.045, 0.3, option_type)
    scalar = [bs_price(s, 100.0, 7 / 365, 0.045, 0.3, option_type) for s in spots]
    assert np.allclose(vec, scalar, atol=1e-5)


def test_implied_vol_round_trip():
    price = bs_price(100.0, 102.0, 5 / 365, 0.045, 0.42, "call")
    assert implied_vol(price, 100.0, 102.0, 5 / 365, 0.045, "call") == pytest.approx(0.42, abs=1e-6)


def test_implied_vol_rejects_price_below_intrinsic():
    assert implied_vol(1.0, 110.0, 100.0, 5 / 365, 0.045, "call") is None


def test_years_until_counts_to_the_four_pm_close():
    t = years_until(DAY, dt.datetime.combine(DAY, dt.time(10, 0)))
    assert t == pytest.approx(6 * 3600 / (365 * 24 * 3600))


# --- bars and setup -------------------------------------------------------


def test_slot_clock_helpers():
    assert clock_to_slot_close("15:55") == 76
    assert clock_to_slot_close("09:50") == 3
    assert bar_close_time(DAY, 0) == dt.datetime(2026, 10, 26, 9, 35)


def test_split_sessions_keeps_full_days_and_closed_bars_only():
    idx, rows = [], []
    for day, n in ((DAY - dt.timedelta(days=3), 78), (DAY - dt.timedelta(days=2), 40), (DAY, 10)):
        for s in range(n):
            idx.append(dt.datetime.combine(day, dt.time(9, 30)) + dt.timedelta(minutes=5 * s))
            rows.append((1.0, 1.0, 1.0, 1.0))
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(idx), columns=["Open", "High", "Low", "Close"])
    sb = split_sessions(df, DAY, dt.datetime.combine(DAY, dt.time(10, 0)))
    assert len(sb.past) == 1 and sb.short_sessions == 1
    assert len(sb.today) == 6  # 9:30..9:55 bars have closed by 10:00


def test_orb_call_trigger_and_midpoint_stop():
    today = _bars([100.0, 100.5, 100.2, 101.2, 101.4])
    s = find_orb(today, 3, 70)
    assert s.status == "triggered" and s.option_type == "call" and s.trigger_slot == 3
    assert s.stop == math.floor((s.or_high + s.or_low) / 2 * 100) / 100


def test_orb_put_trigger_rounds_stop_up():
    today = _bars([100.0, 99.7, 100.1, 99.0, 98.8])
    s = find_orb(today, 3, 70)
    assert s.status == "triggered" and s.option_type == "put"
    assert s.stop == math.ceil((s.or_high + s.or_low) / 2 * 100) / 100


def test_orb_faded_when_back_inside_range():
    today = _bars([100.0, 100.5, 100.2, 101.2, 100.3])
    assert find_orb(today, 3, 70).status == "faded"


def test_orb_waiting_and_forming():
    assert find_orb(_bars([100.0, 100.5, 100.2, 100.3]), 3, 70).status == "waiting"
    assert find_orb(_bars([100.0, 100.5]), 3, 70).status == "forming"


# --- contracts ------------------------------------------------------------


def test_pick_expiration_prefers_about_a_week():
    exps = [(DAY + dt.timedelta(days=d)).isoformat() for d in (0, 4, 8, 11, 32)]
    assert pick_expiration(exps, DAY, 1, 10, 7) == (DAY + dt.timedelta(days=8)).isoformat()
    assert pick_expiration([DAY.isoformat()], DAY, 1, 10, 7) is None


def test_select_contract_skips_wide_and_thin_strikes():
    now = dt.datetime.combine(DAY, dt.time(10, 0))
    exp = (DAY + dt.timedelta(days=4)).isoformat()
    t = years_until(DAY + dt.timedelta(days=4), now)
    mids = {k: bs_price(100.0, k, t, 0.045, 0.3, "call") for k in (99.0, 100.0, 101.0)}
    table = pd.DataFrame({
        "strike": [100.0, 101.0, 99.0],
        "bid": [mids[100.0] * 0.7, mids[101.0] * 0.99, mids[99.0] * 0.99],  # 100: 35% spread
        "ask": [mids[100.0] * 1.3, mids[101.0] * 1.01, mids[99.0] * 1.01],
        "openInterest": [5000, 10, 5000],  # 101: too thin
    })
    c, why = select_contract(table, exp, 100.0, "call", now, 0.045, 10.0, 500, 6)
    assert c is not None and c.strike == 99.0 and why == ""
    assert c.iv == pytest.approx(0.3, abs=0.01)


def test_select_contract_reports_why_nothing_passed():
    now = dt.datetime.combine(DAY, dt.time(10, 0))
    table = pd.DataFrame({"strike": [100.0], "bid": [0.0], "ask": [1.0], "openInterest": [5000]})
    c, why = select_contract(table, (DAY + dt.timedelta(days=4)).isoformat(), 100.0, "call", now, 0.045, 10.0, 500, 6)
    assert c is None and "no two-sided quote" in why


# --- the time machine on the option ----------------------------------------


def test_simulation_is_repeatable_and_sums_to_one():
    pool = build_pool(_past())
    k = _contract()
    a = simulate_option(pool, k, 100.0, 99.0, 10, 76, DAY, 0.045, 30.0, replays=2000, seed=19)
    b = simulate_option(pool, k, 100.0, 99.0, 10, 76, DAY, 0.045, 30.0, replays=2000, seed=19)
    assert a == b
    assert a.p_target + a.p_stop + a.p_neither == pytest.approx(1.0)


def test_tight_stop_mostly_stops_and_tiny_target_mostly_wins():
    pool = build_pool(_past())
    k = _contract()
    tight = simulate_option(pool, k, 100.0, 99.95, 10, 76, DAY, 0.045, 30.0, replays=2000)
    assert tight.p_stop > 0.8
    easy = simulate_option(pool, k, 100.0, 90.0, 10, 76, DAY, 0.045, 1.0, replays=2000)
    assert easy.p_target > tight.p_target


def test_put_simulation_uses_the_stop_above():
    pool = build_pool(_past())
    k = _contract(option_type="put")
    odds = simulate_option(pool, k, 100.0, 100.05, 10, 76, DAY, 0.045, 30.0, replays=2000)
    assert odds.p_stop > 0.8


def test_spot_needed_is_beyond_the_entry():
    call = _contract(bid=2.0, ask=2.1)
    put = _contract(option_type="put", bid=2.0, ask=2.1)
    assert spot_needed_now(call, 100.0, DAY, 10, 0.045, 30.0) > 100.0
    assert spot_needed_now(put, 100.0, DAY, 10, 0.045, 30.0) < 100.0


def test_history_replay_counts_add_up():
    h = history_replay(_past(), _contract(), 3, 72, 76, 0.045, 30.0)
    assert h.sessions == 30
    assert h.target + h.stop + h.neither == h.triggered


# --- rules ------------------------------------------------------------------


def test_sizing_binds_on_max_loss_and_explains_zero():
    trader = dict(TRADER_DEFAULTS)
    s = size_contracts(2.0, 1.5, trader)  # $50 per contract at the stop
    assert s.contracts == 1 and s.binding == "max loss"
    z = size_contracts(9.0, 6.0, trader)  # $300 per contract at the stop
    assert z.contracts == 0 and "max loss" in z.note


def test_earnings_gate():
    exp = (DAY + dt.timedelta(days=4)).isoformat()
    assert earnings_gate(DAY, DAY, exp)[0].startswith("Earnings today")
    skip, flags = earnings_gate(DAY + dt.timedelta(days=2), DAY, exp)
    assert skip is None and "before this option expires" in flags[0]
    assert earnings_gate(DAY + dt.timedelta(days=30), DAY, exp) == (None, [])


def test_day_gates_fomc_and_vix():
    assert day_gates(dt.date(2026, 10, 28), {"2026-10-28": "FOMC decision"}, 15.0, 20.0)
    assert day_gates(DAY, {}, 25.0, 20.0)
    assert day_gates(DAY, {}, 15.0, 20.0) == []


# --- the whole run on synthetic data ----------------------------------------


def test_pipeline_on_synthetic_market():
    now = dt.datetime.combine(DAY, dt.time(10, 20))
    cfg = replace(Config(), replays=2000)
    result = run(["TEST"], SyntheticMarket(now), now, cfg, dict(TRADER_DEFAULTS))
    assert result.status == "ok"
    (cand,) = result.candidates
    assert cand.setup.status == "triggered" and cand.contract is not None and cand.odds is not None
    md = render_markdown(result)
    assert "TEST" in md and "How this works" in md


def test_pipeline_before_the_range_is_set():
    now = dt.datetime.combine(DAY, dt.time(9, 40))
    result = run(["TEST"], SyntheticMarket(now), now, Config(), dict(TRADER_DEFAULTS))
    assert result.status == "early"


def test_pick_needs_a_positive_average():
    def cand(p, avg):
        c = type("C", (), {})()
        c.odds = OptionOdds(100, 19, 10, p, 0.1, 0.0, 0.5, avg, 0, -10, 10, -20)
        c.skip_reason = None
        return c

    likely_loser, smaller_winner = cand(0.6, -2.0), cand(0.4, 3.0)
    ranked = rank([smaller_winner, likely_loser])
    assert ranked[0] is likely_loser
    assert choose_pick(ranked) is smaller_winner


def test_verdict_lines():
    o = OptionOdds(100, 19, 10, 0.1, 0.0, 0.9, 0.5, 0.1, 0, -10, 10, -20)
    lines = verdict(o, 30.0)
    assert lines[0].startswith("Close to break-even") and len(lines) == 2
