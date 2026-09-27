"""Contract selection: the cheap, short-dated, slightly out-of-the-money
call or put this style is known for -- a defined-risk (premium paid),
high-variance "lottery ticket" shape, not a risk-managed near-ATM
position. Selection is entirely mechanical (nearest strike to a target
% OTM, nearest expiration at/under a DTE cap); it does not try to guess
the "best" contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .config import Config
from .data import OptionChain
from .options_math import bs_delta, time_to_expiration_years


@dataclass
class SuggestedContract:
    option_type: str  # "call" or "put"
    expiration: str
    dte: int
    strike: float
    mid_price: float
    bid_ask_spread_pct: Optional[float]
    delta: float
    implied_vol: float
    open_interest: Optional[int]
    warning: Optional[str] = None


def _mid_price(row: pd.Series) -> float:
    bid = float(row.get("bid", 0.0) or 0.0)
    ask = float(row.get("ask", 0.0) or 0.0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return float(row.get("lastPrice", 0.0) or 0.0)


def _spread_pct(row: pd.Series, mid: float) -> Optional[float]:
    bid = float(row.get("bid", 0.0) or 0.0)
    ask = float(row.get("ask", 0.0) or 0.0)
    if bid > 0 and ask > 0 and mid > 0:
        return (ask - bid) / mid
    return None


def select_contract(
    chain: OptionChain,
    spot: float,
    option_type: str,
    cfg: Config,
) -> tuple[Optional[SuggestedContract], Optional[str]]:
    """Returns (contract, skip_reason). skip_reason is set (contract is
    None) when nothing in the chain clears the liquidity/spread bar --
    that's a hard gate on *this candidate's options*, independent of the
    directional score."""
    t_years = time_to_expiration_years(chain.dte)
    df = (chain.calls if option_type == "call" else chain.puts).copy()
    if df.empty:
        return None, f"No {option_type} contracts listed for expiration {chain.expiration}."

    target_strike = spot * (1 + cfg.target_otm_pct) if option_type == "call" else spot * (1 - cfg.target_otm_pct)
    df["strike_distance"] = (df["strike"] - target_strike).abs()
    df = df.sort_values("strike_distance")

    for _, row in df.iterrows():
        mid = _mid_price(row)
        if mid <= 0:
            continue
        spread_pct = _spread_pct(row, mid)
        open_interest = int(row["openInterest"]) if "openInterest" in row and row["openInterest"] == row["openInterest"] else None

        if spread_pct is not None and spread_pct > cfg.max_bid_ask_spread_pct:
            continue
        if open_interest is not None and open_interest < cfg.min_option_open_interest:
            continue

        iv = float(row.get("impliedVolatility", 0.0) or 0.0)
        delta = bs_delta(spot, float(row["strike"]), t_years, cfg.risk_free_rate, iv, option_type) if iv > 0 else None

        warning = None
        if spread_pct is not None and spread_pct > 0.25:
            warning = f"Wide bid/ask spread ({spread_pct * 100:.0f}% of mid) -- expect real slippage vs. this mid price."
        elif spread_pct is None:
            warning = "No live bid/ask on this quote -- price shown is last trade, may be stale."

        return (
            SuggestedContract(
                option_type=option_type,
                expiration=chain.expiration,
                dte=chain.dte,
                strike=float(row["strike"]),
                mid_price=round(mid, 2),
                bid_ask_spread_pct=round(spread_pct * 100, 1) if spread_pct is not None else None,
                delta=round(delta, 3) if delta is not None else float("nan"),
                implied_vol=round(iv * 100, 1),
                open_interest=open_interest,
                warning=warning,
            ),
            None,
        )

    return None, "No contract in this chain clears the liquidity/spread bar (min open interest, max bid/ask spread)."
