"""Hot-narrative detection: themes several active coins share right now.

Meme coins explode in clusters. In the Nov 2026 review, most of the
biggest catchable runs were GTA 6 coins (GTA6, GTAVI, WASTED, Leonida,
GATO, BAWSAQ...) running together for days. This finds such clusters
with no outside data: it pulls words out of every priced coin's name,
symbol, description, social handles and website, and calls a word "hot"
when enough separate coins carrying it have real 24h volume and most of
them are up on the day.

It's a heuristic: two coins sharing a word aren't always one narrative,
and a theme with no shared word (WASTED and Leonida are GTA references
without "gta" in the name) is only caught through words that do overlap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Set

from .data import PairSnapshot

STOPWORDS = {
    # generic coin / chain / launchpad words
    "coin", "coins", "token", "tokens", "the", "and", "for", "sol", "solana", "meme", "memes", "memecoin",
    "pump", "pumpfun", "fun", "official", "community", "inu", "real", "first", "new", "best", "only", "just",
    "you", "your", "our", "with", "this", "that", "are", "was", "has", "all", "not", "but", "its", "get",
    "launch", "launched", "chain", "crypto", "degen", "moon", "holders", "holder", "buy", "sell", "ticker",
    "ca", "dev", "team", "cto", "takeover", "project", "world", "people", "money", "time", "day", "web",
    # url / social noise
    "https", "http", "www", "com", "xyz", "app", "site", "online", "org", "net", "lol", "io", "live",
    "status", "communities", "twitter", "telegram", "discord", "home", "index", "html", "vercel", "info",
}

_WORD = re.compile(r"[a-z]+")


def coin_words(p: PairSnapshot) -> Set[str]:
    """Theme words for one coin (letters only, 3+ chars, no stopwords)."""
    text = " ".join([p.name, p.symbol, p.description] + list(p.socials.values()) + list(p.websites))
    return {w for w in _WORD.findall(text.lower()) if len(w) >= 3 and w not in STOPWORDS}


def _related(a: str, b: str) -> bool:
    """Same word, or one a short extension of the other ('gta' / 'gtavi', 'cat' / 'cats')."""
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 3 and long_.startswith(short) and len(long_) - len(short) <= 3


@dataclass
class Theme:
    word: str
    coins: Set[str] = field(default_factory=set)  # token addresses
    symbols: List[str] = field(default_factory=list)
    volume_h24: float = 0.0
    up_count: int = 0

    @property
    def size(self) -> int:
        return len(self.coins)


def find_hot_themes(pairs: Iterable[PairSnapshot], min_coins: int = 3, min_volume_h24: float = 1_000_000.0,
                    min_coin_volume_h24: float = 50_000.0, min_up_share: float = 0.6) -> Dict[str, Theme]:
    """Hot themes by word. A coin counts toward a theme only with real volume."""
    active = [p for p in pairs if p.volume.get("h24", 0.0) >= min_coin_volume_h24]
    words_by_coin = {p.token_address: coin_words(p) for p in active}
    all_words = sorted({w for ws in words_by_coin.values() for w in ws}, key=len)

    themes: Dict[str, Theme] = {}
    for word in all_words:
        t = Theme(word)
        for p in active:
            if any(_related(word, w) for w in words_by_coin[p.token_address]):
                t.coins.add(p.token_address)
                t.symbols.append(p.symbol)
                t.volume_h24 += p.volume.get("h24", 0.0)
                t.up_count += 1 if p.price_change.get("h24", 0.0) > 0 else 0
        if t.size >= min_coins and t.volume_h24 >= min_volume_h24 and t.up_count / t.size >= min_up_share:
            themes[word] = t

    # Keep the shortest root of each related group ('gta' over 'gtavi') so one
    # narrative isn't counted twice.
    kept: Dict[str, Theme] = {}
    for word in sorted(themes, key=len):
        if not any(_related(word, k) for k in kept):
            kept[word] = themes[word]
    return kept


def theme_for(p: PairSnapshot, themes: Dict[str, Theme]) -> Optional[Theme]:
    """The biggest hot theme this coin belongs to, if any."""
    matches = [t for t in themes.values() if p.token_address in t.coins]
    if not matches:
        return None
    return max(matches, key=lambda t: (t.size, t.volume_h24))
