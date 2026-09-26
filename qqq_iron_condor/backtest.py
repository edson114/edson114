"""Historical backtest of the delta-targeted iron condor rule.

Data constraint: there is no free source of historical QQQ option-chain
data (strike-level bid/ask/IV by date -- that requires a paid feed like
CBOE DataShop, ORATS historical, or Polygon.io). What's freely available
is historical *underlying* prices and VIX. This backtest bridges that gap
by simulating the option chain at each entry/exit with Black-Scholes,
using VIX as a flat (no-skew, no term-structure) implied-vol proxy for
every strike -- the same approach the app's own offline `--self-test`
uses for synthetic data. Critically, it feeds those simulated chains
through the *unmodified* `strategy.build_iron_condor`, so the rule being
backtested is exactly the rule the live scanner runs, not a separate
re-implementation that could silently drift from it.

What this can and can't tell you:
  - It CAN show the mechanical edge (win rate, expectancy, drawdown) of
    "sell the 0.16-delta condor, hold to expiration" against real
    historical QQQ/VIX moves, including real historical vol regimes and
    real gap days.
  - It CANNOT capture bid/ask slippage, commissions, early assignment,
    intraday IV skew/smile, or the actual liquidity of any specific
    strike -- real fills will be worse than these simulated mid-price
    settlements.
  - The "scheduled macro event" hard gate can only be backtested for
    dates present in `Config.macro_event_dates`, which is currently only
    populated for 2026 -- it will not fire for earlier history. The gap
    and VIX-spike hard gates ARE backtested for real, since both are
    computed from real historical daily bars.
  - Trades are non-overlapping per expiration label (a new trade only
    opens after the prior one of that label has settled), so the
    reported drawdown reflects one strategy "slot" run sequentially, not
    a portfolio of simultaneously-open positions.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from .config import Config, ExpirationTarget
from .data import OptionChain
from .options_math import bs_price, time_to_expiration_years
from .strategy import IronCondorTrade, build_iron_condor

MIN_HISTORY_DAYS = 252  # need a trailing year for HV/VIX-percentile inputs before the first entry


def simulate_chain(spot: float, iv: float, dte: int, rate: float, expiration: str,
                    strike_step: float = 1.0, strike_range: Optional[float] = None) -> OptionChain:
    """Build a synthetic option chain by pricing every strike with flat
    Black-Scholes IV. No delta column is included, so strategy.py derives
    delta the same way it would from a real chain with a flat vol skew.

    strike_range defaults to a multiple of the IV-implied expected move so
    high-vol regimes (e.g. a historical VIX spike) still have far enough
    OTM strikes listed for the delta target and wing to resolve, rather
    than silently clipping against a fixed-width chain.
    """
    t_years = time_to_expiration_years(dte)
    if strike_range is None:
        expected_move = spot * iv * (t_years ** 0.5) if t_years > 0 else 0.0
        strike_range = max(60.0, 4.0 * expected_move)
    lo = round((spot - strike_range) / strike_step) * strike_step
    hi = round((spot + strike_range) / strike_step) * strike_step
    strikes = np.arange(lo, hi + strike_step, strike_step)

    call_fair = np.array([bs_price(spot, k, t_years, rate, iv, "call") for k in strikes])
    put_fair = np.array([bs_price(spot, k, t_years, rate, iv, "put") for k in strikes])
    half_spread = 0.02  # a tight, idealized spread -- real fills are worse than this

    calls = pd.DataFrame({
        "strike": strikes,
        "bid": np.maximum(0.01, call_fair - half_spread),
        "ask": call_fair + half_spread,
        "lastPrice": call_fair,
        "impliedVolatility": np.full(len(strikes), iv),
    })
    puts = pd.DataFrame({
        "strike": strikes,
        "bid": np.maximum(0.01, put_fair - half_spread),
        "ask": put_fair + half_spread,
        "lastPrice": put_fair,
        "impliedVolatility": np.full(len(strikes), iv),
    })
    return OptionChain(expiration=expiration, dte=dte, calls=calls, puts=puts)


def _settle_pnl(trade: IronCondorTrade, exit_spot: float) -> float:
    """Exact expiration P&L (credit received minus intrinsic loss on each
    spread, each capped at its own wing width) -- independent of the
    `max_loss` summary field, which uses max(call_width, put_width) as a
    single risk figure and can slightly understate an asymmetric-width loss."""
    short_call = trade.legs["short_call"].strike
    long_call = trade.legs["long_call"].strike
    short_put = trade.legs["short_put"].strike
    long_put = trade.legs["long_put"].strike

    call_loss = min(max(exit_spot - short_call, 0.0), long_call - short_call)
    put_loss = min(max(short_put - exit_spot, 0.0), short_put - long_put)
    return trade.credit - call_loss - put_loss


@dataclass
class BacktestTrade:
    label: str
    entry_date: dt.date
    exit_date: dt.date
    entry_spot: float
    exit_spot: float
    credit: float
    max_loss: float
    pnl: float
    short_call_strike: float
    short_put_strike: float
    vix_at_entry: float
    iv_regime: str


@dataclass
class RegimeStats:
    regime: str
    num_trades: int
    win_rate_pct: float
    expectancy: float
    total_pnl: float


@dataclass
class BacktestSummary:
    label: str
    num_trades: int
    win_rate_pct: float
    avg_win: float
    avg_loss: float
    expectancy: float
    total_pnl: float
    profit_factor: float
    max_drawdown: float
    by_regime: list = field(default_factory=list)  # list[RegimeStats]


def run_backtest(
    price_history: pd.DataFrame,
    vix_history: pd.DataFrame,
    target: ExpirationTarget,
    cfg: Config,
    apply_gates: bool = True,
) -> list[BacktestTrade]:
    dates = price_history.index
    close = price_history["Close"]
    open_ = price_history["Open"]
    hv20 = ind.historical_volatility(close, 20)

    vix_close = vix_history["Close"].reindex(dates, method="ffill").ffill()

    rep_dte = 0 if (target.min_dte == 0 and target.max_dte == 0) else round((target.min_dte + target.max_dte) / 2.0)
    target_delta = target.short_delta_target or cfg.short_delta_target
    wing_width = target.wing_width or cfg.wing_width

    trades: list[BacktestTrade] = []
    i = MIN_HISTORY_DAYS
    n = len(dates)
    while i < n:
        entry_date = dates[i]

        if apply_gates:
            event_label = cfg.macro_event_dates.get(entry_date.date().isoformat())
            gap_pct = (float(close.iloc[i]) / float(close.iloc[i - 1]) - 1.0) * 100.0
            vix_level = float(vix_close.iloc[i])
            if event_label or abs(gap_pct) >= cfg.gap_threshold_pct or vix_level >= cfg.vix_spike_threshold:
                i += 1
                continue

        spot_entry = float(open_.iloc[i]) if rep_dte == 0 else float(close.iloc[i])
        iv = float(vix_close.iloc[i]) / 100.0
        hv_ref = float(hv20.iloc[i]) if pd.notna(hv20.iloc[i]) else None

        if rep_dte == 0:
            j = i
        else:
            exit_target_date = entry_date + pd.Timedelta(days=rep_dte)
            later = dates[dates >= exit_target_date]
            if later.empty:
                break  # not enough remaining history to settle this trade
            j = dates.get_loc(later[0])

        exit_spot = float(close.iloc[j])
        expiration = dates[j].date().isoformat()
        chain = simulate_chain(spot_entry, iv, rep_dte, cfg.risk_free_rate, expiration)

        trade = build_iron_condor(
            target.label, chain, spot_entry, cfg.risk_free_rate, target_delta, wing_width, reference_hv=hv_ref,
        )
        if trade is None:
            i += 1
            continue

        pnl = _settle_pnl(trade, exit_spot)
        vix_pctile = ind.percentile_rank(vix_close.iloc[max(0, i - MIN_HISTORY_DAYS):i + 1], float(vix_close.iloc[i]))
        regime = "High IV" if vix_pctile >= 70 else ("Low IV" if vix_pctile <= 20 else "Normal IV")

        trades.append(BacktestTrade(
            label=target.label,
            entry_date=entry_date.date(),
            exit_date=dates[j].date(),
            entry_spot=spot_entry,
            exit_spot=exit_spot,
            credit=trade.credit,
            max_loss=trade.max_loss,
            pnl=round(pnl, 4),
            short_call_strike=trade.legs["short_call"].strike,
            short_put_strike=trade.legs["short_put"].strike,
            vix_at_entry=float(vix_close.iloc[i]),
            iv_regime=regime,
        ))

        i = j + 1  # non-overlapping: next entry only after this trade settles

    return trades


def _regime_stats(regime: str, trades: list[BacktestTrade]) -> RegimeStats:
    if not trades:
        return RegimeStats(regime=regime, num_trades=0, win_rate_pct=float("nan"), expectancy=float("nan"), total_pnl=0.0)
    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    return RegimeStats(
        regime=regime,
        num_trades=len(trades),
        win_rate_pct=len(wins) / len(trades) * 100.0,
        expectancy=sum(pnls) / len(trades),
        total_pnl=sum(pnls),
    )


def summarize(label: str, trades: list[BacktestTrade]) -> BacktestSummary:
    if not trades:
        return BacktestSummary(
            label=label, num_trades=0, win_rate_pct=float("nan"), avg_win=float("nan"),
            avg_loss=float("nan"), expectancy=float("nan"), total_pnl=0.0, profit_factor=float("nan"),
            max_drawdown=0.0, by_regime=[],
        )

    pnls = [t.pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    equity = np.cumsum(pnls)
    running_peak = np.maximum.accumulate(equity)
    drawdowns = running_peak - equity
    max_drawdown = float(drawdowns.max()) if len(drawdowns) else 0.0

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))

    by_regime = [
        _regime_stats(regime, [t for t in trades if t.iv_regime == regime])
        for regime in ("Low IV", "Normal IV", "High IV")
    ]
    by_regime = [r for r in by_regime if r.num_trades > 0]

    return BacktestSummary(
        label=label,
        num_trades=len(trades),
        win_rate_pct=len(wins) / len(trades) * 100.0,
        avg_win=(sum(wins) / len(wins)) if wins else float("nan"),
        avg_loss=(sum(losses) / len(losses)) if losses else float("nan"),
        expectancy=sum(pnls) / len(trades),
        total_pnl=sum(pnls),
        profit_factor=(gross_win / gross_loss) if gross_loss > 0 else float("nan"),
        max_drawdown=max_drawdown,
        by_regime=by_regime,
    )


def _fmt(x: float, decimals: int = 2) -> str:
    if x is None or x != x:
        return "n/a"
    return f"{x:.{decimals}f}"


def render_backtest_report(symbol: str, years: float, summaries: list[BacktestSummary], apply_gates: bool) -> str:
    generated_at = dt.datetime.now()
    parts = [
        f"# {symbol} Iron Condor Backtest -- {generated_at.strftime('%Y-%m-%d %H:%M')}",
        "",
        f"> Simulated over ~{years:.1f} years of history. Per-contract P&L (multiply by 100 for "
        "a standard contract). **This is a Black-Scholes simulation driven by real historical "
        "underlying/VIX data, not a replay of real historical option quotes** -- see the module "
        "docstring in `qqq_iron_condor/backtest.py` for what it can and can't capture. Not "
        "financial advice.",
        "",
        f"_Hard skip-day gates {'applied' if apply_gates else 'NOT applied'} (gap and VIX-spike "
        "gates are backtested for real; the scheduled-macro-event gate only fires for dates "
        "present in `Config.macro_event_dates`, currently populated for 2026 only)._",
        "",
    ]

    for s in summaries:
        parts.append(f"## {s.label}")
        parts.append("")
        if s.num_trades == 0:
            parts.append("_No trades were generated for this expiration window over the backtest period._")
            parts.append("")
            continue
        parts.append(f"- **Trades:** {s.num_trades}")
        parts.append(f"- **Win rate:** {_fmt(s.win_rate_pct, 1)}%")
        parts.append(f"- **Avg win / avg loss:** ${_fmt(s.avg_win)} / ${_fmt(s.avg_loss)}")
        parts.append(f"- **Expectancy (avg P&L per trade):** ${_fmt(s.expectancy)}")
        parts.append(f"- **Total P&L (sum, per contract):** ${_fmt(s.total_pnl)}")
        parts.append(f"- **Profit factor (gross win / gross loss):** {_fmt(s.profit_factor, 2)}")
        parts.append(f"- **Max drawdown (equity curve, per contract):** ${_fmt(s.max_drawdown)}")
        parts.append("")
        if s.by_regime:
            parts.append("| IV Regime (VIX percentile at entry) | Trades | Win rate | Expectancy | Total P&L |")
            parts.append("|---|---|---|---|---|")
            for r in s.by_regime:
                parts.append(f"| {r.regime} | {r.num_trades} | {_fmt(r.win_rate_pct, 1)}% | ${_fmt(r.expectancy)} | ${_fmt(r.total_pnl)} |")
            parts.append("")

    return "\n".join(parts)


def main(argv=None) -> int:
    import argparse
    import sys
    from pathlib import Path

    from . import data

    parser = argparse.ArgumentParser(description="Backtest the QQQ iron condor delta-targeting rule")
    parser.add_argument("--years", type=float, default=3.0, help="Years of history to backtest (default 3)")
    parser.add_argument("--labels", nargs="*", default=None, help="Subset of expiration labels to run (default: all configured)")
    parser.add_argument("--no-gates", action="store_true", help="Disable the hard skip-day gates (gap/VIX-spike/macro)")
    parser.add_argument("--output-dir", default="reports/backtests", help="Directory to save the report")
    args = parser.parse_args(argv)

    cfg = Config()
    period = f"{max(1, round(args.years))}y"

    print(f"Fetching {period} of {cfg.symbol}/VIX history from yfinance for the backtest...", file=sys.stderr)
    price_history = data.get_price_history(cfg.symbol, period)
    vix_history = data.get_vix_history(period)

    targets = [t for t in cfg.expiration_targets if not args.labels or t.label in args.labels]

    summaries = []
    for target in targets:
        trades = run_backtest(price_history, vix_history, target, cfg, apply_gates=not args.no_gates)
        summaries.append(summarize(target.label, trades))

    report_md = render_backtest_report(cfg.symbol, args.years, summaries, apply_gates=not args.no_gates)
    print(report_md)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{dt.date.today().isoformat()}.md"
    out_path.write_text(report_md)
    print(f"\nSaved backtest report to {out_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
