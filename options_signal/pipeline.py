"""One run: every symbol through setup -> contract -> replays -> history ->
gates -> sizing, then the ranking and the pick."""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from qqq_iron_condor.config import Config as RepoConfig

from .bars import bar_close_time, clock_to_slot_close, split_sessions
from .config import Config
from .market import Market
from .options import Contract, pick_expiration, select_contract, years_until
from .rules import Sizing, day_gates, earnings_gate, size_contracts
from .setup import OrbSetup, find_orb
from .simulate import (
    OptionOdds,
    RuleHistory,
    build_pool,
    history_replay,
    option_exit_value,
    simulate_option,
    spot_needed_now,
)


@dataclass
class Candidate:
    symbol: str
    setup: Optional[OrbSetup] = None
    contract: Optional[Contract] = None
    odds: Optional[OptionOdds] = None
    history: Optional[RuleHistory] = None
    spot_needed: Optional[float] = None
    sizing: Optional[Sizing] = None
    skip_reason: Optional[str] = None
    flags: list = field(default_factory=list)
    sessions: int = 0
    late: bool = False
    data_error: bool = False  # skipped because the feed failed, not because of the rules


@dataclass
class RunResult:
    now: dt.datetime
    status: str  # ok | gated | closed | early | late
    message: str
    gates: list
    vix: Optional[float]
    candidates: list
    pick: Optional[Candidate]
    cfg: Config
    trader: dict


def _hhmm(clock: str) -> dt.time:
    h, m = (int(x) for x in clock.split(":"))
    return dt.time(h, m)


def evaluate_symbol(symbol: str, market: Market, now: dt.datetime, cfg: Config, trader: dict) -> Candidate:
    cand = Candidate(symbol)
    today = now.date()
    try:
        sb = split_sessions(market.bars(symbol, cfg.history_period), today, now)
    except Exception as exc:  # data gaps are per-symbol, never fatal to the run
        cand.skip_reason = f"no bars: {exc}"
        cand.data_error = True
        return cand
    cand.sessions = len(sb.past)
    if len(sb.today) == 0:
        cand.skip_reason = "no bars today"
        return cand
    if len(sb.past) < cfg.min_full_sessions:
        cand.skip_reason = f"only {len(sb.past)} full past sessions (need {cfg.min_full_sessions})"
        return cand

    or_bars = cfg.or_minutes // cfg.bar_minutes
    last_entry_slot = clock_to_slot_close(trader.get("no_entries_after") or cfg.no_entries_after)
    flat_slot = clock_to_slot_close(trader.get("flat_by") or cfg.flat_by)
    setup = find_orb(sb.today, or_bars, last_entry_slot)
    cand.setup = setup
    if setup.status != "triggered":
        return cand

    entry_slot = setup.last_slot
    entry_spot = setup.last_close
    if entry_slot >= flat_slot:
        cand.skip_reason = "no bars left before the flat-by time"
        return cand
    bars_since = entry_slot - setup.trigger_slot
    if bars_since > cfg.late_after_bars:
        cand.late = True
        cand.flags.append(
            f"Late: triggered {bars_since} bars ago at {setup.trigger_price:.2f}; "
            f"evaluated from the latest close, {entry_spot:.2f}."
        )

    try:
        expirations = market.expirations(symbol)
    except Exception as exc:
        cand.skip_reason = f"data feed: {exc}"
        cand.data_error = True
        return cand
    if not expirations:
        cand.skip_reason = "data feed: no option expirations returned"
        cand.data_error = True
        return cand
    expiration = pick_expiration(expirations, today, cfg.min_dte, cfg.max_dte, cfg.preferred_dte)
    if expiration is None:
        cand.skip_reason = f"no expiration {cfg.min_dte}-{cfg.max_dte} days out"
        return cand
    try:
        table = market.chain(symbol, expiration, setup.option_type)
    except Exception as exc:
        cand.skip_reason = f"data feed: {exc}"
        cand.data_error = True
        return cand
    when = bar_close_time(today, entry_slot)
    contract, why = select_contract(
        table, expiration, entry_spot, setup.option_type, when, cfg.risk_free_rate,
        cfg.max_spread_pct, cfg.min_open_interest, cfg.strikes_to_try,
    )
    if contract is None:
        cand.skip_reason = why
        return cand
    cand.contract = contract

    cand.odds = simulate_option(
        build_pool(sb.past), contract, entry_spot, setup.stop, entry_slot, flat_slot, today,
        cfg.risk_free_rate, cfg.profit_target_pct, cfg.replays, cfg.seed, cfg.window,
    )
    cand.history = history_replay(
        sb.past, contract, or_bars, last_entry_slot, flat_slot, cfg.risk_free_rate, cfg.profit_target_pct,
    )
    cand.spot_needed = spot_needed_now(contract, entry_spot, today, entry_slot, cfg.risk_free_rate, cfg.profit_target_pct)

    exp_date = dt.datetime.strptime(expiration, "%Y-%m-%d").date()
    at_stop = float(option_exit_value(np.array([setup.stop]), contract, years_until(exp_date, when), cfg.risk_free_rate)[0])
    cand.sizing = size_contracts(contract.ask, at_stop, trader)

    if symbol in cfg.etfs:
        return cand
    try:
        earnings = market.earnings(symbol)
    except Exception:
        earnings = None
    skip, flags = earnings_gate(earnings, today, expiration)
    cand.flags.extend(flags)
    if skip:
        cand.skip_reason = skip
    return cand


def rank(candidates: list) -> list:
    """Evaluated, not skipped, most likely to reach the target first."""
    ok = [c for c in candidates if c.odds is not None and not c.skip_reason]
    return sorted(ok, key=lambda c: (c.odds.p_target, c.odds.avg_return_pct), reverse=True)


def choose_pick(ranked: list) -> Optional[Candidate]:
    """The most probable target-first, among those whose average replay
    result is positive: a high hit rate with a negative average loses more
    at the stop than it makes at the target."""
    return next((c for c in ranked if c.odds.avg_return_pct > 0), None)


def run(symbols: list, market: Market, now: dt.datetime, cfg: Config, trader: dict) -> RunResult:
    repo = RepoConfig()
    vix = market.vix()
    gates = day_gates(now.date(), repo.macro_event_dates, vix, repo.vix_spike_threshold)

    if now.weekday() >= 5:
        return RunResult(now, "closed", "The market is closed today (weekend).", gates, vix, [], None, cfg, trader)
    if now.time() < bar_close_time(now.date(), cfg.or_minutes // cfg.bar_minutes).time():
        return RunResult(now, "early", "The opening range is still forming; run after 9:50 AM ET.", gates, vix, [], None, cfg, trader)

    candidates = [evaluate_symbol(s, market, now, cfg, trader) for s in symbols]
    # Yahoo's rate limit comes in bursts: the symbols that hit it early in
    # the run usually get through once the rest have been fetched.
    if any(c.data_error for c in candidates) and cfg.data_retry_pause_s >= 0:
        time.sleep(cfg.data_retry_pause_s)
        candidates = [evaluate_symbol(c.symbol, market, now, cfg, trader) if c.data_error else c for c in candidates]
    if all(c.skip_reason and c.skip_reason.startswith("no bars") for c in candidates):
        return RunResult(now, "closed", "No bars today: the market is closed or the data feed is down.",
                         gates, vix, candidates, None, cfg, trader)

    ranked = rank(candidates)
    pick = None if gates else choose_pick(ranked)
    status = "gated" if gates else "ok"
    if now.time() > _hhmm(trader.get("no_entries_after") or cfg.no_entries_after):
        status, pick = "late", None
    message = {
        "gated": "Skip day: " + " ".join(gates),
        "late": "Past your no-new-entries time; shown for reference only.",
        "ok": "" if pick else "No pick: nothing triggered, or nothing had a positive average in the replays.",
    }[status]
    failed = [c.symbol for c in candidates if c.data_error]
    if failed:
        note = (f"Incomplete run: {len(failed)} of {len(candidates)} symbols could not be checked because the "
                f"data feed failed ({', '.join(failed)}). Re-run in a few minutes.")
        if status == "ok" and not pick:
            message = "No pick among the symbols it could check. " + note
        else:
            message = " ".join(x for x in (message, note) if x)
    return RunResult(now, status, message, gates, vix, candidates, pick, cfg, trader)
