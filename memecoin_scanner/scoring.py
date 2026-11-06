"""Gates and the 0-100 score.

Score components (weights sum to 100):
  momentum      25  1h and 6h price change in a "running, not blown off" band
  buy_pressure  20  buy/sell transaction ratio over 1h and 6h, discounted
                    when the average trade is bot-sized
  acceleration  15  last hour's volume vs the 6h hourly average (+ last 5m)
  liquidity     15  liquidity/market-cap ratio and absolute depth
  upside_room   10  smaller market cap = more room to multiply
  safety        15  holder count, top-10 concentration, LP locked (RugCheck)
Penalties then come off for wash-trading-sized volume, bot micro-trades, an
already-15x day, a falling 5m candle, very new pairs, missing socials, RugCheck warnings and insider clusters.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import Config
from .data import PairSnapshot, SafetyReport
from .entry import EntryPlan


def _ramp(x: float, lo: float, hi: float) -> float:
    """0 at/below lo, 1 at/above hi, linear between."""
    if hi == lo:
        return 1.0 if x >= hi else 0.0
    return min(1.0, max(0.0, (x - lo) / (hi - lo)))


def _band(x: float, zero_lo: float, full_lo: float, full_hi: float, zero_hi: float) -> float:
    """Trapezoid: 0 outside [zero_lo, zero_hi], 1 inside [full_lo, full_hi]."""
    if x <= zero_lo or x >= zero_hi:
        return 0.0
    if x < full_lo:
        return (x - zero_lo) / (full_lo - zero_lo)
    if x > full_hi:
        return (zero_hi - x) / (zero_hi - full_hi)
    return 1.0


def _log10(x: float) -> float:
    return math.log10(x) if x > 0 else 0.0


@dataclass
class Scored:
    pair: PairSnapshot
    safety: Optional[SafetyReport] = None
    components: Dict[str, float] = field(default_factory=dict)  # name -> points earned
    penalties: List[tuple] = field(default_factory=list)  # (points, reason)
    flags: List[str] = field(default_factory=list)  # informational risk notes
    reasons: List[str] = field(default_factory=list)  # why it ranks
    rejected: Optional[str] = None
    entry: Optional[EntryPlan] = None

    @property
    def score(self) -> float:
        raw = sum(self.components.values()) - sum(p for p, _ in self.penalties)
        return max(0.0, min(100.0, raw))


# --- Gates -------------------------------------------------------------------

def market_gate(p: PairSnapshot, cfg: Config, now: Optional[dt.datetime] = None) -> Optional[str]:
    """Reason the pair is rejected on market data alone, or None if it passes."""
    if p.symbol.upper() in cfg.excluded_symbols:
        return "major / stable / impersonated ticker"
    if p.quote_symbol not in cfg.allowed_quote_symbols:
        return f"quoted in {p.quote_symbol or '?'}"
    if p.liquidity_usd < cfg.min_liquidity_usd:
        return f"liquidity ${p.liquidity_usd:,.0f} < ${cfg.min_liquidity_usd:,.0f}"
    if p.market_cap < cfg.min_market_cap:
        return f"market cap ${p.market_cap:,.0f} below floor"
    if p.market_cap > cfg.max_market_cap:
        return f"market cap ${p.market_cap:,.0f} above ceiling (little meme upside left)"
    if p.volume.get("h24", 0.0) < cfg.min_volume_h24:
        return "24h volume too thin"
    if p.txns_h24 < cfg.min_txns_h24:
        return "too few 24h trades"
    age = p.age_hours(now)
    if age is not None and age < cfg.min_age_hours:
        return f"pair only {age * 60:.0f} min old (sniper zone)"
    if p.price_change.get("h1", 0.0) < cfg.max_drop_h1_pct:
        return f"dumping: {p.price_change['h1']:+.0f}% in 1h"
    if p.price_change.get("h6", 0.0) < cfg.max_drop_h6_pct:
        return f"dumping: {p.price_change['h6']:+.0f}% in 6h"
    return None


def safety_gate(s: SafetyReport, cfg: Config) -> Optional[str]:
    """Reason the token is rejected on contract/holder safety, or None."""
    if not s.available:
        return None  # unverified tokens are scored, not rejected (flagged instead)
    if s.rugged:
        return "RugCheck marks it rugged"
    if not s.mint_authority_revoked:
        return "mint authority not revoked (dev can print supply)"
    if not s.freeze_authority_revoked:
        return "freeze authority not revoked (dev can freeze your tokens)"
    if s.danger_risks:
        return "RugCheck danger: " + "; ".join(s.danger_risks[:3])
    if s.top10_pct is not None and s.top10_pct > cfg.max_top10_holder_pct:
        return f"top-10 wallets hold {s.top10_pct:.0f}% of supply"
    return None


# --- Score -------------------------------------------------------------------

def score_market(p: PairSnapshot, cfg: Config, now: Optional[dt.datetime] = None) -> Scored:
    """All components except safety, plus market penalties."""
    sc = Scored(pair=p)
    h1 = p.price_change.get("h1", 0.0)
    h6 = p.price_change.get("h6", 0.0)
    h24 = p.price_change.get("h24", 0.0)

    mom = 0.5 * _band(h1, -10, 5, 60, 200) + 0.5 * _band(h6, -20, 15, 250, 1000)
    sc.components["momentum"] = 25 * mom
    if mom >= 0.6 and h1 > 0 and h6 > 0:
        label = "strong momentum" if h1 >= 15 else "trending up"
        sc.reasons.append(f"{label}: {h1:+.0f}% 1h, {h6:+.0f}% 6h")
    m5 = p.price_change.get("m5", 0.0)
    if m5 <= cfg.falling_m5_pct:
        # 4 points at -10%, growing 0.3/pt, capped at 10.
        pts = min(10.0, 4.0 + 0.3 * (cfg.falling_m5_pct - m5))
        sc.penalties.append((pts, f"falling right now ({m5:+.0f}% in 5m): wait for it to base"))

    r1, r6 = p.buy_sell_ratio("h1"), p.buy_sell_ratio("h6")
    bp = 0.6 * _ramp(r1, 0.9, 1.6) + 0.4 * _ramp(r6, 0.9, 1.6)
    # DexScreener counts trades, not wallets: volume bots spraying $5 buys
    # fake "buy pressure". Discount it when the average trade is tiny.
    avg_trade = p.avg_trade_usd("h6")
    if avg_trade is not None:
        bp *= 0.3 + 0.7 * _ramp(avg_trade, cfg.bot_avg_trade_usd, cfg.organic_avg_trade_usd)
        if avg_trade < cfg.bot_avg_trade_usd + 5:
            sc.penalties.append((6, f"average trade only ${avg_trade:.0f}: bot micro-trades inflate buys and volume"))
    sc.components["buy_pressure"] = 20 * bp
    if bp >= 0.6:
        sc.reasons.append(f"buyers outnumber sellers ({r1:.2f}x 1h, {r6:.2f}x 6h, avg trade ${avg_trade or 0:.0f})")

    v1, v6, v5m = p.volume.get("h1", 0.0), p.volume.get("h6", 0.0), p.volume.get("m5", 0.0)
    accel_h1 = v1 / (v6 / 6) if v6 > 0 else 0.0
    accel_m5 = v5m / (v1 / 12) if v1 > 0 else 0.0
    acc = 0.7 * _ramp(accel_h1, 0.8, 2.0) + 0.3 * _ramp(accel_m5, 0.8, 2.0)
    sc.components["acceleration"] = 15 * acc
    if accel_h1 >= 1.5:
        sc.reasons.append(f"volume accelerating: last hour {accel_h1:.1f}x the 6h hourly pace")

    liq_ratio = p.liquidity_usd / p.market_cap if p.market_cap > 0 else 0.0
    liq = 0.6 * _ramp(liq_ratio, 0.03, 0.12) + 0.4 * _ramp(_log10(p.liquidity_usd), _log10(30_000), _log10(500_000))
    sc.components["liquidity"] = 15 * liq
    if liq_ratio >= 0.10:
        sc.reasons.append(f"healthy liquidity ({liq_ratio:.0%} of market cap)")
    elif liq_ratio < 0.04:
        sc.flags.append(f"thin liquidity ({liq_ratio:.1%} of market cap): big slippage on exit")

    room = 1.0 - _ramp(_log10(p.market_cap), _log10(300_000), _log10(30_000_000))
    sc.components["upside_room"] = 10 * room
    if p.market_cap <= 3_000_000:
        sc.reasons.append(f"small cap (${p.market_cap / 1e6:.2f}M): room to multiply")

    # Market penalties.
    if p.liquidity_usd > 0 and p.volume.get("h24", 0.0) / p.liquidity_usd > cfg.wash_volume_liquidity_ratio:
        sc.penalties.append((10, "24h volume >40x liquidity: likely bot/wash volume"))
    if h24 > cfg.late_entry_h24_pct:
        sc.penalties.append((8, f"already {h24:+,.0f}% in 24h: late entry, high pullback risk"))
    age = p.age_hours(now)
    if age is not None and age < cfg.young_age_hours:
        sc.penalties.append((5, f"pair is {age:.1f}h old: no history, rug risk highest"))
    if not p.socials and not p.websites:
        sc.penalties.append((5, "no website or socials listed"))
    elif "twitter" not in p.socials:
        sc.flags.append("no X/Twitter account listed")
    if p.boosts_active:
        sc.flags.append(f"paid DexScreener boosts active ({p.boosts_active}): promoted, not purely organic")
    return sc


def apply_safety(sc: Scored, s: SafetyReport, cfg: Config) -> Scored:
    sc.safety = s
    if not s.available:
        sc.components["safety"] = 0.0
        sc.flags.append("UNVERIFIED: RugCheck unavailable, check the contract yourself before buying")
        return sc

    holders = s.total_holders or 0
    top10 = s.top10_pct if s.top10_pct is not None else 100.0
    lp = s.lp_locked_pct if s.lp_locked_pct is not None else 0.0
    safe = (
        0.4 * _ramp(_log10(holders), _log10(300), _log10(10_000))
        + 0.4 * (1.0 - _ramp(top10, 15, 35))
        + 0.2 * _ramp(lp, 50, 95)
    )
    sc.components["safety"] = 15 * safe
    if holders >= 2_000 and top10 <= 25:
        sc.reasons.append(f"well distributed: {holders:,} holders, top 10 hold {top10:.0f}%")
    if lp < 50:
        sc.flags.append(f"only {lp:.0f}% of LP locked/burned: liquidity can be pulled")
    if s.warn_risks:
        sc.penalties.append((min(12, 3 * len(s.warn_risks)), "RugCheck warnings: " + "; ".join(s.warn_risks[:4])))
    if s.insiders_detected >= cfg.insider_flag_count:
        sc.penalties.append((3, f"RugCheck sees {s.insiders_detected} linked insider wallets"))
    return sc
