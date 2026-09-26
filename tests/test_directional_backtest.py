"""Tests for the directional signal backtest.

All data is synthetic and hand-built (not random) so entries/exits are
deterministic and exact outcomes can be asserted, rather than just
smoke-testing that nothing crashes.
"""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from qqq_iron_condor import directional_backtest as db
from qqq_iron_condor.config import Config

START_DATE = dt.date(2025, 1, 1)  # outside the 2026-only macro_event_dates table


def _daily_history(n_days: int = 220, end_date: dt.date = None, final_price: float = 400.0, total_drift: float = 25.0, seed: int = 3):
    """A daily series with *accelerating* gains into `end_date` (bigger
    day-over-day moves near the end than at the start) -- unlike a plain
    linear trend, this reliably leaves MACD histogram and RSI(14) clearly
    positive at the final close, which lands exactly on `final_price` so
    the fixture can be anchored to match a specific intraday day's open."""
    end_date = end_date or (START_DATE - dt.timedelta(days=1))
    dates = pd.bdate_range(end=end_date, periods=n_days)
    rng = np.random.default_rng(seed)
    weights = np.linspace(0.2, 2.0, n_days)
    weights = weights / weights.sum() * total_drift
    price = final_price - total_drift + np.cumsum(weights)
    price = price + rng.normal(0, 0.05, n_days)
    price = price - (price[-1] - final_price)  # snap the last close to final_price exactly
    high = price + 0.3
    low = price - 0.3
    return pd.DataFrame(
        {"Open": price, "High": high, "Low": low, "Close": price, "Volume": rng.integers(2_000_000, 4_000_000, n_days)},
        index=dates,
    )


def _vix_history(n_days: int = 220, end_date: dt.date = None, level: float = 15.0):
    end_date = end_date or (START_DATE - dt.timedelta(days=1))
    dates = pd.bdate_range(end=end_date, periods=n_days)
    return pd.DataFrame({"Open": level, "High": level, "Low": level, "Close": level}, index=dates)


def _bars(day: dt.date, closes, start_minute=0):
    n = len(closes)
    closes = np.array(closes, dtype=float)
    idx = [pd.Timestamp(dt.datetime.combine(day, dt.time(9, 30))) + pd.Timedelta(minutes=start_minute + 5 * i) for i in range(n)]
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes + 0.05,
            "Low": closes - 0.05,
            "Close": closes,
            "Volume": [200_000] * n,
        },
        index=pd.DatetimeIndex(idx),
    )


def _flat_spy_bars(day: dt.date, n: int, level: float = 500.0):
    return _bars(day, [level + 0.01 * i for i in range(n)])


def test_close_trade_return_sign_for_call_and_put():
    call_trade = {"entry_time": 0, "direction": "CALL", "entry_price": 100.0, "stop": 95.0, "target": 110.0, "score": 0.5, "confidence": "High"}
    win = db._close_trade(call_trade, exit_time=1, exit_price=110.0, outcome="target")
    assert win.return_pct == pytest.approx(10.0)
    loss = db._close_trade(call_trade, exit_time=1, exit_price=95.0, outcome="stop")
    assert loss.return_pct == pytest.approx(-5.0)

    put_trade = {"entry_time": 0, "direction": "PUT", "entry_price": 100.0, "stop": 105.0, "target": 90.0, "score": -0.5, "confidence": "High"}
    win = db._close_trade(put_trade, exit_time=1, exit_price=90.0, outcome="target")
    assert win.return_pct == pytest.approx((100.0 / 90.0 - 1.0) * 100.0, abs=1e-3) and win.return_pct > 0
    loss = db._close_trade(put_trade, exit_time=1, exit_price=105.0, outcome="stop")
    assert loss.return_pct < 0


def test_simulate_day_opens_call_and_hits_target():
    day = START_DATE
    daily_history = _daily_history(end_date=day - dt.timedelta(days=1))
    vix_history = _vix_history(end_date=day - dt.timedelta(days=1))

    # The daily context alone (accelerating uptrend -> positive MACD hist,
    # RSI pinned high) is bullish enough to trigger a CALL from the first
    # bar; the sustained intraday rally then lets at least one entry ride
    # to its underlying target.
    closes = [400.0, 401.0, 400.5] + list(np.linspace(402.0, 420.0, 30))
    qqq_bars = _bars(day, closes)
    spy_bars = _flat_spy_bars(day, len(closes))

    cfg = Config()
    trades = db._simulate_day(day, daily_history, vix_history, qqq_bars, spy_bars, cfg)

    assert len(trades) >= 1
    first = trades[0]
    assert first.direction == "CALL"
    assert first.outcome in ("target", "eod")
    assert first.return_pct > 0
    assert first.exit_time > first.entry_time


def test_simulate_day_call_hits_stop_on_reversal():
    day = START_DATE
    daily_history = _daily_history(end_date=day - dt.timedelta(days=1))
    vix_history = _vix_history(end_date=day - dt.timedelta(days=1))

    # Same breakout entry, but immediately reverse hard back through the
    # opening-range low (the stop level for an ORB-triggered CALL).
    closes = [400.0, 401.0, 400.5, 402.0, 403.0] + list(np.linspace(403.0, 390.0, 15))
    qqq_bars = _bars(day, closes)
    spy_bars = _flat_spy_bars(day, len(closes))

    cfg = Config()
    trades = db._simulate_day(day, daily_history, vix_history, qqq_bars, spy_bars, cfg)

    # The daily context is strongly bullish enough that entries fire (and
    # some hit their quick, ATR-scaled target) even during the brief initial
    # uptick -- the real reversal should still stop out at least one of them.
    stopped = [t for t in trades if t.direction == "CALL" and t.outcome == "stop"]
    assert len(stopped) >= 1
    assert stopped[0].return_pct < 0


def test_simulate_day_force_closes_open_trade_at_end_of_day():
    day = START_DATE
    daily_history = _daily_history(end_date=day - dt.timedelta(days=1))
    vix_history = _vix_history(end_date=day - dt.timedelta(days=1))

    # A rally that never quite reaches the target nor gives back the stop.
    closes = [400.0, 401.0, 400.5] + list(np.linspace(402.0, 405.0, 20))
    qqq_bars = _bars(day, closes)
    spy_bars = _flat_spy_bars(day, len(closes))

    cfg = Config()
    trades = db._simulate_day(day, daily_history, vix_history, qqq_bars, spy_bars, cfg)

    assert len(trades) >= 1
    assert trades[-1].outcome == "eod"
    assert trades[-1].exit_time == qqq_bars.index[-1]


def test_simulate_day_skips_entries_on_large_gap():
    day = START_DATE
    daily_history = _daily_history(end_date=day - dt.timedelta(days=1))
    vix_history = _vix_history(end_date=day - dt.timedelta(days=1))

    prior_close = float(daily_history["Close"].iloc[-1])
    gapped_open = prior_close * 1.05  # +5%, exceeds the 0.8% hard-gate threshold
    closes = [gapped_open, gapped_open + 1.0, gapped_open + 0.5] + list(np.linspace(gapped_open + 2, gapped_open + 20, 30))
    qqq_bars = _bars(day, closes)
    spy_bars = _flat_spy_bars(day, len(closes))

    cfg = Config()
    trades = db._simulate_day(day, daily_history, vix_history, qqq_bars, spy_bars, cfg)

    assert trades == []


def test_simulate_day_returns_empty_with_insufficient_daily_history():
    day = START_DATE
    short_daily_history = _daily_history(n_days=50, end_date=day - dt.timedelta(days=1))
    vix_history = _vix_history(n_days=50, end_date=day - dt.timedelta(days=1))
    qqq_bars = _bars(day, [400.0, 401.0, 402.0])
    spy_bars = _flat_spy_bars(day, 3)

    trades = db._simulate_day(day, short_daily_history, vix_history, qqq_bars, spy_bars, Config())
    assert trades == []


def test_simulate_directional_backtest_orchestrates_across_days():
    day1 = START_DATE
    day2 = START_DATE + dt.timedelta(days=1)
    daily_history = _daily_history(end_date=day2 - dt.timedelta(days=1))
    vix_history = _vix_history(end_date=day2 - dt.timedelta(days=1))

    closes1 = [400.0, 401.0, 400.5] + list(np.linspace(402.0, 420.0, 30))
    closes2 = [420.0, 421.0, 420.5] + list(np.linspace(422.0, 440.0, 30))
    qqq_intraday = pd.concat([_bars(day1, closes1), _bars(day2, closes2)])
    spy_intraday = pd.concat([_flat_spy_bars(day1, len(closes1)), _flat_spy_bars(day2, len(closes2), level=500.5)])

    trades = db.simulate_directional_backtest(daily_history, vix_history, qqq_intraday, spy_intraday, Config())
    assert len(trades) >= 2
    # Trades from day2 must not start before day1's bars end.
    day1_end = qqq_intraday[qqq_intraday.index.date == day1].index[-1]
    day2_trades = [t for t in trades if t.entry_time.date() == day2]
    assert all(t.entry_time > day1_end for t in day2_trades)


def test_summarize_handles_no_trades():
    summary = db.summarize([])
    assert summary.num_trades == 0
    assert summary.by_confidence == []


def test_summarize_computes_exact_stats_for_known_trades():
    now = pd.Timestamp.now()
    trades = [
        db.DirectionalBacktestTrade(now, now, "CALL", 100, 110, 95, 110, "target", 10.0, 0.5, "High"),
        db.DirectionalBacktestTrade(now, now, "CALL", 100, 95, 95, 110, "stop", -5.0, 0.4, "Medium"),
        db.DirectionalBacktestTrade(now, now, "PUT", 100, 90, 105, 90, "target", 10.0, -0.5, "High"),
        db.DirectionalBacktestTrade(now, now, "PUT", 100, 105, 105, 90, "stop", -5.0, -0.35, "Medium"),
    ]
    summary = db.summarize(trades)

    assert summary.num_trades == 4
    assert summary.win_rate_pct == pytest.approx(50.0)
    assert summary.avg_win_pct == pytest.approx(10.0)
    assert summary.avg_loss_pct == pytest.approx(-5.0)
    assert summary.expectancy_pct == pytest.approx(2.5)
    assert summary.total_return_pct == pytest.approx(10.0)
    assert summary.profit_factor == pytest.approx(20.0 / 10.0)
    assert summary.calls == 2
    assert summary.puts == 2

    high = next(c for c in summary.by_confidence if c.confidence == "High")
    assert high.num_trades == 2
    assert high.win_rate_pct == pytest.approx(100.0)
    medium = next(c for c in summary.by_confidence if c.confidence == "Medium")
    assert medium.num_trades == 2
    assert medium.win_rate_pct == pytest.approx(0.0)


def test_render_report_handles_zero_trades():
    report = db.render_directional_backtest_report("QQQ", 59, db.summarize([]), score_threshold=0.30)
    assert "No trades were generated" in report


def test_render_report_includes_stats_for_populated_summary():
    now = pd.Timestamp.now()
    trades = [db.DirectionalBacktestTrade(now, now, "CALL", 100, 110, 95, 110, "target", 10.0, 0.5, "High")]
    report = db.render_directional_backtest_report("QQQ", 59, db.summarize(trades), score_threshold=0.30)
    assert "Win rate" in report
    assert "Expectancy" in report
    assert "High" in report
    assert "0.30" in report


def test_export_trades_csv_includes_component_columns(tmp_path):
    now = pd.Timestamp.now()
    trade = db.DirectionalBacktestTrade(
        now, now, "CALL", 100, 110, 95, 110, "target", 10.0, 0.5, "High",
        components={"vwap": 0.2, "ema": 0.15},
    )
    out_path = tmp_path / "trades.csv"
    db.export_trades_csv([trade], out_path)

    df = pd.read_csv(out_path)
    assert len(df) == 1
    assert df.loc[0, "comp_vwap"] == pytest.approx(0.2)
    assert df.loc[0, "comp_ema"] == pytest.approx(0.15)
    assert pd.isna(df.loc[0, "comp_orb"])  # not provided -> NaN, not a crash
    assert df.loc[0, "direction"] == "CALL"
    assert df.loc[0, "return_pct"] == pytest.approx(10.0)


def test_score_threshold_override_suppresses_weaker_signals():
    day = START_DATE
    daily_history = _daily_history(end_date=day - dt.timedelta(days=1))
    vix_history = _vix_history(end_date=day - dt.timedelta(days=1))
    closes = [400.0, 401.0, 400.5] + list(np.linspace(402.0, 420.0, 30))
    qqq_bars = _bars(day, closes)
    spy_bars = _flat_spy_bars(day, len(closes))

    import dataclasses
    strict_cfg = dataclasses.replace(Config(), signal_score_threshold=0.95)  # above any real score here
    trades = db._simulate_day(day, daily_history, vix_history, qqq_bars, spy_bars, strict_cfg)
    assert trades == []
