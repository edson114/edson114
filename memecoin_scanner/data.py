"""Free, keyless data clients: DexScreener (discovery + pairs) and RugCheck (safety).

Every fetcher degrades to an empty result on network or HTTP errors so one
flaky source never kills the scan; the report says which sources answered.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

import requests

DEXSCREENER = "https://api.dexscreener.com"
RUGCHECK = "https://api.rugcheck.xyz/v1"
GECKOTERMINAL = "https://api.geckoterminal.com/api/v2"

_SESSION = requests.Session()
_SESSION.headers.update({"Accept": "application/json", "User-Agent": "memecoin-scanner/1.0"})

# Holder accounts that are not "people": the burn/system owner.
_NON_HOLDER_OWNERS = {"11111111111111111111111111111111", "1nc1nerator11111111111111111111111111111111"}


def _get_json(url: str, timeout: float, params: Optional[dict] = None, retries: int = 2, backoff_s: float = 1.5):
    """GET a JSON document, backing off on 429/5xx. Returns None on failure."""
    for attempt in range(retries + 1):
        try:
            resp = _SESSION.get(url, params=params, timeout=timeout)
        except requests.RequestException:
            resp = None
        if resp is not None and resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                return None
        retryable = resp is None or resp.status_code == 429 or resp.status_code >= 500
        if not retryable or attempt == retries:
            return None
        time.sleep(backoff_s * (attempt + 1))
    return None


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class PairSnapshot:
    chain: str
    token_address: str
    pair_address: str
    dex_id: str
    name: str
    symbol: str
    quote_symbol: str
    price_usd: float
    liquidity_usd: float
    market_cap: float
    volume: Dict[str, float]
    txns: Dict[str, Tuple[int, int]]  # window -> (buys, sells)
    price_change: Dict[str, float]
    pair_created_at: Optional[dt.datetime]
    url: str
    websites: List[str] = field(default_factory=list)
    socials: Dict[str, str] = field(default_factory=dict)  # type -> url
    boosts_active: int = 0
    sources: set = field(default_factory=set)
    # Unique wallets per window from GeckoTerminal: window -> (buyers, sellers).
    wallets: Dict[str, Tuple[int, int]] = field(default_factory=dict)

    def age_hours(self, now: Optional[dt.datetime] = None) -> Optional[float]:
        if self.pair_created_at is None:
            return None
        now = now or dt.datetime.now(dt.timezone.utc)
        return max(0.0, (now - self.pair_created_at).total_seconds() / 3600.0)

    def buy_sell_ratio(self, window: str) -> float:
        buys, sells = self.txns.get(window, (0, 0))
        if sells == 0:
            return float(buys) if buys else 0.0
        return buys / sells

    def wallet_ratio(self, window: str) -> Optional[float]:
        """Unique buyers / unique sellers, or None without wallet data."""
        buyers, sellers = self.wallets.get(window, (0, 0))
        if buyers + sellers == 0:
            return None
        return buyers / sellers if sellers else float(buyers)

    def avg_trade_usd(self, window: str) -> Optional[float]:
        buys, sells = self.txns.get(window, (0, 0))
        if buys + sells == 0:
            return None
        return self.volume.get(window, 0.0) / (buys + sells)

    @property
    def txns_h24(self) -> int:
        buys, sells = self.txns.get("h24", (0, 0))
        return buys + sells


def parse_pair(raw: dict) -> Optional[PairSnapshot]:
    """Turn one DexScreener pair object into a PairSnapshot (None if unusable)."""
    try:
        base = raw.get("baseToken") or {}
        quote = raw.get("quoteToken") or {}
        address = base.get("address")
        if not address:
            return None
        txns = {
            window: (int(v.get("buys") or 0), int(v.get("sells") or 0))
            for window, v in (raw.get("txns") or {}).items()
            if isinstance(v, dict)
        }
        created_ms = raw.get("pairCreatedAt")
        created = (
            dt.datetime.fromtimestamp(created_ms / 1000.0, tz=dt.timezone.utc) if created_ms else None
        )
        info = raw.get("info") or {}
        socials = {}
        for s in info.get("socials") or []:
            if s.get("type") and s.get("url"):
                socials.setdefault(s["type"], s["url"])
        return PairSnapshot(
            chain=raw.get("chainId", ""),
            token_address=address,
            pair_address=raw.get("pairAddress", ""),
            dex_id=raw.get("dexId", ""),
            name=(base.get("name") or "").strip(),
            symbol=(base.get("symbol") or "").strip(),
            quote_symbol=(quote.get("symbol") or "").upper(),
            price_usd=_f(raw.get("priceUsd")),
            liquidity_usd=_f((raw.get("liquidity") or {}).get("usd")),
            market_cap=_f(raw.get("marketCap")) or _f(raw.get("fdv")),
            volume={k: _f(v) for k, v in (raw.get("volume") or {}).items()},
            txns=txns,
            price_change={k: _f(v) for k, v in (raw.get("priceChange") or {}).items()},
            pair_created_at=created,
            url=raw.get("url", ""),
            websites=[w.get("url") for w in info.get("websites") or [] if w.get("url")],
            socials=socials,
            boosts_active=int((raw.get("boosts") or {}).get("active") or 0),
        )
    except (TypeError, ValueError, AttributeError):
        return None


# --- Discovery -------------------------------------------------------------

def discover_token_addresses(chains: Iterable[str], search_queries: Iterable[str], timeout: float):
    """Collect candidate token addresses per chain from several free lists.

    Returns (addresses: {chain: {address: set(source names)}},
             prefetched_pairs: list[PairSnapshot], sources_ok: list[str]).
    """
    chains = set(chains)
    found: Dict[str, Dict[str, set]] = {c: {} for c in chains}
    prefetched: List[PairSnapshot] = []
    sources_ok: List[str] = []

    def add(chain, address, source):
        if chain in chains and address:
            found[chain].setdefault(address, set()).add(source)

    lists = (
        ("dexscreener top boosts", "/token-boosts/top/v1"),
        ("dexscreener latest boosts", "/token-boosts/latest/v1"),
        ("dexscreener new profiles", "/token-profiles/latest/v1"),
        ("dexscreener community takeovers", "/community-takeovers/latest/v1"),
    )
    for source, path in lists:
        data = _get_json(DEXSCREENER + path, timeout)
        if isinstance(data, list):
            sources_ok.append(source)
            for item in data:
                add(item.get("chainId"), item.get("tokenAddress"), source)

    for q in search_queries:
        data = _get_json(DEXSCREENER + "/latest/dex/search", timeout, params={"q": q})
        pairs = (data or {}).get("pairs") if isinstance(data, dict) else None
        if pairs is None:
            continue
        sources_ok.append(f"dexscreener search '{q}'")
        for raw in pairs:
            snap = parse_pair(raw)
            if snap and snap.chain in chains:
                snap.sources.add("dexscreener search")
                prefetched.append(snap)
                add(snap.chain, snap.token_address, "dexscreener search")

    # GeckoTerminal is often rate-limited on shared IPs; one try per list.
    gecko_lists = [(f"geckoterminal trending ({c})", c, f"/networks/{c}/trending_pools") for c in chains]
    if "solana" in chains:  # most-traded graduated pump.fun coins
        gecko_lists.append(("geckoterminal pumpswap most traded", "solana",
                            "/networks/solana/dexes/pumpswap/pools?sort=h24_tx_count_desc"))
    for source, chain, path in gecko_lists:
        data = _get_json(GECKOTERMINAL + path, timeout, retries=0)
        pools = (data or {}).get("data") if isinstance(data, dict) else None
        if not pools:
            continue
        sources_ok.append(source)
        for pool in pools:
            base_id = (((pool.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id", "")
            # ids look like "solana_<mint>"
            if base_id.startswith(chain + "_"):
                add(chain, base_id[len(chain) + 1:], source)

    return found, prefetched, sources_ok


def fetch_pairs(chain: str, addresses: List[str], timeout: float) -> List[PairSnapshot]:
    """All pairs for up to N token addresses, 30 per DexScreener call."""
    out: List[PairSnapshot] = []
    for i in range(0, len(addresses), 30):
        batch = addresses[i:i + 30]
        data = _get_json(f"{DEXSCREENER}/tokens/v1/{chain}/{','.join(batch)}", timeout)
        if not isinstance(data, list):
            continue
        for raw in data:
            snap = parse_pair(raw)
            if snap:
                out.append(snap)
    return out


def parse_wallet_counts(payload) -> Dict[str, Dict[str, Tuple[int, int]]]:
    """GeckoTerminal pools payload -> {pool address: {window: (buyers, sellers)}}."""
    out: Dict[str, Dict[str, Tuple[int, int]]] = {}
    items = (payload or {}).get("data") if isinstance(payload, dict) else None
    if isinstance(items, dict):
        items = [items]
    for item in items or []:
        attrs = item.get("attributes") or {}
        address = attrs.get("address")
        windows = {}
        for window, t in (attrs.get("transactions") or {}).items():
            try:
                windows[window] = (int(t.get("buyers") or 0), int(t.get("sellers") or 0))
            except (TypeError, ValueError, AttributeError):
                continue
        if address and windows:
            out[address] = windows
    return out


def fetch_wallet_counts(chain: str, pool_addresses: List[str], timeout: float,
                        retries: int = 2, backoff_s: float = 4.0) -> Dict[str, Dict[str, Tuple[int, int]]]:
    """Unique buyers/sellers for up to 30 pools per GeckoTerminal call ({} on failure)."""
    out: Dict[str, Dict[str, Tuple[int, int]]] = {}
    pools = [a for a in pool_addresses if a]
    for i in range(0, len(pools), 30):
        batch = pools[i:i + 30]
        payload = _get_json(f"{GECKOTERMINAL}/networks/{chain}/pools/multi/{','.join(batch)}", timeout,
                            retries=retries, backoff_s=backoff_s)
        out.update(parse_wallet_counts(payload))
    return out


def best_pair_per_token(pairs: Iterable[PairSnapshot]) -> Dict[str, PairSnapshot]:
    """Keep the deepest-liquidity pair for each token (where price is actually set)."""
    best: Dict[str, PairSnapshot] = {}
    for p in pairs:
        cur = best.get(p.token_address)
        if cur is None or p.liquidity_usd > cur.liquidity_usd:
            if cur is not None:
                p.sources |= cur.sources
            best[p.token_address] = p
        else:
            cur.sources |= p.sources
    return best


# --- Safety ------------------------------------------------------------------

@dataclass
class SafetyReport:
    available: bool
    mint_authority_revoked: bool = False
    freeze_authority_revoked: bool = False
    rugged: bool = False
    lp_locked_pct: Optional[float] = None
    total_holders: Optional[int] = None
    top10_pct: Optional[float] = None  # pools / AMMs / burn excluded
    insiders_detected: int = 0
    danger_risks: List[str] = field(default_factory=list)
    warn_risks: List[str] = field(default_factory=list)
    launchpad: str = ""


def parse_rugcheck_report(report: dict) -> SafetyReport:
    known = set((report.get("knownAccounts") or {}).keys())
    lp_locked = []
    for m in report.get("markets") or []:
        for key in ("pubkey", "liquidityA", "liquidityB"):
            if m.get(key):
                known.add(m[key])
        lp = m.get("lp") or {}
        if lp.get("lpLockedPct") is not None:
            lp_locked.append((_f(lp.get("lpLockedUSD")), _f(lp.get("lpLockedPct"))))

    holders = []
    for h in report.get("topHolders") or []:
        if h.get("address") in known or h.get("owner") in known or h.get("owner") in _NON_HOLDER_OWNERS:
            continue
        holders.append(_f(h.get("pct")))
    holders.sort(reverse=True)
    top10 = sum(holders[:10]) if report.get("topHolders") is not None else None

    danger, warn = [], []
    for r in report.get("risks") or []:
        label = r.get("name") or "unnamed risk"
        if r.get("value"):
            label = f"{label} ({r['value']})"
        level = (r.get("level") or "").lower()
        if level == "danger":
            danger.append(label)
        elif level == "warn":
            warn.append(label)

    # Weight LP lock by locked USD across markets; fall back to the best market.
    lp_pct = None
    if lp_locked:
        total_usd = sum(u for u, _ in lp_locked)
        lp_pct = (
            sum(u * p for u, p in lp_locked) / total_usd if total_usd > 0 else max(p for _, p in lp_locked)
        )

    holders_total = report.get("totalHolders")
    return SafetyReport(
        available=True,
        mint_authority_revoked=report.get("mintAuthority") is None,
        freeze_authority_revoked=report.get("freezeAuthority") is None,
        rugged=bool(report.get("rugged")),
        lp_locked_pct=lp_pct,
        total_holders=int(holders_total) if holders_total is not None else None,
        top10_pct=top10,
        insiders_detected=int(report.get("graphInsidersDetected") or 0),
        danger_risks=danger,
        warn_risks=warn,
        launchpad=((report.get("launchpad") or {}).get("name") or ""),
    )


def fetch_safety(token_address: str, timeout: float, retries: int = 3, backoff_s: float = 3.0) -> SafetyReport:
    # RugCheck rate-limits bursts; an unanswered check leaves a pick UNVERIFIED,
    # so it gets more patience (3s, 6s, 9s) than the discovery lists.
    report = _get_json(f"{RUGCHECK}/tokens/{token_address}/report", timeout, retries=retries, backoff_s=backoff_s)
    if not isinstance(report, dict) or not report:
        return SafetyReport(available=False)
    try:
        return parse_rugcheck_report(report)
    except (TypeError, ValueError, AttributeError):
        return SafetyReport(available=False)
