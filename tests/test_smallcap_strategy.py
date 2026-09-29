"""Unit tests for contract selection (strategy.py)."""

from dataclasses import replace

import pandas as pd

from smallcap_options.config import Config
from smallcap_options.data import OptionChain
from smallcap_options.strategy import select_contract

CFG = Config()


def _chain(strikes, bids, asks, ivs, open_interest=None, dte=5) -> OptionChain:
    n = len(strikes)
    df = pd.DataFrame(
        {
            "strike": strikes,
            "bid": bids,
            "ask": asks,
            "lastPrice": [(b + a) / 2 for b, a in zip(bids, asks)],
            "impliedVolatility": ivs,
            "openInterest": open_interest if open_interest is not None else [100] * n,
        }
    )
    empty = pd.DataFrame(columns=df.columns)
    return OptionChain(expiration="2026-10-05", dte=dte, calls=df, puts=empty)


def test_selects_strike_nearest_target_otm_for_calls():
    cfg = replace(CFG, target_otm_pct=0.10, max_bid_ask_spread_pct=0.9, min_option_open_interest=1)
    spot = 10.0
    strikes = [9.0, 10.0, 11.0, 12.0, 13.0]
    chain = _chain(strikes, bids=[1.0] * 5, asks=[1.1] * 5, ivs=[0.8] * 5)
    contract, skip_reason = select_contract(chain, spot, "call", cfg)
    assert skip_reason is None
    assert contract.strike == 11.0  # 10% OTM of a $10 spot -> $11 target


def test_no_contracts_listed_returns_skip_reason():
    cfg = CFG
    empty_chain = OptionChain(expiration="2026-10-05", dte=5, calls=pd.DataFrame(columns=["strike"]), puts=pd.DataFrame(columns=["strike"]))
    contract, skip_reason = select_contract(empty_chain, 10.0, "call", cfg)
    assert contract is None
    assert "no" in skip_reason.lower()


def test_wide_spread_is_skipped_in_favor_of_a_tighter_strike():
    cfg = replace(CFG, target_otm_pct=0.10, max_bid_ask_spread_pct=0.30, min_option_open_interest=1)
    spot = 10.0
    # Nearest-to-target strike (11.0) has a blown-out spread; the next
    # nearest (12.0) is tight and should be picked instead.
    chain = _chain(
        strikes=[11.0, 12.0],
        bids=[0.05, 1.00],
        asks=[0.50, 1.05],
        ivs=[0.8, 0.8],
    )
    contract, skip_reason = select_contract(chain, spot, "call", cfg)
    assert skip_reason is None
    assert contract.strike == 12.0


def test_thin_open_interest_is_hard_skipped():
    cfg = replace(CFG, min_option_open_interest=100)
    spot = 10.0
    chain = _chain(strikes=[11.0], bids=[1.0], asks=[1.05], ivs=[0.8], open_interest=[5])
    contract, skip_reason = select_contract(chain, spot, "call", cfg)
    assert contract is None
    assert skip_reason is not None


def test_all_contracts_illiquid_returns_none_with_reason():
    cfg = replace(CFG, max_bid_ask_spread_pct=0.05, min_option_open_interest=1)
    spot = 10.0
    chain = _chain(strikes=[11.0, 12.0], bids=[0.05, 0.05], asks=[0.50, 0.60], ivs=[0.8, 0.8])
    contract, skip_reason = select_contract(chain, spot, "call", cfg)
    assert contract is None
    assert "liquidity" in skip_reason.lower() or "spread" in skip_reason.lower()
