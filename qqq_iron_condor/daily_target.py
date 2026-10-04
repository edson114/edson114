"""Daily $ target plan: buy a deep-ITM (~0.80 delta), ~60 DTE QQQ call or
put, 10 contracts, and close the whole position at +$1,000.

The math the plan is built on: 10 contracts x 100 shares = 1,000 shares of
option exposure, so +$1,000 means the option has to gain $1.00/share. At
0.80 delta that takes roughly a $1.25 QQQ move in your favour (a bit less
as gamma kicks in, a bit more as theta bleeds). With QQQ's daily range
typically several times that, the target is usually *reachable* in a
session -- the hard part is being on the right side of it, which is what
the direction call (and the stop) are for.

Direction comes from first-hour momentum by default (calls if QQQ is
above its open at 10:30 ET, puts if below -- see `first_hour_read`), or
from the existing directional signal (signal.py) with
`--direction-source signal`. The signal's hard skip-day gates apply either
way. This module adds contract selection at the deeper delta/longer
expiry, the $-based exit levels, a one-position-at-a-time rule, and a
position check. It never places orders.

Usage:
    python -m qqq_iron_condor.daily_target                    # today's plan
    python -m qqq_iron_condor.daily_target --direction call   # override the direction
    python -m qqq_iron_condor.daily_target --direction-source signal  # use signal.py's score
    python -m qqq_iron_condor.daily_target --check --side call --strike 700 \\
        --expiration 2026-12-04 --entry 58.40 --opened 2026-10-05
    python -m qqq_iron_condor.daily_target --self-test        # offline, synthetic data
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from .config import Config
from .data import OptionChain
from .direction import SuggestedContract, _mid_price, _select_contract
from .options_math import bs_delta, bs_price, time_to_expiration_years

DISCLAIMER = (
    "> Decision-support, not an auto-trader and not financial advice. A $1,000/day goal is a "
    "*target*, not an expected outcome: some days the stop or the time stop gets hit instead, "
    "and a 0.80-delta, 10-contract position loses roughly $800 for every $1 QQQ moves against "
    "it. Confirm live quotes, greeks and buying power with your broker before trading."
)


def per_share(usd: float, contracts: int) -> float:
    """Dollar P&L for the whole position -> required option price change per share."""
    return usd / (contracts * 100.0)


def underlying_for_option_price(
    target_price: float, strike: float, t_years: float, rate: float, iv: float, option_type: str
) -> Optional[float]:
    """QQQ price at which the option's Black-Scholes value equals
    `target_price` (same IV and time) -- i.e. the underlying level to
    watch/alert on for an option-price exit. None if unreachable (a put
    can never be worth more than its discounted strike)."""
    if target_price <= 0:
        return None
    lo, hi = 0.01, strike * 4.0
    price = lambda s: bs_price(s, strike, t_years, rate, iv, option_type)  # noqa: E731
    if not min(price(lo), price(hi)) <= target_price <= max(price(lo), price(hi)):
        return None
    increasing = option_type == "call"
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if (price(mid) < target_price) == increasing:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def strike_for_delta(spot: float, t_years: float, rate: float, iv: float, option_type: str, target_delta: float) -> float:
    """Whole-dollar strike whose Black-Scholes |delta| is closest to
    `target_delta` (backtest only -- live plans pick from the real chain)."""
    lo, hi = spot * 0.2, spot * 2.0
    for _ in range(100):
        mid = (lo + hi) / 2.0
        d = abs(bs_delta(spot, mid, t_years, rate, iv, option_type))
        # |delta| falls with strike for calls and rises with strike for puts.
        if (d > target_delta) == (option_type == "call"):
            lo = mid
        else:
            hi = mid
    return float(round((lo + hi) / 2.0))


@dataclass
class LegPlan:
    contract: SuggestedContract
    contracts: int
    spot: float
    entry_price: float  # per share (chain mid)
    cost_usd: float
    target_option_price: float
    stop_option_price: float
    target_underlying: Optional[float]
    stop_underlying: Optional[float]
    move_to_target: Optional[float]  # QQQ $ move needed (absolute)
    move_to_stop: Optional[float]
    move_to_target_atr: Optional[float]  # as a fraction of ATR14
    theta_per_day_usd: float  # whole position, negative = cost of holding a day


@dataclass
class FirstHourRead:
    ready: bool
    side: Optional[str]  # "call", "put", or None (flat / not ready)
    open_price: Optional[float]
    end_price: Optional[float]
    move: Optional[float]
    detail: str


def first_hour_read(bars, minutes: int = 60) -> FirstHourRead:
    """Today's first-hour momentum from intraday bars (index = ET wall
    time, today's session only). Ready only once a bar has started at or
    after open + `minutes`, so the last first-hour bar is complete -- an
    in-progress bar's close isn't the 10:30 price yet."""
    if bars is None or len(bars) == 0:
        return FirstHourRead(False, None, None, None, None, "No intraday bars for today yet.")
    cutoff = bars.index[0] + dt.timedelta(minutes=minutes)
    window = bars[bars.index < cutoff]
    if not (bars.index >= cutoff).any():
        return FirstHourRead(
            False, None, None, None, None,
            f"First hour not finished yet (ends {cutoff:%H:%M} ET) -- re-run after it.",
        )
    open_px = float(window["Open"].iloc[0])
    end_px = float(window["Close"].iloc[-1])
    move = end_px - open_px
    side = "call" if move > 0 else ("put" if move < 0 else None)
    direction = {"call": "up", "put": "down"}.get(side, "flat")
    return FirstHourRead(
        True, side, round(open_px, 2), round(end_px, 2), round(move, 2),
        f"QQQ {direction} {move:+.2f} in the first hour (${open_px:,.2f} open -> ${end_px:,.2f} at {cutoff:%H:%M}).",
    )


def choose_bias(
    source: str,
    override: Optional[str],
    forced_no_trade: list,
    signal_bias: Optional[str],
    first_hour: Optional[FirstHourRead],
) -> tuple[str, str]:
    """(bias, description of where it came from). Bias is CALL / PUT /
    NO TRADE / WAIT (first hour not finished) / UNKNOWN (no data)."""
    if override:
        return override.upper(), "manual override (--direction)"
    if forced_no_trade:
        return "NO TRADE", "hard skip-day gate"
    if source == "signal":
        if signal_bias is None:
            return "UNKNOWN", "directional signal unavailable"
        return signal_bias, "directional signal (signal.py)"
    if first_hour is None:
        return "UNKNOWN", "first-hour momentum unavailable"
    if not first_hour.ready:
        return "WAIT", "first-hour momentum (not ready yet)"
    if first_hour.side is None:
        return "NO TRADE", "first-hour momentum (flat first hour)"
    return first_hour.side.upper(), "first-hour momentum"


@dataclass
class DailyTargetPlan:
    bias: str  # "CALL", "PUT", "NO TRADE", "WAIT", or "UNKNOWN" (no data)
    bias_source: str
    signal_score: Optional[float]
    spot: float
    atr14: float
    legs: dict = field(default_factory=dict)  # "call"/"put" -> Optional[LegPlan]
    notes: list = field(default_factory=list)
    signal_bias: Optional[str] = None
    first_hour: Optional[FirstHourRead] = None

    @property
    def chosen(self) -> Optional[LegPlan]:
        if self.bias == "CALL":
            return self.legs.get("call")
        if self.bias == "PUT":
            return self.legs.get("put")
        return None


def build_leg_plan(
    chain: OptionChain, spot: float, atr14: float, option_type: str, cfg: Config, reference_hv: Optional[float] = None
) -> Optional[LegPlan]:
    contract = _select_contract(chain, spot, cfg.risk_free_rate, option_type, cfg.daily_target_delta, reference_hv)
    if contract is None or contract.mid_price <= 0:
        return None

    n = cfg.daily_target_contracts
    entry = contract.mid_price
    target_px = entry + per_share(cfg.daily_target_profit_usd, n)
    stop_px = max(entry - per_share(cfg.daily_target_stop_usd, n), 0.01)

    t_years = time_to_expiration_years(contract.dte)
    iv = contract.implied_vol / 100.0
    # Solve against the model value +/- the required change rather than the
    # quoted mid +/- it, so a mid that sits off the Black-Scholes value
    # (wide spread, stale IV) doesn't distort the QQQ move needed.
    model_now = bs_price(spot, contract.strike, t_years, cfg.risk_free_rate, iv, option_type)
    target_u = underlying_for_option_price(
        model_now + (target_px - entry), contract.strike, t_years, cfg.risk_free_rate, iv, option_type
    )
    stop_u = underlying_for_option_price(
        model_now - (entry - stop_px), contract.strike, t_years, cfg.risk_free_rate, iv, option_type
    )

    move_t = abs(target_u - spot) if target_u is not None else None
    move_s = abs(stop_u - spot) if stop_u is not None else None

    one_day = 1.0 / 365.0
    theta_share = (
        bs_price(spot, contract.strike, max(t_years - one_day, one_day), cfg.risk_free_rate, iv, option_type) - model_now
    )

    return LegPlan(
        contract=contract,
        contracts=n,
        spot=spot,
        entry_price=round(entry, 2),
        cost_usd=round(entry * 100 * n, 2),
        target_option_price=round(target_px, 2),
        stop_option_price=round(stop_px, 2),
        target_underlying=round(target_u, 2) if target_u is not None else None,
        stop_underlying=round(stop_u, 2) if stop_u is not None else None,
        move_to_target=round(move_t, 2) if move_t is not None else None,
        move_to_stop=round(move_s, 2) if move_s is not None else None,
        move_to_target_atr=round(move_t / atr14, 2) if move_t is not None and atr14 > 0 else None,
        theta_per_day_usd=round(theta_share * 100 * n, 2),
    )


def build_plan(
    spot: float,
    atr14: float,
    chain: Optional[OptionChain],
    bias: str,
    bias_source: str,
    cfg: Config,
    signal_score: Optional[float] = None,
    reference_hv: Optional[float] = None,
    notes: Optional[list] = None,
    signal_bias: Optional[str] = None,
    first_hour: Optional[FirstHourRead] = None,
) -> DailyTargetPlan:
    notes = list(notes or [])
    legs: dict = {"call": None, "put": None}
    if chain is None:
        notes.append(
            f"No QQQ expiration listed {cfg.daily_target_expiration.min_dte}-"
            f"{cfg.daily_target_expiration.max_dte} days out -- no contract selected."
        )
    else:
        for option_type in ("call", "put"):
            legs[option_type] = build_leg_plan(chain, spot, atr14, option_type, cfg, reference_hv)
    return DailyTargetPlan(
        bias=bias, bias_source=bias_source, signal_score=signal_score, spot=spot, atr14=atr14, legs=legs,
        notes=notes, signal_bias=signal_bias, first_hour=first_hour,
    )


# --- Position check -----------------------------------------------------

@dataclass
class PositionStatus:
    action: str  # "HOLD", "CLOSE -- TARGET HIT", "CLOSE -- STOP HIT", "CLOSE -- TIME STOP"
    pnl_usd: float
    current_price: float
    target_option_price: float
    stop_option_price: float
    days_held: int


def evaluate_position(entry_price: float, current_price: float, days_held: int, cfg: Config, contracts: Optional[int] = None) -> PositionStatus:
    """Apply the exit rules to an open position. `days_held` counts
    completed sessions since entry (0 on the entry day)."""
    n = contracts or cfg.daily_target_contracts
    pnl = (current_price - entry_price) * 100 * n
    target_px = entry_price + per_share(cfg.daily_target_profit_usd, n)
    stop_px = entry_price - per_share(cfg.daily_target_stop_usd, n)
    if pnl >= cfg.daily_target_profit_usd - 1e-9:
        action = "CLOSE -- TARGET HIT"
    elif pnl <= -cfg.daily_target_stop_usd + 1e-9:
        action = "CLOSE -- STOP HIT"
    elif days_held >= cfg.daily_target_max_hold_days:
        action = "CLOSE -- TIME STOP"
    else:
        action = "HOLD"
    return PositionStatus(
        action=action,
        pnl_usd=round(pnl, 2),
        current_price=round(current_price, 2),
        target_option_price=round(target_px, 2),
        stop_option_price=round(stop_px, 2),
        days_held=days_held,
    )


def sessions_held(opened: dt.date, today: dt.date) -> int:
    return int(np.busday_count(opened, today))


# --- Rendering ------------------------------------------------------------

def _usd(x: Optional[float]) -> str:
    if x is None:
        return "n/a"
    return f"-${-x:,.2f}" if x < 0 else f"${x:,.2f}"


def _leg_table(leg: LegPlan, cfg: Config) -> list[str]:
    c = leg.contract
    side = "CALL" if c.option_type == "call" else "PUT"
    lines = [
        f"| | |",
        f"|---|---|",
        f"| **Contract** | QQQ {c.expiration} ${c.strike:g} {side} ({c.dte} DTE) |",
        f"| **Delta / IV** | {c.delta:+.2f} / {c.implied_vol:.1f}% |",
        f"| **Buy** | {leg.contracts} contracts, limit ~{_usd(leg.entry_price)} (mid) -- cost {_usd(leg.cost_usd)} |",
        f"| **Take profit** | sell all at {_usd(leg.target_option_price)} = **+{_usd(cfg.daily_target_profit_usd)}** "
        f"(QQQ ~{_usd(leg.target_underlying)}, a {_usd(leg.move_to_target)} move"
        + (f", {leg.move_to_target_atr:.2f}x ATR" if leg.move_to_target_atr is not None else "")
        + ") |",
        f"| **Stop** | sell all at {_usd(leg.stop_option_price)} = **-{_usd(cfg.daily_target_stop_usd)}** "
        f"(QQQ ~{_usd(leg.stop_underlying)}, a {_usd(leg.move_to_stop)} move against) |",
        f"| **Time stop** | close at the end of session {cfg.daily_target_max_hold_days} if neither level hits |",
        f"| **Theta** | ~{_usd(leg.theta_per_day_usd)}/day for the whole position |",
    ]
    if c.warning:
        lines.append(f"| ⚠️ | {c.warning} |")
    return lines


def render_plan(plan: DailyTargetPlan, cfg: Config, now: Optional[dt.datetime] = None) -> str:
    now = now or dt.datetime.now()
    n = cfg.daily_target_contracts
    move_per_share = per_share(cfg.daily_target_profit_usd, n)
    lines = [
        f"# QQQ Daily $ Target Plan -- {now:%Y-%m-%d %H:%M}",
        "",
        DISCLAIMER,
        "",
        "## Goal math",
        "",
        f"- **Goal:** +{_usd(cfg.daily_target_profit_usd)} = {n} contracts x 100 shares x **{_usd(move_per_share)}/share** "
        f"option gain.",
        f"- At {cfg.daily_target_delta:.2f} delta that's a **~{_usd(move_per_share / cfg.daily_target_delta)} QQQ move** "
        f"in your favour (QQQ ${plan.spot:,.2f}, ATR14 {_usd(plan.atr14)}).",
        f"- Every $1 QQQ moves *against* you costs ~{_usd(cfg.daily_target_delta * 100 * n)}.",
        "",
    ]

    icon = {"CALL": "🟢", "PUT": "🔴", "WAIT": "⏳"}.get(plan.bias, "⚪")
    lines.append(f"## {icon} Today: {plan.bias}")
    lines.append("")
    lines.append(f"_Direction source: {plan.bias_source}_")
    lines.append("")
    lines += ["| Read | Says |", "|---|---|"]
    if plan.first_hour is not None:
        fh = plan.first_hour
        says = (fh.side or "flat").upper() if fh.ready else "not ready"
        lines.append(f"| First-hour momentum | **{says}** -- {fh.detail} |")
    if plan.signal_bias is not None:
        score = f" (score {plan.signal_score:+.2f})" if plan.signal_score is not None else ""
        lines.append(f"| Directional signal | **{plan.signal_bias}**{score} |")
    lines.append("")
    if plan.first_hour is not None and plan.signal_bias in ("CALL", "PUT") and plan.first_hour.side:
        agree = plan.first_hour.side.upper() == plan.signal_bias
        lines.append(
            "Both reads agree." if agree else "⚠️ The two reads disagree -- consider sitting this one out or sizing down."
        )
        lines.append("")
    if "first-hour" in plan.bias_source:
        lines.append(
            "_First-hour momentum is a **paper-trade-only** rule: it made +$30k over 455 backtested trades at "
            "$0.05/share slippage, but lost money at $0.10/share or with worst-case scoring "
            "(`reports/backtests/daily-target-experiments-2026-10-04.md`). Log real fills before risking money._"
        )
        lines.append("")
    for note in plan.notes:
        lines.append(f"- {note}")
    if plan.notes:
        lines.append("")

    chosen = plan.chosen
    if chosen is not None:
        lines.append("### Order ticket")
        lines.append("")
        lines.extend(_leg_table(chosen, cfg))
        lines.append("")
        lines.append(
            f"**Right after the fill:** place a GTC limit *sell to close* for all {n} at your actual fill + "
            f"{_usd(move_per_share)} -- that's what closes the day at +{_usd(cfg.daily_target_profit_usd)} "
            "automatically. Set a price alert on QQQ at the stop level (option stop orders on wide "
            "spreads fill badly) and exit when it triggers."
        )
        lines.append("")
    elif plan.bias == "NO TRADE":
        lines.append("**No entry today.** Sitting out a gated / no-edge day is part of the plan, not a missed $1,000.")
        lines.append("")
    elif plan.bias == "WAIT":
        lines.append("**Not yet.** Re-run after the first hour closes (10:30 ET) to get today's direction.")
        lines.append("")

    other_label = "Both setups" if chosen is None else "The other side (for reference)"
    others = [leg for key, leg in plan.legs.items() if leg is not None and leg is not chosen]
    if others:
        lines.append(f"### {other_label}")
        lines.append("")
        for leg in others:
            lines.extend(_leg_table(leg, cfg))
            lines.append("")

    lines += [
        "## Rules",
        "",
        "1. **One position at a time.** If yesterday's position is still open, don't open another -- "
        "run `--check` on it instead. Ten more contracts every day would stack risk fast.",
        f"2. **Exit on whichever comes first:** +{_usd(cfg.daily_target_profit_usd)}, "
        f"-{_usd(cfg.daily_target_stop_usd)}, or the end of session {cfg.daily_target_max_hold_days}.",
        "3. **Don't enter on hard-gate days** (FOMC/CPI, a big opening gap, VIX spike) -- the plan returns NO TRADE.",
        "4. **Enter at ~10:30 ET** (after the first hour), with a limit at or near the mid -- "
        "deep-ITM spreads can be wide; paying $0.30 over mid is 30% of the day's target, and the "
        "backtested edge disappears at $0.10/share.",
        "5. **Check buying power:** a 0.80-delta 60 DTE QQQ contract costs tens of dollars per share, "
        "so 10 of them is tens of thousands of dollars of premium.",
        "",
        "## Limitations",
        "",
        "- Neither direction read is a proven edge. See `daily_target_backtest.py` and "
        "`daily_target_experiments.py` for how these exact rules have done historically.",
        "- Levels are Black-Scholes estimates from the chain's own IV at a fixed IV; a volatility "
        "move shifts the option price independently of QQQ.",
        "- Free quotes can be delayed or stale; re-pull before acting.",
        "- This tool never places trades.",
    ]
    return "\n".join(lines) + "\n"


def render_status(status: PositionStatus, side: str, strike: float, expiration: str, cfg: Config) -> str:
    icon = "✅" if "TARGET" in status.action else ("🛑" if "STOP" in status.action else "⏳")
    return "\n".join(
        [
            f"## {icon} {status.action}",
            "",
            f"- **Position:** QQQ {expiration} ${strike:g} {side.upper()}",
            f"- **Current mid:** {_usd(status.current_price)}  |  **P&L:** {_usd(status.pnl_usd)}",
            f"- **Take profit at:** {_usd(status.target_option_price)}  |  **Stop at:** {_usd(status.stop_option_price)}",
            f"- **Sessions held:** {status.days_held} of {cfg.daily_target_max_hold_days}",
            "",
        ]
    )


# --- Live orchestration -----------------------------------------------------

def _today_intraday(cfg: Config):
    from .data import get_intraday_history

    bars = get_intraday_history(cfg.symbol, cfg.intraday_interval, cfg.intraday_period)
    return bars[bars.index.date == dt.date.today()]


def run_plan(cfg: Config, direction: Optional[str] = None, source: Optional[str] = None) -> tuple[str, DailyTargetPlan]:
    from . import providers
    from .analysis import build_snapshot

    source = source or cfg.daily_target_direction_source
    price_history = providers.get_price_history(cfg.symbol, cfg.price_history_period)
    vix_history = providers.get_vix_history(cfg.vix_history_period, cfg.vix_symbol, cfg.tradier_vix_symbol)
    daily = build_snapshot(price_history, vix_history, cfg.adx_trend_threshold)

    notes: list = []
    score = signal_bias = None
    forced: list = []
    # The signal always runs: its hard skip-day gates apply to both sources.
    try:
        from .signal import run_signal

        _, signal = run_signal(cfg)
        signal_bias, score, forced = signal.bias, signal.score, list(signal.forced_no_trade_reasons)
        notes += [f"⛔ {r}" for r in forced]
        notes += [f"⚠️ {r}" for r in signal.advisory_notes]
    except Exception as exc:
        notes.append(
            f"Directional signal could not run ({exc}). Re-run during market hours, or pass "
            "--direction call|put to use your own read."
        )

    try:
        first_hour = first_hour_read(_today_intraday(cfg), cfg.daily_target_first_hour_minutes)
    except Exception as exc:
        first_hour = FirstHourRead(False, None, None, None, None, f"Intraday bars unavailable ({exc}).")

    bias, bias_source = choose_bias(source, direction, forced, signal_bias, first_hour)
    chains = providers.pick_expirations_for_targets(cfg.symbol, (cfg.daily_target_expiration,))
    reference_hv = daily.hv20_pct / 100.0 if daily.hv20_pct == daily.hv20_pct else None
    plan = build_plan(
        daily.spot, daily.atr14, chains.get(cfg.daily_target_expiration.label), bias, bias_source, cfg,
        signal_score=score, reference_hv=reference_hv, notes=notes, signal_bias=signal_bias, first_hour=first_hour,
    )
    return render_plan(plan, cfg), plan


def _synthetic_chain(spot: float, dte: int, cfg: Config, iv: float = 0.20) -> OptionChain:
    import pandas as pd

    strikes = np.arange(round(spot * 0.7), round(spot * 1.3), 1.0)
    t_years = time_to_expiration_years(dte)

    def side(option_type: str) -> "pd.DataFrame":
        fair = np.array([bs_price(spot, k, t_years, cfg.risk_free_rate, iv, option_type) for k in strikes])
        return pd.DataFrame(
            {
                "strike": strikes,
                "bid": np.maximum(0.01, fair - 0.10),
                "ask": fair + 0.10,
                "lastPrice": fair,
                "impliedVolatility": iv,
                "volume": 100,
            }
        )

    expiration = (dt.date.today() + dt.timedelta(days=dte)).isoformat()
    return OptionChain(expiration=expiration, dte=dte, calls=side("call"), puts=side("put"))


def _self_test_report() -> tuple[str, DailyTargetPlan]:
    cfg = Config()
    spot = 750.0
    import pandas as pd

    idx = pd.date_range(dt.datetime.combine(dt.date.today(), dt.time(9, 30)), periods=14, freq="5min")
    closes = np.linspace(spot - 2.0, spot, len(idx))
    bars = pd.DataFrame({"Open": closes - 0.1, "High": closes + 0.2, "Low": closes - 0.3, "Close": closes}, index=idx)
    first_hour = first_hour_read(bars, cfg.daily_target_first_hour_minutes)
    bias, source = choose_bias("first_hour", None, [], "CALL", first_hour)
    plan = build_plan(
        spot, 9.5, _synthetic_chain(spot, 60, cfg), bias, f"{source} (synthetic self-test)", cfg,
        signal_score=0.42, signal_bias="CALL", first_hour=first_hour,
    )
    return render_plan(plan, cfg), plan


def _quote_mid(cfg: Config, side: str, strike: float, expiration: str) -> float:
    from . import providers

    chain = providers.get_option_chain_for_expiration(cfg.symbol, expiration)
    df = chain.calls if side == "call" else chain.puts
    rows = df[(df["strike"] - strike).abs() < 1e-6]
    if rows.empty:
        raise RuntimeError(f"No {side} at strike {strike:g} for {expiration}")
    return _mid_price(rows.iloc[0])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="QQQ daily $ target plan (0.80-delta ~60 DTE calls/puts)")
    parser.add_argument("--direction", choices=["call", "put"], help="Override the direction entirely")
    parser.add_argument(
        "--direction-source",
        choices=["first-hour", "signal"],
        help="Which read picks CALL/PUT (default: Config.daily_target_direction_source)",
    )
    parser.add_argument("--check", action="store_true", help="Check an open position against the exit rules")
    parser.add_argument("--side", choices=["call", "put"])
    parser.add_argument("--strike", type=float)
    parser.add_argument("--expiration", help="YYYY-MM-DD")
    parser.add_argument("--entry", type=float, help="Your fill price per share")
    parser.add_argument("--opened", help="Entry date YYYY-MM-DD (default: today)")
    parser.add_argument("--contracts", type=int, help="Contracts held (default: config)")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--self-test", action="store_true", help="Run on synthetic data (no network)")
    args = parser.parse_args(argv)

    cfg = Config()

    if args.check:
        missing = [k for k in ("side", "strike", "expiration", "entry") if getattr(args, k) is None]
        if missing:
            parser.error("--check needs " + ", ".join(f"--{m}" for m in missing))
        opened = dt.date.fromisoformat(args.opened) if args.opened else dt.date.today()
        try:
            mid = _quote_mid(cfg, args.side, args.strike, args.expiration)
        except Exception as exc:
            print(f"Quote failed: {exc}", file=sys.stderr)
            return 1
        status = evaluate_position(args.entry, mid, sessions_held(opened, dt.date.today()), cfg, args.contracts)
        print(render_status(status, args.side, args.strike, args.expiration, cfg))
        return 0

    try:
        source = args.direction_source.replace("-", "_") if args.direction_source else None
        report_md, _ = _self_test_report() if args.self_test else run_plan(cfg, args.direction, source)
    except Exception as exc:
        print(f"Plan failed: {exc}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir or cfg.daily_target_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{dt.datetime.now():%Y-%m-%d-%H%M}.md"
    out_path.write_text(report_md)
    print(report_md)
    print(f"\nSaved report to {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
