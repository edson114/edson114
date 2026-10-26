"""Black-Scholes on numpy arrays, implied volatility from the quote, and the
contract pick: a near-the-money call or put about a week out that clears a
spread and open-interest bar.

The implied volatility is solved from the contract's own mid price rather
than taken from the feed's impliedVolatility column, which is often stale or
zero on free data; that way the model's price at entry IS the mid.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from qqq_iron_condor.options_math import bs_delta, bs_price

YEAR_SECONDS = 365.0 * 24 * 3600
MIN_T = 60.0 / YEAR_SECONDS  # one minute: keeps d1 finite at the last bar of an expiry day


def norm_cdf(x: np.ndarray) -> np.ndarray:
    """Standard normal CDF via Abramowitz & Stegun 7.1.26 (|error| < 1.5e-7)."""
    z = np.abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * z)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))))
    erf = 1.0 - poly * np.exp(-z * z)
    return 0.5 * (1.0 + np.sign(x) * erf)


def bs_price_vec(spot: np.ndarray, strike: float, t_years: float, rate: float, iv: float, option_type: str) -> np.ndarray:
    t = max(t_years, MIN_T)
    spot = np.maximum(np.asarray(spot, dtype=float), 1e-9)
    vol_t = iv * math.sqrt(t)
    d1 = (np.log(spot / strike) + (rate + 0.5 * iv * iv) * t) / vol_t
    d2 = d1 - vol_t
    disc = strike * math.exp(-rate * t)
    if option_type == "call":
        return spot * norm_cdf(d1) - disc * norm_cdf(d2)
    return disc * norm_cdf(-d2) - spot * norm_cdf(-d1)


def years_until(expiration: dt.date, when: dt.datetime) -> float:
    """Black-Scholes T from `when` (New York, naive) to the 16:00 close on
    the expiration date."""
    expiry = dt.datetime.combine(expiration, dt.time(16, 0))
    return max((expiry - when).total_seconds() / YEAR_SECONDS, MIN_T)


def implied_vol(price: float, spot: float, strike: float, t_years: float, rate: float, option_type: str) -> Optional[float]:
    """Bisection on [1%, 500%]; None when the price is outside what any
    volatility can produce (below intrinsic, or above the no-arbitrage cap)."""
    lo, hi = 0.01, 5.0
    p_lo = bs_price(spot, strike, t_years, rate, lo, option_type)
    p_hi = bs_price(spot, strike, t_years, rate, hi, option_type)
    if not (p_lo <= price <= p_hi):
        return None
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if bs_price(spot, strike, t_years, rate, mid, option_type) < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


@dataclass
class Contract:
    option_type: str
    expiration: str
    dte: int
    strike: float
    bid: float
    ask: float
    open_interest: Optional[int]
    iv: float
    delta: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def half_spread(self) -> float:
        return (self.ask - self.bid) / 2.0

    @property
    def spread_pct(self) -> float:
        return (self.ask - self.bid) / self.mid * 100.0


def pick_expiration(expirations: list, today: dt.date, min_dte: int, max_dte: int, preferred_dte: int) -> Optional[str]:
    dated = []
    for e in expirations:
        try:
            d = (dt.datetime.strptime(e, "%Y-%m-%d").date() - today).days
        except ValueError:
            continue
        if min_dte <= d <= max_dte:
            dated.append((abs(d - preferred_dte), d, e))
    return min(dated)[2] if dated else None


def _int_or_none(v) -> Optional[int]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else int(f)


def select_contract(
    table: pd.DataFrame,
    expiration: str,
    spot: float,
    option_type: str,
    now: dt.datetime,
    rate: float,
    max_spread_pct: float,
    min_open_interest: int,
    strikes_to_try: int,
) -> tuple[Optional[Contract], str]:
    """The nearest-the-money strike that has a live two-sided quote inside
    the spread cap and enough open interest. Returns (contract, reason)."""
    if table is None or table.empty:
        return None, f"no {option_type}s listed for {expiration}"
    exp_date = dt.datetime.strptime(expiration, "%Y-%m-%d").date()
    t_years = years_until(exp_date, now)
    rows = table.assign(_dist=(table["strike"] - spot).abs()).sort_values("_dist").head(strikes_to_try)

    reasons = []
    for _, row in rows.iterrows():
        bid = float(row.get("bid", 0) or 0)
        ask = float(row.get("ask", 0) or 0)
        strike = float(row["strike"])
        if bid <= 0 or ask <= 0 or ask < bid:
            reasons.append(f"{strike:g}: no two-sided quote")
            continue
        mid = (bid + ask) / 2.0
        spread = (ask - bid) / mid * 100.0
        if spread > max_spread_pct:
            reasons.append(f"{strike:g}: spread {spread:.0f}% of mid")
            continue
        oi = _int_or_none(row.get("openInterest"))
        if oi is not None and oi < min_open_interest:
            reasons.append(f"{strike:g}: open interest {oi}")
            continue
        iv = implied_vol(mid, spot, strike, t_years, rate, option_type)
        if iv is None:
            reasons.append(f"{strike:g}: mid {mid:.2f} has no implied volatility")
            continue
        delta = bs_delta(spot, strike, t_years, rate, iv, option_type)
        dte = (exp_date - now.date()).days
        return Contract(option_type, expiration, dte, strike, bid, ask, oi, iv, delta), ""
    return None, "no strike near the money passed: " + "; ".join(reasons)
