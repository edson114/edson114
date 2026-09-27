"""Unit tests for the small-cap directional (CALL/PUT/NO TRADE) scoring."""

from dataclasses import replace

from smallcap_options.config import Config
from smallcap_options.data import CandidateQuote
from smallcap_options.direction import build_directional_signal
from smallcap_options.intraday import IntradaySnapshot
from smallcap_options.news import CatalystHit
from smallcap_options.data import Headline

CFG = Config()


def _quote(price=5.0, change_pct=30.0, day_volume=2_000_000, avg_volume_3m=200_000):
    return CandidateQuote(
        symbol="TEST",
        name="Test Corp",
        price=price,
        change_pct=change_pct,
        day_volume=day_volume,
        avg_volume_3m=avg_volume_3m,
        market_cap=50_000_000,
        source_query="test",
    )


def _snapshot(last_price=5.0, vwap=4.5, session_high=5.05, session_low=3.8):
    price_vs_vwap_pct = (last_price / vwap - 1.0) * 100.0
    pct_off_high = max(0.0, (session_high / last_price - 1.0) * 100.0)
    pct_off_low = max(0.0, (last_price / session_low - 1.0) * 100.0)
    return IntradaySnapshot(
        last_price=last_price,
        vwap=vwap,
        price_vs_vwap_pct=price_vs_vwap_pct,
        session_high=session_high,
        session_low=session_low,
        pct_off_high=pct_off_high,
        pct_off_low=pct_off_low,
        bars_used=60,
    )


def test_extending_breakout_is_a_call():
    quote = _quote(change_pct=35.0)
    intraday = _snapshot(last_price=5.0, vwap=4.5, session_high=5.02, session_low=3.8)  # near high, above vwap
    signal = build_directional_signal(quote, intraday, [], CFG)
    assert signal.bias == "CALL"
    assert signal.score > 0


def test_green_day_rolling_over_is_a_put_despite_positive_change_pct():
    """The classic small-cap "faded gapper" short: still up on the day,
    but price action right now is a rollover, not a continuation."""
    quote = _quote(change_pct=45.0)
    # Session high well above last price (faded hard), and now below VWAP.
    intraday = _snapshot(last_price=5.0, vwap=5.3, session_high=6.5, session_low=4.8)
    signal = build_directional_signal(quote, intraday, [], CFG)
    assert signal.bias == "PUT"
    assert signal.score < 0
    # The gap component itself must have flipped sign to match the lean.
    gap_component = next(c for c in signal.components if c.name == "gap")
    assert gap_component.contribution < 0


def test_breakdown_continuation_is_a_put():
    quote = _quote(change_pct=-30.0, price=3.0)
    intraday = _snapshot(last_price=3.0, vwap=3.4, session_high=4.2, session_low=2.98)  # near low, below vwap
    signal = build_directional_signal(quote, intraday, [], CFG)
    assert signal.bias == "PUT"


def test_red_day_bounce_is_a_call():
    quote = _quote(change_pct=-35.0, price=3.5)
    # Bounced hard off the session low, back near/above vwap.
    intraday = _snapshot(last_price=3.5, vwap=3.3, session_high=5.0, session_low=3.0)
    signal = build_directional_signal(quote, intraday, [], CFG)
    assert signal.bias == "CALL"


def test_choppy_mid_range_is_no_trade():
    quote = _quote(change_pct=9.0)  # right at the prefilter floor, small magnitude
    intraday = _snapshot(last_price=5.0, vwap=5.0, session_high=5.3, session_low=4.7)
    signal = build_directional_signal(quote, intraday, [], CFG)
    assert signal.bias == "NO TRADE"


def test_catalyst_reinforces_the_lean_not_the_raw_gap_sign():
    quote = _quote(change_pct=45.0)
    intraday = _snapshot(last_price=5.0, vwap=5.3, session_high=6.5, session_low=4.8)  # fading -> PUT lean
    hit = CatalystHit(headline=Headline(source="x", title="Test Corp misses on guidance", link="#"), matched_keywords=["guidance"])
    signal = build_directional_signal(quote, intraday, [hit], CFG)
    catalyst_component = next(c for c in signal.components if c.name == "catalyst")
    assert catalyst_component.contribution < 0  # follows the PUT lean, not the +45% raw change


def test_component_weights_sum_to_configured_weights_normalized():
    quote = _quote()
    intraday = _snapshot()
    signal = build_directional_signal(quote, intraday, [], CFG)
    assert abs(sum(c.weight for c in signal.components) - 1.0) < 1e-9


def test_higher_threshold_config_can_force_no_trade():
    cfg = replace(CFG, signal_score_threshold=0.99)
    quote = _quote(change_pct=35.0)
    intraday = _snapshot(last_price=5.0, vwap=4.5, session_high=5.02, session_low=3.8)
    signal = build_directional_signal(quote, intraday, [], cfg)
    assert signal.bias == "NO TRADE"
