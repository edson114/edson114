"""Offline smoke test for the full directional signal pipeline (no network calls).

Run with: python -m pytest tests/ -q
"""

from qqq_iron_condor.signal import _self_test_report


def test_self_test_report_contains_expected_sections():
    report, signal = _self_test_report()
    assert "Directional Signal (Calls/Puts)" in report
    assert "Score breakdown" in report
    assert "Market Snapshot" in report
    assert "News & Catalysts" in report
    assert "Limitations" in report
    assert signal.bias in ("CALL", "PUT", "NO TRADE")


def test_self_test_report_bias_matches_header():
    report, signal = _self_test_report()
    if signal.bias == "CALL":
        assert "BUY CALLS" in report
    elif signal.bias == "PUT":
        assert "BUY PUTS" in report
    else:
        assert "NO TRADE" in report


def test_signal_scores_qqq_even_when_the_condor_symbol_is_spx(monkeypatch, tmp_path):
    import dataclasses

    from qqq_iron_condor import signal as sig
    from qqq_iron_condor.config import Config

    spx = dataclasses.replace(Config(), symbol="^SPX")
    assert sig.signal_config(spx).symbol == "QQQ"

    seen = {}

    def fake_run_signal(cfg):
        seen["symbol"] = cfg.symbol
        return sig._self_test_report()

    monkeypatch.setattr(sig, "run_signal", fake_run_signal)
    assert sig.main(["--no-issue", "--output-dir", str(tmp_path)]) == 0
    assert seen["symbol"] == "QQQ"


def test_self_test_report_is_titled_qqq():
    report, _ = _self_test_report()
    assert report.startswith("# QQQ Directional Signal")
