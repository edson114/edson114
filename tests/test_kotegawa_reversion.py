"""Kotegawa-style reversion: signal rules, backtest fill mechanics, and an
offline smoke test of the CLI."""

from dataclasses import replace

import pandas as pd

from kotegawa_reversion.backtest import run_backtest
from kotegawa_reversion.config import Config
from kotegawa_reversion.scan import main
from kotegawa_reversion.signals import evaluate_symbol

CFG = replace(
    Config(),
    ma_window=5,
    entry_deviation=0.10,
    exit_deviation=0.0,
    stop_loss_pct=0.10,
    max_hold_days=5,
    min_avg_dollar_volume=0.0,
    min_price=1.0,
    max_positions=1,
    position_fraction=1.0,
    cost_bps=0.0,
)


def _bars(closes, opens=None, lows=None, volume=1_000_000) -> pd.DataFrame:
    opens = opens or closes
    lows = lows or [min(o, c) for o, c in zip(opens, closes)]
    highs = [max(o, c) for o, c in zip(opens, closes)]
    idx = pd.bdate_range("2025-01-01", periods=len(closes))
    return pd.DataFrame({"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volume}, index=idx)


# Twenty flat bars at 100 so the 20-day dollar-volume average is defined.
FLAT = [100.0] * 20


def test_signal_requires_deep_deviation_and_green_close():
    red_drop = _bars(FLAT + [80.0], opens=FLAT + [85.0])
    sig = evaluate_symbol("X", red_drop, CFG)
    assert sig.deviation < -0.10 and not sig.is_entry  # closed red

    green_drop = _bars(FLAT + [80.0], opens=FLAT + [78.0])
    sig = evaluate_symbol("X", green_drop, CFG)
    assert sig.is_entry
    assert sig.stop_price == 80.0 * 0.9


def test_shallow_dip_and_collapse_are_not_entries():
    shallow = evaluate_symbol("X", _bars(FLAT + [95.0], opens=FLAT + [94.0]), CFG)
    assert not shallow.is_entry
    collapse = evaluate_symbol("X", _bars(FLAT + [30.0], opens=FLAT + [29.0]), CFG)
    assert not collapse.is_entry and "fundamental" in collapse.reason


def test_entry_fills_next_open_and_exits_on_reversion():
    # Signal on bar 20 (green close at 80), fill at bar 21's open (81),
    # close back above the MA on bar 22 -> target exit at that close.
    closes = FLAT + [80.0, 84.0, 100.0, 100.0]
    opens = FLAT + [78.0, 81.0, 90.0, 100.0]
    res = run_backtest({"X": _bars(closes, opens)}, CFG)
    t = res.trades[0]
    assert t.entry_date == pd.Timestamp(_bars(closes).index[21])
    assert t.entry_price == 81.0
    assert t.exit_reason == "target (reverted to MA)"
    assert t.exit_price == 100.0
    assert res.stats["final_equity"] > CFG.starting_capital


def test_gap_below_stop_fills_at_open():
    closes = FLAT + [80.0, 80.0, 60.0]
    opens = FLAT + [78.0, 80.0, 65.0]  # stop = 72; gaps to 65
    t = run_backtest({"X": _bars(closes, opens)}, CFG).trades[0]
    assert t.exit_reason == "stop (gap)" and t.exit_price == 65.0


def test_time_stop():
    # Drifts lower after entry: stays below the MA, never reaches the stop.
    drift = [81.0, 80.0, 79.0, 78.0, 77.0, 76.0, 75.0, 74.0]
    closes = FLAT + [80.0] + drift
    opens = FLAT + [78.0] + drift
    t = run_backtest({"X": _bars(closes, opens)}, CFG).trades[0]
    assert t.exit_reason == "time stop" and t.bars_held == CFG.max_hold_days


def test_self_test_runs_offline(capsys):
    assert main(["--self-test"]) == 0
    out = capsys.readouterr().out
    assert "Reversion Scan" in out and "Backtest" in out
