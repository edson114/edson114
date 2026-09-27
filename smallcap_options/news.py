"""Catalyst detection: prefers a candidate's own per-symbol news (far more
precise), falls back to keyword-matching broad market/press-release feeds
by company name/symbol mention. Neither is a paid, curated catalyst feed
-- this is a best-effort scan over free data, not a guarantee that a real
catalyst is (or isn't) behind a given move.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .data import Headline

_CORPORATE_SUFFIXES = {
    "inc", "inc.", "corp", "corp.", "corporation", "co", "co.", "ltd", "ltd.",
    "llc", "holdings", "holding", "group", "plc", "sa", "nv", "ag",
    "therapeutics", "pharmaceuticals", "pharma", "technologies", "technology",
    "systems", "solutions", "industries", "resources", "capital", "acquisition",
}


@dataclass
class CatalystHit:
    headline: Headline
    matched_keywords: list


def flag_catalysts(headlines: list[Headline], keywords: tuple) -> list[CatalystHit]:
    hits = []
    for h in headlines:
        title_lower = h.title.lower()
        matched = [kw.strip() for kw in keywords if kw.strip() in title_lower]
        if matched:
            hits.append(CatalystHit(headline=h, matched_keywords=matched))
    return hits


def find_catalyst_for_symbol(
    symbol: str,
    name: str,
    ticker_headlines: list[Headline],
    market_headlines: list[Headline],
    keywords: tuple,
) -> list[CatalystHit]:
    """Combine the symbol's own news (kept as-is -- it's already about
    this ticker) with keyword hits from broad market/press-release feeds
    whose title mentions the symbol or the most distinctive word in the
    company name (the longest word that isn't a generic corporate suffix
    like "Inc"/"Holdings"/"Therapeutics" -- those are common enough across
    unrelated small caps to cause false catalyst matches)."""
    hits = [CatalystHit(headline=h, matched_keywords=["ticker-specific news"]) for h in ticker_headlines]

    name_words = [w.strip(".,").lower() for w in (name or "").split()]
    distinctive = [w for w in name_words if w not in _CORPORATE_SUFFIXES and len(w) > 3]
    name_token = max(distinctive, key=len) if distinctive else ""
    symbol_pattern = re.compile(rf"\b{re.escape(symbol.lower())}\b")

    for h in market_headlines:
        title_lower = h.title.lower()
        mentions = bool(symbol_pattern.search(title_lower)) or (name_token and name_token in title_lower)
        if not mentions:
            continue
        matched = [kw.strip() for kw in keywords if kw.strip() in title_lower]
        if matched:
            hits.append(CatalystHit(headline=h, matched_keywords=matched))
    return hits
