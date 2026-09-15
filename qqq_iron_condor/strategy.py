"""Iron condor construction: delta-targeted short strikes with a fixed
wing width, built from a live (or mocked) option chain DataFrame.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .data import OptionChain
from .options_math import bs_delta, expected_move_from_iv, probability_between, time_to_expiration_years


def _mid_price(row: pd.Series) -> float:
    bid = float(row.get("bid", 0.0) or 0.0)
    ask = float(row.get("ask", 0.0) or 0.0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    last = float(row.get("lastPrice", 0.0) or 0.0)
    return last


def _with_delta(chain_df: pd.DataFrame, spot: float, t_years: float, rate: float, option_type: str) -> pd.DataFrame:
    df = chain_df.copy()
    df["iv"] = df["impliedVolatility"].fillna(0.0)
    df = df[df["iv"] > 0.01]
    if df.empty:
        return df
    df["delta"] = df.apply(
        lambda r: bs_delta(spot, float(r["strike"]), t_years, rate, float(r["iv"]), option_type),
        axis=1,
    )
    df["mid"] = df.apply(_mid_price, axis=1)
    return df


def _closest_by_delta(df: pd.DataFrame, target_abs_delta: float) -> Optional[pd.Series]:
    if df.empty:
        return None
    idx = (df["delta"].abs() - target_abs_delta).abs().idxmin()
    return df.loc[idx]


def _closest_by_strike(df: pd.DataFrame, target_strike: float) -> Optional[pd.Series]:
    if df.empty:
        return None
    idx = (df["strike"] - target_strike).abs().idxmin()
    return df.loc[idx]


@dataclass
class IronCondorLeg:
    role: str  # "short_call", "long_call", "short_put", "long_put"
    strike: float
    mid_price: float
    delta: float
    implied_vol: float


@dataclass
class IronCondorTrade:
    label: str
    expiration: str
    dte: int
    spot: float
    legs: dict  # role -> IronCondorLeg
    credit: float
    max_profit: float
    max_loss: float
    call_width: float
    put_width: float
    breakeven_upper: float
    breakeven_lower: float
    probability_of_profit: float
    position_delta: float
    expected_move_iv: float
    atm_iv: float
    warning: Optional[str] = None

    def return_on_risk(self) -> float:
        if self.max_loss <= 0:
            return float("nan")
        return self.max_profit / self.max_loss


def build_iron_condor(
    label: str,
    chain: OptionChain,
    spot: float,
    rate: float,
    target_delta: float,
    wing_width: float,
) -> Optional[IronCondorTrade]:
    t_years = time_to_expiration_years(chain.dte)

    calls = _with_delta(chain.calls, spot, t_years, rate, "call")
    puts = _with_delta(chain.puts, spot, t_years, rate, "put")

    otm_calls = calls[calls["strike"] > spot]
    otm_puts = puts[puts["strike"] < spot]

    short_call = _closest_by_delta(otm_calls, target_delta)
    short_put = _closest_by_delta(otm_puts, target_delta)

    if short_call is None or short_put is None:
        return None

    long_call_target = float(short_call["strike"]) + wing_width
    long_put_target = float(short_put["strike"]) - wing_width

    long_call_candidates = calls[calls["strike"] > short_call["strike"]]
    long_put_candidates = puts[puts["strike"] < short_put["strike"]]

    long_call = _closest_by_strike(long_call_candidates, long_call_target)
    long_put = _closest_by_strike(long_put_candidates, long_put_target)

    if long_call is None or long_put is None:
        return None

    legs = {
        "short_call": IronCondorLeg("short_call", float(short_call["strike"]), _mid_price(short_call), float(short_call["delta"]), float(short_call["iv"])),
        "long_call": IronCondorLeg("long_call", float(long_call["strike"]), _mid_price(long_call), float(long_call["delta"]), float(long_call["iv"])),
        "short_put": IronCondorLeg("short_put", float(short_put["strike"]), _mid_price(short_put), float(short_put["delta"]), float(short_put["iv"])),
        "long_put": IronCondorLeg("long_put", float(long_put["strike"]), _mid_price(long_put), float(long_put["delta"]), float(long_put["iv"])),
    }

    credit = (legs["short_call"].mid_price - legs["long_call"].mid_price) + (
        legs["short_put"].mid_price - legs["long_put"].mid_price
    )
    credit = max(credit, 0.0)

    call_width = legs["long_call"].strike - legs["short_call"].strike
    put_width = legs["short_put"].strike - legs["long_put"].strike
    max_loss = max(call_width, put_width) - credit
    max_profit = credit

    breakeven_upper = legs["short_call"].strike + credit
    breakeven_lower = legs["short_put"].strike - credit

    pop = probability_between(legs["short_put"].delta, legs["short_call"].delta)

    position_delta = (
        -legs["short_call"].delta
        - legs["short_put"].delta
        + legs["long_call"].delta
        + legs["long_put"].delta
    )

    atm_iv_pool = pd.concat([
        calls[(calls["strike"] - spot).abs() <= wing_width]["iv"],
        puts[(puts["strike"] - spot).abs() <= wing_width]["iv"],
    ])
    atm_iv = float(atm_iv_pool.mean()) if not atm_iv_pool.empty else float(pd.concat([calls["iv"], puts["iv"]]).mean() or 0.0)
    expected_move = expected_move_from_iv(spot, atm_iv, t_years)

    warning = None
    if max_loss <= 0:
        warning = "Computed max loss is non-positive -- check for stale/illiquid quotes before trading this."

    return IronCondorTrade(
        label=label,
        expiration=chain.expiration,
        dte=chain.dte,
        spot=spot,
        legs=legs,
        credit=round(credit, 2),
        max_profit=round(max_profit, 2),
        max_loss=round(max_loss, 2),
        call_width=call_width,
        put_width=put_width,
        breakeven_upper=round(breakeven_upper, 2),
        breakeven_lower=round(breakeven_lower, 2),
        probability_of_profit=round(pop * 100, 1),
        position_delta=round(position_delta, 3),
        expected_move_iv=round(expected_move, 2),
        atm_iv=round(atm_iv * 100, 1),
        warning=warning,
    )
