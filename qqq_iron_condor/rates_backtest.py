"""Does the 10-year Treasury yield help? Two tests.

1. **Bond-put event study** (QQQ daily closes since 2000). An event is the
   first close where the 10-year (^TNX) is up >= `threshold` bp over 5
   sessions, then a cool-down so one episode counts once. For each event:
   QQQ's return over those same 5 sessions (did stocks correct while
   yields spiked?) and over the next 1 / 5 / 20 sessions (did they bounce
   -- the "bond put"?), against the period's normal 5- and 20-session
   returns. Split into 2000-2016, Trump's first term, Biden, and Trump's
   second term.

2. **Filters on the daily $ target signal.** First-hour momentum + the
   level stop, with entries skipped by the yield read known at the prior
   close (spiking, rising, at a 52-week high...). Run on real QQQ hourly
   prices (shares, no option modeling) and through the options backtest
   (Black-Scholes at a VIX-based IV, +$1,000 target, level stop on an
   hourly close, -$4,000 backstop), each with a half-window split.

3. **The bond put as a trade** (QQQ daily since 2000). After each spike,
   buy at the next session's open and hold 5 / 10 / 20 sessions, on QQQ
   itself and on a 0.80-delta ~60 DTE call. Compared with a random entry
   in the same period: a bootstrap p-value gives the chance that luck alone
   matches the result.

Usage:
    python -m qqq_iron_condor.rates_backtest     # report to reports/backtests/
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import sys
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from .config import Config
from .rates import YieldParams, daily_states, state_on

PERIODS = (
    ("2000-2016", dt.date(2000, 1, 1), dt.date(2017, 1, 19)),
    ("Trump 1st term (2017-2021)", dt.date(2017, 1, 20), dt.date(2021, 1, 19)),
    ("Biden (2021-2025)", dt.date(2021, 1, 20), dt.date(2025, 1, 19)),
    ("Trump 2nd term (2025-)", dt.date(2025, 1, 20), dt.date(2100, 1, 1)),
)


# --- 1. Event study ---------------------------------------------------------

def spike_events(states: pd.DataFrame, threshold_bp: float, cooldown: int = 10) -> list[pd.Timestamp]:
    """First close of each episode with a 5-session rise >= threshold_bp."""
    events, skip_until = [], -1
    hits = (states["change_5d_bp"] >= threshold_bp).to_numpy()
    for k, hit in enumerate(hits):
        if hit and k > skip_until:
            events.append(states.index[k])
            skip_until = k + cooldown
    return events


def event_study(qqq: pd.DataFrame, tnx: pd.DataFrame, threshold_bp: float = 20.0, cooldown: int = 10) -> list[dict]:
    close = qqq["Close"].astype(float)
    close.index = pd.to_datetime(close.index).normalize()
    states = daily_states(tnx)
    states = states[states.index >= close.index[0]]
    pos = {d: k for k, d in enumerate(close.index)}
    c = close.to_numpy()

    def ret(k0, k1):
        return (c[k1] / c[k0] - 1.0) * 100.0 if 0 <= k0 < len(c) and 0 <= k1 < len(c) else np.nan

    rows = []
    for e in spike_events(states, threshold_bp, cooldown):
        k = pos.get(e)
        if k is None:  # a bond-market session with no stock session
            continue
        rows.append(
            {
                "date": e.date(),
                "bp": float(states.loc[e, "change_5d_bp"]),
                "during": ret(k - 5, k),
                "fwd1": ret(k, k + 1),
                "fwd5": ret(k, k + 5),
                "fwd20": ret(k, k + 20),
            }
        )
    ev = pd.DataFrame(rows)
    base5 = pd.Series((c[5:] / c[:-5] - 1.0) * 100.0, index=close.index[5:])
    base20 = pd.Series((c[20:] / c[:-20] - 1.0) * 100.0, index=close.index[20:])
    out = []
    for name, lo, hi in PERIODS:
        e = ev[(ev["date"] >= lo) & (ev["date"] <= hi)] if len(ev) else ev
        in_p = lambda s: s[(s.index.date >= lo) & (s.index.date <= hi)]  # noqa: E731
        b5, b20 = in_p(base5), in_p(base20)
        out.append(
            {
                "period": name,
                "events": len(e),
                "during": float(e["during"].mean()) if len(e) else np.nan,
                "during_down": float((e["during"] < 0).mean()) if len(e) else np.nan,
                "fwd1": float(e["fwd1"].mean()) if len(e) else np.nan,
                "fwd5": float(e["fwd5"].mean()) if len(e) else np.nan,
                "fwd5_up": float((e["fwd5"] > 0).mean()) if len(e) else np.nan,
                "fwd20": float(e["fwd20"].mean()) if len(e) else np.nan,
                "base5": float(b5.mean()) if len(b5) else np.nan,
                "base5_up": float((b5 > 0).mean()) if len(b5) else np.nan,
                "base20": float(b20.mean()) if len(b20) else np.nan,
                "dates": [d.isoformat() for d in e["date"]] if len(e) else [],
            }
        )
    return out


def weekly_correlation(qqq: pd.DataFrame, tnx: pd.DataFrame) -> list[tuple[str, float, int]]:
    """Correlation of weekly 10-year changes (bp) with weekly QQQ returns (%)."""
    q = qqq["Close"].astype(float)
    t = tnx["Close"].astype(float)
    q.index = pd.to_datetime(q.index).normalize()
    t.index = pd.to_datetime(t.index).normalize()
    w = pd.DataFrame({"q": q.resample("W-FRI").last().pct_change() * 100, "t": t.resample("W-FRI").last().diff() * 100}).dropna()
    out = []
    for name, lo, hi in PERIODS:
        x = w[(w.index.date >= lo) & (w.index.date <= hi)]
        out.append((name, float(x["q"].corr(x["t"])) if len(x) > 10 else np.nan, len(x)))
    return out


# --- 2. Filters on the daily $ target signal -------------------------------

def make_filters(states: pd.DataFrame, p: YieldParams = YieldParams()) -> list[tuple[str, Optional[Callable]]]:
    """(name, skip(date, side) -> bool). side: +1 call/long, -1 put/short.
    Every read is the one known at the prior close."""

    def read(d):
        return state_on(states, d)

    def ch5(d):
        r = read(d)
        return float(r["change_5d_bp"]) if r is not None and r["change_5d_bp"] == r["change_5d_bp"] else 0.0

    def ch1(d):
        r = read(d)
        return float(r["change_1d_bp"]) if r is not None and r["change_1d_bp"] == r["change_1d_bp"] else 0.0

    def high(d):
        r = read(d)
        return bool(r is not None and r["at_52w_high"])

    s = p.spike_bp_5d
    return [
        ("No yield filter (baseline)", None),
        (f"Skip all trades when the 10-yr is spiking (5-day rise >= {s:.0f}bp)", lambda d, side: ch5(d) >= s),
        ("...same at >= 15bp", lambda d, side: ch5(d) >= 15),
        ("...same at >= 25bp", lambda d, side: ch5(d) >= 25),
        (f"Skip calls when spiking (>= {s:.0f}bp)", lambda d, side: side > 0 and ch5(d) >= s),
        (f"Skip puts when spiking (>= {s:.0f}bp) -- the 'bond put'", lambda d, side: side < 0 and ch5(d) >= s),
        (f"Skip calls when rising (5-day rise >= {p.trend_bp_5d:.0f}bp)", lambda d, side: side > 0 and ch5(d) >= p.trend_bp_5d),
        ("Skip calls after a +8bp day", lambda d, side: side > 0 and ch1(d) >= 8),
        ("Skip all trades when the 10-yr is at a 52-week high", lambda d, side: high(d)),
    ]


def run_filters(daily, vix, intraday, tnx, cfg: Config) -> tuple[list, list, dt.date, dt.date, dt.date]:
    from .daily_target_backtest import simulate
    from .shares_backtest import simulate_shares, summarize

    states = daily_states(tnx)
    filters = make_filters(states)
    days = sorted(set(intraday.index.date))
    midpoint = days[len(days) // 2]
    opt_cfg = dataclasses.replace(cfg, daily_target_stop_usd=float(cfg.daily_target_backstop_usd))
    dates = [d.date() for d in pd.to_datetime(daily.index)]

    def fh_side(bars):
        move = float(bars["Close"].iloc[-1] - bars["Open"].iloc[0]) if len(bars) else 0.0
        return "call" if move > 0 else ("put" if move < 0 else None)

    share_rows, option_rows = [], []
    for name, skip in filters:
        share_rows.append((name, summarize(simulate_shares(daily, vix, intraday, cfg, skip_day=skip), midpoint)))

        def direction(i, bars, skip=skip):
            side = fh_side(bars)
            if side is None or (skip is not None and skip(dates[i], 1 if side == "call" else -1)):
                return None
            return side

        trades = simulate(daily, vix, opt_cfg, intraday=intraday, direction=direction, entry_bar=1,
                          structure_stop=True, stop_on_close=True, backstop=True)
        option_rows.append((name, _summarize_options(trades, midpoint)))
    return share_rows, option_rows, days[0], midpoint, days[-1]


def _summarize_options(trades, midpoint: dt.date) -> dict:
    pnl = pd.Series([t.pnl_usd for t in trades], dtype=float)
    equity = pnl.cumsum()
    return {
        "trades": len(trades),
        "win_rate": float((pnl > 0).mean()) if len(pnl) else 0.0,
        "total": float(pnl.sum()),
        "worst": float(pnl.min()) if len(pnl) else 0.0,
        "max_dd": float((equity.cummax().clip(lower=0) - equity).max()) if len(equity) else 0.0,
        "first_half": float(sum(t.pnl_usd for t in trades if t.entry_date < midpoint)),
        "second_half": float(sum(t.pnl_usd for t in trades if t.entry_date >= midpoint)),
    }


# --- 3. The bond put as a trade -----------------------------------------------

def _daily_arrays(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["Open", "Close"]].astype(float).copy()
    out.index = pd.to_datetime(out.index).normalize()
    return out


def bond_put_trades(qqq, tnx, vix, threshold_bp: float, hold: int, cfg: Config) -> list[dict]:
    """Buy after each 10-yr spike: enter at the open of the first QQQ session
    after the spike close, exit at the close `hold` sessions later (the entry
    session counts as one). One position at a time (cool-down = `hold`).
    Each trade is priced two ways: QQQ itself, and a 0.80-delta ~60 DTE call
    (Black-Scholes at VIX x the backtest's IV multiple, the actual VIX at
    exit, and the backtest's slippage on both fills)."""
    from .daily_target import strike_for_delta
    from .options_math import bs_price

    q = _daily_arrays(qqq)
    v = vix["Close"].astype(float).copy()
    v.index = pd.to_datetime(v.index).normalize()
    v = v.reindex(q.index).ffill()
    states = daily_states(tnx)
    states = states[states.index >= q.index[0]]
    dates = q.index
    opens, closes, vixes = q["Open"].to_numpy(), q["Close"].to_numpy(), v.to_numpy()
    r, slip, mult, dte = cfg.risk_free_rate, cfg.daily_target_slippage, cfg.daily_target_iv_vix_multiple, 60

    trades = []
    for e in spike_events(states, threshold_bp, cooldown=hold):
        k = int(np.searchsorted(dates.values, e.to_datetime64(), side="right"))
        x = k + hold - 1
        if x >= len(dates) or k < 1 or vixes[k - 1] != vixes[k - 1]:
            continue
        entry, exit_ = opens[k], closes[x]
        iv_in, iv_out = vixes[k - 1] / 100 * mult, vixes[x] / 100 * mult
        t_in = dte / 365.0
        t_out = max(dte - (dates[x] - dates[k]).days, 1) / 365.0
        strike = strike_for_delta(entry, t_in, r, iv_in, "call", cfg.daily_target_delta)
        fill = bs_price(entry, strike, t_in, r, iv_in, "call") + slip
        out = bs_price(exit_, strike, t_out, r, iv_out, "call") - slip
        trades.append(
            {
                "signal": e.date(),
                "entry": dates[k].date(),
                "exit": dates[x].date(),
                "bp": float(states.loc[e, "change_5d_bp"]),
                "qqq": (exit_ / entry - 1.0) * 100.0,
                "option": (out / fill - 1.0) * 100.0,
            }
        )
    return trades


def _in_ranges(d, ranges) -> bool:
    return any(lo <= d <= hi for lo, hi in ranges)


def random_entry_returns(qqq: pd.DataFrame, hold: int, ranges) -> np.ndarray:
    """QQQ % return from every session's open to the close `hold` sessions
    later, for entries inside `ranges` ([(lo, hi), ...]) -- what a random
    entry would have got."""
    q = _daily_arrays(qqq)
    o, c = q["Open"].to_numpy(), q["Close"].to_numpy()
    rets = c[hold - 1:] / o[: len(o) - hold + 1] - 1.0
    d = q.index[: len(o) - hold + 1].date
    mask = np.array([_in_ranges(x, ranges) for x in d], dtype=bool)
    return rets[mask] * 100.0


def _bootstrap_p(sample_mean: float, pool: np.ndarray, n: int, draws: int = 20000, seed: int = 7) -> float:
    """Share of random same-size samples from `pool` whose mean is at least
    `sample_mean`: the chance luck alone matches the result."""
    if n == 0 or len(pool) == 0:
        return float("nan")
    rng = np.random.default_rng(seed)
    means = rng.choice(pool, size=(draws, n), replace=True).mean(axis=1)
    return float((means >= sample_mean).mean())


def bond_put_summary(trades: list[dict], qqq: pd.DataFrame, hold: int, ranges) -> dict:
    t = [x for x in trades if _in_ranges(x["entry"], ranges)]
    pool = random_entry_returns(qqq, hold, ranges)
    q = np.array([x["qqq"] for x in t])
    o = np.array([x["option"] for x in t])
    mean = float(q.mean()) if len(q) else float("nan")
    return {
        "trades": len(t),
        "qqq_avg": mean,
        "qqq_up": float((q > 0).mean()) if len(q) else float("nan"),
        "random_avg": float(pool.mean()) if len(pool) else float("nan"),
        "random_up": float((pool > 0).mean()) if len(pool) else float("nan"),
        "p": _bootstrap_p(mean, pool, len(q)) if len(q) else float("nan"),
        "opt_avg": float(o.mean()) if len(o) else float("nan"),
        "opt_win": float((o > 0).mean()) if len(o) else float("nan"),
        "opt_worst": float(o.min()) if len(o) else float("nan"),
    }


GRID_THRESHOLDS = (15.0, 20.0, 25.0, 30.0)
GRID_HOLDS = (5, 10, 20)


def run_bond_put(qqq, tnx, vix, cfg: Config) -> dict:
    full = [(dt.date(2000, 1, 1), dt.date(2100, 1, 1))]
    trades = {(th, h): bond_put_trades(qqq, tnx, vix, th, h, cfg) for th in GRID_THRESHOLDS for h in GRID_HOLDS}
    grid = [(th, h, bond_put_summary(trades[(th, h)], qqq, h, full)) for th in GRID_THRESHOLDS for h in GRID_HOLDS]
    periods = [(name, [(lo, hi)]) for name, lo, hi in PERIODS]
    periods.append(("Both Trump terms", [(lo, hi) for name, lo, hi in PERIODS if name.startswith("Trump")]))
    by_period = [
        (th, name, bond_put_summary(trades[(th, 20)], qqq, 20, ranges))
        for th in (20.0, 25.0)
        for name, ranges in periods
    ]
    recent = [x for x in trades[(20.0, 20)] if x["entry"] >= PERIODS[1][1]]
    return {"grid": grid, "by_period": by_period, "recent": recent}


# --- Report -----------------------------------------------------------------

def _money(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def _pct(x: float) -> str:
    return "n/a" if x != x else f"{x:+.2f}%"


def render_report(study, corr, threshold_bp, share_rows, option_rows, first, midpoint, last, latest, bond_put=None) -> str:
    lines = [
        f"# 10-Year Yield Backtest -- {dt.date.today():%Y-%m-%d}",
        "",
        f"Latest: {latest}",
        "",
        "## 1. Bond-put event study (QQQ daily, 2000 onward)",
        "",
        f"Event: the first close where the 10-year yield is up **>= {threshold_bp:.0f}bp over 5 sessions** "
        "(then a 10-session cool-down, so one episode counts once). \"During\" is QQQ over those same 5 "
        "sessions; \"next\" is from the event close. \"Normal\" is QQQ's average over any 5 / 20 sessions in "
        "the same period.",
        "",
        "| Period | Events | QQQ during | Fell during | Next day | Next 5 days | Up after 5 | Normal 5 days (up %) | Next 20 days | Normal 20 days |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in study:
        lines.append(
            f"| {r['period']} | {r['events']} | {_pct(r['during'])} | "
            + (f"{r['during_down']:.0%}" if r["events"] else "n/a")
            + f" | {_pct(r['fwd1'])} | {_pct(r['fwd5'])} | "
            + (f"{r['fwd5_up']:.0%}" if r["events"] else "n/a")
            + f" | {_pct(r['base5'])} ({r['base5_up']:.0%}) | {_pct(r['fwd20'])} | {_pct(r['base20'])} |"
        )
    recent = next((r for r in study if r["period"].startswith("Trump 2")), None)
    if recent and recent["dates"]:
        lines += ["", f"Trump 2nd-term events: {', '.join(recent['dates'])}."]
    lines += [
        "",
        "Weekly correlation of 10-year changes with QQQ returns (negative = stocks fall when yields rise): "
        + ", ".join(f"{n} {c:+.2f}" for n, c, _ in corr)
        + ".",
        "",
        f"## 2. Yield filters on first-hour momentum + level stop ({first} to {last}, first half ends {midpoint})",
        "",
        "Each filter uses the yield read from the prior close, so nothing is known that wasn't known at the "
        "entry. Shares: 800 QQQ shares, $0.03/share costs each way, +$1.25 target. Options: the options "
        "backtest's +$1,000 target, level stop on an hourly close, -$4,000 backstop.",
        "",
        "| Filter | Shares: trades | Win | Total | 1st half | 2nd half | Options: trades | Total | 1st half | 2nd half | Worst |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for (name, s), (_, o) in zip(share_rows, option_rows):
        lines.append(
            f"| {name} | {s['trades']} | {s['win_rate']:.0%} | {_money(s['total'])} | {_money(s['first_half'])} | "
            f"{_money(s['second_half'])} | {o['trades']} | {_money(o['total'])} | {_money(o['first_half'])} | "
            f"{_money(o['second_half'])} | {_money(o['worst'])} |"
        )
    if bond_put is not None:
        lines += _render_bond_put(bond_put)
    lines += [
        "",
        "**Caveats:** few spike episodes per period (especially the last two years), so these averages "
        "carry a lot of noise; the filter test covers ~2 years of hourly data only. Trump's posts "
        "themselves aren't in any free backtestable feed, so the event study tests the yield side of the "
        "idea -- whether stocks fall during spikes and bounce after -- not the posts.",
    ]
    return "\n".join(lines) + "\n"


def _render_bond_put(res: dict) -> list[str]:
    def row(label, s):
        if not s["trades"]:
            return f"| {label} | 0 | | | | | | |"
        return (
            f"| {label} | {s['trades']} | {_pct(s['qqq_avg'])} ({s['qqq_up']:.0%} up) | "
            f"{_pct(s['random_avg'])} ({s['random_up']:.0%} up) | {s['p']:.2f} | {_pct(s['opt_avg'])} | "
            f"{s['opt_win']:.0%} | {_pct(s['opt_worst'])} |"
        )

    head = (
        "| {} | Trades | QQQ after the spike | Random entry | p (luck) | Call: avg | Call: win | Call: worst |",
        "|---|---|---|---|---|---|---|---|",
    )
    lines = [
        "",
        "## 3. The bond put as a trade (QQQ daily, 2000 onward)",
        "",
        "Buy at the open of the session after a spike close, exit at the close N sessions later, one position "
        "at a time. \"Random entry\" is QQQ over the same holding period from every session in the same "
        "years; p is the chance that random entries match the spike entries' average (low = unlikely to be "
        "luck). Calls: 0.80-delta ~60 DTE, Black-Scholes at VIX x the backtest's IV multiple (actual VIX at "
        "exit), $0.05/share slippage per fill; returns are % of the premium paid.",
        "",
        head[0].format("Spike / hold"),
        head[1],
    ]
    for th, h, s in res["grid"]:
        lines.append(row(f">= {th:.0f}bp, {h} sessions", s))
    lines += ["", "By period, 20-session hold:", "", head[0].format("Period"), head[1]]
    for th, name, s in res["by_period"]:
        lines.append(row(f"{name}, >= {th:.0f}bp", s))
    if res["recent"]:
        lines += [
            "",
            "Trades since 2017 (>= 20bp, 20 sessions): "
            + "; ".join(f"{t['entry']} {t['qqq']:+.1f}% (call {t['option']:+.0f}%)" for t in res["recent"])
            + ".",
        ]
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Backtest the 10-year yield's link to QQQ")
    parser.add_argument("--threshold-bp", type=float, default=20.0, help="5-session 10-yr rise that counts as a spike")
    parser.add_argument("--output-dir", default="reports/backtests")
    args = parser.parse_args(argv)

    from .data import get_intraday_history, get_price_history, get_vix_history
    from .rates import ten_year_read

    cfg = Config()
    sym = cfg.daily_target_symbol
    try:
        tnx = get_price_history(cfg.ten_year_symbol, "max")
        qqq_long = get_price_history(sym, "max")
        study = event_study(qqq_long, tnx, args.threshold_bp)
        corr = weekly_correlation(qqq_long, tnx)
        daily = get_price_history(sym, "5y")
        vix = get_vix_history("5y")
        intraday = get_intraday_history(sym, "1h", "730d")
        share_rows, option_rows, first, mid, last = run_filters(daily, vix, intraday, tnx, cfg)
        bond_put = run_bond_put(qqq_long, tnx, get_vix_history("max"), cfg)
    except Exception as exc:
        print(f"Rates backtest failed: {exc}", file=sys.stderr)
        return 1
    report = render_report(study, corr, args.threshold_bp, share_rows, option_rows, first, mid, last,
                           ten_year_read(tnx).detail, bond_put)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"rates-{dt.date.today():%Y-%m-%d}.md"
    out.write_text(report)
    print(report)
    print(f"Saved report to {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
