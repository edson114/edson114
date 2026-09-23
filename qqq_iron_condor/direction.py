"""Directional (buy calls/puts) signal: blends the daily trend/momentum
snapshot, an intraday VWAP/EMA/opening-range read, and QQQ-vs-SPY relative
strength into a single -1..+1 score, then maps that (subject to the same
hard skip-day gate as the iron condor scanner) to a CALL / PUT / NO TRADE
bias with a suggested near-ATM contract.

This is a rules-based checklist, not a backtested or machine-learned edge
-- see the "Limitations" section the report renders. No day-trading system
is close to "99% profitable"; this exists to structure the same inputs a
discretionary trader would look at, consistently, not to promise a win rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .analysis import MarketSnapshot
from .config import Config
from .data import OptionChain
from .gates import GateResult
from .intraday import IntradaySnapshot
from .options_math import bs_delta, time_to_expiration_years


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class ScoreComponent:
    name: str
    weight: float
    contribution: float
    detail: str


@dataclass
class SuggestedContract:
    option_type: str  # "call" or "put"
    expiration: str
    dte: int
    strike: float
    mid_price: float
    delta: float
    implied_vol: float
    warning: Optional[str] = None


@dataclass
class DirectionalSignal:
    bias: str  # "CALL", "PUT", "NO TRADE"
    score: float
    confidence: str
    components: list = field(default_factory=list)
    forced_no_trade_reasons: list = field(default_factory=list)
    advisory_notes: list = field(default_factory=list)
    stop_loss_underlying: Optional[float] = None
    target_underlying: Optional[float] = None
    contracts: dict = field(default_factory=dict)  # label -> Optional[SuggestedContract]


_WEIGHTS = {
    "daily_trend": 0.20,
    "macd": 0.10,
    "rsi": 0.10,
    "vwap": 0.20,
    "ema": 0.15,
    "orb": 0.15,
    "relative_strength": 0.10,
}


def _trend_component(daily: MarketSnapshot) -> tuple[float, str]:
    if daily.trend_label == "Uptrend":
        return 1.0, "Daily trend: Uptrend (spot > SMA50 > SMA200, MACD histogram positive)."
    if daily.trend_label == "Downtrend":
        return -1.0, "Daily trend: Downtrend (spot < SMA50 < SMA200, MACD histogram negative)."
    if daily.trend_label == "Range-bound / low trend strength":
        return 0.0, f"Daily trend: range-bound (ADX {daily.adx14:.1f} below the trend threshold) -- no edge from trend."
    val = 0.4 if daily.macd_hist > 0 else (-0.4 if daily.macd_hist < 0 else 0.0)
    lean = "bullish" if val > 0 else ("bearish" if val < 0 else "flat")
    return val, f"Daily trend: mixed/transitional -- leaning {lean} on MACD histogram sign."


def _macd_component(daily: MarketSnapshot) -> tuple[float, str]:
    val = 1.0 if daily.macd_hist > 0 else (-1.0 if daily.macd_hist < 0 else 0.0)
    lean = "bullish" if val > 0 else ("bearish" if val < 0 else "flat")
    return val, f"MACD histogram {daily.macd_hist:+.3f} ({lean})."


def _rsi_component(daily: MarketSnapshot) -> tuple[float, str]:
    val = _clip((daily.rsi14 - 50.0) / 25.0)
    note = f"RSI(14) daily {daily.rsi14:.1f}"
    if daily.rsi14 >= 70:
        note += " -- overbought; a trend day can extend, but watch for mean-reversion risk."
    elif daily.rsi14 <= 30:
        note += " -- oversold; a trend day can extend, but watch for a bounce."
    return val, note


def _vwap_component(intraday: IntradaySnapshot) -> tuple[float, str]:
    pct = intraday.price_vs_vwap_pct
    if pct != pct:  # NaN
        return 0.0, "VWAP unavailable yet (too early in the session)."
    val = _clip(pct / 0.5)
    side = "above" if pct >= 0 else "below"
    return val, f"Price {side} VWAP by {abs(pct):.2f}% (${intraday.last_price:.2f} vs VWAP ${intraday.vwap:.2f})."


def _ema_component(intraday: IntradaySnapshot) -> tuple[float, str]:
    if intraday.ema9 != intraday.ema9 or intraday.ema21 != intraday.ema21:  # NaN check
        return 0.0, "Intraday EMA9/21 not available yet (too early in the session for a 21-bar EMA)."
    val = 1.0 if intraday.ema_trend == "bullish" else (-1.0 if intraday.ema_trend == "bearish" else 0.0)
    rel = ">" if val > 0 else ("<" if val < 0 else "≈")
    return val, f"Intraday EMA9 {rel} EMA21 (${intraday.ema9:.2f} vs ${intraday.ema21:.2f}) -- {intraday.ema_trend}."


def _orb_component(intraday: IntradaySnapshot, opening_range_minutes: int) -> tuple[float, str]:
    if intraday.orb_status == "above_range":
        return 1.0, f"Price broke above the opening {opening_range_minutes}-min range high (${intraday.opening_range_high:.2f})."
    if intraday.orb_status == "below_range":
        return -1.0, f"Price broke below the opening {opening_range_minutes}-min range low (${intraday.opening_range_low:.2f})."
    if intraday.orb_status == "inside_range":
        return 0.0, (
            f"Still inside the opening {opening_range_minutes}-min range "
            f"(${intraday.opening_range_low:.2f}-${intraday.opening_range_high:.2f}) -- no breakout yet."
        )
    return 0.0, "Opening range not yet established (too early in the session)."


def _relative_strength_component(rel_strength_pct: float) -> tuple[float, str]:
    val = _clip(rel_strength_pct / 0.3)
    lean = "outperforming" if rel_strength_pct >= 0 else "underperforming"
    tag = "tech-specific strength" if rel_strength_pct >= 0 else "tech-specific weakness"
    return val, f"QQQ is {lean} SPY today by {abs(rel_strength_pct):.2f} pts -- {tag}."


def _mid_price(row: pd.Series) -> float:
    bid = float(row.get("bid", 0.0) or 0.0)
    ask = float(row.get("ask", 0.0) or 0.0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return float(row.get("lastPrice", 0.0) or 0.0)


def _select_contract(
    chain: OptionChain,
    spot: float,
    rate: float,
    option_type: str,
    target_delta: float,
    reference_hv: Optional[float],
) -> Optional[SuggestedContract]:
    t_years = time_to_expiration_years(chain.dte)
    df = (chain.calls if option_type == "call" else chain.puts).copy()
    df["iv"] = df["impliedVolatility"].fillna(0.0)
    df = df[df["iv"] > 0.03]  # below ~3% IV a QQQ quote is essentially always stale/untraded
    if df.empty:
        return None

    df["delta"] = df.apply(
        lambda r: bs_delta(spot, float(r["strike"]), t_years, rate, float(r["iv"]), option_type), axis=1
    )
    df["mid"] = df.apply(_mid_price, axis=1)

    idx = (df["delta"].abs() - target_delta).abs().idxmin()
    row = df.loc[idx]
    iv = float(row["iv"])
    delta = float(row["delta"])

    warning = None
    if reference_hv and reference_hv > 0 and iv < 0.5 * reference_hv:
        warning = (
            f"Near-money IV ({iv * 100:.1f}%) looks implausibly low next to trailing realized "
            f"volatility ({reference_hv * 100:.1f}%) -- likely stale/untraded quotes; re-pull before trusting this."
        )
    elif abs(abs(delta) - target_delta) > 0.15:
        warning = (
            f"Selected contract's delta ({delta:.3f}) landed noticeably off the {target_delta:.2f} "
            "target -- per-contract IV may not have populated reliably yet (common right after the "
            "open); re-pull quotes before trusting this."
        )

    return SuggestedContract(
        option_type=option_type,
        expiration=chain.expiration,
        dte=chain.dte,
        strike=float(row["strike"]),
        mid_price=round(float(row["mid"]), 2),
        delta=round(delta, 3),
        implied_vol=round(iv * 100, 1),
        warning=warning,
    )


def build_directional_signal(
    daily: MarketSnapshot,
    intraday: IntradaySnapshot,
    relative_strength_pct: float,
    gate: GateResult,
    chains: dict,
    cfg: Config,
) -> DirectionalSignal:
    raw = {
        "daily_trend": _trend_component(daily),
        "macd": _macd_component(daily),
        "rsi": _rsi_component(daily),
        "vwap": _vwap_component(intraday),
        "ema": _ema_component(intraday),
        "orb": _orb_component(intraday, cfg.opening_range_minutes),
        "relative_strength": _relative_strength_component(relative_strength_pct),
    }
    components = [
        ScoreComponent(name=name, weight=_WEIGHTS[name], contribution=round(_WEIGHTS[name] * val, 4), detail=detail)
        for name, (val, detail) in raw.items()
    ]
    score = round(sum(c.contribution for c in components), 3)

    forced_no_trade_reasons = list(gate.hard_reasons)
    advisory_notes = list(gate.soft_reasons)

    if forced_no_trade_reasons:
        bias = "NO TRADE"
        confidence = "N/A (hard gate triggered -- see reasons below)"
    elif score >= cfg.signal_score_threshold:
        bias = "CALL"
        confidence = "High" if score >= 0.6 else "Medium"
    elif score <= -cfg.signal_score_threshold:
        bias = "PUT"
        confidence = "High" if score <= -0.6 else "Medium"
    else:
        bias = "NO TRADE"
        confidence = "N/A (no directional edge / choppy)"

    stop_loss_underlying = None
    target_underlying = None
    contracts: dict = {}

    if bias in ("CALL", "PUT"):
        option_type = "call" if bias == "CALL" else "put"
        spot = daily.spot
        atr_stop = cfg.stop_atr_multiple * daily.atr14

        if bias == "CALL":
            stop_loss_underlying = intraday.opening_range_low if intraday.orb_status == "above_range" else spot - atr_stop
            risk = max(spot - stop_loss_underlying, 0.01)
            target_underlying = spot + cfg.reward_risk_ratio * risk
        else:
            stop_loss_underlying = intraday.opening_range_high if intraday.orb_status == "below_range" else spot + atr_stop
            risk = max(stop_loss_underlying - spot, 0.01)
            target_underlying = spot - cfg.reward_risk_ratio * risk

        reference_hv = daily.hv20_pct / 100.0 if daily.hv20_pct == daily.hv20_pct else None
        for label, chain in chains.items():
            contracts[label] = (
                _select_contract(chain, spot, cfg.risk_free_rate, option_type, cfg.directional_delta_target, reference_hv)
                if chain is not None
                else None
            )

    return DirectionalSignal(
        bias=bias,
        score=score,
        confidence=confidence,
        components=components,
        forced_no_trade_reasons=forced_no_trade_reasons,
        advisory_notes=advisory_notes,
        stop_loss_underlying=round(stop_loss_underlying, 2) if stop_loss_underlying is not None else None,
        target_underlying=round(target_underlying, 2) if target_underlying is not None else None,
        contracts=contracts,
    )
