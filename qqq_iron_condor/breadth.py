"""Mega-cap breadth: did QQQ's biggest holdings move with it in the first hour?

NVDA, MSFT, AAPL, AMZN, META, GOOGL and AVGO are roughly half of QQQ. A
QQQ move that most of them share is broad; one that only a couple are
driving (or that they're fighting) is fragile. The read counts how many
of the seven closed their first hourly bar (9:30-10:30 ET) above / below
that bar's open, the same window first-hour momentum uses.

Rules built on it (all wait for the first hour):
  - "breadth": call if at least `min_agree` of the seven were up, put if at
    least `min_agree` were down, otherwise no trade;
  - "first hour + breadth": first-hour momentum's side, taken only when at
    least `min_agree` of the seven moved the same way.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

import pandas as pd

MEGACAPS = ("NVDA", "MSFT", "AAPL", "AMZN", "META", "GOOGL", "AVGO")


@dataclass
class BreadthRead:
    ready: bool
    up: int = 0
    down: int = 0
    total: int = 0
    movers: dict = None  # symbol -> first-hour % move
    detail: str = ""

    def side(self, min_agree: int) -> Optional[str]:
        if not self.ready:
            return None
        if self.up >= min_agree:
            return "call"
        if self.down >= min_agree:
            return "put"
        return None


def first_bar_moves(hourly: dict, date: dt.date) -> dict:
    """symbol -> % move of that symbol's first hourly bar on `date`
    (close vs open). Symbols without a bar that day are left out."""
    moves = {}
    for sym, bars in hourly.items():
        day = bars[bars.index.date == date]
        if len(day):
            o, c = float(day["Open"].iloc[0]), float(day["Close"].iloc[0])
            if o > 0:
                moves[sym] = (c / o - 1.0) * 100.0
    return moves


def breadth_read(moves: dict) -> BreadthRead:
    if not moves:
        return BreadthRead(False, detail="No mega-cap first-hour bars available.")
    up = sum(1 for m in moves.values() if m > 0)
    down = sum(1 for m in moves.values() if m < 0)
    parts = ", ".join(f"{s} {m:+.1f}%" for s, m in sorted(moves.items(), key=lambda kv: -kv[1]))
    return BreadthRead(True, up, down, len(moves), moves, f"{up} of {len(moves)} up, {down} down in the first hour ({parts}).")


def make_backtest_direction(hourly: dict, min_agree: int = 5, with_first_hour: bool = True):
    """Direction callable for daily_target_backtest.simulate(direction=...,
    entry_bar=1). `hourly` maps each mega-cap symbol to its hourly bars.
    Pre-computes each day's breadth once."""
    by_day: dict = {}
    for sym, bars in hourly.items():
        firsts = bars.groupby(bars.index.date).head(1)
        for ts, row in firsts.iterrows():
            o, c = float(row["Open"]), float(row["Close"])
            if o > 0:
                by_day.setdefault(ts.date(), {})[sym] = (c / o - 1.0) * 100.0

    def direction(i, bars_before_entry: pd.DataFrame) -> Optional[str]:
        if bars_before_entry is None or len(bars_before_entry) == 0:
            return None
        read = breadth_read(by_day.get(bars_before_entry.index[0].date(), {}))
        side = read.side(min_agree)
        if side is None or not with_first_hour:
            return side
        move = float(bars_before_entry["Close"].iloc[-1] - bars_before_entry["Open"].iloc[0])
        fh = "call" if move > 0 else ("put" if move < 0 else None)
        return side if fh == side else None

    return direction


def fetch_hourly(symbols=MEGACAPS, period: str = "5d") -> dict:
    """Hourly bars per symbol in one batched Yahoo request (index = ET wall
    time). Symbols that come back empty are left out."""
    import yfinance as yf

    raw = yf.download(list(symbols), period=period, interval="1h", group_by="ticker", progress=False, auto_adjust=False)
    out = {}
    for sym in symbols:
        try:
            df = raw[sym].dropna(how="all")
        except KeyError:
            continue
        if len(df):
            df.index = pd.to_datetime(df.index).tz_localize(None) if df.index.tz is not None else pd.to_datetime(df.index)
            out[sym] = df
    return out
