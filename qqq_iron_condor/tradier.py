"""Tradier-backed market data: real bid/ask and broker-computed greeks.

Activated automatically when the TRADIER_TOKEN environment variable is
set (see providers.py for the dispatch). Unlike the free yfinance path,
Tradier returns delta/gamma/theta/vega computed server-side (via ORATS)
alongside every quote -- this removes the whole class of "implied
volatility hasn't populated across the chain yet" artifacts that
plagued the yfinance-only path in the first several minutes after the
open, since we're no longer deriving delta ourselves from a possibly
stale per-contract IV field.

Get a free developer sandbox token (15-minute delayed data, no funded
brokerage account required) at https://tradier.com, or a production
token from a funded/linked account for real-time data.

Environment variables:
  TRADIER_TOKEN     -- bearer token (required to activate this provider)
  TRADIER_BASE_URL  -- API base, defaults to production
                       (https://api.tradier.com/v1). Set to
                       https://sandbox.tradier.com/v1 if you're using a
                       sandbox token.

Caveat: symbol conventions for indices (VIX) are inferred from Tradier's
documented conventions but not verified against a live account in this
codebase -- if get_vix_history() fails with a 4xx, check the correct
symbol for your account/market data plan and adjust Config accordingly.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import Optional

import pandas as pd
import requests

from .data import Headline, OptionChain

DEFAULT_BASE_URL = "https://api.tradier.com/v1"
REQUEST_TIMEOUT = 20


def is_available() -> bool:
    return bool(os.environ.get("TRADIER_TOKEN"))


def _base_url() -> str:
    return os.environ.get("TRADIER_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _headers() -> dict:
    token = os.environ.get("TRADIER_TOKEN")
    if not token:
        raise RuntimeError("TRADIER_TOKEN is not set")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _get(path: str, params: dict) -> dict:
    resp = requests.get(f"{_base_url()}{path}", headers=_headers(), params=params, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _as_list(value):
    """Tradier collapses a single-item array to a bare object/string in
    JSON responses -- normalize to a list either way."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _period_to_start_date(period: str) -> dt.date:
    period = period.strip().lower()
    today = dt.date.today()
    try:
        if period.endswith("mo"):
            months = int(period[:-2])
            return today - dt.timedelta(days=months * 31)
        if period.endswith("y"):
            years = int(period[:-1])
            return today - dt.timedelta(days=years * 366)
        if period.endswith("d"):
            days = int(period[:-1])
            return today - dt.timedelta(days=days)
    except ValueError:
        pass
    return today - dt.timedelta(days=366)  # fallback: ~1 year


def get_quote(symbol: str) -> dict:
    data = _get("/markets/quotes", {"symbols": symbol})
    quotes = _as_list((data.get("quotes") or {}).get("quote"))
    if not quotes:
        raise RuntimeError(f"No quote returned for {symbol}")
    return quotes[0]


def get_price_history(symbol: str, period: str = "1y") -> pd.DataFrame:
    start = _period_to_start_date(period)
    data = _get("/markets/history", {
        "symbol": symbol,
        "interval": "daily",
        "start": start.isoformat(),
        "end": dt.date.today().isoformat(),
    })
    history = data.get("history")
    days = _as_list(history.get("day")) if history else []
    if not days:
        raise RuntimeError(f"No price history returned for {symbol}")

    df = pd.DataFrame(days)
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date").sort_index()
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})
    return df[["Open", "High", "Low", "Close", "Volume"]].astype(float)


def get_spot_price(symbol: str) -> float:
    return float(get_quote(symbol)["last"])


def get_gap_info(symbol: str) -> tuple[Optional[float], bool]:
    """Tradier's quote endpoint always returns a live (or, on a sandbox
    token, 15-minute-delayed) last price and the prior session's close,
    so -- unlike the yfinance path -- this is always a confirmed reading,
    never a pre-market approximation."""
    try:
        quote = get_quote(symbol)
        last_price = float(quote["last"])
        prev_close = float(quote["prevclose"])
    except Exception:
        return None, False
    if prev_close <= 0:
        return None, False
    return (last_price / prev_close - 1.0) * 100.0, True


def list_expirations(symbol: str) -> list[str]:
    data = _get("/markets/options/expirations", {"symbol": symbol, "includeAllRoots": "true", "strikes": "false"})
    expirations = data.get("expirations")
    return _as_list(expirations.get("date")) if expirations else []


def _option_row(opt: dict) -> dict:
    greeks = opt.get("greeks") or {}
    return {
        "strike": float(opt["strike"]),
        "bid": float(opt.get("bid") or 0.0),
        "ask": float(opt.get("ask") or 0.0),
        "lastPrice": float(opt.get("last") or 0.0),
        "impliedVolatility": float(greeks.get("mid_iv") or 0.0),
        "delta": float(greeks["delta"]) if greeks.get("delta") is not None else None,
    }


def get_option_chain_for_expiration(symbol: str, expiration: str) -> OptionChain:
    data = _get("/markets/options/chains", {"symbol": symbol, "expiration": expiration, "greeks": "true"})
    options = data.get("options")
    contracts = _as_list(options.get("option")) if options else []

    calls = [_option_row(o) for o in contracts if o.get("option_type") == "call"]
    puts = [_option_row(o) for o in contracts if o.get("option_type") == "put"]
    if not calls or not puts:
        raise RuntimeError(f"Incomplete option chain for {symbol} {expiration}")

    exp_date = dt.datetime.strptime(expiration, "%Y-%m-%d").date()
    dte = (exp_date - dt.date.today()).days
    return OptionChain(
        expiration=expiration,
        dte=dte,
        calls=pd.DataFrame(calls),
        puts=pd.DataFrame(puts),
    )


def pick_expirations_for_targets(symbol: str, targets: tuple) -> dict[str, OptionChain]:
    """Same selection logic as data.pick_expirations_for_targets, against
    Tradier's expiration list and chain endpoints instead of yfinance's."""
    available = list_expirations(symbol)
    today = dt.date.today()
    parsed = [(e, (dt.datetime.strptime(e, "%Y-%m-%d").date() - today).days) for e in available]

    picks: dict[str, OptionChain] = {}
    for target in targets:
        midpoint = (target.min_dte + target.max_dte) / 2.0
        in_window = [(e, d) for e, d in parsed if target.min_dte <= d <= target.max_dte]
        if not in_window and not target.allow_fallback:
            continue
        candidates = in_window if in_window else parsed
        if not candidates:
            continue
        best_exp, best_dte = min(candidates, key=lambda item: abs(item[1] - midpoint))
        picks[target.label] = get_option_chain_for_expiration(symbol, best_exp)
    return picks


def get_news(feeds: tuple, max_per_feed: int = 8) -> list[Headline]:
    """Tradier has no news feed of its own -- news stays on the free RSS
    scan regardless of which market-data provider is active."""
    from .data import get_news as _rss_get_news

    return _rss_get_news(feeds, max_per_feed)
