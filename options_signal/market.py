"""Every network call the options signal makes, behind one small interface so
the pipeline can run on synthetic data in tests and in --self-test."""

from __future__ import annotations

import datetime as dt
from typing import Optional, Protocol

import pandas as pd

from .bars import to_new_york


class Market(Protocol):
    def bars(self, symbol: str, period: str) -> pd.DataFrame: ...
    def expirations(self, symbol: str) -> list: ...
    def chain(self, symbol: str, expiration: str, option_type: str) -> pd.DataFrame: ...
    def vix(self) -> Optional[float]: ...
    def earnings(self, symbol: str) -> Optional[dt.date]: ...


class LiveMarket:
    """yfinance 5-minute bars (60 days is Yahoo's cap); option chains through
    the repo's provider dispatch (Tradier when TRADIER_TOKEN is set, else
    yfinance)."""

    def bars(self, symbol: str, period: str) -> pd.DataFrame:
        import yfinance as yf

        df = yf.Ticker(symbol).history(period=period, interval="5m", auto_adjust=False, prepost=False)
        if df.empty:
            raise RuntimeError(f"no 5-minute bars for {symbol}")
        return to_new_york(df)

    def expirations(self, symbol: str) -> list:
        from qqq_iron_condor import providers

        return providers.list_expirations(symbol)

    def chain(self, symbol: str, expiration: str, option_type: str) -> pd.DataFrame:
        from qqq_iron_condor import providers

        oc = providers.get_option_chain_for_expiration(symbol, expiration)
        return oc.calls if option_type == "call" else oc.puts

    def vix(self) -> Optional[float]:
        from qqq_iron_condor import providers

        try:
            return float(providers.get_vix_history("5d")["Close"].iloc[-1])
        except Exception:
            return None

    def earnings(self, symbol: str) -> Optional[dt.date]:
        """One retry: Yahoo's calendar call fails now and then on a stale
        session crumb ("401 Invalid Crumb") and succeeds on the next try.
        yfinance prints those errors itself, so its output is swallowed."""
        import contextlib
        import io

        from smallcap_options.data import get_next_earnings_date

        for _ in range(2):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                found = get_next_earnings_date(symbol)
            if found is not None:
                return found
        return None
