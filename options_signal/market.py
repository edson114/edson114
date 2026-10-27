"""Every network call the options signal makes, behind one small interface so
the pipeline can run on synthetic data in tests and in --self-test."""

from __future__ import annotations

import datetime as dt
from typing import Optional, Protocol

import pandas as pd

from .bars import to_new_york

RETRY_WAITS = (2.0, 5.0)


class DataFeedError(RuntimeError):
    """The feed answered, but with nothing usable (rate limit, outage)."""


def _retry(fetch, empty_message: str):
    """fetch() until it returns something non-empty: one try plus one per
    entry in RETRY_WAITS, sleeping that long first."""
    import time

    last_error: Optional[Exception] = None
    for wait in (0.0, *RETRY_WAITS):
        if wait:
            time.sleep(wait)
        try:
            got = fetch()
        except Exception as exc:  # network errors get the same retries
            last_error = exc
            continue
        if got is not None and len(got) > 0:
            return got
    raise DataFeedError(f"{empty_message} after {1 + len(RETRY_WAITS)} tries"
                        + (f" (last error: {last_error})" if last_error else ""))


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
        """Yahoo answers a rate-limited request (HTTP 429) with an empty list
        rather than an error, so an empty answer is retried, and a final
        empty one is raised as a data failure, never read as "no options"."""
        from qqq_iron_condor import providers

        return _retry(lambda: providers.list_expirations(symbol), f"no option expirations returned for {symbol}")

    def chain(self, symbol: str, expiration: str, option_type: str) -> pd.DataFrame:
        from qqq_iron_condor import providers

        def fetch():
            oc = providers.get_option_chain_for_expiration(symbol, expiration)
            return oc.calls if option_type == "call" else oc.puts

        return _retry(fetch, f"empty {option_type} chain returned for {symbol} {expiration}")

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
