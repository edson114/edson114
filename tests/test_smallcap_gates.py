"""Unit tests for per-candidate gates (gates.py)."""

import datetime as dt

from smallcap_options.gates import evaluate_candidate_gates

TODAY = dt.date(2026, 9, 27)


def test_clean_candidate_does_not_skip():
    result = evaluate_candidate_gates(
        contract_skip_reason=None, float_shares=10_000_000.0, next_earnings_date=None, catalyst_found=True, today=TODAY
    )
    assert result.skip is False
    assert result.hard_reasons == []


def test_no_tradable_contract_forces_skip():
    result = evaluate_candidate_gates(
        contract_skip_reason="No contract clears the spread bar.",
        float_shares=10_000_000.0,
        next_earnings_date=None,
        catalyst_found=True,
        today=TODAY,
    )
    assert result.skip is True
    assert "spread bar" in result.hard_reasons[0]


def test_unknown_float_is_soft_flag_only():
    result = evaluate_candidate_gates(
        contract_skip_reason=None, float_shares=None, next_earnings_date=None, catalyst_found=True, today=TODAY
    )
    assert result.skip is False
    assert any("float" in r.lower() for r in result.soft_reasons)


def test_earnings_within_five_days_is_soft_flag():
    result = evaluate_candidate_gates(
        contract_skip_reason=None,
        float_shares=10_000_000.0,
        next_earnings_date=TODAY + dt.timedelta(days=3),
        catalyst_found=True,
        today=TODAY,
    )
    assert result.skip is False
    assert any("earnings" in r.lower() for r in result.soft_reasons)


def test_earnings_far_out_does_not_flag():
    result = evaluate_candidate_gates(
        contract_skip_reason=None,
        float_shares=10_000_000.0,
        next_earnings_date=TODAY + dt.timedelta(days=30),
        catalyst_found=True,
        today=TODAY,
    )
    assert not any("earnings" in r.lower() for r in result.soft_reasons)


def test_no_catalyst_is_soft_flag():
    result = evaluate_candidate_gates(
        contract_skip_reason=None, float_shares=10_000_000.0, next_earnings_date=None, catalyst_found=False, today=TODAY
    )
    assert result.skip is False
    assert any("catalyst" in r.lower() for r in result.soft_reasons)
