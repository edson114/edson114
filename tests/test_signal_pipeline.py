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
