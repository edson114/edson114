"""Unit tests for the Tradier provider's response parsing.

These test parsing logic against synthetic JSON shaped like Tradier's
documented responses -- they don't hit the network or require a real
account/token. Tradier's own well-known quirk (a single-item array
collapsing to a bare object/string in JSON) is exercised explicitly,
since it's an easy thing to get wrong and there's no live account in
this codebase to catch it against.
"""

from unittest import mock

import pandas as pd
import pytest

from qqq_iron_condor import tradier


def _mock_response(json_body):
    resp = mock.Mock()
    resp.json.return_value = json_body
    resp.raise_for_status.return_value = None
    return resp


def test_is_available_reflects_env_var(monkeypatch):
    monkeypatch.delenv("TRADIER_TOKEN", raising=False)
    assert tradier.is_available() is False
    monkeypatch.setenv("TRADIER_TOKEN", "fake-token")
    assert tradier.is_available() is True


def test_as_list_normalizes_singleton_and_none():
    assert tradier._as_list(None) == []
    assert tradier._as_list("2026-10-02") == ["2026-10-02"]
    assert tradier._as_list(["a", "b"]) == ["a", "b"]


def test_get_quote_handles_singleton_quote(monkeypatch):
    monkeypatch.setenv("TRADIER_TOKEN", "fake-token")
    body = {"quotes": {"quote": {"symbol": "QQQ", "last": 744.77, "prevclose": 741.10}}}
    with mock.patch("requests.get", return_value=_mock_response(body)) as mock_get:
        quote = tradier.get_quote("QQQ")
    assert quote["last"] == 744.77
    assert mock_get.call_args.kwargs["params"] == {"symbols": "QQQ"}


def test_get_gap_info_uses_quote_and_is_always_confirmed(monkeypatch):
    monkeypatch.setenv("TRADIER_TOKEN", "fake-token")
    body = {"quotes": {"quote": {"symbol": "QQQ", "last": 744.77, "prevclose": 741.10}}}
    with mock.patch("requests.get", return_value=_mock_response(body)):
        gap_pct, confirmed = tradier.get_gap_info("QQQ")
    assert confirmed is True
    assert gap_pct == pytest.approx((744.77 / 741.10 - 1.0) * 100.0)


def test_list_expirations_handles_singleton_date(monkeypatch):
    monkeypatch.setenv("TRADIER_TOKEN", "fake-token")
    body = {"expirations": {"date": "2026-10-02"}}
    with mock.patch("requests.get", return_value=_mock_response(body)):
        expirations = tradier.list_expirations("QQQ")
    assert expirations == ["2026-10-02"]


def test_get_price_history_maps_columns(monkeypatch):
    monkeypatch.setenv("TRADIER_TOKEN", "fake-token")
    body = {
        "history": {
            "day": [
                {"date": "2026-09-23", "open": 740.0, "high": 745.0, "low": 738.0, "close": 743.61, "volume": 30_000_000},
                {"date": "2026-09-24", "open": 742.0, "high": 747.0, "low": 735.0, "close": 735.68, "volume": 35_000_000},
            ]
        }
    }
    with mock.patch("requests.get", return_value=_mock_response(body)):
        df = tradier.get_price_history("QQQ", period="1mo")
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df["Close"].iloc[-1] == 735.68


def test_get_option_chain_parses_greeks_and_splits_calls_puts(monkeypatch):
    monkeypatch.setenv("TRADIER_TOKEN", "fake-token")
    body = {
        "options": {
            "option": [
                {
                    "strike": 750.0, "option_type": "call", "bid": 0.65, "ask": 0.70, "last": 0.68,
                    "greeks": {"delta": 0.16, "mid_iv": 0.18},
                },
                {
                    "strike": 730.0, "option_type": "put", "bid": 1.90, "ask": 1.98, "last": 1.94,
                    "greeks": {"delta": -0.16, "mid_iv": 0.19},
                },
            ]
        }
    }
    with mock.patch("requests.get", return_value=_mock_response(body)):
        chain = tradier.get_option_chain_for_expiration("QQQ", "2026-10-02")

    assert len(chain.calls) == 1
    assert len(chain.puts) == 1
    assert chain.calls.iloc[0]["delta"] == pytest.approx(0.16)
    assert chain.puts.iloc[0]["delta"] == pytest.approx(-0.16)
    assert chain.calls.iloc[0]["impliedVolatility"] == pytest.approx(0.18)


def test_get_option_chain_raises_on_missing_side(monkeypatch):
    monkeypatch.setenv("TRADIER_TOKEN", "fake-token")
    body = {"options": {"option": [{"strike": 750.0, "option_type": "call", "bid": 0.65, "ask": 0.70, "greeks": {}}]}}
    with mock.patch("requests.get", return_value=_mock_response(body)):
        with pytest.raises(RuntimeError):
            tradier.get_option_chain_for_expiration("QQQ", "2026-10-02")
