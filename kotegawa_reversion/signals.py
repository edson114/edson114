"""Deviation ("kairi") indicator and the per-symbol entry signal.

Pure functions over a daily OHLCV DataFrame (columns Open/High/Low/Close/
Volume, ascending DatetimeIndex) so they can be tested without network
access and reused unchanged by the backtester.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .config import Config


def add_indicators(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    out = df.copy()
    out["ma"] = out["Close"].rolling(cfg.ma_window, min_periods=cfg.ma_window).mean()
    out["deviation"] = out["Close"] / out["ma"] - 1.0
    out["avg_dollar_volume"] = (out["Close"] * out["Volume"]).rolling(20, min_periods=20).mean()
    return out


def entry_signal_mask(ind: pd.DataFrame, cfg: Config) -> pd.Series:
    """True on bars whose close qualifies as a buy signal. The trade itself
    is taken at the NEXT bar's open (see backtest.py) so no signal uses
    information it couldn't have had at the close."""
    dev = ind["deviation"]
    mask = (
        (dev <= -cfg.entry_deviation)
        & (dev >= -cfg.max_entry_deviation)
        & (ind["Close"] >= cfg.min_price)
        & (ind["avg_dollar_volume"] >= cfg.min_avg_dollar_volume)
    )
    if cfg.require_green_close:
        mask &= ind["Close"] > ind["Open"]
    return mask.fillna(False)


@dataclass
class SymbolSignal:
    symbol: str
    date: pd.Timestamp
    close: float
    ma: float
    deviation: float
    avg_dollar_volume: float
    green_close: bool
    is_entry: bool
    reason: str
    # Suggested levels for a next-open entry, using today's close as the
    # entry estimate (the real fill will differ).
    stop_price: float
    target_price: float

    @property
    def reward_risk(self) -> float:
        risk = self.close - self.stop_price
        return (self.target_price - self.close) / risk if risk > 0 else float("nan")


def evaluate_symbol(symbol: str, df: pd.DataFrame, cfg: Config) -> Optional[SymbolSignal]:
    if df is None or len(df) < cfg.ma_window + 1:
        return None
    ind = add_indicators(df, cfg)
    last = ind.iloc[-1]
    if pd.isna(last["ma"]):
        return None

    mask = entry_signal_mask(ind, cfg)
    is_entry = bool(mask.iloc[-1])
    dev = float(last["deviation"])
    green = bool(last["Close"] > last["Open"])

    if is_entry:
        reason = f"{dev:+.1%} below the {cfg.ma_window}-day MA with a green close -- buy the panic."
    elif dev < -cfg.max_entry_deviation:
        reason = "Too far below the MA -- likely a fundamental break, not a panic. Pass."
    elif dev <= -cfg.entry_deviation and cfg.require_green_close and not green:
        reason = "Deep enough, but still closing red. Wait for buyers to show up."
    elif dev <= -cfg.entry_deviation:
        reason = "Deep enough, but fails the price/liquidity filter."
    else:
        reason = f"Not stretched enough (needs {-cfg.entry_deviation:+.0%})."

    close = float(last["Close"])
    ma = float(last["ma"])
    return SymbolSignal(
        symbol=symbol,
        date=ind.index[-1],
        close=close,
        ma=ma,
        deviation=dev,
        avg_dollar_volume=float(last["avg_dollar_volume"]) if pd.notna(last["avg_dollar_volume"]) else 0.0,
        green_close=green,
        is_entry=is_entry,
        reason=reason,
        stop_price=close * (1 - cfg.stop_loss_pct),
        target_price=ma * (1 - cfg.exit_deviation),
    )
