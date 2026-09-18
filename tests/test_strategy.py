"""Unit tests for iron condor construction, in particular the stale-quote
sanity check that guards against pre-market/untraded option data."""

import datetime as dt

import numpy as np
import pandas as pd

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
