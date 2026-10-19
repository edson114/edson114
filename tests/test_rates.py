"""Tests for the 10-year yield read (rates.py) and its backtest (rates_backtest.py)."""

import dataclasses
import datetime as dt

import numpy as np
import pandas as pd
import pytest

from qqq_iron_condor import daily_target as dtg
from qqq_iron_condor import rates
from qqq_iron_condor import rates_backtest as rb
from qqq_iron_condor.config import Config


def _tnx(values, end=dt.date(2026, 10, 16)):
    idx = pd.bdate_range(end=end, periods=len(values))
    return pd.DataFrame({"Close": values}, index=idx)


@pytest.mark.parametrize("bp, state", [(25, "spiking"), (20, "spiking"), (10, "rising"), (3, "calm"), (-9, "falling")])
def test_classify(bp, state):
    assert rates.classify(bp) == state


def test_ten_year_read_changes_and_high():
    vals = [4.50] * 20 + [4.60, 4.62, 4.65, 4.70, 4.78, 4.82]  # +32bp over the last 5 sessions
    r = rates.ten_year_read(_tnx(vals))
    assert r.ready and r.level == pytest.approx(4.82)
    assert r.change_1d_bp == pytest.approx(4.0)
    assert r.change_5d_bp == pytest.approx(22.0)  # 4.82 - 4.60
    assert r.state == "spiking" and r.at_52w_high
    assert "spiking" in r.detail and "52-week high" in r.detail


def test_prior_close_spike_ignores_todays_bar():
    # Flat until today, then a +30bp jump today: live 5d is a spike, but the
    # backtested rule (prior close) isn't triggered.
    vals = [4.50] * 25 + [4.80]
    today = pd.bdate_range(end=dt.date(2026, 10, 16), periods=1)[0].date()
    r = rates.ten_year_read(_tnx(vals), today=today)
    assert r.state == "spiking" and r.prior_5d_bp == pytest.approx(0.0) and not r.spiking_at_prior_close
    # Without `today`, the last bar counts as a completed session.
    assert rates.ten_year_read(_tnx(vals)).spiking_at_prior_close


def test_state_on_uses_only_prior_closes():
    vals = [4.0] * 10 + [4.5]
    states = rates.daily_states(_tnx(vals))
    last = states.index[-1].date()
    assert rates.state_on(states, last)["change_5d_bp"] == pytest.approx(0.0)  # the jump day isn't visible yet
    assert rates.state_on(states, last + dt.timedelta(days=3))["change_5d_bp"] == pytest.approx(50.0)
    assert rates.state_on(states, states.index[0].date()) is None


def test_spike_events_cooldown():
    states = pd.DataFrame({"change_5d_bp": [25, 30, 0, 0, 22, 0]}, index=pd.bdate_range("2025-01-01", periods=6))
    assert len(rb.spike_events(states, 20, cooldown=2)) == 2  # day 0, then day 4 (day 1 is in the cool-down)


def test_event_study_forward_returns():
    idx = pd.bdate_range("2025-02-03", periods=60)
    tnx = pd.DataFrame({"Close": [4.0] * 30 + [4.3] + [4.3] * 29}, index=idx)
    qqq = pd.DataFrame({"Close": np.linspace(100, 159, 60)}, index=idx)
    rows = {r["period"]: r for r in rb.event_study(qqq, tnx, 20.0)}
    t2 = rows["Trump 2nd term (2025-)"]
    assert t2["events"] == 1 and t2["dates"] == [idx[30].date().isoformat()]
    assert t2["fwd1"] == pytest.approx((131 / 130 - 1) * 100)
    assert rows["Biden (2021-2025)"]["events"] == 0


def test_filters_skip_on_spike_by_side():
    states = rates.daily_states(_tnx([4.0] * 10 + [4.0, 4.1, 4.2, 4.3, 4.4]))
    day = states.index[-1].date() + dt.timedelta(days=3)  # after the +40bp week
    f = dict(rb.make_filters(states))
    assert f["No yield filter (baseline)"] is None
    puts = f["Skip puts when spiking (>= 20bp) -- the 'bond put'"]
    assert puts(day, -1) and not puts(day, 1)
    assert not f["Skip all trades when the 10-yr is spiking (5-day rise >= 20bp)"](states.index[5].date(), 1)


SPIKE = rates.TenYearRead(True, 5.1, 2.0, 25.0, 30.0, 5.1, True, "spiking", "10-yr detail", prior_5d_bp=23.0)
CALM = rates.TenYearRead(True, 4.5, 0.0, 2.0, 1.0, 5.0, False, "calm", "10-yr calm detail", prior_5d_bp=2.0)
FH_DOWN = dtg.FirstHourRead(True, "put", 752.0, 750.0, -2.0, "down", high=753.0, low=749.0)


def test_plan_shows_yield_row_and_warns_on_spike_puts():
    chain = dtg._synthetic_chain(750.0, 60, Config())
    put = dtg.render_plan(dtg.build_plan(750.0, 9.5, chain, "PUT", "first-hour momentum", Config(), first_hour=FH_DOWN, ten_year=SPIKE), Config())
    assert "| 10-yr Treasury yield | **SPIKING** -- 10-yr detail |" in put
    assert "⚠️ **10-yr yield spiking** (+23bp" in put
    call = dtg.render_plan(dtg.build_plan(750.0, 9.5, chain, "CALL", "first-hour momentum", Config(), ten_year=SPIKE), Config())
    assert "⚠️ **10-yr yield spiking**" not in call and "calls on spike days did fine" in call
    calm = dtg.render_plan(dtg.build_plan(750.0, 9.5, chain, "PUT", "first-hour momentum", Config(), ten_year=CALM), Config())
    assert "**CALM**" in calm and "spiking" not in calm


def _bond_put_inputs():
    idx = pd.bdate_range("2025-02-03", periods=60)
    tnx = pd.DataFrame({"Close": [4.0] * 30 + [4.3] * 30}, index=idx)  # spike close on idx[30]
    px = np.linspace(100, 159, 60)
    qqq = pd.DataFrame({"Open": px - 0.5, "Close": px}, index=idx)
    vix = pd.DataFrame({"Close": 15.0}, index=idx)
    return idx, tnx, qqq, vix


def test_bond_put_trade_enters_next_open_and_holds():
    idx, tnx, qqq, vix = _bond_put_inputs()
    trades = rb.bond_put_trades(qqq, tnx, vix, 20.0, 5, Config())
    assert len(trades) == 1
    t = trades[0]
    assert t["signal"] == idx[30].date() and t["entry"] == idx[31].date() and t["exit"] == idx[35].date()
    assert t["qqq"] == pytest.approx((135.0 / 130.5 - 1) * 100)  # open of idx[31] -> close of idx[35]
    assert t["option"] > t["qqq"]  # ~0.8 delta on a smaller premium: leverage


def test_bond_put_summary_vs_random_entries():
    idx, tnx, qqq, vix = _bond_put_inputs()
    trades = rb.bond_put_trades(qqq, tnx, vix, 20.0, 5, Config())
    s = rb.bond_put_summary(trades, qqq, 5, [(dt.date(2025, 1, 20), dt.date(2100, 1, 1))])
    assert s["trades"] == 1 and s["qqq_up"] == 1.0
    pool = rb.random_entry_returns(qqq, 5, [(dt.date(2025, 1, 20), dt.date(2100, 1, 1))])
    assert len(pool) == 56 and s["random_avg"] == pytest.approx(pool.mean())
    assert rb._bootstrap_p(1e9, pool, 3) == 0.0 and rb._bootstrap_p(-1e9, pool, 3) == 1.0
