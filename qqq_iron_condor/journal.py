"""Trade journal: a durable record of iron condors you actually opened
(not the daily scan's suggestions, and not the backtest's simulated
trades), so realized performance can be tracked over time.

Storage is a plain CSV at `journal/trades.csv` (or a path you choose) --
git-diffable, human-readable, and requires no database. Nothing here is
written automatically by the scan: you (or a chat session helping you)
log a trade when you actually place it, and close it when you actually
exit it. That's a deliberate choice -- the scanner recommends, it never
executes, so the journal should only ever reflect trades a human decided
to take.

Realized P&L on a close is computed one of two ways:
  - `exit_debit`: what it actually cost to buy back the condor (a normal
    early close) -- pnl = (credit - exit_debit) * 100 * contracts.
  - `settle_underlying`: the underlying's price at expiration, if you let
    it expire -- pnl is derived from the condor's intrinsic value at that
    price, same settlement math as the backtest.
Exactly one of the two must be given; mixing them (or giving neither)
is almost always a data-entry mistake, so it's a hard error rather than
a silent guess.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

DEFAULT_JOURNAL_PATH = "journal/trades.csv"

COLUMNS = [
    "id", "status", "opened_at", "closed_at", "label", "symbol", "expiration", "dte_at_entry",
    "short_call", "long_call", "short_put", "long_put", "contracts",
    "credit", "max_profit", "max_loss", "call_width", "put_width",
    "entry_underlying", "vix_at_entry",
    "exit_underlying", "exit_value", "realized_pnl",
    "notes",
]


@dataclass
class NewTradeInput:
    label: str
    symbol: str
    expiration: str
    dte_at_entry: int
    short_call: float
    long_call: float
    short_put: float
    long_put: float
    credit: float
    contracts: int = 1
    entry_underlying: Optional[float] = None
    vix_at_entry: Optional[float] = None
    opened_at: Optional[dt.date] = None
    notes: str = ""


@dataclass
class LabelStats:
    label: str
    num_trades: int
    win_rate_pct: float
    avg_win: float
    avg_loss: float
    expectancy: float
    total_pnl: float
    profit_factor: float
    max_drawdown: float


def load_journal(path: str = DEFAULT_JOURNAL_PATH) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(p)
    for col in COLUMNS:
        if col not in df.columns:
            df[col] = pd.NA
    # Keep every column as plain Python objects rather than a single
    # inferred dtype (e.g. float64 when a column is all-NaN before any
    # trade is closed) -- otherwise later in-place assignment of a date
    # string into a since-closed trade's row raises a dtype error.
    return df[COLUMNS].astype(object)


def _save_journal(df: pd.DataFrame, path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(p, index=False)


def add_trade(trade: NewTradeInput, path: str = DEFAULT_JOURNAL_PATH) -> int:
    df = load_journal(path)
    next_id = int(df["id"].max()) + 1 if not df.empty else 1

    call_width = trade.long_call - trade.short_call
    put_width = trade.short_put - trade.long_put
    if call_width <= 0 or put_width <= 0:
        raise ValueError("long_call must be above short_call and long_put must be below short_put")
    max_profit = trade.credit
    max_loss = max(call_width, put_width) - trade.credit

    row = {
        "id": next_id,
        "status": "OPEN",
        "opened_at": (trade.opened_at or dt.date.today()).isoformat(),
        "closed_at": pd.NA,
        "label": trade.label,
        "symbol": trade.symbol,
        "expiration": trade.expiration,
        "dte_at_entry": trade.dte_at_entry,
        "short_call": trade.short_call,
        "long_call": trade.long_call,
        "short_put": trade.short_put,
        "long_put": trade.long_put,
        "contracts": trade.contracts,
        "credit": trade.credit,
        "max_profit": round(max_profit, 4),
        "max_loss": round(max_loss, 4),
        "call_width": call_width,
        "put_width": put_width,
        "entry_underlying": trade.entry_underlying,
        "vix_at_entry": trade.vix_at_entry,
        "exit_underlying": pd.NA,
        "exit_value": pd.NA,
        "realized_pnl": pd.NA,
        "notes": trade.notes,
    }
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    _save_journal(df, path)
    return next_id


def _settlement_value(short_call: float, long_call: float, short_put: float, long_put: float, underlying: float) -> float:
    """Intrinsic value of the condor's spreads at expiration -- what it
    would cost to close (or, equivalently, the loss beyond the credit)."""
    call_width = long_call - short_call
    put_width = short_put - long_put
    call_loss = min(max(underlying - short_call, 0.0), call_width)
    put_loss = min(max(short_put - underlying, 0.0), put_width)
    return call_loss + put_loss


def close_trade(
    trade_id: int,
    closed_at: Optional[dt.date] = None,
    exit_debit: Optional[float] = None,
    settle_underlying: Optional[float] = None,
    notes: Optional[str] = None,
    path: str = DEFAULT_JOURNAL_PATH,
) -> float:
    if (exit_debit is None) == (settle_underlying is None):
        raise ValueError("Provide exactly one of exit_debit or settle_underlying")

    df = load_journal(path)
    matches = df.index[df["id"] == trade_id]
    if len(matches) == 0:
        raise ValueError(f"No journal entry with id {trade_id}")
    idx = matches[0]
    if df.loc[idx, "status"] == "CLOSED":
        raise ValueError(f"Trade {trade_id} is already closed")

    credit = float(df.loc[idx, "credit"])
    contracts = float(df.loc[idx, "contracts"])

    if settle_underlying is not None:
        exit_value = _settlement_value(
            float(df.loc[idx, "short_call"]), float(df.loc[idx, "long_call"]),
            float(df.loc[idx, "short_put"]), float(df.loc[idx, "long_put"]),
            settle_underlying,
        )
        df.loc[idx, "exit_underlying"] = settle_underlying
    else:
        exit_value = float(exit_debit)

    realized_pnl = (credit - exit_value) * 100.0 * contracts

    df.loc[idx, "status"] = "CLOSED"
    df.loc[idx, "closed_at"] = (closed_at or dt.date.today()).isoformat()
    df.loc[idx, "exit_value"] = round(exit_value, 4)
    df.loc[idx, "realized_pnl"] = round(realized_pnl, 2)
    if notes:
        existing = df.loc[idx, "notes"]
        df.loc[idx, "notes"] = f"{existing} | {notes}" if isinstance(existing, str) and existing else notes

    _save_journal(df, path)
    return realized_pnl


def list_open(path: str = DEFAULT_JOURNAL_PATH) -> pd.DataFrame:
    df = load_journal(path)
    return df[df["status"] == "OPEN"]


def _label_stats(label: str, closed: pd.DataFrame) -> LabelStats:
    if closed.empty:
        return LabelStats(label, 0, float("nan"), float("nan"), float("nan"), float("nan"), 0.0, float("nan"), 0.0)

    pnls = closed["realized_pnl"].astype(float).tolist()
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    equity = np.cumsum(pnls)
    running_peak = np.maximum.accumulate(equity)
    max_drawdown = float((running_peak - equity).max())

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    return LabelStats(
        label=label,
        num_trades=len(pnls),
        win_rate_pct=len(wins) / len(pnls) * 100.0,
        avg_win=(sum(wins) / len(wins)) if wins else float("nan"),
        avg_loss=(sum(losses) / len(losses)) if losses else float("nan"),
        expectancy=sum(pnls) / len(pnls),
        total_pnl=sum(pnls),
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else float("nan"),
        max_drawdown=max_drawdown,
    )


def summarize(df: pd.DataFrame) -> dict:
    closed = df[df["status"] == "CLOSED"].copy()
    summaries = {"All": _label_stats("All", closed)}
    for label in sorted(closed["label"].dropna().unique()):
        summaries[label] = _label_stats(label, closed[closed["label"] == label])
    return summaries


def _fmt(x, decimals: int = 2) -> str:
    if x is None or (isinstance(x, float) and x != x):
        return "n/a"
    return f"{x:.{decimals}f}"


def render_journal_report(df: pd.DataFrame) -> str:
    generated_at = dt.datetime.now()
    open_trades = df[df["status"] == "OPEN"]
    summaries = summarize(df)

    parts = [
        f"# Trade Journal -- {generated_at.strftime('%Y-%m-%d %H:%M')}",
        "",
        "> Realized performance from trades actually placed (logged via "
        "`python -m qqq_iron_condor.journal`), not the daily scan's suggestions "
        "or the backtest's simulated trades.",
        "",
        "## Open positions",
        "",
    ]
    if open_trades.empty:
        parts.append("_No open positions logged._")
    else:
        parts.append("| ID | Opened | Label | Expiration | Short Call/Put | Credit | Max Loss |")
        parts.append("|---|---|---|---|---|---|---|")
        for _, r in open_trades.iterrows():
            parts.append(
                f"| {int(r['id'])} | {r['opened_at']} | {r['label']} | {r['expiration']} | "
                f"{_fmt(r['short_call'],0)} / {_fmt(r['short_put'],0)} | ${_fmt(r['credit'])} | ${_fmt(r['max_loss'])} |"
            )
    parts.append("")

    parts.append("## Realized performance")
    parts.append("")
    all_stats = summaries["All"]
    if all_stats.num_trades == 0:
        parts.append("_No closed trades logged yet._")
    else:
        parts.append("| Label | Trades | Win rate | Avg win | Avg loss | Expectancy | Total P&L | Profit factor | Max drawdown |")
        parts.append("|---|---|---|---|---|---|---|---|---|")
        for label, s in summaries.items():
            parts.append(
                f"| {label} | {s.num_trades} | {_fmt(s.win_rate_pct,1)}% | ${_fmt(s.avg_win)} | ${_fmt(s.avg_loss)} | "
                f"${_fmt(s.expectancy)} | ${_fmt(s.total_pnl)} | {_fmt(s.profit_factor,2)} | ${_fmt(s.max_drawdown)} |"
            )
    parts.append("")

    return "\n".join(parts)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="QQQ iron condor trade journal")
    parser.add_argument("--path", default=DEFAULT_JOURNAL_PATH, help="Journal CSV path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="Log a newly opened trade")
    p_add.add_argument("--label", required=True)
    p_add.add_argument("--symbol", default="QQQ")
    p_add.add_argument("--expiration", required=True)
    p_add.add_argument("--dte", type=int, required=True)
    p_add.add_argument("--short-call", type=float, required=True)
    p_add.add_argument("--long-call", type=float, required=True)
    p_add.add_argument("--short-put", type=float, required=True)
    p_add.add_argument("--long-put", type=float, required=True)
    p_add.add_argument("--credit", type=float, required=True)
    p_add.add_argument("--contracts", type=int, default=1)
    p_add.add_argument("--entry-underlying", type=float, default=None)
    p_add.add_argument("--vix", type=float, default=None)
    p_add.add_argument("--opened-at", default=None, help="YYYY-MM-DD, default today")
    p_add.add_argument("--notes", default="")

    p_close = sub.add_parser("close", help="Close a previously logged trade")
    p_close.add_argument("--id", type=int, required=True)
    p_close.add_argument("--closed-at", default=None, help="YYYY-MM-DD, default today")
    p_close.add_argument("--exit-debit", type=float, default=None, help="What it cost to buy back the condor")
    p_close.add_argument("--settle-underlying", type=float, default=None, help="Underlying price if held to expiration")
    p_close.add_argument("--notes", default=None)

    sub.add_parser("list", help="List open positions")
    sub.add_parser("report", help="Render realized-performance report")

    args = parser.parse_args(argv)

    if args.command == "add":
        trade = NewTradeInput(
            label=args.label, symbol=args.symbol, expiration=args.expiration, dte_at_entry=args.dte,
            short_call=args.short_call, long_call=args.long_call, short_put=args.short_put, long_put=args.long_put,
            credit=args.credit, contracts=args.contracts, entry_underlying=args.entry_underlying,
            vix_at_entry=args.vix, opened_at=dt.date.fromisoformat(args.opened_at) if args.opened_at else None,
            notes=args.notes,
        )
        new_id = add_trade(trade, path=args.path)
        print(f"Logged trade id {new_id} ({args.label}, {args.expiration}).")
    elif args.command == "close":
        pnl = close_trade(
            args.id,
            closed_at=dt.date.fromisoformat(args.closed_at) if args.closed_at else None,
            exit_debit=args.exit_debit, settle_underlying=args.settle_underlying, notes=args.notes,
            path=args.path,
        )
        print(f"Closed trade {args.id}: realized P&L ${pnl:.2f}")
    elif args.command == "list":
        open_df = list_open(args.path)
        print(open_df.to_string(index=False) if not open_df.empty else "No open positions.")
    elif args.command == "report":
        df = load_journal(args.path)
        report_md = render_journal_report(df)
        print(report_md)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
