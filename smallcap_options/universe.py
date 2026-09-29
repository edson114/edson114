"""Build the scored candidate universe: cheap prefilter over screener
fields, then a deeper per-symbol filter (float shares, $ liquidity) for
the names that survive, capped so a run only makes a bounded number of
extra network calls.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from . import data
from .config import Config
from .data import CandidateQuote


@dataclass
class DeepCandidate:
    quote: CandidateQuote
    float_shares: Optional[float]
    avg_dollar_volume: float
    price_history: pd.DataFrame


def extra_symbols_from_env() -> tuple:
    raw = os.environ.get("SMALLCAP_SYMBOLS", "")
    return tuple(s.strip().upper() for s in raw.split(",") if s.strip())


def cheap_prefilter(quotes: list[CandidateQuote], cfg: Config) -> list[CandidateQuote]:
    """Filter + dedup on fields the screener already returned -- no extra
    network calls. Ranked by relative_volume * |change%|, a cheap proxy
    for "how unusual is today's move" that favors both the CALL side
    (big green gainers) and the PUT side (big red losers)."""
    seen: set[str] = set()
    kept: list[CandidateQuote] = []
    for q in quotes:
        if q.symbol in seen:
            continue
        seen.add(q.symbol)
        if not (cfg.min_price <= q.price <= cfg.max_price):
            continue
        if cfg.max_market_cap and q.market_cap and q.market_cap > cfg.max_market_cap:
            continue
        if q.day_volume < cfg.min_day_volume:
            continue
        if q.relative_volume < cfg.min_relative_volume:
            continue
        if abs(q.change_pct) < cfg.min_abs_change_pct:
            continue
        kept.append(q)
    kept.sort(key=lambda q: q.relative_volume * abs(q.change_pct), reverse=True)
    return kept


def deep_scan(quotes: list[CandidateQuote], cfg: Config) -> list[DeepCandidate]:
    """Per-symbol float-shares + liquidity check -- the only filter
    manually-added watchlist symbols go through, since they skip the
    market-cap/price/rel-volume screen prefilter entirely (the user
    explicitly wants those tracked regardless)."""
    results: list[DeepCandidate] = []
    for q in quotes[: cfg.max_candidates_deep_scanned]:
        float_shares = data.get_float_shares(q.symbol)
        if float_shares is not None and float_shares > cfg.max_float_shares:
            continue
        price_history = data.get_price_history(q.symbol)
        if price_history is None or len(price_history) < 2:
            continue
        avg_dollar_volume = data.get_avg_dollar_volume(price_history)
        if avg_dollar_volume < cfg.min_avg_dollar_volume:
            continue
        results.append(
            DeepCandidate(quote=q, float_shares=float_shares, avg_dollar_volume=avg_dollar_volume, price_history=price_history)
        )
    return results


def get_manual_quotes(symbols: tuple) -> list[CandidateQuote]:
    import sys

    import yfinance as yf

    quotes = []
    for symbol in symbols:
        try:
            info = yf.Ticker(symbol).fast_info
            price = float(info["last_price"])
            prev_close = float(info["previous_close"])
            change_pct = (price / prev_close - 1.0) * 100.0 if prev_close else 0.0
            quotes.append(
                CandidateQuote(
                    symbol=symbol,
                    name=symbol,
                    price=price,
                    change_pct=change_pct,
                    day_volume=int(info.get("last_volume") or 0),
                    avg_volume_3m=float(info.get("three_month_average_volume") or 0.0),
                    market_cap=None,
                    source_query="manual",
                )
            )
        except Exception as exc:
            print(f"Warning: manual symbol '{symbol}' quote failed ({exc}); skipping.", file=sys.stderr)
            continue
    return quotes


def build_universe(cfg: Config, extra_symbols: tuple = ()) -> tuple[list[DeepCandidate], dict]:
    screened = data.get_universe_quotes(cfg.universe_queries, cfg.universe_size_per_query)
    prefiltered = cheap_prefilter(screened, cfg)
    deep = deep_scan(prefiltered, cfg)

    if extra_symbols:
        manual_quotes = get_manual_quotes(extra_symbols)
        already = {c.quote.symbol for c in deep}
        manual_quotes = [q for q in manual_quotes if q.symbol not in already]
        deep.extend(deep_scan(manual_quotes, cfg))

    deep = deep[: cfg.max_candidates_deep_scanned]
    stats = {
        "screened": len(screened),
        "prefiltered": len(prefiltered),
        "deep_scanned": len(deep),
        "manual_symbols": list(extra_symbols),
    }
    return deep, stats
