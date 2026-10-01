"""Daily price history via yfinance, plus a synthetic generator for
offline self-tests. All network access lives here."""

from __future__ import annotations

import os
import sys
from typing import Optional

import numpy as np
import pandas as pd

from .config import DEFAULT_UNIVERSE


def load_universe(cli_symbols: Optional[str] = None) -> tuple[str, ...]:
    raw = cli_symbols or os.environ.get("KOTEGAWA_SYMBOLS", "")
    symbols = tuple(s.strip().upper() for s in raw.split(",") if s.strip())
    return symbols or DEFAULT_UNIVERSE


def get_histories(symbols: tuple[str, ...], period: str) -> dict[str, pd.DataFrame]:
    """One batched download. A symbol that fails or comes back empty is
    dropped with a warning rather than aborting the run."""
    import yfinance as yf

    raw = yf.download(
        list(symbols), period=period, interval="1d", auto_adjust=True,
        group_by="ticker", progress=False, threads=True,
    )
    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        try:
            df = raw[symbol] if isinstance(raw.columns, pd.MultiIndex) else raw
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        except (KeyError, TypeError):
            df = None
        if df is None or df.empty:
            print(f"Warning: no data for {symbol}; skipping.", file=sys.stderr)
            continue
        df.index = pd.to_datetime(df.index).tz_localize(None)
        out[symbol] = df
    return out


def get_benchmark(symbol: str, period: str) -> Optional[pd.Series]:
    histories = get_histories((symbol,), period)
    return histories[symbol]["Close"] if symbol in histories else None


def synthetic_histories(n_symbols: int = 12, n_days: int = 500, seed: int = 7) -> dict[str, pd.DataFrame]:
    """Random walks with occasional sharp sell-offs that partially recover --
    enough structure to exercise every code path offline."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    out = {}
    for i in range(n_symbols):
        rets = rng.normal(0.0004, 0.015, n_days)
        for start in rng.choice(np.arange(60, n_days - 20), size=4, replace=False):
            rets[start:start + 4] -= 0.05  # panic
            rets[start + 4:start + 12] += 0.018  # rebound
        close = 100 * np.exp(np.cumsum(rets))
        open_ = close * (1 + rng.normal(0, 0.006, n_days))
        high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n_days)))
        low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n_days)))
        volume = rng.integers(2_000_000, 6_000_000, n_days)
        out[f"SYN{i:02d}"] = pd.DataFrame(
            {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=dates
        )
    return out
