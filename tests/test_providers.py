"""Tests for the data-provider dispatch -- specifically the symbol
normalization for Tradier, which never uses yfinance's caret-prefixed
index ticker convention (e.g. "^SPX", "^VIX"). This matters now that
Config.symbol defaults to "^SPX": without stripping the caret, every
Tradier call would 404 on a symbol that doesn't exist.
"""

from unittest import mock

import pandas as pd
import pytest

from qqq_iron_condor import providers


@pytest.mark.parametrize("raw_symbol,expected", [("^SPX", "SPX"), ("QQQ", "QQQ"), ("^VIX", "VIX")])
def test_tradier_symbol_strips_leading_caret_only_when_present(raw_symbol, expected):
    assert providers._tradier_symbol(raw_symbol) == expected


def test_get_price_history_strips_caret_before_calling_tradier(monkeypatch):
    monkeypatch.setattr(providers._tradier, "is_available", lambda: True)
    captured = {}

    def fake_get_price_history(symbol, period):
        captured["symbol"] = symbol
        return pd.DataFrame({"Close": [1.0]})

    monkeypatch.setattr(providers._tradier, "get_price_history", fake_get_price_history)
    providers.get_price_history("^SPX", "1y")
    assert captured["symbol"] == "SPX"


def test_get_spot_price_strips_caret_before_calling_tradier(monkeypatch):
    monkeypatch.setattr(providers._tradier, "is_available", lambda: True)
    captured = {}

    def fake_get_spot_price(symbol):
        captured["symbol"] = symbol
        return 7800.0

    monkeypatch.setattr(providers._tradier, "get_spot_price", fake_get_spot_price)
    providers.get_spot_price("^SPX", pd.DataFrame({"Close": [7800.0]}))
    assert captured["symbol"] == "SPX"


def test_pick_expirations_for_targets_strips_caret_before_calling_tradier(monkeypatch):
    monkeypatch.setattr(providers._tradier, "is_available", lambda: True)
    captured = {}

    def fake_pick(symbol, targets):
        captured["symbol"] = symbol
        return {}

    monkeypatch.setattr(providers._tradier, "pick_expirations_for_targets", fake_pick)
    providers.pick_expirations_for_targets("^SPX", ())
    assert captured["symbol"] == "SPX"


def test_yfinance_path_receives_symbol_unmodified(monkeypatch):
    monkeypatch.setattr(providers._tradier, "is_available", lambda: False)
    captured = {}

    def fake_get_price_history(symbol, period):
        captured["symbol"] = symbol
        return pd.DataFrame({"Close": [1.0]})

    monkeypatch.setattr(providers._yf, "get_price_history", fake_get_price_history)
    providers.get_price_history("^SPX", "1y")
    # yfinance wants the caret-prefixed ticker -- only the Tradier path strips it.
    assert captured["symbol"] == "^SPX"
