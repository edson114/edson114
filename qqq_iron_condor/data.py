"""Market data access: price history, option chains, VIX, and news.

All network calls are isolated here so the rest of the app (indicators,
strategy, report) can be tested with plain DataFrames/dicts and no
network access.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
import yfinance as yf

from .config import Config


@dataclass
class OptionChain:
    expiration: str
    dte: int
    calls: pd.DataFrame
    puts: pd.DataFrame


@dataclass
class Headline:
    source: str
    title: str
    link: str
    published: Optional[str] = None


def get_price_history(symbol: str, period: str = "1y") -> pd.DataFrame:
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval="1d", auto_adjust=False)
    if df.empty:
        raise RuntimeError(f"No price history returned for {symbol}")
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


def get_spot_price(price_history: pd.DataFrame) -> float:
    return float(price_history["Close"].iloc[-1])


def list_expirations(symbol: str) -> list[str]:
    return list(yf.Ticker(symbol).options)


def get_option_chain_for_expiration(symbol: str, expiration: str) -> OptionChain:
    ticker = yf.Ticker(symbol)
    chain = ticker.option_chain(expiration)
    exp_date = dt.datetime.strptime(expiration, "%Y-%m-%d").date()
    dte = (exp_date - dt.date.today()).days
    return OptionChain(expiration=expiration, dte=dte, calls=chain.calls, puts=chain.puts)


def pick_expirations_for_targets(symbol: str, targets: tuple) -> dict[str, OptionChain]:
    """Pick the best available expiration for each (label, min_dte, max_dte)
    target window, preferring the one closest to the midpoint of the window."""
    available = list_expirations(symbol)
    today = dt.date.today()
    parsed = [(e, (dt.datetime.strptime(e, "%Y-%m-%d").date() - today).days) for e in available]

    picks: dict[str, OptionChain] = {}
    for label, min_dte, max_dte in targets:
        midpoint = (min_dte + max_dte) / 2.0
        in_window = [(e, d) for e, d in parsed if min_dte <= d <= max_dte]
        candidates = in_window if in_window else parsed
        if not candidates:
            continue
        best_exp, best_dte = min(candidates, key=lambda item: abs(item[1] - midpoint))
        picks[label] = get_option_chain_for_expiration(symbol, best_exp)
    return picks


def get_vix_history(period: str = "1y") -> pd.DataFrame:
    return get_price_history("^VIX", period=period)


def get_news(feeds: tuple, max_per_feed: int = 8) -> list[Headline]:
    import feedparser

    headlines: list[Headline] = []
    for source, url in feeds:
        try:
            parsed = feedparser.parse(url)
        except Exception:
            continue
        for entry in parsed.entries[:max_per_feed]:
            headlines.append(
                Headline(
                    source=source,
                    title=getattr(entry, "title", "").strip(),
                    link=getattr(entry, "link", ""),
                    published=getattr(entry, "published", None),
                )
            )
    return headlines
