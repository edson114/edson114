"""Market data access: screener universe, price/intraday history, option
chains, float shares, and news.

All network calls are isolated here so the rest of the app (universe
filtering, direction scoring, strategy, report) can be tested with plain
DataFrames/dicts and no network access. Every function here is
best-effort: a single symbol's data failing (delisted, illiquid, no
options listed, feed timeout) must not take down the whole scan, so
failures are caught and represented as None/empty rather than raised,
mirroring qqq_iron_condor's news-feed handling.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import yfinance as yf


@dataclass
class CandidateQuote:
    """One row from a screener result -- cheap fields only, no extra
    network call yet."""

    symbol: str
    name: str
    price: float
    change_pct: float
    day_volume: int
    avg_volume_3m: float
    market_cap: Optional[float]
    source_query: str

    @property
    def relative_volume(self) -> float:
        if not self.avg_volume_3m or self.avg_volume_3m <= 0:
            return 0.0
        return self.day_volume / self.avg_volume_3m


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


def get_universe_quotes(queries: tuple, size_per_query: int = 100) -> list[CandidateQuote]:
    """Pull each predefined screener query and flatten to CandidateQuote
    rows. A single query failing (Yahoo screener endpoint is unofficial
    and occasionally rate-limits or changes shape) is logged and skipped
    rather than aborting the whole scan."""
    import sys

    quotes: list[CandidateQuote] = []
    for query in queries:
        try:
            result = yf.screen(query, size=size_per_query)
        except Exception as exc:
            print(f"Warning: screener query '{query}' failed ({exc}); skipping.", file=sys.stderr)
            continue

        for row in (result or {}).get("quotes", []):
            try:
                symbol = row.get("symbol")
                price = row.get("regularMarketPrice")
                if not symbol or price is None:
                    continue
                quotes.append(
                    CandidateQuote(
                        symbol=symbol,
                        name=row.get("shortName") or row.get("longName") or symbol,
                        price=float(price),
                        change_pct=float(row.get("regularMarketChangePercent") or 0.0),
                        day_volume=int(row.get("regularMarketVolume") or 0),
                        avg_volume_3m=float(row.get("averageDailyVolume3Month") or 0.0),
                        market_cap=(float(row["marketCap"]) if row.get("marketCap") is not None else None),
                        source_query=query,
                    )
                )
            except (TypeError, ValueError):
                continue
    return quotes


def get_float_shares(symbol: str) -> Optional[float]:
    try:
        info = yf.Ticker(symbol).get_info()
    except Exception:
        return None
    value = info.get("floatShares")
    return float(value) if value is not None else None


def get_price_history(symbol: str, period: str = "2mo") -> Optional[pd.DataFrame]:
    try:
        df = yf.Ticker(symbol).history(period=period, interval="1d", auto_adjust=False)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


def get_avg_dollar_volume(price_history: pd.DataFrame, window: int = 20) -> float:
    tail = price_history.tail(window)
    if tail.empty:
        return 0.0
    return float((tail["Close"] * tail["Volume"]).mean())


def get_intraday_history(symbol: str, interval: str = "5m", period: str = "1d") -> Optional[pd.DataFrame]:
    try:
        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    df.index = pd.to_datetime(df.index).tz_localize(None)
    return df


def list_expirations(symbol: str) -> list[str]:
    try:
        return list(yf.Ticker(symbol).options)
    except Exception:
        return []


def get_option_chain_for_expiration(symbol: str, expiration: str) -> Optional[OptionChain]:
    try:
        chain = yf.Ticker(symbol).option_chain(expiration)
    except Exception:
        return None
    exp_date = dt.datetime.strptime(expiration, "%Y-%m-%d").date()
    dte = (exp_date - dt.date.today()).days
    return OptionChain(expiration=expiration, dte=dte, calls=chain.calls, puts=chain.puts)


def pick_expiration_within_dte(symbol: str, max_dte: int) -> Optional[OptionChain]:
    """Nearest listed expiration at/under max_dte (short-dated, Temiz-style
    weekly), falling back to the single nearest expiration if every listed
    one already exceeds max_dte (some small caps only list monthlies)."""
    available = list_expirations(symbol)
    if not available:
        return None
    today = dt.date.today()
    parsed = [(e, (dt.datetime.strptime(e, "%Y-%m-%d").date() - today).days) for e in available]
    parsed = [(e, d) for e, d in parsed if d >= 0]
    if not parsed:
        return None

    in_window = [(e, d) for e, d in parsed if d <= max_dte]
    best_exp, _ = min(in_window or parsed, key=lambda item: item[1])
    return get_option_chain_for_expiration(symbol, best_exp)


def get_next_earnings_date(symbol: str) -> Optional[dt.date]:
    """Best-effort nearest upcoming earnings date, for an IV-crush/gap-risk
    caution flag. Returns None if unavailable -- small caps frequently
    don't have a confirmed date listed."""
    try:
        calendar = yf.Ticker(symbol).get_calendar()
    except Exception:
        return None
    if not isinstance(calendar, dict):
        return None
    dates = calendar.get("Earnings Date")
    if not dates:
        return None
    try:
        candidates = [d if isinstance(d, dt.date) else dt.date.fromisoformat(str(d)) for d in dates]
        upcoming = [d for d in candidates if d >= dt.date.today()]
        return min(upcoming) if upcoming else None
    except (TypeError, ValueError):
        return None


def get_ticker_news(symbol: str, max_items: int = 10) -> list[Headline]:
    """Per-symbol news, far more precise than keyword-matching broad
    market feeds. Defensive about shape: yfinance's news payload has
    changed format across versions and isn't guaranteed stable."""
    try:
        items = yf.Ticker(symbol).get_news(count=max_items)
    except Exception:
        return []

    headlines: list[Headline] = []
    for item in items or []:
        try:
            content = item.get("content", item) if isinstance(item, dict) else {}
            title = content.get("title") or item.get("title")
            link = None
            canonical = content.get("canonicalUrl")
            if isinstance(canonical, dict):
                link = canonical.get("url")
            link = link or content.get("link") or item.get("link") or ""
            published = content.get("pubDate") or content.get("displayTime") or item.get("providerPublishTime")
            if title:
                headlines.append(Headline(source=f"Yahoo Finance ({symbol})", title=str(title).strip(), link=str(link), published=str(published) if published else None))
        except Exception:
            continue
    return headlines


def get_news(feeds: tuple, max_per_feed: int = 15) -> list[Headline]:
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
