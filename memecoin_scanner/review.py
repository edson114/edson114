"""Grade past picks: what happened after each scan, under the scan's own plan.

`run-scan --review` reads every saved report (the JSON sidecar when there
is one, else the Markdown), pulls 15-minute GeckoTerminal candles for the
`horizon` hours after each scan, and simulates the plan that report gave:

  fill   BUY NOW buys at the scan price; WAIT buys only if the price
         reaches the entry within 24h (else NO FILL)
  stop   the plan's stop (worst case: a candle that touches both the stop
         and a target counts as stopped)
  ladder sells 50% at the first target, 25% at the second, then trails the
         rest from the third with the plan's trailing stop
  end    whatever is left is sold at the last close in the horizon

Returns are per trade, before fees and slippage, which in thin meme pools
cost several percent a round trip. A few dozen picks is a small sample:
use it to spot what keeps failing, not to fine-tune numbers.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import data
from .config import Config, fast_config


@dataclass
class Pick:
    ts: dt.datetime
    report: str
    rank: int
    symbol: str
    contract: str
    score: float
    price: float
    action: str  # BUY NOW / WAIT / OLD (report predates entry plans)
    entry: Optional[float] = None
    stop: Optional[float] = None
    mode: str = "standard"
    pool: str = ""
    mcap: Optional[float] = None
    liq: Optional[float] = None
    age_h: Optional[float] = None
    h1: Optional[float] = None
    h6: Optional[float] = None
    h24: Optional[float] = None
    theme: str = ""


# --- loading picks -------------------------------------------------------------------

def _num(s: str) -> Optional[float]:
    s = s.strip().replace("$", "").replace(",", "").replace("+", "").rstrip("%")
    mult = 1.0
    for suf, m in (("K", 1e3), ("M", 1e6), ("B", 1e9)):
        if s.endswith(suf):
            s, mult = s[:-1], m
    try:
        return float(s) * mult
    except ValueError:
        return None


def _age_hours(s: str) -> Optional[float]:
    s = s.strip()
    if s.endswith("h"):
        return _num(s[:-1])
    if s.endswith("d"):
        v = _num(s[:-1])
        return v * 24 if v is not None else None
    return None


def _report_time(path: Path) -> Optional[dt.datetime]:
    try:
        return dt.datetime.strptime(path.stem, "%Y-%m-%d-%H%M").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def parse_report_md(path: Path) -> List[Pick]:
    """Picks from a Markdown report (older reports have no JSON sidecar)."""
    ts = _report_time(path)
    if ts is None:
        return []
    text = path.read_text()
    mode = "fast" if text.startswith("# Meme Coin Fast Scan") else "standard"
    picks = []
    for block in re.split(r"\n### (?=\d+\. \$)", text)[1:]:
        head = re.match(r"(\d+)\. \$(\S+) \(.*?\): score (\d+)/100", block)
        addr = re.search(r"\*\*Contract \(paste into fomo search\):\*\* `(\w+)`", block)
        row = re.search(r"\|---\|.*\n\| (.+) \|", block)
        if not (head and addr and row):
            continue
        cells = [c.strip() for c in row.group(1).split("|")]
        if len(cells) < 9:
            continue
        price = _num(cells[0])
        if not price:
            continue
        action = re.search(r"\*\*Entry: (BUY NOW|WAIT|SKIP)\.\*\*", block)
        entry = re.search(r"- Entry price: \$([\d.e-]+)", block)
        stop = re.search(r"- Stop: \$([\d.e-]+)", block)
        theme = re.search(r"part of a hot narrative: '(\w+)'", block)
        picks.append(Pick(
            ts=ts, report=path.name, rank=int(head.group(1)), symbol=head.group(2), contract=addr.group(1),
            score=float(head.group(3)), price=price, action=action.group(1) if action else "OLD",
            entry=float(entry.group(1)) if entry else None, stop=float(stop.group(1)) if stop else None,
            mode=mode, mcap=_num(cells[1]), liq=_num(cells[2]), age_h=_age_hours(cells[4]),
            h1=_num(cells[6]), h6=_num(cells[7]), h24=_num(cells[8]), theme=theme.group(1) if theme else "",
        ))
    return picks


def load_picks(root: Path) -> List[Pick]:
    """Every pick under `root` (reports/memecoins, fast/ included; review/ skipped)."""
    picks: List[Pick] = []
    for md in sorted(root.rglob("*.md")):
        if "review" in md.parts:
            continue
        sidecar = md.with_suffix(".json")
        if sidecar.exists():
            for d in json.loads(sidecar.read_text()):
                d["ts"] = dt.datetime.fromisoformat(d["ts"])
                picks.append(Pick(**{k: v for k, v in d.items() if k in Pick.__dataclass_fields__}))
        else:
            picks.extend(parse_report_md(md))
    return picks


def picks_to_json(picks, cfg: Config, now: dt.datetime, report_name: str) -> str:
    """JSON sidecar for a scan's picks (exact pool address and plan) for later reviews."""
    out = []
    for rank, sc in enumerate(picks, 1):
        p, e = sc.pair, sc.entry
        out.append(dict(
            ts=now.isoformat(), report=report_name, rank=rank, symbol=p.symbol, contract=p.token_address,
            score=round(sc.score, 1), price=p.price_usd, action=e.action if e else "?",
            entry=e.entry if e else None, stop=e.stop if e else None, mode=cfg.mode, pool=p.pair_address,
            mcap=p.market_cap, liq=p.liquidity_usd, age_h=p.age_hours(now), h1=p.price_change.get("h1"),
            h6=p.price_change.get("h6"), h24=p.price_change.get("h24"), theme=p.theme_word,
        ))
    return json.dumps(out, indent=1)


# --- simulation ------------------------------------------------------------------------

Candles = List[Tuple[float, float, float, float, float]]  # (ts, open, high, low, close), oldest first


@dataclass
class Outcome:
    pick: Pick
    event: str  # TARGET / STOP / NONE / NO FILL / GONE / NO DATA / TOO RECENT
    ret: Optional[float] = None  # plan return per $1 invested (marked to market if still open)
    max_mult: Optional[float] = None  # best high in the horizon vs the scan price
    close_mult: Optional[float] = None  # last close in the horizon vs the scan price
    filled_ts: Optional[float] = None  # when the entry filled
    open_fraction: float = 0.0  # share of the position still held (only without liquidation)
    last_price: Optional[float] = None


def simulate(pick: Pick, candles: Candles, cfg: Config, horizon_h: float,
             liquidate_at_end: bool = True) -> Outcome:
    """Replay the plan over the candles. With liquidate_at_end=False (the paper
    bot, mid-trade) whatever is still held stays open and is marked at the last close."""
    t0 = pick.ts.timestamp()
    c = [k for k in candles if t0 <= k[0] < t0 + horizon_h * 3600]
    if not c:
        return Outcome(pick, "NO DATA")
    p0 = pick.price
    out = Outcome(pick, "NONE", max_mult=max(k[2] for k in c) / p0, close_mult=c[-1][4] / p0,
                  last_price=c[-1][4])

    E = pick.entry or p0
    S = pick.stop or E * (1 - cfg.stop_loss_pct / 100)
    start = None
    for i, k in enumerate(c):
        if k[0] >= t0 + 24 * 3600:
            break
        if pick.action in ("BUY NOW", "OLD") or (E < p0 and k[3] <= E) or (E >= p0 and k[2] >= E):
            start = i
            break
    if start is None:
        out.event = "NO FILL"
        return out
    out.filled_ts = c[start][0]

    targets = [m for m, _ in cfg.take_profit_ladder]
    fractions = (0.5, 0.25)  # of the original; the rest trails from the last target
    held, cash, hit, peak, trailing = 1.0, 0.0, 0, 0.0, False
    for k in c[start:]:
        high, low = k[2], k[3]
        if held > 0 and not trailing and low <= S:
            cash += held * S / E
            held = 0.0
            out.event = "STOP" if hit == 0 else out.event
            break
        while hit < len(fractions) and hit < len(targets) and high >= targets[hit] * E:
            sell = min(held, fractions[hit])
            cash += sell * targets[hit]
            held -= sell
            hit += 1
            out.event = "TARGET"
        if trailing:
            peak = max(peak, high)
            floor = peak * (1 - cfg.trail_stop_pct / 100)
            if low <= floor:
                cash += held * floor / E
                held = 0.0
                break
        elif hit >= len(fractions) and len(targets) > hit and high >= targets[hit] * E:
            # Trailing starts here; this candle's low may have come before its high,
            # so the floor is first checked on the next candle.
            trailing, peak = True, high
    if held > 0 and liquidate_at_end:
        cash += held * c[-1][4] / E
        held = 0.0
    out.open_fraction = held
    out.ret = cash + held * c[-1][4] / E - 1.0
    return out


def fetch_history(pool: str, chain: str, since: dt.datetime, horizon_h: float, timeout: float) -> Candles:
    payload = data._get_json(
        f"{data.GECKOTERMINAL}/networks/{chain}/pools/{pool}/ohlcv/minute", timeout,
        params={"aggregate": 15, "limit": int(horizon_h * 4) + 8,
                "before_timestamp": int(since.timestamp() + horizon_h * 3600)},
        retries=3, backoff_s=5.0,
    )
    rows = (payload or {}).get("data", {}).get("attributes", {}).get("ohlcv_list", []) if isinstance(payload, dict) else []
    out = []
    for r in rows or []:
        try:
            out.append((float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4])))
        except (TypeError, ValueError, IndexError):
            continue
    return sorted(out)


def run_review(picks: List[Pick], now: dt.datetime, horizon_h: float = 72.0,
               resolve_pools: Optional[Callable[[List[str]], Dict[str, str]]] = None,
               history: Optional[Callable[[str, dt.datetime], Candles]] = None,
               pause_s: float = 0.0) -> List[Outcome]:
    """Outcomes for every pick old enough to have a full horizon."""
    due = [p for p in picks if p.ts.timestamp() + horizon_h * 3600 <= now.timestamp()]
    missing = sorted({p.contract for p in due if not p.pool})
    pools = resolve_pools(missing) if (resolve_pools and missing) else {}
    outcomes = [Outcome(p, "TOO RECENT") for p in picks if p not in due]
    for n, p in enumerate(due):
        cfg = fast_config() if p.mode == "fast" else Config()
        pool = p.pool or pools.get(p.contract, "")
        if not pool and resolve_pools is not None:
            # No pool trades it any more: the coin died. Counted as a stop-out,
            # so dead picks don't quietly drop out of the stats.
            entry = p.entry or p.price
            stop = p.stop or entry * (1 - cfg.stop_loss_pct / 100)
            outcomes.append(Outcome(p, "GONE", ret=stop / entry - 1))
            continue
        if not pool or history is None:
            outcomes.append(Outcome(p, "NO DATA"))
            continue
        if n and pause_s:
            time.sleep(pause_s)
        outcomes.append(simulate(p, history(pool, p.ts), cfg, horizon_h))
    return outcomes


# --- report ------------------------------------------------------------------------------

def _stats(rows: List[Outcome]) -> str:
    traded = [o for o in rows if o.ret is not None]
    if not traded:
        return "| 0 | – | – | – | – |"
    rets = [o.ret for o in traded]
    hit = sum(1 for o in traded if o.event == "TARGET")
    stop = sum(1 for o in traded if o.event in ("STOP", "GONE"))
    return (f"| {len(traded)} | {hit / len(traded):.0%} | {stop / len(traded):.0%} | {statistics.mean(rets):+.0%} "
            f"| {statistics.median(rets):+.0%} |")


def _bucket_table(title: str, rows: List[Outcome], key: Callable[[Pick], str], order: Iterable[str]) -> List[str]:
    lines = [f"**By {title}**", "", "| Bucket | Trades | Hit 1st target | Stopped | Avg return | Median |",
             "|---|---|---|---|---|---|"]
    for b in order:
        sub = [o for o in rows if key(o.pick) == b]
        if sub:
            lines.append(f"| {b} " + _stats(sub))
    return lines + [""]


def _score_bucket(p: Pick) -> str:
    return "under 50" if p.score < 50 else ("50-64" if p.score < 65 else "65+")


def _h1_bucket(p: Pick) -> str:
    if p.h1 is None:
        return "unknown"
    return "falling (<-10%)" if p.h1 < -10 else ("resting (-10% to +20%)" if p.h1 <= 20 else
                                                 ("rising (+20% to +40%)" if p.h1 <= 40 else "vertical (>+40%)"))


def _age_bucket(p: Pick) -> str:
    if p.age_h is None:
        return "unknown"
    return "under 24h" if p.age_h < 24 else ("1-7 days" if p.age_h < 168 else "over 7 days")


def render_review(outcomes: List[Outcome], horizon_h: float, now: dt.datetime) -> str:
    traded = [o for o in outcomes if o.ret is not None]
    out = [f"# Scan Review: {now.strftime('%Y-%m-%d %H:%M')} UTC", ""]
    out.append(
        f"Every saved pick, graded on the {horizon_h:g} hours after its scan under that scan's own plan (fill rules, "
        "stop, 50%/25% ladder, trailing stop). Before fees and slippage, worst case when a candle touches both the "
        "stop and a target. A small sample: look for what keeps failing, don't fine-tune on it."
    )
    out.append("")
    counts = {e: sum(1 for o in outcomes if o.event == e)
              for e in ("TARGET", "STOP", "GONE", "NONE", "NO FILL", "NO DATA", "TOO RECENT")}
    out.append(f"{len(outcomes)} picks: " + ", ".join(f"{v} {k.lower()}" for k, v in counts.items() if v) + ".")
    if counts["GONE"]:
        out.append("GONE = no pool trades the coin any more (it died); counted as a stop-out.")
    out.append("")
    out += ["| Trades | Hit 1st target | Stopped | Avg return | Median |", "|---|---|---|---|---|", _stats(traded), ""]
    if traded:
        best = max(traded, key=lambda o: o.ret)
        worst = min(traded, key=lambda o: o.ret)
        out.append(f"Best: ${best.pick.symbol} {best.ret:+.0%}. Worst: ${worst.pick.symbol} {worst.ret:+.0%}. "
                   f"Sum: {sum(o.ret for o in traded):+.1f}x of one trade's stake.")
        out.append("")
    out += _bucket_table("score", traded, _score_bucket, ("under 50", "50-64", "65+"))
    out += _bucket_table("last-hour move at the scan", traded, _h1_bucket,
                         ("falling (<-10%)", "resting (-10% to +20%)", "rising (+20% to +40%)", "vertical (>+40%)",
                          "unknown"))
    out += _bucket_table("coin age at the scan", traded, _age_bucket, ("under 24h", "1-7 days", "over 7 days",
                                                                        "unknown"))
    out += _bucket_table("mode", traded, lambda p: p.mode, ("standard", "fast"))
    out += _bucket_table("narrative", traded, lambda p: f"'{p.theme}'" if p.theme else "none",
                         sorted({f"'{o.pick.theme}'" for o in traded if o.pick.theme}) + ["none"])

    out += ["## Every pick", "",
            "| Scan | Coin | Score | Action | 1h at scan | Age | Result | Plan return | Best high | Last close |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for o in sorted(outcomes, key=lambda o: (o.pick.ts, o.pick.rank)):
        p = o.pick
        out.append(
            f"| {p.ts.strftime('%m-%d %H:%M')} {p.mode[0].upper()} | ${p.symbol} | {p.score:.0f} | {p.action} "
            f"| {'' if p.h1 is None else f'{p.h1:+.0f}%'} | {'' if p.age_h is None else f'{p.age_h:.0f}h'} "
            f"| {o.event} | {'' if o.ret is None else f'{o.ret:+.0%}'} "
            f"| {'' if o.max_mult is None else f'{o.max_mult:.2f}x'} "
            f"| {'' if o.close_mult is None else f'{o.close_mult:.2f}x'} |"
        )
    out.append("")
    return "\n".join(out)


def resolve_pools_live(contracts: List[str], timeout: float) -> Dict[str, str]:
    pools: Dict[str, str] = {}
    for i in range(0, len(contracts), 30):
        best = data.best_pair_per_token(data.fetch_pairs("solana", contracts[i:i + 30], timeout))
        pools.update({addr: p.pair_address for addr, p in best.items()})
    return pools
