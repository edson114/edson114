"""Unit tests for the trade gate (skip-day rules)."""

import datetime as dt

from qqq_iron_condor.config import Config
from qqq_iron_condor.gates import evaluate_gates
from qqq_iron_condor.news import CatalystHit, Headline

TODAY = dt.date(2026, 9, 18)


def _snapshot(adx14=10.0, volume_ratio=1.0):
    """Minimal stand-in with just the attributes evaluate_gates reads."""
    class _S:
        pass
    s = _S()
    s.adx14 = adx14
    s.volume_ratio = volume_ratio
    return s


def _base_kwargs(**overrides):
    kwargs = dict(
        today=TODAY,
        macro_event_dates={},
        gap_pct=0.1,
        gap_confirmed=True,
        gap_threshold_pct=0.8,
        vix_level=15.0,
        vix_spike_threshold=20.0,
        snapshot=_snapshot(),
        adx_trend_threshold=25.0,
        volume_ratio_threshold=1.3,
        catalyst_hits=[],
    )
    kwargs.update(overrides)
    return kwargs


def test_clean_day_does_not_skip():
    result = evaluate_gates(**_base_kwargs())
    assert result.skip is False
    assert result.hard_reasons == []


def test_scheduled_macro_event_forces_skip():
    result = evaluate_gates(**_base_kwargs(macro_event_dates={TODAY.isoformat(): "FOMC decision"}))
    assert result.skip is True
    assert any("FOMC" in r for r in result.hard_reasons)


def test_large_gap_forces_skip():
    result = evaluate_gates(**_base_kwargs(gap_pct=1.5, gap_confirmed=True))
    assert result.skip is True
    assert any("gap" in r.lower() for r in result.hard_reasons)


def test_negative_gap_magnitude_also_forces_skip():
    result = evaluate_gates(**_base_kwargs(gap_pct=-1.2, gap_confirmed=False))
    assert result.skip is True
    assert any("pre-market gap" in r.lower() for r in result.hard_reasons)


def test_gap_below_threshold_does_not_skip():
    result = evaluate_gates(**_base_kwargs(gap_pct=0.5))
    assert result.skip is False


def test_vix_spike_forces_skip():
    result = evaluate_gates(**_base_kwargs(vix_level=22.0))
    assert result.skip is True
    assert any("VIX" in r for r in result.hard_reasons)


def test_missing_gap_is_soft_not_hard():
    result = evaluate_gates(**_base_kwargs(gap_pct=None, gap_confirmed=False))
    assert result.skip is False
    assert any("could not determine" in r.lower() for r in result.soft_reasons)


def test_trending_and_high_volume_is_soft_flag_only():
    result = evaluate_gates(**_base_kwargs(snapshot=_snapshot(adx14=30.0, volume_ratio=1.5)))
    assert result.skip is False
    assert any("trending" in r.lower() for r in result.soft_reasons)


def test_trending_without_high_volume_does_not_flag():
    result = evaluate_gates(**_base_kwargs(snapshot=_snapshot(adx14=30.0, volume_ratio=1.0)))
    assert not any("trending" in r.lower() for r in result.soft_reasons)


def test_catalyst_hits_are_soft_flag_only():
    hits = [CatalystHit(headline=Headline(source="x", title="Fed hikes rates", link="#"), matched_keywords=["fed"])]
    result = evaluate_gates(**_base_kwargs(catalyst_hits=hits))
    assert result.skip is False
    assert any("catalyst" in r.lower() for r in result.soft_reasons)


def test_default_config_has_2026_fomc_and_cpi_dates():
    cfg = Config()
    assert len(cfg.macro_event_dates) == 20
    assert cfg.macro_event_dates["2026-09-16"] == "FOMC decision"
    assert cfg.macro_event_dates["2026-10-14"] == "CPI release (September 2026 data)"


def test_real_fomc_date_triggers_skip_via_default_config():
    cfg = Config()
    result = evaluate_gates(**_base_kwargs(today=dt.date(2026, 9, 16), macro_event_dates=cfg.macro_event_dates))
    assert result.skip is True
    assert any("FOMC" in r for r in result.hard_reasons)
