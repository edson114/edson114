"""Tests for render_report's data-provider-dependent text, which has no
other coverage -- the mismatch this guards against (the banner correctly
switching for Tradier while the Risk Management footer kept its hardcoded
Black-Scholes wording) shipped and ran live undetected for exactly that
reason.
"""

from qqq_iron_condor.analysis import MarketSnapshot
from qqq_iron_condor.gates import GateResult
from qqq_iron_condor.report import render_report


def _snapshot(**overrides) -> MarketSnapshot:
    defaults = dict(
        spot=750.0, prev_close=745.0, day_change_pct=0.67,
        rsi14=60.0, sma20=730.0, sma50=720.0, sma200=670.0, ema9=740.0, ema21=735.0,
        macd_line=5.0, macd_signal=4.0, macd_hist=1.0,
        bb_upper=760.0, bb_mid=730.0, bb_lower=700.0, bb_width_pct=8.0,
        atr14=9.0, adx14=14.0, hv20_pct=15.0,
        high_20d=755.0, low_20d=700.0, high_50d=755.0, low_50d=660.0,
        volume_last_session=3_000_000, volume_avg20=3_200_000, volume_ratio=0.94,
        vix_level=16.0, vix_percentile_1y=30.0,
        trend_label="Range-bound / low trend strength", regime_label="Normal IV", regime_notes=[],
    )
    defaults.update(overrides)
    return MarketSnapshot(**defaults)


def _gate() -> GateResult:
    return GateResult(skip=False, hard_reasons=[], soft_reasons=[], gap_pct=0.3, gap_confirmed=True)


def test_render_report_tradier_banner_and_footer_both_mention_real_greeks():
    report_md = render_report("QQQ", _snapshot(), {}, [], [], gate=_gate(), data_provider="tradier")
    assert "Tradier" in report_md
    assert "Black-Scholes delta approximation" not in report_md
    assert "Real broker-computed greeks" in report_md


def test_render_report_yfinance_banner_and_footer_both_mention_approximation():
    report_md = render_report("QQQ", _snapshot(), {}, [], [], gate=_gate(), data_provider="yfinance")
    assert "yfinance" in report_md
    assert "Black-Scholes delta approximation" in report_md
    assert "Real broker-computed greeks" not in report_md


def test_render_report_handles_missing_data_provider():
    # No data_provider passed (e.g. the offline --self-test path) -- should
    # render without raising and fall back to the approximation wording
    # rather than silently claiming real broker greeks.
    report_md = render_report("QQQ", _snapshot(), {}, [], [], gate=_gate())
    assert "Black-Scholes delta approximation" in report_md
