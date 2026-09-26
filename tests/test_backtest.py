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


def test_render_backtest_report_includes_each_label():
    price_history, vix_history = _synthetic_history(n_days=252 + 260)
    cfg = Config()
    target = ExpirationTarget("Weekly", 5, 10)
    trades = backtest.run_backtest(price_history, vix_history, target, cfg)
    summary = backtest.summarize("Weekly", trades)

    report_md = backtest.render_backtest_report("QQQ", 2.0, [summary], apply_gates=True)
    assert "Weekly" in report_md
    assert "Win rate" in report_md
