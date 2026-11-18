"""Trade log for meme coins you actually bought (not the scan's suggestions).

One CSV row per trade at `journal/memecoin_trades.csv`. A buy opens a
trade; sells close it, all at once or in parts (the take-profit ladder
sells 50% / 25% / 25%), and the row keeps running totals. `stats` gives
the Phase 1 numbers from the trading plan: win rate, average win and
loss, expectancy per trade, and progress toward 50 closed trades.

Usage:
    trade-log buy --symbol WEALTH --contract <mint> --usd 200 --price 0.000347
    trade-log sell --id 1 --price 0.00093              # sell everything left
    trade-log sell --id 1 --price 0.00070 --fraction 0.5
    trade-log correct --id 1 --proceeds 530.12          # real fill for an estimated exit
    trade-log stats [--account 10000]
    trade-log list
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_PATH = "journal/memecoin_trades.csv"
PHASE1_TRADES = 50
DAILY_LOSS_LIMIT_PCT = 3.0

COLUMNS = [
    "id", "status", "symbol", "contract", "opened_at", "entry_price", "cost_usd", "tokens",
    "tokens_sold", "proceeds_usd", "closed_at", "exit_price", "pnl_usd", "pnl_pct",
    "exit_estimated", "followed_rules", "notes",
]


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def load(path: str = DEFAULT_PATH) -> List[Dict[str, str]]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open(newline="") as f:
        return list(csv.DictReader(f))


def save(rows: List[Dict[str, str]], path: str = DEFAULT_PATH) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in COLUMNS})


def _num(s: str) -> float:
    return float(s) if s not in ("", None) else 0.0


def _g(x: float) -> str:
    return f"{x:.10g}"


def buy(symbol: str, usd: float, price: float, contract: str = "", opened_at: Optional[str] = None,
        followed_rules: bool = True, notes: str = "", path: str = DEFAULT_PATH) -> int:
    if usd <= 0 or price <= 0:
        raise ValueError("usd and price must be positive")
    rows = load(path)
    new_id = max((int(r["id"]) for r in rows), default=0) + 1
    rows.append({
        "id": str(new_id), "status": "OPEN", "symbol": symbol.lstrip("$").upper(), "contract": contract,
        "opened_at": opened_at or _today(), "entry_price": _g(price), "cost_usd": f"{usd:.2f}",
        "tokens": _g(usd / price), "tokens_sold": "0", "proceeds_usd": "0",
        "exit_estimated": "no", "followed_rules": "yes" if followed_rules else "no", "notes": notes,
    })
    save(rows, path)
    return new_id


def sell(trade_id: int, price: float, fraction: Optional[float] = None, proceeds_usd: Optional[float] = None,
         closed_at: Optional[str] = None, estimated: bool = False, followed_rules: Optional[bool] = None,
         notes: str = "", path: str = DEFAULT_PATH) -> Dict[str, str]:
    """Sell `fraction` of the ORIGINAL position (default: everything left).

    `proceeds_usd` overrides price x tokens when you know the exact dollars
    received (fees and slippage included).
    """
    if price <= 0:
        raise ValueError("price must be positive")
    rows = load(path)
    row = next((r for r in rows if r["id"] == str(trade_id)), None)
    if row is None:
        raise ValueError(f"no trade with id {trade_id}")
    if row["status"] == "CLOSED":
        raise ValueError(f"trade {trade_id} is already closed")

    tokens, sold = _num(row["tokens"]), _num(row["tokens_sold"])
    left = tokens - sold
    qty = left if fraction is None else tokens * fraction
    if qty <= 0 or qty > left * (1 + 1e-9):
        raise ValueError(f"can't sell {qty:,.0f} tokens; {left:,.0f} left")
    qty = min(qty, left)

    sold += qty
    proceeds = _num(row["proceeds_usd"]) + (proceeds_usd if proceeds_usd is not None else qty * price)
    row["tokens_sold"], row["proceeds_usd"] = _g(sold), f"{proceeds:.2f}"
    row["exit_price"] = _g(proceeds / sold)  # average exit
    if estimated:
        row["exit_estimated"] = "yes"
    if followed_rules is not None:
        row["followed_rules"] = "yes" if followed_rules else "no"
    if notes:
        row["notes"] = f"{row['notes']} | {notes}" if row["notes"] else notes

    if sold >= tokens * (1 - 1e-9):
        cost = _num(row["cost_usd"])
        row["status"], row["closed_at"] = "CLOSED", closed_at or _today()
        row["pnl_usd"] = f"{proceeds - cost:.2f}"
        row["pnl_pct"] = f"{(proceeds / cost - 1) * 100:.1f}"
    else:
        row["status"] = "PARTIAL"
    save(rows, path)
    return row


@dataclass
class Stats:
    closed: int
    open: int
    wins: int
    losses: int
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    expectancy_pct: float  # average % return per closed trade
    total_pnl: float
    pnl_today: float
    estimated_exits: int
    rule_breaks: int


def compute_stats(rows: List[Dict[str, str]], today: Optional[str] = None) -> Stats:
    today = today or _today()
    closed = [r for r in rows if r["status"] == "CLOSED"]
    rets = [_num(r["pnl_pct"]) for r in closed]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    n = len(closed)
    return Stats(
        closed=n,
        open=sum(1 for r in rows if r["status"] in ("OPEN", "PARTIAL")),
        wins=len(wins),
        losses=len(losses),
        win_rate=len(wins) / n * 100 if n else 0.0,
        avg_win_pct=sum(wins) / len(wins) if wins else 0.0,
        avg_loss_pct=sum(losses) / len(losses) if losses else 0.0,
        expectancy_pct=sum(rets) / n if n else 0.0,
        total_pnl=sum(_num(r["pnl_usd"]) for r in closed),
        pnl_today=sum(_num(r["pnl_usd"]) for r in closed if r["closed_at"] == today),
        estimated_exits=sum(1 for r in closed if r["exit_estimated"] == "yes"),
        rule_breaks=sum(1 for r in rows if r["followed_rules"] == "no"),
    )


def _avg(pct: float, count: int) -> str:
    return f"{pct:+.1f}%" if count else "n/a"


def correct(trade_id: int, proceeds_usd: Optional[float] = None, exit_price: Optional[float] = None,
            path: str = DEFAULT_PATH) -> Dict[str, str]:
    """Replace a closed trade's estimated exit with the real fill (total proceeds or average price)."""
    if (proceeds_usd is None) == (exit_price is None):
        raise ValueError("give exactly one of proceeds_usd or exit_price")
    rows = load(path)
    row = next((r for r in rows if r["id"] == str(trade_id)), None)
    if row is None or row["status"] != "CLOSED":
        raise ValueError(f"no closed trade with id {trade_id}")
    sold, cost = _num(row["tokens_sold"]), _num(row["cost_usd"])
    proceeds = proceeds_usd if proceeds_usd is not None else sold * exit_price
    row["proceeds_usd"], row["exit_price"] = f"{proceeds:.2f}", _g(proceeds / sold)
    row["pnl_usd"], row["pnl_pct"] = f"{proceeds - cost:.2f}", f"{(proceeds / cost - 1) * 100:.1f}"
    row["exit_estimated"] = "no"
    save(rows, path)
    return row


def render_stats(s: Stats, account: Optional[float] = None) -> str:
    lines = [f"Phase 1 progress: {s.closed}/{PHASE1_TRADES} closed trades ({s.open} open)"]
    if s.closed:
        lines += [
            f"Win rate: {s.win_rate:.0f}% ({s.wins} wins, {s.losses} losses)",
            f"Average win: {_avg(s.avg_win_pct, s.wins)}   Average loss: {_avg(s.avg_loss_pct, s.losses)}",
            f"Expectancy: {s.expectancy_pct:+.1f}% per trade",
            f"Total P&L: ${s.total_pnl:+,.2f}   Today: ${s.pnl_today:+,.2f}",
        ]
        if s.closed < PHASE1_TRADES:
            lines.append(f"Too few trades to judge the edge yet; keep size fixed until {PHASE1_TRADES}.")
    if account:
        limit = account * DAILY_LOSS_LIMIT_PCT / 100
        status = "HIT: stop trading today" if s.pnl_today <= -limit else "ok"
        lines.append(f"Daily loss limit (-{DAILY_LOSS_LIMIT_PCT:g}% of ${account:,.0f} = -${limit:,.0f}): {status}")
    if s.estimated_exits:
        lines.append(f"Note: {s.estimated_exits} trade(s) use an estimated exit price; update them with real fills.")
    if s.rule_breaks:
        lines.append(f"Rule breaks logged: {s.rule_breaks}")
    return "\n".join(lines)


def render_list(rows: List[Dict[str, str]]) -> str:
    out = ["| # | Coin | Status | Opened | Entry | Cost | Exit | Proceeds | P&L |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        pnl = f"${_num(r['pnl_usd']):+,.2f} ({_num(r['pnl_pct']):+.0f}%)" if r["status"] == "CLOSED" else ""
        est = " (est.)" if r["exit_estimated"] == "yes" else ""
        out.append(
            f"| {r['id']} | ${r['symbol']} | {r['status']} | {r['opened_at']} | {r['entry_price']} "
            f"| ${_num(r['cost_usd']):,.2f} | {r['exit_price']}{est} | ${_num(r['proceeds_usd']):,.2f} | {pnl} |"
        )
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="trade-log", description="Meme coin trade log")
    parser.add_argument("--path", default=DEFAULT_PATH)
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("buy", help="Log a buy (opens a trade)")
    b.add_argument("--symbol", required=True)
    b.add_argument("--usd", type=float, required=True, help="Dollars spent")
    b.add_argument("--price", type=float, required=True, help="Fill price")
    b.add_argument("--contract", default="")
    b.add_argument("--opened-at", default=None, help="YYYY-MM-DD, default today (UTC)")
    b.add_argument("--broke-rules", action="store_true", help="Mark the entry as breaking the plan's rules")
    b.add_argument("--notes", default="")

    s = sub.add_parser("sell", help="Log a sell (all that's left, or a fraction of the original)")
    s.add_argument("--id", type=int, required=True)
    s.add_argument("--price", type=float, required=True)
    s.add_argument("--fraction", type=float, default=None, help="Share of the ORIGINAL position, e.g. 0.5")
    s.add_argument("--proceeds", type=float, default=None, help="Exact dollars received, if known")
    s.add_argument("--closed-at", default=None, help="YYYY-MM-DD, default today (UTC)")
    s.add_argument("--estimated", action="store_true", help="The price is an estimate, not the real fill")
    s.add_argument("--broke-rules", action="store_true")
    s.add_argument("--notes", default="")

    c = sub.add_parser("correct", help="Replace a closed trade's estimated exit with the real fill")
    c.add_argument("--id", type=int, required=True)
    c.add_argument("--proceeds", type=float, default=None, help="Total dollars received")
    c.add_argument("--price", type=float, default=None, help="Average sell price")

    st = sub.add_parser("stats", help="Phase 1 numbers: win rate, expectancy, daily loss limit")
    st.add_argument("--account", type=float, default=None, help="Account size in USD for the daily loss limit")
    sub.add_parser("list", help="All trades as a table")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "buy":
            tid = buy(args.symbol, args.usd, args.price, args.contract, args.opened_at,
                      not args.broke_rules, args.notes, args.path)
            print(f"Logged trade #{tid}: bought ${args.usd:,.2f} of ${args.symbol.lstrip('$').upper()} at {args.price:g}")
        elif args.cmd == "sell":
            row = sell(args.id, args.price, args.fraction, args.proceeds, args.closed_at, args.estimated,
                       False if args.broke_rules else None, args.notes, args.path)
            msg = f"Trade #{row['id']} ${row['symbol']}: {row['status']}"
            if row["status"] == "CLOSED":
                msg += f", P&L ${_num(row['pnl_usd']):+,.2f} ({_num(row['pnl_pct']):+.1f}%)"
            print(msg)
        elif args.cmd == "correct":
            row = correct(args.id, args.proceeds, args.price, args.path)
            print(f"Trade #{row['id']} ${row['symbol']}: P&L ${_num(row['pnl_usd']):+,.2f} ({_num(row['pnl_pct']):+.1f}%)")
        elif args.cmd == "stats":
            print(render_stats(compute_stats(load(args.path)), args.account))
        else:
            print(render_list(load(args.path)))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
