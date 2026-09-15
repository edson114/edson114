"""Headline catalyst flagging: cheap keyword scan over free RSS feeds.

This is intentionally simple -- there's no paid economic-calendar feed
wired in. It surfaces headlines that mention known market-moving topics
(Fed policy, inflation prints, jobs data, mega-cap earnings, geopolitics)
so the report can warn about elevated event risk before you open a
premium-selling position.
"""

from dataclasses import dataclass

from .data import Headline


@dataclass
class CatalystHit:
    headline: Headline
    matched_keywords: list[str]


def flag_catalysts(headlines: list[Headline], keywords: tuple) -> list[CatalystHit]:
    hits = []
    for h in headlines:
        title_lower = h.title.lower()
        matched = [kw.strip() for kw in keywords if kw.strip() in title_lower]
        if matched:
            hits.append(CatalystHit(headline=h, matched_keywords=matched))
    return hits
