"""Black-Scholes option greeks, used only to estimate delta for strike
selection when the data provider doesn't supply greeks directly."""

import datetime as dt
import math

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - zoneinfo is stdlib on Python 3.9+
    ZoneInfo = None

MARKET_TIMEZONE = "America/New_York"
MARKET_CLOSE_HOUR = 16


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def d1_d2(spot: float, strike: float, t_years: float, rate: float, iv: float):
    if t_years <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return None, None
    sqrt_t = math.sqrt(t_years)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t_years) / (iv * sqrt_t)
    d2 = d1 - iv * sqrt_t
    return d1, d2


def bs_delta(spot: float, strike: float, t_years: float, rate: float, iv: float, option_type: str) -> float:
    """Return signed Black-Scholes delta for a European call/put.

    option_type: "call" or "put".
    Falls back to a moneyness heuristic if inputs are degenerate
    (e.g. zero IV from a stale/illiquid quote).
    """
    d1, _ = d1_d2(spot, strike, t_years, rate, iv)
    if d1 is None:
        return 1.0 if (option_type == "call" and spot > strike) else (
            -1.0 if option_type == "put" and spot < strike else 0.0
        )
    if option_type == "call":
        return _norm_cdf(d1)
    return _norm_cdf(d1) - 1.0


def bs_gamma(spot: float, strike: float, t_years: float, rate: float, iv: float) -> float:
    d1, _ = d1_d2(spot, strike, t_years, rate, iv)
    if d1 is None or iv <= 0:
        return 0.0
    return _norm_pdf(d1) / (spot * iv * math.sqrt(t_years))


def probability_between(put_delta: float, call_delta: float) -> float:
    """Rough probability the underlying finishes between the two short
    strikes at expiration, approximated from their deltas.

    abs(delta) is commonly used as a rough proxy for probability of
    finishing ITM; this is an approximation, not an exact calculation.
    """
    p_below_put_strike = abs(put_delta)
    p_above_call_strike = call_delta
    p_outside = p_below_put_strike + p_above_call_strike
    return max(0.0, min(1.0, 1.0 - p_outside))


def time_to_expiration_years(dte: int, now: "dt.datetime | None" = None) -> float:
    """Convert a days-to-expiration count into a Black-Scholes T (years).

    For a same-day (0DTE) expiration, a plain dte/365 would collapse to
    (near) zero regardless of whether it's 9:31am or 3:59pm, which breaks
    delta/price estimation. Instead this uses actual clock time remaining
    until the 4:00pm ET close, floored at 15 minutes so it never hits zero.
    """
    if dte > 0:
        return dte / 365.0

    if now is None:
        now = dt.datetime.now(ZoneInfo(MARKET_TIMEZONE)) if ZoneInfo else dt.datetime.now()
    close = now.replace(hour=MARKET_CLOSE_HOUR, minute=0, second=0, microsecond=0)
    seconds_remaining = (close - now).total_seconds()
    hours_remaining = max(seconds_remaining, 15 * 60) / 3600.0
    return hours_remaining / (24 * 365.0)


def bs_price(spot: float, strike: float, t_years: float, rate: float, iv: float, option_type: str) -> float:
    """Black-Scholes European option price. Used only for generating
    realistic synthetic quotes in the offline self-test."""
    d1, d2 = d1_d2(spot, strike, t_years, rate, iv)
    if d1 is None:
        intrinsic = (spot - strike) if option_type == "call" else (strike - spot)
        return max(intrinsic, 0.0)
    if option_type == "call":
        return spot * _norm_cdf(d1) - strike * math.exp(-rate * t_years) * _norm_cdf(d2)
    return strike * math.exp(-rate * t_years) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def expected_move_from_iv(spot: float, iv: float, t_years: float) -> float:
    if iv <= 0 or t_years <= 0:
        return 0.0
    return spot * iv * math.sqrt(t_years)
