"""5-minute session bars: today's closed bars and the past full sessions.

Same conventions as trader-timemachine: a session is the 78 five-minute bars
from 9:30 to 15:55 (New York), a "full" session has all 78, and only bars
that have finished (start + 5 minutes <= now) count for today.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd

SESSION_BARS = 78
OPEN = dt.time(9, 30)


@dataclass
class SessionBars:
    today: np.ndarray  # (n, 4) open/high/low/close of today's closed bars, slot order
    past: list  # [(date, (78, 4) ndarray)] full sessions before today, oldest first
    short_sessions: int  # past sessions left out for missing bars (half days, halts)


def slot_of(ts: pd.Timestamp) -> int | None:
    """Bar slot 0..77 for a bar starting at ts (New York wall time), or None
    when it is off the 5-minute grid or outside 9:30-15:55."""
    minutes = (ts.hour * 60 + ts.minute) - (OPEN.hour * 60 + OPEN.minute)
    if ts.second or minutes < 0 or minutes % 5:
        return None
    slot = minutes // 5
    return slot if slot < SESSION_BARS else None


def bar_close_time(day: dt.date, slot: int) -> dt.datetime:
    """When bar `slot` finishes: the 9:30 bar (slot 0) closes at 9:35."""
    return dt.datetime.combine(day, OPEN) + dt.timedelta(minutes=5 * (slot + 1))


def clock_to_slot_close(clock: str) -> int:
    """The slot whose bar CLOSES at clock ("15:55" -> 76)."""
    h, m = (int(x) for x in clock.split(":"))
    return ((h * 60 + m) - (OPEN.hour * 60 + OPEN.minute)) // 5 - 1


def to_new_york(df: pd.DataFrame) -> pd.DataFrame:
    idx = pd.to_datetime(df.index)
    if idx.tz is not None:
        idx = idx.tz_convert("America/New_York").tz_localize(None)
    out = df.copy()
    out.index = idx
    return out


def split_sessions(df: pd.DataFrame, today: dt.date, now: dt.datetime) -> SessionBars:
    """df: 5-minute OHLC bars indexed by New York wall time (naive)."""
    days: dict = {}
    for ts, row in df.iterrows():
        slot = slot_of(ts)
        if slot is None or any(pd.isna(row[c]) for c in ("Open", "High", "Low", "Close")):
            continue
        days.setdefault(ts.date(), {})[slot] = (
            float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
        )

    past, short = [], 0
    for day in sorted(days):
        if day >= today:
            continue
        bars = days[day]
        if len(bars) == SESSION_BARS:
            past.append((day, np.array([bars[i] for i in range(SESSION_BARS)], dtype=float)))
        else:
            short += 1

    todays = days.get(today, {})
    closed = []
    for slot in range(SESSION_BARS):
        if slot not in todays or bar_close_time(today, slot) > now:
            break
        closed.append(todays[slot])
    today_arr = np.array(closed, dtype=float) if closed else np.empty((0, 4))
    return SessionBars(today=today_arr, past=past, short_sessions=short)
