"""CLI entrypoint: run the meme coin scan and print the top 5.

Usage:
    python -m memecoin_scanner.scan
    python -m memecoin_scanner.scan --top 5 --no-save
    python -m memecoin_scanner.scan --self-test   # offline, synthetic data
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import data
from .config import Config
from .data import PairSnapshot, SafetyReport
from .entry import Candle, fetch_candles, plan_entry
from .report import ScanStats, render_report
from .scoring import Scored, apply_safety, market_gate, safety_gate, score_market


def run_pipeline(
    cfg: Config,
    discover: Callable,
    fetch_pairs: Callable[[str, List[str]], List[PairSnapshot]],
    fetch_safety: Callable[[str], SafetyReport],
    now: dt.datetime,
    pause_s: float = 0.0,
    fetch_candles: Optional[Callable[[PairSnapshot], Optional[List[Candle]]]] = None,
    candle_pause_s: float = 0.0,
) -> Tuple[List[Scored], List[Scored], ScanStats]:
    """Discover → price → market gates → safety on the best → rank → entry plans."""
    stats = ScanStats()
    found, prefetched, stats.sources_ok = discover()

    all_pairs: List[PairSnapshot] = list(prefetched)
    for chain, addr_sources in found.items():
        addresses = list(addr_sources.keys())
        stats.discovered += len(addresses)
        fetched = fetch_pairs(chain, addresses)
        for p in fetched:
            p.sources |= addr_sources.get(p.token_address, set())
        all_pairs.extend(fetched)

    best = data.best_pair_per_token(all_pairs)
    stats.priced = len(best)

    candidates: List[Scored] = []
    for p in best.values():
        reason = market_gate(p, cfg, now)
        if reason:
            stats.rejections[reason] += 1
            continue
        candidates.append(score_market(p, cfg, now))
    stats.passed_market = len(candidates)

    # Safety calls are the slow part: only the best market setups get one.
    candidates.sort(key=lambda sc: sc.score, reverse=True)
    survivors: List[Scored] = []
    for sc in candidates[: cfg.max_safety_checks]:
        safety = fetch_safety(sc.pair.token_address)
        stats.safety_checked += 1
        reason = safety_gate(safety, cfg)
        if reason:
            sc.rejected = reason
            sc.safety = safety
            stats.rejections[reason] += 1
        else:
            survivors.append(apply_safety(sc, safety, cfg))
        if pause_s:
            time.sleep(pause_s)
    stats.passed_safety = len(survivors)

    # Verified coins outrank unverified ones at any score.
    survivors.sort(key=lambda sc: (sc.safety is not None and sc.safety.available, sc.score), reverse=True)

    # Entry plans, in rank order. A pick whose setup already failed (SKIP)
    # drops to the runners-up and the next coin takes its place.
    picks: List[Scored] = []
    lookups = 0
    for sc in survivors:
        if len(picks) >= cfg.top_n:
            break
        candles = None
        if fetch_candles is not None and lookups < cfg.max_entry_checks:
            if lookups and candle_pause_s:
                time.sleep(candle_pause_s)
            candles = fetch_candles(sc.pair)
            lookups += 1
        sc.entry = plan_entry(sc.pair, candles, cfg)
        if sc.entry.action != "SKIP":
            picks.append(sc)
    stats.entry_skips = sum(1 for sc in survivors if sc.entry is not None and sc.entry.action == "SKIP")
    runners_up = [sc for sc in survivors if sc not in picks][:5]
    return picks, runners_up, stats


def run_scan(cfg: Config, now: Optional[dt.datetime] = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    t = cfg.request_timeout_s
    picks, runners_up, stats = run_pipeline(
        cfg,
        discover=lambda: data.discover_token_addresses(cfg.chains, cfg.search_queries, t),
        fetch_pairs=lambda chain, addrs: data.fetch_pairs(chain, addrs, t),
        fetch_safety=lambda addr: data.fetch_safety(addr, t),
        now=now,
        pause_s=cfg.rugcheck_pause_s,
        fetch_candles=lambda pair: fetch_candles(pair, cfg),
        candle_pause_s=cfg.candle_pause_s,
    )
    if stats.priced == 0:
        raise RuntimeError("no market data came back from DexScreener (network down or blocked?)")
    return render_report(picks, runners_up, stats, cfg, now)


# --- Offline self-test ---------------------------------------------------------

def _synthetic_pair(symbol: str, address: str, now: dt.datetime, **kw) -> PairSnapshot:
    base = dict(
        chain="solana", token_address=address, pair_address=address[::-1], dex_id="pumpswap",
        name=f"{symbol} Coin", symbol=symbol, quote_symbol="SOL", price_usd=0.0021,
        liquidity_usd=180_000.0, market_cap=1_800_000.0,
        volume={"h24": 1_500_000.0, "h6": 400_000.0, "h1": 120_000.0, "m5": 14_000.0},
        txns={"h24": (9_000, 6_000), "h6": (3_000, 2_000), "h1": (800, 500), "m5": (90, 50)},
        price_change={"m5": 3.0, "h1": 22.0, "h6": 60.0, "h24": 240.0},
        pair_created_at=now - dt.timedelta(hours=30), url=f"https://dexscreener.com/solana/{address}",
        websites=["https://example.com"], socials={"twitter": "https://x.com/example"},
    )
    base.update(kw)
    return PairSnapshot(**base)


def _synthetic_candles(low: float, high: float, last: float, now: dt.datetime) -> List[Candle]:
    """A 2h climb from low to high, then a 30-minute pullback to last (oldest first)."""
    closes = [low + (high - low) * i / 23 for i in range(24)]
    closes += [high + (last - high) * i / 6 for i in range(1, 7)]
    start = int(now.timestamp()) - 300 * len(closes)
    out, prev = [], low
    for i, c in enumerate(closes):
        out.append(Candle(start + 300 * i, prev, max(prev, c) * 1.002, min(prev, c) * 0.998, c, 2_000.0))
        prev = c
    return out


def _self_test_report(now: Optional[dt.datetime] = None) -> str:
    now = now or dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.timezone.utc)
    pairs = [
        _synthetic_pair("GOOD", "Good1111111111111111111111111111111111pump", now),
        _synthetic_pair("FRESH", "Fresh111111111111111111111111111111111pump", now,
                        pair_created_at=now - dt.timedelta(hours=2), market_cap=400_000.0, liquidity_usd=45_000.0),
        _synthetic_pair("MINT", "Mint11111111111111111111111111111111111pump", now),
        _synthetic_pair("DUMP", "Dump11111111111111111111111111111111111pump", now,
                        price_change={"m5": -8.0, "h1": -40.0, "h6": -55.0, "h24": 10.0}),
        _synthetic_pair("THIN", "Thin11111111111111111111111111111111111pump", now, liquidity_usd=8_000.0),
        _synthetic_pair("WHALE", "Whale1111111111111111111111111111111111pump", now),
    ]
    safety: Dict[str, SafetyReport] = {
        p.token_address: SafetyReport(
            available=True, mint_authority_revoked=True, freeze_authority_revoked=True,
            lp_locked_pct=100.0, total_holders=5_000, top10_pct=18.0, launchpad="Pump.Fun",
        )
        for p in pairs
    }
    safety[pairs[2].token_address] = replace(safety[pairs[2].token_address], mint_authority_revoked=False)
    safety[pairs[5].token_address] = replace(safety[pairs[5].token_address], top10_pct=62.0)

    picks, runners_up, stats = run_pipeline(
        Config(),
        discover=lambda: ({"solana": {p.token_address: {"synthetic"} for p in pairs}}, [], ["synthetic"]),
        fetch_pairs=lambda chain, addrs: [replace(p, sources=set()) for p in pairs if p.token_address in addrs],
        fetch_safety=lambda addr: safety.get(addr, SafetyReport(available=False)),
        now=now,
        # GOOD pulled back into its zone; FRESH has no candles (estimated levels).
        fetch_candles=lambda pair: (
            _synthetic_candles(0.0012, 0.0027, pair.price_usd, now) if pair.symbol == "GOOD" else None
        ),
    )
    return render_report(picks, runners_up, stats, Config(), now)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Solana meme coin scanner (top picks for the fomo app)")
    parser.add_argument("--top", type=int, default=None, help="How many picks to list (default 5)")
    parser.add_argument("--output-dir", default=None, help="Override the reports output directory")
    parser.add_argument("--no-save", action="store_true", help="Print only; don't write a report file")
    parser.add_argument("--self-test", action="store_true", help="Run the pipeline on synthetic data (no network)")
    args = parser.parse_args(argv)

    cfg = Config()
    if args.top:
        cfg = replace(cfg, top_n=args.top)

    now = dt.datetime.now(dt.timezone.utc)
    try:
        report_md = _self_test_report() if args.self_test else run_scan(cfg, now)
    except Exception as exc:
        print(f"Scan failed: {exc}", file=sys.stderr)
        return 1

    print(report_md)
    if not args.no_save and not args.self_test:
        out_dir = Path(args.output_dir or cfg.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{now.strftime('%Y-%m-%d-%H%M')}.md"
        out_path.write_text(report_md)
        print(f"\nSaved report to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
