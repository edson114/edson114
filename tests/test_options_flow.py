"""Unit tests for the options put/call volume skew read."""

import pandas as pd
import pytest

from qqq_iron_condor.data import OptionChain
from qqq_iron_condor.options_flow import compute_call_put_skew


def _chain(call_volumes, put_volumes) -> OptionChain:
    calls = pd.DataFrame({"strike": range(len(call_volumes)), "volume": call_volumes})
    puts = pd.DataFrame({"strike": range(len(put_volumes)), "volume": put_volumes})
    return OptionChain(expiration="2026-12-31", dte=30, calls=calls, puts=puts)


def test_all_call_volume_gives_skew_of_one():
    chain = _chain([100, 200], [0, 0])
    skew, calls, puts = compute_call_put_skew({"Weekly": chain})
    assert skew == pytest.approx(1.0)
    assert calls == 300
    assert puts == 0


def test_all_put_volume_gives_skew_of_negative_one():
    chain = _chain([0, 0], [50, 150])
    skew, calls, puts = compute_call_put_skew({"Weekly": chain})
    assert skew == pytest.approx(-1.0)
    assert calls == 0
    assert puts == 200


def test_balanced_volume_gives_zero_skew():
    chain = _chain([100], [100])
    skew, calls, puts = compute_call_put_skew({"Weekly": chain})
    assert skew == pytest.approx(0.0)


def test_call_skewed_gives_positive_partial_skew():
    chain = _chain([300], [100])
    skew, calls, puts = compute_call_put_skew({"Weekly": chain})
    assert skew == pytest.approx((300 - 100) / 400)
    assert 0 < skew < 1


def test_combines_volume_across_multiple_chains():
    chain_a = _chain([100], [0])
    chain_b = _chain([0], [100])
    skew, calls, puts = compute_call_put_skew({"0DTE": chain_a, "Weekly": chain_b})
    assert calls == 100
    assert puts == 100
    assert skew == pytest.approx(0.0)


def test_no_chains_returns_neutral_skew():
    skew, calls, puts = compute_call_put_skew({})
    assert skew == 0.0
    assert calls == 0
    assert puts == 0


def test_none_chain_values_are_skipped_without_crashing():
    chain = _chain([100], [0])
    skew, calls, puts = compute_call_put_skew({"0DTE": None, "Weekly": chain})
    assert calls == 100
    assert skew == pytest.approx(1.0)


def test_missing_volume_treated_as_zero_not_a_crash():
    calls = pd.DataFrame({"strike": [1, 2], "volume": [100, None]})
    puts = pd.DataFrame({"strike": [1, 2], "volume": [None, None]})
    chain = OptionChain(expiration="2026-12-31", dte=30, calls=calls, puts=puts)
    skew, call_total, put_total = compute_call_put_skew({"Weekly": chain})
    assert call_total == 100
    assert put_total == 0
    assert skew == pytest.approx(1.0)
