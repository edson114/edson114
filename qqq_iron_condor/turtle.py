"""Richard Dennis's Turtle Trading system: a portfolio backtest plus a
daily "levels for the next session" report.

The rules follow the original Turtle rules as published by Curtis Faith
("Way of the Turtle", and the freely released "Original Turtle Trading
Rules"), applied to daily ETF bars instead of the futures the Turtles
actually traded:

  - **Volatility (N):** a 20-day Wilder-smoothed average true range.
  - **Unit size:** 1% of account equity risked per 1 N move --
    ``shares = floor(risk_pct * equity / N)`` (for a stock/ETF, one point
    of price is $1 per share, so there's no contract multiplier).
  - **System 1:** enter on a 20-day breakout, exit on a 10-day breakout
    in the opposite direction. A breakout is *skipped* if the last
    System 1 breakout in that market would have been a winner (whether
    or not it was actually taken). A skipped breakout is still entered
    if price then reaches the 55-day "failsafe" breakout.
  - **System 2:** enter on a 55-day breakout (always taken), exit on a
    20-day breakout in the opposite direction.
  - **Stops:** 2 N from the most recent unit's fill; each pyramided unit
    raises (for longs) the stop on *every* unit to 2 N from its own fill.
  - **Pyramiding:** add one unit every 1/2 N of favorable movement from
    the previous unit's fill, to a maximum of 4 units per market.
  - **Portfolio limit:** at most 12 units long and 12 units short across
    all markets (the Turtles' "loosely correlated"/"single market" limits
    are approximated only by the per-market 4 unit cap).

What this deliberately doesn't (or can't) model -- read these before
trusting the numbers:
  - The Turtles traded ~20 liquid futures markets (bonds, currencies,
    metals, energies, grains, softs, stock indices). Their edge came
    from diversification across *uncorrelated* trends; a single equity
    index ETF like QQQ alone is a much weaker test of the idea, which is
    why the default universe is a diversified set of ETFs.
  - The original "reduce notional equity 20% for every 10% drawdown"
    rule isn't modeled; sizing uses mark-to-market equity at the prior
    close instead.
  - Daily bars only: intrabar order is unknown. Fills are at the
    breakout/stop level, or at the open if price gapped through it.
    When a bar spans both a fill and a stop, the stop is assumed hit
    (the conservative resolution), and a bar that breaks out both up
    and down is skipped as ambiguous.
  - A gross leverage cap (``max_leverage``, default 2x = Reg-T margin)
    limits unit sizing, since 4 units of a low-volatility ETF at 1% risk
    each can otherwise exceed what a brokerage account can hold. The
    Turtles had no such constraint trading futures.
  - Short ETF borrow costs and dividends are ignored (prices are
    split/dividend-adjusted closes from yfinance).
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .indicators import atr

# Diversified across equities, bonds, commodities, and currencies -- the
# closest free-data approximation of the Turtles' futures portfolio.
DEFAULT_UNIVERSE = (
    "QQQ",  # Nasdaq-100
    "SPY",  # S&P 500
    "IWM",  # Russell 2000
    "EFA",  # developed ex-US equities
    "EEM",  # emerging-market equities
    "TLT",  # 20+yr Treasuries
    "IEF",  # 7-10yr Treasuries
    "GLD",  # gold
    "SLV",  # silver
    "USO",  # crude oil
    "DBA",  # agriculture
    "UUP",  # US dollar index
    "FXE",  # euro
    "FXY",  # yen
)


@dataclass(frozen=True)
class TurtleConfig:
    system: int = 1
    n_window: int = 20
    entry_window: Optional[int] = None  # None -> 20 for System 1, 55 for System 2
    exit_window: Optional[int] = None  # None -> 10 for System 1, 20 for System 2
    failsafe_window: int = 55  # System 1 only
    risk_pct: float = 0.01
    stop_n: float = 2.0
    add_n: float = 0.5
    max_units_per_market: int = 4
    max_units_per_direction: int = 12
    max_leverage: float = 2.0
    cost_bps: float = 2.0  # per side, applied to fill notional (commission + slippage)
    long_only: bool = False
    use_s1_filter: bool = True
    starting_equity: float = 100_000.0

    @property
    def entry(self) -> int:
        return self.entry_window or (20 if self.system == 1 else 55)

    @property
    def exit(self) -> int:
        return self.exit_window or (10 if self.system == 1 else 20)


@dataclass
class Position:
    symbol: str
    direction: int  # +1 long, -1 short
    entry_date: pd.Timestamp
    n: float  # N at the initial breakout; used for stop/add spacing
    unit_shares: int
    fills: list = field(default_factory=list)  # list[(shares, price)]
    stop: float = 0.0
    next_add: float = 0.0
    failsafe: bool = False
    costs: float = 0.0

    @property
    def units(self) -> int:
        return len(self.fills)

    @property
    def shares(self) -> int:
        return sum(s for s, _ in self.fills)

    @property
    def avg_price(self) -> float:
        return sum(s * p for s, p in self.fills) / self.shares

    def unrealized(self, price: float) -> float:
        return self.direction * sum(s * (price - p) for s, p in self.fills)


@dataclass
class ShadowTrade:
    """A one-unit, un-pyramided System 1 trade tracked for every breakout,
    taken or not, purely to apply the "skip if the last breakout was a
    winner" filter."""

    direction: int
    entry: float
    stop: float


@dataclass
class TurtleTrade:
    symbol: str
    system: int
    direction: str  # "LONG" / "SHORT"
    entry_date: dt.date
    exit_date: dt.date
    units: int
    shares: int
    avg_entry: float
    exit_price: float
    exit_reason: str  # "stop", "exit", "end"
    pnl: float
    failsafe: bool


@dataclass
class TurtleResult:
    config: TurtleConfig
    trades: list  # list[TurtleTrade]
    equity: pd.Series
    open_positions: dict  # symbol -> Position
    last_breakout_winner: dict  # symbol -> Optional[bool] (System 1 filter state)


@dataclass
class TurtleSummary:
    system: int
    num_trades: int
    win_rate_pct: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    total_return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    ending_equity: float
    years: float
    by_symbol: dict = field(default_factory=dict)  # symbol -> (trades, pnl)


def prepare_indicators(df: pd.DataFrame, cfg: TurtleConfig) -> pd.DataFrame:
    """N and the Donchian channels, all shifted so row t only uses bars
    through t-1 -- i.e. the levels that were known before bar t opened."""
    high, low, close = df["High"], df["Low"], df["Close"]
    out = pd.DataFrame(index=df.index)
    out["Open"], out["High"], out["Low"], out["Close"] = df["Open"], high, low, close
    out["N"] = atr(high, low, close, cfg.n_window).shift(1)
    out["entry_hi"] = high.rolling(cfg.entry).max().shift(1)
    out["entry_lo"] = low.rolling(cfg.entry).min().shift(1)
    out["exit_hi"] = high.rolling(cfg.exit).max().shift(1)
    out["exit_lo"] = low.rolling(cfg.exit).min().shift(1)
    out["fs_hi"] = high.rolling(cfg.failsafe_window).max().shift(1)
    out["fs_lo"] = low.rolling(cfg.failsafe_window).min().shift(1)
    return out


def _gap_fill(direction: int, open_: float, level: float) -> float:
    """Fill for a stop-style order at `level` (buy stop above / sell stop
    below): the level itself, or the open if price gapped through it."""
    return max(open_, level) if direction > 0 else min(open_, level)


def _crossed(direction: int, bar: pd.Series, level: float) -> bool:
    """Whether the bar traded through `level` in `direction` (+1 = up)."""
    return bar["High"] > level if direction > 0 else bar["Low"] < level


def _update_shadow(shadow: Optional[ShadowTrade], bar: pd.Series) -> tuple[Optional[ShadowTrade], Optional[bool]]:
    """Advance the System 1 shadow trade one bar. Returns (shadow, result)
    where result is True/False once the shadow trade closes this bar."""
    if shadow is None:
        return None, None
    d = shadow.direction
    exit_level = bar["exit_lo"] if d > 0 else bar["exit_hi"]
    # Price moving against the position hits whichever level is nearer first.
    level = max(shadow.stop, exit_level) if d > 0 else min(shadow.stop, exit_level)
    if _crossed(-d, bar, level):
        fill = _gap_fill(-d, bar["Open"], level)
        return None, bool(d * (fill - shadow.entry) > 0)
    return shadow, None


def run_backtest(prices: dict, cfg: TurtleConfig) -> TurtleResult:
    """Run one Turtle system over a portfolio of daily OHLC DataFrames
    (``{symbol: df}``) sharing a single equity account."""
    ind = {s: prepare_indicators(df, cfg) for s, df in prices.items()}
    dates = sorted(set().union(*(d.index for d in ind.values())))

    cash = cfg.starting_equity  # realized equity
    positions: dict[str, Position] = {}
    shadows: dict[str, Optional[ShadowTrade]] = {s: None for s in ind}
    last_winner: dict[str, Optional[bool]] = {s: None for s in ind}
    last_close: dict[str, float] = {}
    trades: list[TurtleTrade] = []
    equity_curve = {}

    def mtm_equity() -> float:
        return cash + sum(p.unrealized(last_close[s]) for s, p in positions.items())

    def gross_notional() -> float:
        return sum(p.shares * last_close[s] for s, p in positions.items())

    def units_in(direction: int) -> int:
        return sum(p.units for p in positions.values() if p.direction == direction)

    def cost(shares: int, price: float) -> float:
        return shares * price * cfg.cost_bps / 10_000.0

    def close_position(sym: str, date, price: float, reason: str) -> None:
        nonlocal cash
        pos = positions.pop(sym)
        exit_cost = cost(pos.shares, price)
        pnl = pos.unrealized(price) - pos.costs - exit_cost
        cash += pos.unrealized(price) - exit_cost  # entry costs were already debited
        trades.append(TurtleTrade(
            symbol=sym, system=cfg.system, direction="LONG" if pos.direction > 0 else "SHORT",
            entry_date=pos.entry_date.date(), exit_date=pd.Timestamp(date).date(), units=pos.units,
            shares=pos.shares, avg_entry=pos.avg_price, exit_price=price, exit_reason=reason,
            pnl=pnl, failsafe=pos.failsafe,
        ))

    def room_for(shares: int, price: float, equity: float) -> int:
        cap = cfg.max_leverage * equity - gross_notional()
        return max(0, min(shares, int(cap // price))) if price > 0 else 0

    def add_unit(pos: Position, price: float, equity: float) -> bool:
        nonlocal cash
        shares = room_for(pos.unit_shares, price, equity)
        if shares <= 0:
            return False
        c = cost(shares, price)
        cash -= c
        pos.costs += c
        pos.fills.append((shares, price))
        pos.stop = price - pos.direction * cfg.stop_n * pos.n
        pos.next_add = price + pos.direction * cfg.add_n * pos.n
        return True

    for date in dates:
        equity_at_open = mtm_equity() if last_close else cfg.starting_equity
        for sym in sorted(ind):
            frame = ind[sym]
            if date not in frame.index:
                continue
            bar = frame.loc[date]
            ready = not bar[["N", "entry_hi", "entry_lo", "exit_hi", "exit_lo", "fs_hi", "fs_lo"]].isna().any()
            if not ready:
                last_close[sym] = float(bar["Close"])
                continue

            exited_today = False
            pos = positions.get(sym)

            # 1) Stops and channel exits on the open position.
            if pos is not None:
                d = pos.direction
                exit_level = bar["exit_lo"] if d > 0 else bar["exit_hi"]
                level = max(pos.stop, exit_level) if d > 0 else min(pos.stop, exit_level)
                if _crossed(-d, bar, level):
                    reason = "stop" if level == pos.stop else "exit"
                    close_position(sym, date, _gap_fill(-d, bar["Open"], level), reason)
                    exited_today = True
                    pos = None

            # 2) Pyramid adds every 1/2 N of favorable movement.
            if pos is not None:
                while (
                    pos.units < cfg.max_units_per_market
                    and units_in(pos.direction) < cfg.max_units_per_direction
                    and _crossed(pos.direction, bar, pos.next_add)
                ):
                    if not add_unit(pos, _gap_fill(pos.direction, bar["Open"], pos.next_add), equity_at_open):
                        break
                if _crossed(-pos.direction, bar, pos.stop):
                    close_position(sym, date, pos.stop, "stop")
                    exited_today = True
                    pos = None

            # 3) System 1 shadow trade (filter bookkeeping), exits first.
            if cfg.system == 1:
                shadows[sym], result = _update_shadow(shadows[sym], bar)
                if result is not None:
                    last_winner[sym] = result

            # 4) New breakouts.
            up = _crossed(+1, bar, bar["entry_hi"])
            down = _crossed(-1, bar, bar["entry_lo"])
            if up and down:
                last_close[sym] = float(bar["Close"])
                continue  # ambiguous outside bar: unknown which broke first
            direction = +1 if up else (-1 if down else 0)

            take = False
            failsafe = False
            level = None
            if direction:
                level = bar["entry_hi"] if direction > 0 else bar["entry_lo"]
                take = True
                if cfg.system == 1:
                    skipped = cfg.use_s1_filter and last_winner[sym] is True
                    if shadows[sym] is None:
                        fill = _gap_fill(direction, bar["Open"], level)
                        shadows[sym] = ShadowTrade(direction, fill, fill - direction * cfg.stop_n * bar["N"])
                    take = not skipped
            if cfg.system == 1 and not take:
                fs_up = _crossed(+1, bar, bar["fs_hi"])
                fs_down = _crossed(-1, bar, bar["fs_lo"])
                if fs_up != fs_down:
                    direction = +1 if fs_up else -1
                    level = bar["fs_hi"] if fs_up else bar["fs_lo"]
                    take = failsafe = True

            if (
                take
                and pos is None
                and not exited_today
                and not (cfg.long_only and direction < 0)
                and units_in(direction) < cfg.max_units_per_direction
            ):
                n = float(bar["N"])
                unit_shares = int(cfg.risk_pct * equity_at_open // n) if n > 0 else 0
                if unit_shares > 0:
                    new = Position(sym, direction, date, n, unit_shares, failsafe=failsafe)
                    positions[sym] = new
                    if not add_unit(new, _gap_fill(direction, bar["Open"], level), equity_at_open):
                        del positions[sym]
                    else:
                        while (
                            new.units < cfg.max_units_per_market
                            and units_in(direction) < cfg.max_units_per_direction
                            and _crossed(direction, bar, new.next_add)
                        ):
                            if not add_unit(new, new.next_add, equity_at_open):
                                break
                        if _crossed(-direction, bar, new.stop):
                            close_position(sym, date, new.stop, "stop")

            last_close[sym] = float(bar["Close"])

        if last_close:
            equity_curve[date] = mtm_equity()

    return TurtleResult(
        config=cfg,
        trades=trades,
        equity=pd.Series(equity_curve, dtype=float),
        open_positions=positions,
        last_breakout_winner=last_winner,
    )


def summarize(result: TurtleResult) -> TurtleSummary:
    cfg = result.config
    trades = result.trades
    eq = result.equity
    pnls = np.array([t.pnl for t in trades], dtype=float)
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    ending = float(eq.iloc[-1]) if len(eq) else cfg.starting_equity
    years = (eq.index[-1] - eq.index[0]).days / 365.25 if len(eq) > 1 else 0.0
    cagr = ((ending / cfg.starting_equity) ** (1 / years) - 1) * 100 if years > 0 and ending > 0 else float("nan")
    dd = (eq / eq.cummax() - 1).min() * 100 if len(eq) else 0.0
    by_symbol: dict = {}
    for t in trades:
        n, p = by_symbol.get(t.symbol, (0, 0.0))
        by_symbol[t.symbol] = (n + 1, p + t.pnl)
    return TurtleSummary(
        system=cfg.system,
        num_trades=len(trades),
        win_rate_pct=len(wins) / len(pnls) * 100 if len(pnls) else float("nan"),
        avg_win=float(wins.mean()) if len(wins) else float("nan"),
        avg_loss=float(losses.mean()) if len(losses) else float("nan"),
        profit_factor=float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf"),
        total_return_pct=(ending / cfg.starting_equity - 1) * 100,
        cagr_pct=cagr,
        max_drawdown_pct=float(dd),
        ending_equity=ending,
        years=years,
        by_symbol=by_symbol,
    )


@dataclass
class NextSessionLevels:
    """Levels in force for the *next* session, computed from bars through
    the latest close (the same channel math as the backtest, unshifted)."""

    symbol: str
    close: float
    n: float
    unit_shares: int
    entry_hi: float
    entry_lo: float
    exit_hi: float
    exit_lo: float
    fs_hi: float
    fs_lo: float


def next_session_levels(symbol: str, df: pd.DataFrame, cfg: TurtleConfig, equity: float) -> NextSessionLevels:
    n = float(atr(df["High"], df["Low"], df["Close"], cfg.n_window).iloc[-1])
    tail = lambda w: df.iloc[-w:]  # noqa: E731
    return NextSessionLevels(
        symbol=symbol,
        close=float(df["Close"].iloc[-1]),
        n=n,
        unit_shares=int(cfg.risk_pct * equity // n) if n > 0 else 0,
        entry_hi=float(tail(cfg.entry)["High"].max()),
        entry_lo=float(tail(cfg.entry)["Low"].min()),
        exit_hi=float(tail(cfg.exit)["High"].max()),
        exit_lo=float(tail(cfg.exit)["Low"].min()),
        fs_hi=float(tail(cfg.failsafe_window)["High"].max()),
        fs_lo=float(tail(cfg.failsafe_window)["Low"].min()),
    )


def _fmt(x: float, digits: int = 2) -> str:
    if x is None or math.isnan(x):
        return "n/a"
    if math.isinf(x):
        return "∞"
    return f"{x:,.{digits}f}"


def render_levels_section(prices: dict, result: TurtleResult, account_equity: float) -> list[str]:
    cfg = result.config
    parts = [f"### System {cfg.system} ({cfg.entry}-day entry / {cfg.exit}-day exit) -- next session", ""]
    parts.append(
        "| Symbol | Close | N | Unit (shares) | Long entry | Short entry | Mechanical position | Stop | Next add | Channel exit |"
    )
    parts.append("|---|---|---|---|---|---|---|---|---|---|")
    for sym in sorted(prices):
        lv = next_session_levels(sym, prices[sym], cfg, account_equity)
        pos = result.open_positions.get(sym)
        long_entry, short_entry = _fmt(lv.entry_hi), _fmt(lv.entry_lo)
        if cfg.system == 1 and cfg.use_s1_filter and result.last_breakout_winner.get(sym) is True:
            long_entry = f"skip; failsafe {_fmt(lv.fs_hi)}"
            short_entry = f"skip; failsafe {_fmt(lv.fs_lo)}"
        if cfg.long_only:
            short_entry = "--"
        if pos is None:
            state, stop, add, chan = "flat", "--", "--", "--"
        else:
            side = "LONG" if pos.direction > 0 else "SHORT"
            state = f"{side} {pos.units}u ({pos.shares} sh @ {_fmt(pos.avg_price)})"
            stop = _fmt(pos.stop)
            add = _fmt(pos.next_add) if pos.units < cfg.max_units_per_market else "max units"
            chan = _fmt(lv.exit_lo if pos.direction > 0 else lv.exit_hi)
            long_entry = short_entry = "in position"
        parts.append(
            f"| {sym} | {_fmt(lv.close)} | {_fmt(lv.n)} | {lv.unit_shares:,} | {long_entry} | {short_entry} "
            f"| {state} | {stop} | {add} | {chan} |"
        )
    parts.append("")
    return parts


def render_summary_section(summary: TurtleSummary, cfg: TurtleConfig) -> list[str]:
    parts = [f"### System {summary.system} backtest ({_fmt(summary.years, 1)} years)", ""]
    if summary.num_trades == 0:
        return parts + ["_No trades were generated._", ""]
    parts += [
        f"- **Trades:** {summary.num_trades}",
        f"- **Win rate:** {_fmt(summary.win_rate_pct, 1)}% (trend following is expected to lose on most trades)",
        f"- **Avg win / avg loss:** ${_fmt(summary.avg_win, 0)} / ${_fmt(summary.avg_loss, 0)}",
        f"- **Profit factor:** {_fmt(summary.profit_factor)}",
        f"- **Total return:** {_fmt(summary.total_return_pct, 1)}% "
        f"(${_fmt(cfg.starting_equity, 0)} -> ${_fmt(summary.ending_equity, 0)})",
        f"- **CAGR:** {_fmt(summary.cagr_pct, 1)}%",
        f"- **Max drawdown (mark-to-market):** {_fmt(summary.max_drawdown_pct, 1)}%",
        "",
        "| Symbol | Trades | Net P&L |",
        "|---|---|---|",
    ]
    for sym, (n, pnl) in sorted(summary.by_symbol.items(), key=lambda kv: -kv[1][1]):
        parts.append(f"| {sym} | {n} | ${_fmt(pnl, 0)} |")
    parts.append("")
    return parts


def render_report(prices: dict, results: list, account_equity: float, as_of: Optional[dt.date] = None) -> str:
    as_of = as_of or max(df.index[-1] for df in prices.values()).date()
    cfg = results[0].config
    parts = [
        f"# Turtle Trading Report -- {as_of.isoformat()}",
        "",
        "> Richard Dennis's Turtle rules applied mechanically to daily ETF bars. "
        "**Not financial advice** -- a decision-support tool that never places orders. "
        "Trend following historically wins on a minority of trades and depends on a few large "
        "winners; expect long flat or losing stretches.",
        "",
        f"Universe: {', '.join(sorted(prices))}  ",
        f"Risk per unit: {cfg.risk_pct * 100:g}% of equity per 1 N; stop {cfg.stop_n:g} N; "
        f"add every {cfg.add_n:g} N up to {cfg.max_units_per_market} units; "
        f"max {cfg.max_units_per_direction} units per direction; gross leverage cap {cfg.max_leverage:g}x; "
        f"costs {cfg.cost_bps:g} bps/side{'; long only' if cfg.long_only else ''}.",
        "",
        "## Levels for the next session",
        "",
        f"Unit sizes are for a ${_fmt(account_equity, 0)} account. Entries are buy-stop / sell-stop "
        "orders at the channel levels; the 'mechanical position' is what the system would hold "
        "right now had it been followed exactly over the backtest window -- your real position "
        "may differ.",
        "",
    ]
    for r in results:
        parts += render_levels_section(prices, r, account_equity)
    parts += ["## Backtest", ""]
    for r in results:
        parts += render_summary_section(summarize(r), r.config)
    parts.append(
        "_Daily-bar simulation: fills at the breakout/stop level or the open on a gap, conservative "
        "same-bar stop resolution, no borrow costs or dividends. See `qqq_iron_condor/turtle.py` "
        "for exactly what is and isn't modeled._"
    )
    parts.append("")
    return "\n".join(parts)


def export_trades_csv(trades: list, path: Path) -> None:
    rows = [t.__dict__ for t in trades]
    pd.DataFrame(rows).to_csv(path, index=False)


def synthetic_prices(symbols=("AAA", "BBB", "CCC"), days: int = 900, seed: int = 7) -> dict:
    """Trending random walks with regime shifts, for the offline self-test."""
    rng = np.random.default_rng(seed)
    out = {}
    dates = pd.bdate_range(end=dt.date.today(), periods=days)
    for i, sym in enumerate(symbols):
        drift = np.repeat(rng.normal(0, 0.002, days // 100 + 1), 100)[:days]
        rets = drift + rng.normal(0, 0.012, days)
        close = 100 * np.exp(np.cumsum(rets))
        spread = close * rng.uniform(0.004, 0.015, days)
        open_ = close * (1 + rng.normal(0, 0.003, days))
        out[sym] = pd.DataFrame(
            {
                "Open": open_,
                "High": np.maximum(open_, close) + spread / 2,
                "Low": np.minimum(open_, close) - spread / 2,
                "Close": close,
            },
            index=dates,
        )
    return out


def fetch_prices(symbols, years: int) -> dict:
    import yfinance as yf

    out = {}
    for sym in symbols:
        try:
            df = yf.Ticker(sym).history(period=f"{years}y", interval="1d", auto_adjust=True)
        except Exception as exc:
            print(f"Warning: fetching {sym} failed ({exc}); skipping", file=sys.stderr)
            continue
        if df.empty:
            print(f"Warning: no history for {sym}; skipping", file=sys.stderr)
            continue
        df.index = pd.to_datetime(df.index).tz_localize(None)
        out[sym] = df[["Open", "High", "Low", "Close"]].dropna()
    if not out:
        raise RuntimeError("No price history fetched for any symbol")
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Richard Dennis's Turtle Trading system: backtest + next-session levels")
    parser.add_argument("--symbols", default=",".join(DEFAULT_UNIVERSE), help="Comma-separated tickers")
    parser.add_argument("--years", type=int, default=10, help="Years of daily history to backtest")
    parser.add_argument("--system", choices=["1", "2", "both"], default="both")
    parser.add_argument("--equity", type=float, default=100_000.0, help="Account size for backtest and unit sizing")
    parser.add_argument("--risk-pct", type=float, default=1.0, help="Percent of equity risked per 1 N (default 1)")
    parser.add_argument("--max-leverage", type=float, default=2.0, help="Gross notional cap as a multiple of equity")
    parser.add_argument("--cost-bps", type=float, default=2.0, help="Commission + slippage per side, in bps")
    parser.add_argument("--long-only", action="store_true", help="Ignore short breakouts")
    parser.add_argument("--no-s1-filter", action="store_true", help="Take every System 1 breakout")
    parser.add_argument("--output-dir", default="reports/turtle")
    parser.add_argument("--self-test", action="store_true", help="Run on synthetic data (no network)")
    args = parser.parse_args(argv)

    systems = [1, 2] if args.system == "both" else [int(args.system)]
    try:
        if args.self_test:
            prices = synthetic_prices()
        else:
            symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            print(f"Fetching {args.years}y of daily history for {len(symbols)} symbols...", file=sys.stderr)
            prices = fetch_prices(symbols, args.years)
    except Exception as exc:
        print(f"Data fetch failed: {exc}", file=sys.stderr)
        return 1

    results = []
    for system in systems:
        cfg = TurtleConfig(
            system=system,
            risk_pct=args.risk_pct / 100.0,
            max_leverage=args.max_leverage,
            cost_bps=args.cost_bps,
            long_only=args.long_only,
            use_s1_filter=not args.no_s1_filter,
            starting_equity=args.equity,
        )
        results.append(run_backtest(prices, cfg))

    report_md = render_report(prices, results, args.equity)
    print(report_md)
    if args.self_test:
        return 0

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.date.today().isoformat()
    out_path = output_dir / f"{stamp}.md"
    out_path.write_text(report_md)
    print(f"\nSaved report to {out_path}", file=sys.stderr)
    for r in results:
        csv_path = output_dir / f"trades-system{r.config.system}-{stamp}.csv"
        export_trades_csv(r.trades, csv_path)
        print(f"Saved {len(r.trades)} System {r.config.system} trades to {csv_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
