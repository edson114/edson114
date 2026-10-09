"""Offline smoke test for the full scan pipeline (no network calls).

Run with: python -m pytest tests/ -q
"""

from qqq_iron_condor.scan import _self_test_report


def test_self_test_report_contains_expected_sections():
    report = _self_test_report()
    assert "QQQ Iron Condor Daily Scan" in report
    assert "Market Snapshot" in report
    assert "Technical Indicators" in report
    assert "News & Catalysts" in report
    assert "Weekly Iron Condor" in report
    assert "Monthly Iron Condor" in report
    assert "Risk Management" in report
    assert "Net credit" in report
