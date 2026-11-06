"""Markdown report for one scan."""

from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field
from typing import List

from .config import Config
from .entry import EntryPlan
from .entry import fmt_price as _price
from .scoring import Scored


@dataclass
class ScanStats:
    sources_ok: List[str] = field(default_factory=list)
    discovered: int = 0
    priced: int = 0
    passed_market: int = 0
    safety_checked: int = 0
    passed_safety: int = 0
    entry_skips: int = 0
    rejections: Counter = field(default_factory=Counter)


def _usd(x: float) -> str:
    if x >= 1e9:
        return f"${x / 1e9:.2f}B"
    if x >= 1e6:
        return f"${x / 1e6:.2f}M"
    if x >= 1e3:
        return f"${x / 1e3:.0f}K"
    return f"${x:,.0f}"


def _pct(x: float) -> str:
    return f"{x:+,.0f}%"


def _age(hours) -> str:
    if hours is None:
        return "?"
    if hours < 48:
        return f"{hours:.0f}h"
    return f"{hours / 24:.0f}d"


_REJECTION_BUCKETS = (
    ("top-10 wallets", "whale concentration"),
    ("mint authority", "mint not revoked"),
    ("freeze authority", "freeze not revoked"),
    ("RugCheck danger", "RugCheck danger"),
    ("rugged", "rugged"),
    ("min old", "too new"),
    ("dumping", "dumping"),
    ("liquidity", "low liquidity"),
    ("above ceiling", "market cap too big"),
    ("below floor", "market cap too small"),
    ("volume", "thin volume"),
    ("trades", "too few trades"),
    ("quoted in", "odd quote token"),
    ("major", "major/stable"),
)


def _rejection_bucket(reason: str) -> str:
    for needle, label in _REJECTION_BUCKETS:
        if needle in reason:
            return label
    return reason


def render_entry(e: EntryPlan, cfg: Config) -> List[str]:
    ladder = "; ".join(f"{m:g}x = {_price(px)}: {what}" for m, px, what in e.take_profits(cfg))
    basis = f"{_price(e.swing_low)} → {_price(e.swing_high)}"
    if e.vwap:
        basis += f", 12h VWAP {_price(e.vwap)}"
    return [
        f"**Entry: {e.action}.** Buy zone {_price(e.zone_low)} – {_price(e.zone_high)}, now {_price(e.price)}: {e.note}.",
        "",
        f"- Entry price: {_price(e.entry)}",
        f"- Stop: {_price(e.stop)} (-{e.stop_pct:.0f}% from entry). Also exit if liquidity drops sharply or the "
        f"top wallets start selling.",
        f"- Take profits: {ladder}",
        f"- Size: at most {cfg.max_position_pct:g}% of your portfolio",
        f"- Levels: {e.method}: {basis}",
    ]


def _summary_table(picks: List[Scored]) -> List[str]:
    rows = ["| # | Coin | Score | Entry | Buy zone | Stop | Contract |", "|---|---|---|---|---|---|---|"]
    for i, sc in enumerate(picks, 1):
        e = sc.entry
        zone = f"{_price(e.zone_low)} – {_price(e.zone_high)}" if e else "?"
        rows.append(
            f"| {i} | ${sc.pair.symbol} | {sc.score:.0f} | {e.action if e else '?'} | {zone} "
            f"| {_price(e.stop) if e else '?'} | `{sc.pair.token_address}` |"
        )
    return rows


def render_pick(rank: int, sc: Scored, cfg: Config, now: dt.datetime) -> str:
    p, s = sc.pair, sc.safety
    label = " (low conviction)" if sc.score < cfg.min_conviction_score else ""
    lines = [f"### {rank}. ${p.symbol} ({p.name}): score {sc.score:.0f}/100{label}", ""]
    lines.append(f"**Contract (paste into fomo search):** `{p.token_address}`")
    lines.append("")
    lines.append("| Price | Mkt cap | Liquidity | 24h vol | Age | 5m | 1h | 6h | 24h | Buys/sells 1h |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    b1, s1 = p.txns.get("h1", (0, 0))
    lines.append(
        f"| {_price(p.price_usd)} | {_usd(p.market_cap)} | {_usd(p.liquidity_usd)} | {_usd(p.volume.get('h24', 0))} "
        f"| {_age(p.age_hours(now))} | {_pct(p.price_change.get('m5', 0))} | {_pct(p.price_change.get('h1', 0))} "
        f"| {_pct(p.price_change.get('h6', 0))} | {_pct(p.price_change.get('h24', 0))} | {b1:,}/{s1:,} |"
    )
    lines.append("")
    if s and s.available:
        top10 = f"{s.top10_pct:.0f}%" if s.top10_pct is not None else "?"
        lp = f"{s.lp_locked_pct:.0f}%" if s.lp_locked_pct is not None else "?"
        holders = f"{s.total_holders:,}" if s.total_holders is not None else "?"
        lines.append(
            f"**Safety (RugCheck):** mint and freeze revoked, {holders} holders, top-10 wallets {top10}, "
            f"LP locked/burned {lp}" + (f", launched on {s.launchpad}" if s.launchpad else "")
        )
        lines.append("")
    if sc.reasons:
        lines.append("**Why it ranks:** " + "; ".join(sc.reasons) + ".")
        lines.append("")
    risks = sc.flags + [reason for _, reason in sc.penalties]
    if risks:
        lines.append("**Risk flags:** " + "; ".join(risks) + ".")
        lines.append("")
    if sc.entry:
        lines.extend(render_entry(sc.entry, cfg))
        lines.append("")
    links = [f"[DexScreener]({p.url})"] if p.url else []
    if "twitter" in p.socials:
        links.append(f"[X]({p.socials['twitter']})")
    if "telegram" in p.socials:
        links.append(f"[Telegram]({p.socials['telegram']})")
    if p.websites:
        links.append(f"[Website]({p.websites[0]})")
    links.append(f"[RugCheck](https://rugcheck.xyz/tokens/{p.token_address})")
    lines.append(" · ".join(links))
    lines.append("")
    return "\n".join(lines)


def render_report(picks: List[Scored], runners_up: List[Scored], stats: ScanStats, cfg: Config,
                  now: dt.datetime) -> str:
    out = [f"# Meme Coin Scan: {now.strftime('%Y-%m-%d %H:%M')} UTC", ""]
    out.append(
        "> **Not financial advice.** Meme coins are the riskiest thing you can buy: most go to zero, and a "
        "passing RugCheck does not rule out a rug. These are the coins that score best on momentum, buy "
        "pressure, liquidity and contract safety right now, not predictions. Only use money you can lose "
        "entirely, and check the chart in fomo before you buy."
    )
    out.append("")
    out.append(
        f"Scanned {stats.discovered} tokens on {', '.join(cfg.chains)} → {stats.passed_market} passed market "
        f"gates → {stats.safety_checked} safety-checked → {stats.passed_safety} passed safety"
        + (f" → {stats.entry_skips} dropped because the setup already failed (SKIP)." if stats.entry_skips else ".")
    )
    out.append("")
    if not picks:
        out.append("**No coin passed every gate this run.** Sitting out is a valid trade; run the scan again later.")
        out.append("")
    else:
        out.append(f"## Top {len(picks)} picks")
        out.append("")
        out.extend(_summary_table(picks))
        out.append("")
        out.append("**BUY NOW** = price is in the buy zone. **WAIT** = set a limit buy in the zone, or skip the "
                   "coin if it never gets there. Never chase a coin above its zone.")
        out.append("")
        for i, sc in enumerate(picks, 1):
            out.append(render_pick(i, sc, cfg, now))

    if runners_up:
        out.append("## Runners-up")
        out.append("")
        out.append("| Coin | Score | Mkt cap | 1h | Entry | Contract |")
        out.append("|---|---|---|---|---|---|")
        for sc in runners_up:
            p = sc.pair
            entry = sc.entry.action if sc.entry else "not checked"
            out.append(
                f"| ${p.symbol} | {sc.score:.0f} | {_usd(p.market_cap)} | {_pct(p.price_change.get('h1', 0))} "
                f"| {entry} | `{p.token_address}` |"
            )
        out.append("")

    if stats.rejections:
        top = Counter()
        for reason, n in stats.rejections.items():
            top[_rejection_bucket(reason)] += n
        out.append("## Why the rest were rejected")
        out.append("")
        out.append(", ".join(f"{k}: {v}" for k, v in top.most_common()))
        out.append("")

    out.append("Data: " + (", ".join(stats.sources_ok) if stats.sources_ok else "no source answered") + ", RugCheck.")
    out.append("")
    return "\n".join(out)
