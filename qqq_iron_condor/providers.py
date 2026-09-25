"""Market data provider dispatch.

Uses Tradier (real bid/ask + broker-computed greeks) when TRADIER_TOKEN
is set in the environment, otherwise falls back to the free yfinance-based
provider that's always available. Every function here has the same
signature regardless of which backend serves it, so scan.py doesn't need
to know or care which one is active.
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

from . import data as _yf
from . import tradier as _tradier
from .data import OptionChain


def active_provider_name() -> str:
    return "tradier" if _tradier.is_available() else "yfinance"


def get_price_history(symbol: str, period: str = "1y") -> pd.DataFrame:
    if _tradier.is_available():
        return _tradier.get_price_history(symbol, period)
    return _yf.get_price_history(symbol, period)


def get_spot_price(symbol: str, price_history: pd.DataFrame) -> float:
    if _tradier.is_available():
        return _tradier.get_spot_price(symbol)
    return _yf.get_spot_price(price_history)


def get_gap_info(symbol: str, price_history: pd.DataFrame) -> tuple[Optional[float], bool]:
    if _tradier.is_available():
        return _tradier.get_gap_info(symbol)
    return _yf.get_gap_info(symbol, price_history)


def list_expirations(symbol: str) -> list[str]:
    if _tradier.is_available():
        return _tradier.list_expirations(symbol)
    return _yf.list_expirations(symbol)


def get_option_chain_for_expiration(symbol: str, expiration: str) -> OptionChain:
    if _tradier.is_available():
        return _tradier.get_option_chain_for_expiration(symbol, expiration)
    return _yf.get_option_chain_for_expiration(symbol, expiration)


def pick_expirations_for_targets(symbol: str, targets: tuple) -> dict[str, OptionChain]:
    if _tradier.is_available():
        return _tradier.pick_expirations_for_targets(symbol, targets)
    return _yf.pick_expirations_for_targets(symbol, targets)


def get_vix_history(period: str = "1y", vix_symbol: str = "^VIX", tradier_vix_symbol: str = "VIX") -> pd.DataFrame:
    if _tradier.is_available():
        return _tradier.get_price_history(tradier_vix_symbol, period)
    return _yf.get_price_history(vix_symbol, period)
