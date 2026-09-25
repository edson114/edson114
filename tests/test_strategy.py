"""Unit tests for iron condor construction, in particular the stale-quote
sanity check that guards against pre-market/untraded option data."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from qqq_iron_condor.data import OptionChain
from qqq_iron_condor.options_math import bs_price
from qqq_iron_condor.strategy import build_iron_condor

SPOT = 700.0
RATE = 0.045


def _chain(dte: int, iv_level: float) -> OptionChain:
    strikes = np.arange(SPOT - 40, SPOT + 40, 1.0)
    t_years = max(dte, 1) / 365.0
    iv = np.full(len(strikes), iv_level)

    call_fair = np.array([bs_price(SPOT, k, t_years, RATE, v, "call") for k, v in zip(strikes, iv)])
    put_fair = np.array([bs_price(SPOT, k, t_years, RATE, v, "put") for k, v in zip(strikes, iv)])

    calls = pd.DataFrame({
        "strike": strikes,
        "bid": np.maximum(0.01, call_fair - 0.02),
        "ask": call_fair + 0.02,
        "lastPrice": call_fair,
        "impliedVolatility": iv,
    })
    puts = pd.DataFrame({
        "strike": strikes,
        "bid": np.maximum(0.01, put_fair - 0.02),
        "ask": put_fair + 0.02,
        "lastPrice": put_fair,
        "impliedVolatility": iv,
    })
    expiration = (dt.date.today() + dt.timedelta(days=dte)).isoformat()
    return OptionChain(expiration=expiration, dte=dte, calls=calls, puts=puts)


def test_normal_iv_produces_no_stale_quote_warning():
    trade = build_iron_condor("Weekly", _chain(7, 0.18), SPOT, RATE, 0.16, 5.0, reference_hv=0.13)
    assert trade is not None
    assert trade.warning is None


def test_implausibly_low_iv_vs_realized_vol_is_flagged():
    # IV far below trailing realized vol is the signature of stale/pre-market
    # option quotes (e.g. the scan running before the market opens).
    trade = build_iron_condor("Weekly", _chain(7, 0.05), SPOT, RATE, 0.16, 5.0, reference_hv=0.13)
    assert trade is not None
    assert trade.warning is not None
    assert "stale" in trade.warning.lower()


def test_without_reference_hv_no_sanity_check_applied():
    trade = build_iron_condor("Weekly", _chain(7, 0.05), SPOT, RATE, 0.16, 5.0, reference_hv=None)
    assert trade is not None
    assert trade.warning is None


def _chain_with_bad_otm_quotes(dte: int, near_spot_iv: float, far_iv: float, band: float) -> OptionChain:
    """A chain where strikes within `band` of spot have a normal IV (so the
    ATM-IV display average looks fine) but strikes further out -- where a
    delta-targeted short strike actually needs to land -- have a near-floor
    IV. This reproduces a real failure seen shortly after the market open:
    the aggregate ATM IV can look plausible while individual per-contract
    IV hasn't populated reliably across the rest of the chain yet, so the
    delta search silently lands on strikes nowhere near the target delta.
    """
    strikes = np.arange(SPOT - 40, SPOT + 40, 1.0)
    t_years = max(dte, 1) / 365.0
    iv = np.where(np.abs(strikes - SPOT) <= band, near_spot_iv, far_iv)

    call_fair = np.array([bs_price(SPOT, k, t_years, RATE, v, "call") for k, v in zip(strikes, iv)])
    put_fair = np.array([bs_price(SPOT, k, t_years, RATE, v, "put") for k, v in zip(strikes, iv)])

    calls = pd.DataFrame({
        "strike": strikes,
        "bid": np.maximum(0.01, call_fair - 0.02),
        "ask": call_fair + 0.02,
        "lastPrice": call_fair,
        "impliedVolatility": iv,
    })
    puts = pd.DataFrame({
        "strike": strikes,
        "bid": np.maximum(0.01, put_fair - 0.02),
        "ask": put_fair + 0.02,
        "lastPrice": put_fair,
        "impliedVolatility": iv,
    })
    expiration = (dt.date.today() + dt.timedelta(days=dte)).isoformat()
    return OptionChain(expiration=expiration, dte=dte, calls=calls, puts=puts)


def test_real_delta_column_used_instead_of_recomputing_via_black_scholes():
    # A Tradier-style chain: a linear (not Black-Scholes-shaped) delta
    # profile that hits exactly +/-0.16 at known strikes 17 away from spot.
    # A real Black-Scholes calc from the IV column (0.30, a curved profile)
    # would never land on these exact values at these exact strikes, so if
    # the selected short strikes match this profile's prediction, the real
    # column won rather than being recomputed.
    strikes = np.arange(SPOT - 40, SPOT + 40, 1.0)
    t_years = 7 / 365.0
    iv = np.full(len(strikes), 0.30)

    call_fair = np.array([bs_price(SPOT, k, t_years, RATE, v, "call") for k, v in zip(strikes, iv)])
    put_fair = np.array([bs_price(SPOT, k, t_years, RATE, v, "put") for k, v in zip(strikes, iv)])

    call_delta = np.clip(0.5 - 0.02 * (strikes - SPOT), 0.0, 1.0)
    put_delta = -np.clip(0.5 - 0.02 * (SPOT - strikes), 0.0, 1.0)

    calls = pd.DataFrame({
        "strike": strikes,
        "bid": np.maximum(0.01, call_fair - 0.02),
        "ask": call_fair + 0.02,
        "lastPrice": call_fair,
        "impliedVolatility": iv,
        "delta": call_delta,
    })
    puts = pd.DataFrame({
        "strike": strikes,
        "bid": np.maximum(0.01, put_fair - 0.02),
        "ask": put_fair + 0.02,
        "lastPrice": put_fair,
        "impliedVolatility": iv,
        "delta": put_delta,
    })
    expiration = (dt.date.today() + dt.timedelta(days=7)).isoformat()
    chain = OptionChain(expiration=expiration, dte=7, calls=calls, puts=puts)

    trade = build_iron_condor("Weekly", chain, SPOT, RATE, 0.16, 5.0)
    assert trade is not None
    assert trade.legs["short_call"].strike == pytest.approx(SPOT + 17)
    assert trade.legs["short_call"].delta == pytest.approx(0.16)
    assert trade.legs["short_put"].strike == pytest.approx(SPOT - 17)
    assert trade.legs["short_put"].delta == pytest.approx(-0.16)


def test_short_strikes_far_from_target_delta_flagged_even_with_normal_atm_iv():
    # Near-spot strikes carry a normal 18% IV (ATM-IV display looks fine),
    # but strikes further out -- where the 0.16-delta short strikes need to
    # land -- carry near-floor IV, exactly like the post-open run that
    # motivated this check.
    chain = _chain_with_bad_otm_quotes(7, near_spot_iv=0.18, far_iv=0.031, band=5.0)
    trade = build_iron_condor("Weekly", chain, SPOT, RATE, 0.16, 5.0, reference_hv=0.13)
    assert trade is not None
    assert abs(trade.legs["short_call"].delta) < 0.3 * 0.16
    assert abs(trade.legs["short_put"].delta) < 0.3 * 0.16
    assert trade.warning is not None
    assert "delta target" in trade.warning.lower()
