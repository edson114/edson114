"""Offline smoke test for the small-cap scan pipeline (no network calls),
plus GitHub issue creation error handling -- mirrors tests/test_pipeline.py
and tests/test_scan.py for the QQQ tool.

Run with: python -m pytest tests/ -q
"""

from unittest import mock

import requests

from smallcap_options.scan import _self_test_report, maybe_create_github_issue


def test_self_test_report_contains_expected_sections():
    report = _self_test_report()
    assert "Small-Cap Momentum Options Scan" in report
    assert "Universe" in report
    assert "Candidates" in report
    assert "Risk management notes" in report
    assert "Limitations" in report
    assert "CALL" in report
    assert "PUT" in report


def test_issue_creation_failure_is_non_fatal(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "edson114/edson114")

    response = mock.Mock()
    response.raise_for_status.side_effect = requests.exceptions.HTTPError("410 Client Error: Gone")

    with mock.patch("requests.post", return_value=response):
        maybe_create_github_issue("report body", "title")  # must not raise

    assert "could not create GitHub issue" in capsys.readouterr().err


def test_no_op_without_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    maybe_create_github_issue("report body", "title")  # must not raise
