"""The time machine, on the option.

trader-timemachine's method: the rest of today is rebuilt 10,000 times, bar
by bar, from the stock's own past 5-minute bars at the same time of day (+-2
bars), each bar scaled to the current price. Here, at every replayed bar the
option is re-priced with Black-Scholes at that bar's time to expiry, holding
the implied volatility at today's level. The stop is the underlying's
opening-range midpoint and is checked first in every bar (a bar that touches
both counts as the stop). The target is the option bid reaching
cost x (1 + target%), where cost is the ask you pay.

Same seed, same data, same numbers -- but the draws come from numpy, so they
are not the same sequence as trader-timemachine's random.Random replays.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from .bars import SESSION_BARS, bar_close_time
from .options import Contract, bs_price_vec, years_until
from .setup import cents_away


@dataclass
class OptionOdds:
    replays: int
    seed: int
    bars_left: int
    p_target: float
    p_stop: float
    p_neither: float
    p_green: float
    avg_return_pct: float
    median_return_pct: float
    p5_return_pct: float
    p95_return_pct: float
    return_at_stop_pct: float  # if the stop fills at the stop, right now


@dataclass
class RuleHistory:
    sessions: int
    triggered: int
    target: int
    stop: int
    neither: int
    avg_return_pct: float | None


def build_pool(past: list) -> np.ndarray:
    """(78, N, 4): every past session's bar i as (o, h, l, c) / bar i-1's close.
    Slot 0 has no previous bar and is never drawn."""
    n = len(past)
    pool = np.ones((SESSION_BARS, n, 4))
    for j, (_, d) in enumerate(past):
        pool[1:, j, :] = d[1:, :] / d[:-1, 3:4]
    return pool


def option_exit_value(spot, contract: Contract, t_years: float, rate: float) -> np.ndarray:
    """What selling it would fetch: the model mid minus half today's spread, floored at zero."""
    mid = bs_price_vec(spot, contract.strike, t_years, rate, contract.iv, contract.option_type)
    return np.maximum(mid - contract.half_spread, 0.0)


def simulate_option(
    pool: np.ndarray,
    contract: Contract,
    entry_spot: float,
    stop: float,
    entry_slot: int,
    last_slot: int,
    today: dt.date,
    rate: float,
    target_pct: float,
    replays: int = 10_000,
    seed: int = 19,
    window: int = 2,
) -> OptionOdds:
    rng = np.random.default_rng(seed)
    exp_date = dt.datetime.strptime(contract.expiration, "%Y-%m-%d").date()
    call = contract.option_type == "call"
    cost = contract.ask
    goal = cost * (1.0 + target_pct / 100.0)
    n_sessions = pool.shape[1]

    spot = np.full(replays, float(entry_spot))
    outcome = np.zeros(replays, dtype=np.int8)  # 0 open, 1 target, -1 stop
    exit_value = np.zeros(replays)

    for i in range(entry_slot + 1, last_slot + 1):
        slots = np.clip(i + rng.integers(-window, window + 1, size=replays), 1, SESSION_BARS - 1)
        picks = rng.integers(0, n_sessions, size=replays)
        ratios = pool[slots, picks]  # (replays, 4)
        hi, lo, cl = spot * ratios[:, 1], spot * ratios[:, 2], spot * ratios[:, 3]
        t_bar = years_until(exp_date, bar_close_time(today, i))

        open_ = outcome == 0
        stop_hit = open_ & ((lo <= stop) if call else (hi >= stop))
        exit_value[stop_hit] = option_exit_value(np.full(stop_hit.sum(), stop), contract, t_bar, rate)
        outcome[stop_hit] = -1

        open_ = outcome == 0
        best = hi if call else lo
        value_best = option_exit_value(best[open_], contract, t_bar, rate)
        hit = np.zeros(replays, dtype=bool)
        hit[open_] = value_best >= goal
        exit_value[hit] = goal
        outcome[hit] = 1
        spot = cl

    still_open = outcome == 0
    t_last = years_until(exp_date, bar_close_time(today, last_slot))
    exit_value[still_open] = option_exit_value(spot[still_open], contract, t_last, rate)

    returns = (exit_value / cost - 1.0) * 100.0
    srt = np.sort(returns)
    pick = lambda q: float(srt[min(len(srt) - 1, int(q * len(srt)))])
    t_now = years_until(exp_date, bar_close_time(today, entry_slot))
    at_stop = float(option_exit_value(np.array([stop]), contract, t_now, rate)[0])
    return OptionOdds(
        replays=replays,
        seed=seed,
        bars_left=last_slot - entry_slot,
        p_target=float((outcome == 1).mean()),
        p_stop=float((outcome == -1).mean()),
        p_neither=float(still_open.mean()),
        p_green=float((returns > 0).mean()),
        avg_return_pct=float(returns.mean()),
        median_return_pct=pick(0.5),
        p5_return_pct=pick(0.05),
        p95_return_pct=pick(0.95),
        return_at_stop_pct=(at_stop / cost - 1.0) * 100.0,
    )


def spot_needed_now(contract: Contract, entry_spot: float, today: dt.date, entry_slot: int, rate: float, target_pct: float) -> float | None:
    """The underlying price at which the option bid would be at the target
    right now (more later in the day, as time value decays)."""
    exp_date = dt.datetime.strptime(contract.expiration, "%Y-%m-%d").date()
    t = years_until(exp_date, bar_close_time(today, entry_slot))
    goal = contract.ask * (1.0 + target_pct / 100.0)
    call = contract.option_type == "call"
    lo, hi = (entry_spot, entry_spot * 1.5) if call else (entry_spot * 0.5, entry_spot)
    value = lambda s: float(option_exit_value(np.array([s]), contract, t, rate)[0])
    if value(hi if call else lo) < goal:
        return None
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if (value(mid) >= goal) == call:
            hi = mid
        else:
            lo = mid
    return round(0.5 * (lo + hi), 2)


def history_replay(
    past: list,
    contract: Contract,
    or_bars: int,
    last_entry_slot: int,
    last_slot: int,
    rate: float,
    target_pct: float,
) -> RuleHistory:
    """The same rule on every past full session, either direction: the first
    close beyond the opening range is the entry, at-the-money option, today's
    implied volatility and spread (as a share of the price) and today's days
    to expiry. What the stock really did; the option side is an estimate,
    since past implied volatility isn't in free data."""
    spread_frac = (contract.ask - contract.bid) / contract.mid
    results = []
    for day, d in past:
        oh, ol = d[:or_bars, 1].max(), d[:or_bars, 2].min()
        k = next((i for i in range(or_bars, last_entry_slot + 1) if d[i, 3] > oh or d[i, 3] < ol), None)
        if k is None:
            continue
        call = d[k, 3] > oh
        option_type = "call" if call else "put"
        entry = float(d[k, 3])
        stop = cents_away((oh + ol) / 2.0, call)
        expiry = day + dt.timedelta(days=contract.dte)
        t0 = years_until(expiry, bar_close_time(day, k))
        mid0 = float(bs_price_vec(np.array([entry]), entry, t0, rate, contract.iv, option_type)[0])
        half = mid0 * spread_frac / 2.0
        cost = mid0 + half
        goal = cost * (1.0 + target_pct / 100.0)
        sim = Contract(option_type, expiry.isoformat(), contract.dte, entry, mid0 - half, mid0 + half, None, contract.iv, 0.5)

        result, value = None, None
        for i in range(k + 1, last_slot + 1):
            t = years_until(expiry, bar_close_time(day, i))
            if (d[i, 2] <= stop) if call else (d[i, 1] >= stop):
                result, value = "stop", float(option_exit_value(np.array([stop]), sim, t, rate)[0])
                break
            best = d[i, 1] if call else d[i, 2]
            if float(option_exit_value(np.array([best]), sim, t, rate)[0]) >= goal:
                result, value = "target", goal
                break
        if result is None:
            t = years_until(expiry, bar_close_time(day, last_slot))
            result, value = "neither", float(option_exit_value(np.array([d[last_slot, 3]]), sim, t, rate)[0])
        results.append((result, (value / cost - 1.0) * 100.0))

    returns = [r for _, r in results]
    return RuleHistory(
        sessions=len(past),
        triggered=len(results),
        target=sum(1 for o, _ in results if o == "target"),
        stop=sum(1 for o, _ in results if o == "stop"),
        neither=sum(1 for o, _ in results if o == "neither"),
        avg_return_pct=float(np.mean(returns)) if returns else None,
    )
