"""Unit test for GitHub issue creation failure handling."""

from unittest import mock

import requests

from qqq_iron_condor.scan import maybe_create_github_issue


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
