"""10-year Treasury yield read for the daily $ target plan.

Rising long-term yields raise the discount rate on future earnings, which
hits long-duration growth stocks -- most of QQQ -- hardest. A fast rise
(a "spike") is often what's behind a Nasdaq pullback. There's also a
popular idea of a "bond put": when the bond market gets disorderly,
policymakers (e.g. the April 9, 2025 tariff pause, when the 10-year had
jumped ~50bp in a week) step in with market-friendly news, and stocks
bounce.

The read uses Yahoo's ^TNX (the 10-year yield in percent; 5.10 = 5.10%):
  - level, and its change over 1, 5 and 20 sessions in basis points;
  - "spiking" when the 5-session rise is >= `spike_bp_5d`, "rising" /
    "falling" for a 5-session move of at least `trend_bp_5d` either way,
    otherwise "calm";
  - whether the yield is at its 52-week high.

`rates_backtest.py` tests whether any of this helps -- see the README.
The one lead it found: first-hour-momentum *puts* taken while the 10-year
was spiking (as of the prior close) lost money -- consistent with the
"bond put", but only 13 trades -- so the plan warns on those days rather
than skipping them.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class YieldParams:
    spike_bp_5d: float = 20.0  # 5-session rise that counts as a spike
    trend_bp_5d: float = 8.0  # smaller 5-session moves read as "calm"
    high_lookback: int = 252  # sessions in the 52-week high check


@dataclass
class TenYearRead:
    ready: bool
    level: Optional[float] = None  # percent
    change_1d_bp: Optional[float] = None
    change_5d_bp: Optional[float] = None
    change_20d_bp: Optional[float] = None
    high_52w: Optional[float] = None
    at_52w_high: bool = False
    state: str = "unknown"  # "spiking", "rising", "falling", "calm"
    detail: str = ""
    prior_5d_bp: Optional[float] = None  # 5-session change as of the prior close (what the backtest used)
    spike_bp: float = YieldParams.spike_bp_5d

    @property
    def spiking_at_prior_close(self) -> bool:
        return self.prior_5d_bp is not None and self.prior_5d_bp >= self.spike_bp


def classify(change_5d_bp: float, p: YieldParams = YieldParams()) -> str:
    if change_5d_bp >= p.spike_bp_5d:
        return "spiking"
    if change_5d_bp >= p.trend_bp_5d:
        return "rising"
    if change_5d_bp <= -p.trend_bp_5d:
        return "falling"
    return "calm"


def ten_year_read(history: pd.DataFrame, today: Optional[dt.date] = None, p: YieldParams = YieldParams()) -> TenYearRead:
    """Read from daily ^TNX bars. During the session Yahoo's last daily bar
    is today's, at the latest price, so "1d" is today's move so far; with
    `today`, that bar is left out of `prior_5d_bp`."""
    if history is None or len(history) < 22:
        return TenYearRead(False, detail="Not enough 10-year yield history.")
    close = history["Close"].astype(float).dropna()
    prior = close.iloc[:-1] if today is not None and pd.Timestamp(close.index[-1]).date() == today else close
    prior_5d = (float(prior.iloc[-1]) - float(prior.iloc[-6])) * 100.0
    level = float(close.iloc[-1])
    ch = {n: (level - float(close.iloc[-1 - n])) * 100.0 for n in (1, 5, 20)}
    high = float(close.tail(p.high_lookback).max())
    at_high = level >= high - 1e-9
    state = classify(ch[5], p)
    words = {
        "spiking": "**spiking**: a fast rise like this is a common trigger for Nasdaq pullbacks",
        "rising": "rising: a headwind for growth stocks",
        "falling": "falling: a tailwind for growth stocks",
        "calm": "calm",
    }[state]
    detail = (
        f"10-yr {level:.2f}% ({ch[1]:+.0f}bp today, {ch[5]:+.0f}bp in 5 days, {ch[20]:+.0f}bp in 20) -- {words}"
        + (". At its 52-week high." if at_high else f". 52-week high {high:.2f}%.")
    )
    return TenYearRead(
        True, round(level, 3), round(ch[1], 1), round(ch[5], 1), round(ch[20], 1), round(high, 3), at_high, state,
        detail, round(prior_5d, 1), p.spike_bp_5d,
    )


def daily_states(history: pd.DataFrame, p: YieldParams = YieldParams()) -> pd.DataFrame:
    """Per ^TNX session: the yield read as of that session's close.
    Columns: level, change_1d_bp, change_5d_bp, at_52w_high, state. Use
    `state_on` to get the read known before a given day's open."""
    close = history["Close"].astype(float).dropna()
    close.index = pd.to_datetime(close.index).normalize()
    df = pd.DataFrame(
        {
            "level": close,
            "change_1d_bp": close.diff(1) * 100.0,
            "change_5d_bp": close.diff(5) * 100.0,
            "at_52w_high": close >= close.rolling(p.high_lookback, min_periods=20).max() - 1e-9,
        }
    )
    df["state"] = [classify(x, p) if x == x else "unknown" for x in df["change_5d_bp"]]
    return df


def state_on(states: pd.DataFrame, date: dt.date) -> Optional[pd.Series]:
    """The read known before `date`'s open: the last close strictly before
    it, so a backtest never sees that day's yield move."""
    ts = pd.Timestamp(date)
    k = int(np.searchsorted(states.index.values, ts.to_datetime64(), side="left")) - 1
    return states.iloc[k] if k >= 0 else None
