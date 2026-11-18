"""Paper-trading bot: trades the scanner's picks on paper, 24/7, under the plan's rules.

No wallet, no keys, no real orders. Each run (`run-bot --once`, hourly from
GitHub Actions or a loop on your own machine):

1. Updates every pending and open position by replaying 15-minute candles
   since its scan through the same simulator `--review` uses: WAIT entries
   fill only if the price reaches the entry within 24h, then the stop, the
   50%/25% take-profit ladder and the trailing stop apply. A position still
   open after `max_hold_hours` is sold at the last close.
2. Charges `cost_pct_per_side` on the way in and out (fees, price impact,
   slippage: thin meme pools cost real money to trade).
3. Runs a scan and opens new positions from the top picks: a fixed stake,
   at most `max_positions` pending or open, no coin it already holds or
   closed in the last `cooldown_hours`, nothing after the day's realized
   loss reaches `daily_loss_limit_pct` of the starting cash.

State lives in `journal/paper_bot.json`, closed trades in
`journal/paper_trades.csv` (the trade-log format, so `trade-log stats
--path journal/paper_trades.csv` works), and a readable status page in
`reports/memecoins/bot/status.md`.

The goal is the plan's Phase 1: 50+ closed trades that show whether the
scan has an edge after costs, before any real money is automated.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import journal, review
from .config import Config, fast_config

DEFAULT_STATE = "journal/paper_bot.json"
DEFAULT_TRADES = "journal/paper_trades.csv"
DEFAULT_STATUS = "reports/memecoins/bot/status.md"


@dataclass(frozen=True)
class BotConfig:
    starting_cash: float = 1000.0
    stake_usd: float = 50.0  # the plan's Phase 1 size
    max_positions: int = 4  # pending + open
    daily_loss_limit_pct: float = 3.0  # of starting cash, realized, per UTC day
    max_hold_hours: float = 72.0
    cooldown_hours: float = 24.0  # no re-entry in a coin it just left
    pending_hours: float = 24.0  # a WAIT entry that doesn't fill by then expires
    cost_pct_per_side: float = 2.0
    mode: str = "standard"  # or "fast" (fast_config)


# --- state ---------------------------------------------------------------------------

def new_state(bcfg: BotConfig) -> dict:
    return {"version": 1, "starting_cash": bcfg.starting_cash, "cash": bcfg.starting_cash, "next_id": 1,
            "positions": [], "runs": 0, "last_run": None}


def load_state(path: str, bcfg: BotConfig) -> dict:
    p = Path(path)
    return json.loads(p.read_text()) if p.exists() else new_state(bcfg)


def save_state(state: dict, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=1))


def _ts(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s)


def _pick(pos: dict) -> review.Pick:
    return review.Pick(
        ts=_ts(pos["scan_ts"]), report="paper-bot", rank=0, symbol=pos["symbol"], contract=pos["contract"],
        score=pos["score"], price=pos["price"], action=pos["action"], entry=pos["entry"], stop=pos["stop"],
        mode=pos["mode"], pool=pos["pool"],
    )


def _net(gross_ret: float, bcfg: BotConfig) -> float:
    c = bcfg.cost_pct_per_side / 100
    return (1 + gross_ret) * (1 - c) / (1 + c) - 1


# --- one cycle -----------------------------------------------------------------------

History = Callable[[str, dt.datetime], review.Candles]


def update_positions(state: dict, now: dt.datetime, bcfg: BotConfig, history: History, trades_path: str,
                     pause_s: float = 0.0) -> List[str]:
    log: List[str] = []
    active = [p for p in state["positions"] if p["status"] in ("pending", "open")]
    for n, pos in enumerate(active):
        if n and pause_s:
            time.sleep(pause_s)
        scan_ts = _ts(pos["scan_ts"])
        done = now >= scan_ts + dt.timedelta(hours=bcfg.max_hold_hours)
        cfg = fast_config() if pos["mode"] == "fast" else Config()
        candles = history(pos["pool"], scan_ts)
        o = review.simulate(_pick(pos), candles, cfg, bcfg.max_hold_hours, liquidate_at_end=done)

        if o.event == "NO DATA":
            if pos["status"] == "pending" and now >= scan_ts + dt.timedelta(hours=bcfg.pending_hours):
                o = review.Outcome(o.pick, "NO FILL")  # nothing traded at all: treat as unfilled
            elif pos["status"] == "open" and done:
                # No price data past the hold limit: the coin most likely died. Count it as stopped out.
                o = review.Outcome(o.pick, "GONE", ret=pos["stop"] / pos["entry"] - 1, last_price=pos["stop"])
            else:
                continue
        if o.event == "NO FILL":
            if now >= scan_ts + dt.timedelta(hours=bcfg.pending_hours):
                pos["status"] = "expired"
                pos["closed_ts"] = now.isoformat()
                state["cash"] += pos["stake_usd"]
                log.append(f"${pos['symbol']}: entry {pos['entry']:.6g} never reached, order expired")
            continue

        if pos["status"] == "pending":
            pos["status"] = "open"
            pos["filled_ts"] = dt.datetime.fromtimestamp(o.filled_ts, dt.timezone.utc).isoformat()
            pos["journal_id"] = journal.buy(
                pos["symbol"], pos["stake_usd"], pos["entry"], contract=pos["contract"],
                opened_at=pos["filled_ts"][:10], notes=f"paper bot, {pos['action']}, score {pos['score']:.0f}",
                path=trades_path)
            log.append(f"${pos['symbol']}: bought ${pos['stake_usd']:.0f} at {pos['entry']:.6g}")

        net = _net(o.ret, bcfg)
        pos["last_price"] = o.last_price
        pos["value_usd"] = round(pos["stake_usd"] * (1 + net), 2)
        pos["event"] = o.event
        if o.open_fraction <= 0:
            proceeds = pos["stake_usd"] * (1 + net)
            state["cash"] += proceeds
            pos.update(status="closed", closed_ts=now.isoformat(), ret=o.ret, ret_net=net)
            journal.sell(pos["journal_id"], max(pos["entry"] * (1 + o.ret), 1e-12), proceeds_usd=proceeds,
                         closed_at=now.date().isoformat(), notes=f"paper bot exit: {o.event}", path=trades_path)
            why = {"STOP": "stopped out", "TARGET": "took profits", "NONE": f"{bcfg.max_hold_hours:g}h time limit",
                   "GONE": "no price data any more, counted as stopped out"}
            log.append(f"${pos['symbol']}: closed ({why.get(o.event, o.event)}), {net:+.0%} after costs, "
                       f"${proceeds - pos['stake_usd']:+.2f}")
    return log


def realized_today(state: dict, now: dt.datetime) -> float:
    day = now.date().isoformat()
    return sum(p["value_usd"] - p["stake_usd"] for p in state["positions"]
               if p["status"] == "closed" and p.get("closed_ts", "")[:10] == day)


def open_positions(state: dict, picks: list, now: dt.datetime, bcfg: BotConfig) -> List[str]:
    log: List[str] = []
    limit = bcfg.starting_cash * bcfg.daily_loss_limit_pct / 100
    if realized_today(state, now) <= -limit:
        return [f"daily loss limit hit (-${limit:.0f} realized today): no new trades until tomorrow (UTC)"]
    active = [p for p in state["positions"] if p["status"] in ("pending", "open")]
    held = {p["contract"] for p in active}
    cutoff = now - dt.timedelta(hours=bcfg.cooldown_hours)
    recent = {p["contract"] for p in state["positions"]
              if p["status"] in ("closed", "expired") and _ts(p.get("closed_ts") or p["scan_ts"]) >= cutoff}
    for sc in picks:
        if len(active) >= bcfg.max_positions:
            break
        e, pair = sc.entry, sc.pair
        if e is None or e.action == "SKIP" or pair.token_address in held | recent:
            continue
        if state["cash"] < bcfg.stake_usd:
            log.append("not enough paper cash for another position")
            break
        pos = dict(
            id=state["next_id"], symbol=pair.symbol, contract=pair.token_address, pool=pair.pair_address,
            chain=pair.chain, scan_ts=now.isoformat(), action=e.action, price=pair.price_usd, entry=e.entry,
            stop=e.stop, score=round(sc.score, 1), mode=bcfg.mode, stake_usd=bcfg.stake_usd, status="pending",
            value_usd=bcfg.stake_usd,
        )
        state["next_id"] += 1
        state["cash"] -= bcfg.stake_usd
        state["positions"].append(pos)
        active.append(pos)
        held.add(pair.token_address)
        how = "buy at the scan price" if e.action == "BUY NOW" else f"buy if it reaches {e.entry:.6g} within 24h"
        log.append(f"${pair.symbol} (score {sc.score:.0f}): {e.action}, {how}, stop {e.stop:.6g}")
    return log


def run_cycle(state: dict, now: dt.datetime, bcfg: BotConfig, scan_picks: Callable[[], list], history: History,
              trades_path: str, pause_s: float = 0.0) -> List[str]:
    log = update_positions(state, now, bcfg, history, trades_path, pause_s)
    try:
        picks = scan_picks()
    except Exception as exc:  # a failed scan shouldn't stop exits from being managed
        picks = []
        log.append(f"scan failed: {exc}")
    log += open_positions(state, picks, now, bcfg)
    state["runs"] += 1
    state["last_run"] = now.isoformat()
    state["last_log"] = log
    return log


# --- status page ---------------------------------------------------------------------

def render_status(state: dict, now: dt.datetime, bcfg: BotConfig, trades_path: str) -> str:
    pos = state["positions"]
    opened = [p for p in pos if p["status"] == "open"]
    pending = [p for p in pos if p["status"] == "pending"]
    closed = [p for p in pos if p["status"] == "closed"]
    equity = state["cash"] + sum(p["value_usd"] for p in opened) + sum(p["stake_usd"] for p in pending)
    start = state["starting_cash"]
    out = [f"# Paper Bot: {now.strftime('%Y-%m-%d %H:%M')} UTC", ""]
    out.append("> **Paper trading only:** no wallet, no real orders. It trades the scanner's picks under the plan's "
               f"rules with ${bcfg.stake_usd:.0f} per trade and {bcfg.cost_pct_per_side:g}% costs each way, to "
               "find out whether the scan makes money before any real money is automated.")
    out.append("")
    out.append(f"**Equity: ${equity:,.2f}** ({(equity / start - 1):+.1%} vs the ${start:,.0f} start). Cash "
               f"${state['cash']:,.2f}, {len(opened)} open, {len(pending)} pending, {len(closed)} closed. "
               f"Runs: {state['runs']}.")
    out.append("")
    stats = journal.compute_stats(journal.load(trades_path), today=now.date().isoformat())
    out.append(journal.render_stats(stats, account=start).replace("\n", "  \n"))
    out.append("")
    if opened:
        out += ["## Open", "", "| # | Coin | Entry | Now | Value | P&L after costs | Stop | Since |",
                "|---|---|---|---|---|---|---|---|"]
        for p in opened:
            pnl = p["value_usd"] / p["stake_usd"] - 1
            out.append(f"| {p['id']} | ${p['symbol']} | {p['entry']:.6g} | {p.get('last_price') or 0:.6g} "
                       f"| ${p['value_usd']:.2f} | {pnl:+.0%} | {p['stop']:.6g} | {p.get('filled_ts', '')[:16]} |")
        out.append("")
    if pending:
        out += ["## Waiting for entry", "", "| # | Coin | Score | Entry | Stop | Expires |", "|---|---|---|---|---|---|"]
        for p in pending:
            exp = _ts(p["scan_ts"]) + dt.timedelta(hours=bcfg.pending_hours)
            out.append(f"| {p['id']} | ${p['symbol']} | {p['score']:.0f} | {p['entry']:.6g} | {p['stop']:.6g} "
                       f"| {exp.strftime('%m-%d %H:%M')} |")
        out.append("")
    if closed:
        out += ["## Last closed", "", "| # | Coin | Result | After costs | P&L | Closed |", "|---|---|---|---|---|---|"]
        for p in sorted(closed, key=lambda p: p["closed_ts"], reverse=True)[:10]:
            out.append(f"| {p['id']} | ${p['symbol']} | {p.get('event', '')} | {p['ret_net']:+.0%} "
                       f"| ${p['value_usd'] - p['stake_usd']:+.2f} | {p['closed_ts'][:16]} |")
        out.append("")
    if state.get("last_log"):
        out += ["## Last run", ""] + [f"- {line}" for line in state["last_log"]] + [""]
    return "\n".join(out)


# --- CLI ---------------------------------------------------------------------------

def _live_scan(cfg: Config, now: dt.datetime) -> list:
    from .scan import run_scan

    picks: list = []
    run_scan(cfg, now, picks_out=picks)
    return picks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="run-bot", description="Paper-trading bot for the meme coin scanner")
    ap.add_argument("--once", action="store_true", help="Run one cycle and exit (default)")
    ap.add_argument("--loop", action="store_true", help="Run forever, one cycle every --interval minutes")
    ap.add_argument("--interval", type=float, default=60.0, help="Minutes between cycles with --loop")
    ap.add_argument("--status", action="store_true", help="Print the status page without trading")
    ap.add_argument("--reset", action="store_true", help="Start over with fresh paper cash (keeps the old trade log)")
    ap.add_argument("--fast", action="store_true", help="Trade fast-mode picks instead of the standard scan")
    ap.add_argument("--stake", type=float, default=None, help="Paper dollars per trade (default 50)")
    ap.add_argument("--cash", type=float, default=None, help="Starting paper cash (new or reset state only)")
    ap.add_argument("--state", default=DEFAULT_STATE)
    ap.add_argument("--trades", default=DEFAULT_TRADES)
    ap.add_argument("--status-path", default=DEFAULT_STATUS)
    args = ap.parse_args(argv)

    bcfg = BotConfig(mode="fast" if args.fast else "standard")
    if args.stake:
        bcfg = replace(bcfg, stake_usd=args.stake)
    if args.cash:
        bcfg = replace(bcfg, starting_cash=args.cash)
    state = new_state(bcfg) if args.reset else load_state(args.state, bcfg)
    cfg = fast_config() if args.fast else Config()
    timeout = cfg.request_timeout_s

    while True:
        now = dt.datetime.now(dt.timezone.utc)
        if not args.status:
            log = run_cycle(
                state, now, bcfg, scan_picks=lambda: _live_scan(cfg, now),
                history=lambda pool, since: review.fetch_history(pool, "solana", since, bcfg.max_hold_hours, timeout),
                trades_path=args.trades, pause_s=cfg.candle_pause_s,
            )
            save_state(state, args.state)
            for line in log:
                print(line, file=sys.stderr)
        page = render_status(state, now, bcfg, args.trades)
        Path(args.status_path).parent.mkdir(parents=True, exist_ok=True)
        Path(args.status_path).write_text(page)
        print(page)
        if not args.loop or args.status:
            return 0
        time.sleep(args.interval * 60)


if __name__ == "__main__":
    raise SystemExit(main())
