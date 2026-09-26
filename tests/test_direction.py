"""Unit tests for the directional (buy calls/puts) signal engine."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from qqq_iron_condor.analysis import MarketSnapshot
from qqq_iron_condor.config import Config
from qqq_iron_condor.data import OptionChain
from qqq_iron_condor.direction import build_directional_signal
from qqq_iron_condor.gates import GateResult
from qqq_iron_condor.intraday import IntradaySnapshot
from qqq_iron_condor.options_math import bs_price, time_to_expiration_years

SPOT = 500.0


def _daily(**overrides) -> MarketSnapshot:
    base = dict(
        spot=SPOT,
        prev_close=498.0,
        day_change_pct=0.4,
        rsi14=50.0,
        sma20=497.0,
        sma50=495.0,
        sma200=480.0,
        ema9=499.0,
        ema21=497.0,
        macd_line=0.5,
        macd_signal=0.3,
        macd_hist=0.0,
        bb_upper=505.0,
        bb_mid=498.0,
        bb_lower=491.0,
        bb_width_pct=2.8,
        atr14=4.0,
        adx14=20.0,
        hv20_pct=18.0,
        high_20d=510.0,
        low_20d=485.0,
        high_50d=515.0,
        low_50d=470.0,
        volume_last_session=3_000_000.0,
        volume_avg20=3_200_000.0,
        volume_ratio=0.94,
        vix_level=15.0,
        vix_percentile_1y=40.0,
        trend_label="Range-bound / low trend strength",
        regime_label="Normal IV",
        regime_notes=[],
    )
    base.update(overrides)
    return MarketSnapshot(**base)


def _intraday(**overrides) -> IntradaySnapshot:
    base = dict(
        last_price=SPOT,
        vwap=SPOT,
        price_vs_vwap_pct=0.0,
        opening_range_high=SPOT + 1.0,
        opening_range_low=SPOT - 1.0,
        orb_status="inside_range",
        ema9=SPOT,
        ema21=SPOT,
        ema_trend="flat",
        rsi14=50.0,
        bars_used=40,
    )
    base.update(overrides)
    return IntradaySnapshot(**base)


def _gate(skip=False, hard_reasons=None, soft_reasons=None) -> GateResult:
    return GateResult(
        skip=skip,
        hard_reasons=hard_reasons or [],
        soft_reasons=soft_reasons or [],
        gap_pct=0.1,
        gap_confirmed=True,
    )


def _synth_chain(dte: int, spot: float, rate: float) -> OptionChain:
    rng = np.random.default_rng(1)
    strikes = np.arange(round(spot) - 30, round(spot) + 30, 1.0)
    iv = 0.18 + rng.uniform(-0.01, 0.01, len(strikes))
    t_years = time_to_expiration_years(dte)
    call_fair = np.array([bs_price(spot, k, t_years, rate, v, "call") for k, v in zip(strikes, iv)])
    put_fair = np.array([bs_price(spot, k, t_years, rate, v, "put") for k, v in zip(strikes, iv)])
    calls = pd.DataFrame({"strike": strikes, "bid": call_fair - 0.02, "ask": call_fair + 0.02, "lastPrice": call_fair, "impliedVolatility": iv})
    puts = pd.DataFrame({"strike": strikes, "bid": put_fair - 0.02, "ask": put_fair + 0.02, "lastPrice": put_fair, "impliedVolatility": iv})
    expiration = (dt.date.today() + dt.timedelta(days=dte)).isoformat()
    return OptionChain(expiration=expiration, dte=dte, calls=calls, puts=puts)


def test_orb_stop_used_when_tighter_than_atr_cap_for_call():
    cfg = Config()  # stop_atr_multiple=0.35, atr14=4.0 -> atr-based stop distance 1.4
    daily = _daily(trend_label="Uptrend", macd_hist=0.8, rsi14=62.0, atr14=4.0)
    intraday = _intraday(
        price_vs_vwap_pct=0.4, ema_trend="bullish", orb_status="above_range",
        opening_range_low=SPOT - 1.0,  # tighter than the 1.4 ATR-based distance
    )
    signal = build_directional_signal(daily, intraday, relative_strength_pct=0.4, gate=_gate(), chains={}, cfg=cfg)
    assert signal.bias == "CALL"
    assert signal.stop_loss_underlying == pytest.approx(SPOT - 1.0)


def test_atr_fallback_used_when_orb_stop_is_wider_for_call():
    cfg = Config()
    daily = _daily(trend_label="Uptrend", macd_hist=0.8, rsi14=62.0, atr14=4.0)
    intraday = _intraday(
        price_vs_vwap_pct=0.4, ema_trend="bullish", orb_status="above_range",
        opening_range_low=SPOT - 5.0,  # wider than the 1.4 ATR-based distance
    )
    signal = build_directional_signal(daily, intraday, relative_strength_pct=0.4, gate=_gate(), chains={}, cfg=cfg)
    assert signal.bias == "CALL"
    assert signal.stop_loss_underlying == pytest.approx(SPOT - cfg.stop_atr_multiple * daily.atr14)


def test_orb_stop_used_when_tighter_than_atr_cap_for_put():
    cfg = Config()
    daily = _daily(trend_label="Downtrend", macd_hist=-0.8, rsi14=38.0, atr14=4.0)
    intraday = _intraday(
        price_vs_vwap_pct=-0.4, ema_trend="bearish", orb_status="below_range",
        opening_range_high=SPOT + 1.0,  # tighter than the 1.4 ATR-based distance
    )
    signal = build_directional_signal(daily, intraday, relative_strength_pct=-0.4, gate=_gate(), chains={}, cfg=cfg)
    assert signal.bias == "PUT"
    assert signal.stop_loss_underlying == pytest.approx(SPOT + 1.0)


def test_atr_fallback_used_when_orb_stop_is_wider_for_put():
    cfg = Config()
    daily = _daily(trend_label="Downtrend", macd_hist=-0.8, rsi14=38.0, atr14=4.0)
    intraday = _intraday(
        price_vs_vwap_pct=-0.4, ema_trend="bearish", orb_status="below_range",
        opening_range_high=SPOT + 6.0,  # wider than the 1.4 ATR-based distance
    )
    signal = build_directional_signal(daily, intraday, relative_strength_pct=-0.4, gate=_gate(), chains={}, cfg=cfg)
    assert signal.bias == "PUT"
    assert signal.stop_loss_underlying == pytest.approx(SPOT + cfg.stop_atr_multiple * daily.atr14)


def test_default_component_weights_sum_to_one():
    weights = Config().component_weights
    assert sum(weights.values()) == pytest.approx(1.0)


def test_zeroing_a_component_removes_its_contribution_and_redistributes():
    import dataclasses

    base_cfg = Config()
    zeroed_weights = {**base_cfg.component_weights, "orb": 0.0}
    cfg = dataclasses.replace(base_cfg, component_weights=zeroed_weights)

    daily = _daily(trend_label="Uptrend", macd_hist=0.8, rsi14=62.0)
    intraday = _intraday(price_vs_vwap_pct=0.4, ema_trend="bullish", orb_status="above_range")
    gate = _gate()

    signal = build_directional_signal(daily, intraday, relative_strength_pct=0.4, gate=gate, chains={}, cfg=cfg)

    orb_component = next(c for c in signal.components if c.name == "orb")
    assert orb_component.weight == pytest.approx(0.0)
    assert orb_component.contribution == pytest.approx(0.0)
    # Weights still sum to 1.0 -- orb's share went to the other six, not just vanished.
    assert sum(c.weight for c in signal.components) == pytest.approx(1.0)
    non_orb_weight = sum(w for name, w in cfg.component_weights.items() if name != "orb")
    vwap_component = next(c for c in signal.components if c.name == "vwap")
    expected_vwap_weight = base_cfg.component_weights["vwap"] / non_orb_weight
    assert vwap_component.weight == pytest.approx(expected_vwap_weight)


def test_all_zero_weights_yield_zero_score_without_crashing():
    import dataclasses

    cfg = dataclasses.replace(Config(), component_weights={k: 0.0 for k in Config().component_weights})
    daily = _daily(trend_label="Uptrend", macd_hist=0.8, rsi14=62.0)
    intraday = _intraday(price_vs_vwap_pct=0.4, ema_trend="bullish", orb_status="above_range")
    signal = build_directional_signal(daily, intraday, relative_strength_pct=0.4, gate=_gate(), chains={}, cfg=cfg)
    assert signal.score == 0.0
    assert signal.bias == "NO TRADE"


def test_all_bullish_signals_produce_call_bias():
    cfg = Config()
    daily = _daily(trend_label="Uptrend", macd_hist=0.8, rsi14=62.0)
    intraday = _intraday(price_vs_vwap_pct=0.4, ema_trend="bullish", orb_status="above_range")
    gate = _gate()
    chains = {"0DTE": _synth_chain(0, SPOT, cfg.risk_free_rate), "Weekly": _synth_chain(7, SPOT, cfg.risk_free_rate)}

    signal = build_directional_signal(daily, intraday, relative_strength_pct=0.4, gate=gate, chains=chains, cfg=cfg)

    assert signal.bias == "CALL"
    assert signal.score > 0
    assert signal.confidence in ("Medium", "High")
    assert signal.stop_loss_underlying < SPOT
    assert signal.target_underlying > SPOT
    assert signal.contracts["0DTE"].option_type == "call"
    assert signal.contracts["Weekly"].option_type == "call"


def test_all_bearish_signals_produce_put_bias():
    cfg = Config()
    daily = _daily(trend_label="Downtrend", macd_hist=-0.8, rsi14=38.0)
    intraday = _intraday(price_vs_vwap_pct=-0.4, ema_trend="bearish", orb_status="below_range")
    gate = _gate()
    chains = {"0DTE": _synth_chain(0, SPOT, cfg.risk_free_rate), "Weekly": _synth_chain(7, SPOT, cfg.risk_free_rate)}

    signal = build_directional_signal(daily, intraday, relative_strength_pct=-0.4, gate=gate, chains=chains, cfg=cfg)

    assert signal.bias == "PUT"
    assert signal.score < 0
    assert signal.stop_loss_underlying > SPOT
    assert signal.target_underlying < SPOT
    assert signal.contracts["0DTE"].option_type == "put"


def test_mixed_signals_yield_no_trade_and_no_contracts():
    cfg = Config()
    daily = _daily()  # range-bound, neutral RSI/MACD
    intraday = _intraday()  # flat EMA, inside range, at VWAP
    gate = _gate()
    chains = {"0DTE": _synth_chain(0, SPOT, cfg.risk_free_rate), "Weekly": _synth_chain(7, SPOT, cfg.risk_free_rate)}

    signal = build_directional_signal(daily, intraday, relative_strength_pct=0.0, gate=gate, chains=chains, cfg=cfg)

    assert signal.bias == "NO TRADE"
    assert signal.contracts == {}
    assert signal.stop_loss_underlying is None


def test_ema_component_handles_not_yet_available_nan_without_crashing():
    cfg = Config()
    daily = _daily()
    # Too early in the session for a 21-bar EMA: ema9/ema21 come back as NaN,
    # not just "equal" (this reproduces a real early-session report).
    intraday = _intraday(ema9=float("nan"), ema21=float("nan"), ema_trend="flat")
    gate = _gate()
    chains = {"Weekly": _synth_chain(7, SPOT, cfg.risk_free_rate)}

    signal = build_directional_signal(daily, intraday, relative_strength_pct=0.0, gate=gate, chains=chains, cfg=cfg)

    ema_component = next(c for c in signal.components if c.name == "ema")
    assert ema_component.contribution == 0.0
    assert "nan" not in ema_component.detail.lower()


def test_hard_gate_forces_no_trade_even_with_bullish_technicals():
    cfg = Config()
    daily = _daily(trend_label="Uptrend", macd_hist=0.8, rsi14=65.0)
    intraday = _intraday(price_vs_vwap_pct=0.4, ema_trend="bullish", orb_status="above_range")
    gate = _gate(skip=True, hard_reasons=["Scheduled macro event today: FOMC decision."])
    chains = {"0DTE": _synth_chain(0, SPOT, cfg.risk_free_rate), "Weekly": _synth_chain(7, SPOT, cfg.risk_free_rate)}

    signal = build_directional_signal(daily, intraday, relative_strength_pct=0.5, gate=gate, chains=chains, cfg=cfg)

    assert signal.bias == "NO TRADE"
    assert signal.forced_no_trade_reasons
    assert signal.contracts == {}


def test_missing_chain_yields_none_contract_without_crashing():
    cfg = Config()
    daily = _daily(trend_label="Uptrend", macd_hist=0.8, rsi14=62.0)
    intraday = _intraday(price_vs_vwap_pct=0.4, ema_trend="bullish", orb_status="above_range")
    gate = _gate()
    chains = {"0DTE": None, "Weekly": _synth_chain(7, SPOT, cfg.risk_free_rate)}

    signal = build_directional_signal(daily, intraday, relative_strength_pct=0.4, gate=gate, chains=chains, cfg=cfg)

    assert signal.bias == "CALL"
    assert signal.contracts["0DTE"] is None
    assert signal.contracts["Weekly"] is not None


def test_selected_contract_delta_is_near_target():
    cfg = Config()
    daily = _daily(trend_label="Uptrend", macd_hist=0.8, rsi14=62.0)
    intraday = _intraday(price_vs_vwap_pct=0.4, ema_trend="bullish", orb_status="above_range")
    gate = _gate()
    chains = {"Weekly": _synth_chain(7, SPOT, cfg.risk_free_rate)}

    signal = build_directional_signal(daily, intraday, relative_strength_pct=0.4, gate=gate, chains=chains, cfg=cfg)

    contract = signal.contracts["Weekly"]
    assert abs(abs(contract.delta) - cfg.directional_delta_target) < 0.2
