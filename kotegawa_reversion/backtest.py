"""Portfolio backtest of the 25-day-MA deviation rule over daily bars.

Mechanics (chosen to avoid look-ahead and to be conservative):

* Signals are computed on a day's close; entries fill at the NEXT bar's
  open. Deepest deviation is taken first when more symbols signal than
  there are free slots.
* Stops are checked before targets on every bar. A gap below the stop
  fills at the open (worse than the stop), otherwise at the stop price.
* Targets and the time stop are evaluated on the close and fill at that
  close.
* A per-side cost (cost_bps) is charged on every fill.

Daily bars can't show whether a stop or target was hit first within a
single day; assuming the stop first understates results rather than
overstating them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .config import Config
from .signals import add_indicators, entry_signal_mask


@dataclass
class Trade:
    symbol: str
    entry_date: pd.Timestamp
    entry_price: float
    shares: int
    entry_deviation: float
    stop_price: float
    exit_date: Optional[pd.Timestamp] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None
    bars_held: int = 0
    cost: float = 0.0

    @property
    def pnl(self) -> float:
        if self.exit_price is None:
            return 0.0
        return (self.exit_price - self.entry_price) * self.shares - self.cost

    @property
    def return_pct(self) -> float:
        basis = self.entry_price * self.shares
        return self.pnl / basis if basis else 0.0


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity: pd.Series
    starting_capital: float
    benchmark: Optional[pd.Series] = None
    stats: dict = field(default_factory=dict)


def _max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    return float((equity / equity.cummax() - 1.0).min())


def compute_stats(trades: list[Trade], equity: pd.Series, starting_capital: float, benchmark: Optional[pd.Series]) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    wins = [t for t in closed if t.pnl > 0]
    losses = [t for t in closed if t.pnl <= 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)

    final = float(equity.iloc[-1]) if not equity.empty else starting_capital
    total_return = final / starting_capital - 1.0
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9) if len(equity) > 1 else 0
    cagr = (final / starting_capital) ** (1 / years) - 1.0 if years and final > 0 else float("nan")

    stats = {
        "start": equity.index[0].date() if not equity.empty else None,
        "end": equity.index[-1].date() if not equity.empty else None,
        "final_equity": final,
        "total_return": total_return,
        "cagr": cagr,
        "max_drawdown": _max_drawdown(equity),
        "trades": len(closed),
        "win_rate": len(wins) / len(closed) if closed else float("nan"),
        "avg_trade_return": sum(t.return_pct for t in closed) / len(closed) if closed else float("nan"),
        "avg_win": sum(t.return_pct for t in wins) / len(wins) if wins else float("nan"),
        "avg_loss": sum(t.return_pct for t in losses) / len(losses) if losses else float("nan"),
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win > 0 else float("nan"),
        "avg_bars_held": sum(t.bars_held for t in closed) / len(closed) if closed else float("nan"),
        "exit_reasons": pd.Series([t.exit_reason for t in closed]).value_counts().to_dict() if closed else {},
    }
    if benchmark is not None and len(benchmark) > 1:
        stats["benchmark_return"] = float(benchmark.iloc[-1] / benchmark.iloc[0] - 1.0)
        stats["benchmark_max_drawdown"] = _max_drawdown(benchmark)
    return stats


def run_backtest(
    histories: dict[str, pd.DataFrame],
    cfg: Config,
    benchmark_close: Optional[pd.Series] = None,
) -> BacktestResult:
    cost_rate = cfg.cost_bps / 10_000.0
    prepared: dict[str, pd.DataFrame] = {}
    for symbol, df in histories.items():
        if df is None or len(df) < cfg.ma_window + 2:
            continue
        ind = add_indicators(df.sort_index(), cfg)
        ind["signal"] = entry_signal_mask(ind, cfg)
        prepared[symbol] = ind

    if not prepared:
        return BacktestResult(trades=[], equity=pd.Series(dtype=float), starting_capital=cfg.starting_capital)

    calendar = sorted(set().union(*(df.index for df in prepared.values())))
    # Plain dict lookups: far faster than DataFrame.at/loc inside the daily loop.
    bars: dict[str, dict] = {
        symbol: {
            date: (o, lo, c, dev, sig)
            for date, o, lo, c, dev, sig in zip(
                df.index, df["Open"], df["Low"], df["Close"], df["deviation"], df["signal"]
            )
        }
        for symbol, df in prepared.items()
    }
    by_date: dict = {}
    for symbol, sym_bars in bars.items():
        for date in sym_bars:
            by_date.setdefault(date, []).append(symbol)
    cash = cfg.starting_capital
    open_positions: dict[str, Trade] = {}
    last_close: dict[str, float] = {}
    trades: list[Trade] = []
    equity_points: dict[pd.Timestamp, float] = {}
    pending: list[tuple[str, float]] = []  # (symbol, signal-day deviation)

    def close_trade(trade: Trade, date, price: float, reason: str) -> None:
        nonlocal cash
        trade.exit_date, trade.exit_price, trade.exit_reason = date, price, reason
        trade.cost += price * trade.shares * cost_rate
        cash += price * trade.shares * (1 - cost_rate)
        trades.append(trade)

    for date in calendar:
        # 1) Fill yesterday's signals at today's open, deepest first.
        equity_now = cash + sum(t.shares * last_close.get(s, t.entry_price) for s, t in open_positions.items())
        for symbol, dev in sorted(pending, key=lambda item: item[1]):
            if len(open_positions) >= cfg.max_positions or symbol in open_positions:
                continue
            bar = bars[symbol].get(date)
            if bar is None:
                continue
            open_px = float(bar[0])
            if not (open_px > 0):
                continue
            budget = min(equity_now * cfg.position_fraction, cash / (1 + cost_rate))
            shares = math.floor(budget / open_px)
            if shares <= 0:
                continue
            entry_cost = open_px * shares * cost_rate
            cash -= open_px * shares + entry_cost
            open_positions[symbol] = Trade(
                symbol=symbol,
                entry_date=date,
                entry_price=open_px,
                shares=shares,
                entry_deviation=dev,
                stop_price=open_px * (1 - cfg.stop_loss_pct),
                cost=entry_cost,
            )
        pending = []

        # 2) Manage open positions on today's bar: stop, then target, then time.
        for symbol in list(open_positions):
            bar = bars[symbol].get(date)
            if bar is None:
                continue
            open_px, low, close, dev, _ = bar
            trade = open_positions[symbol]
            trade.bars_held += 1
            last_close[symbol] = float(close)

            if open_px <= trade.stop_price and trade.entry_date != date:
                close_trade(trade, date, float(open_px), "stop (gap)")
            elif low <= trade.stop_price:
                close_trade(trade, date, trade.stop_price, "stop")
            elif pd.notna(dev) and dev >= -cfg.exit_deviation:
                close_trade(trade, date, float(close), "target (reverted to MA)")
            elif trade.bars_held >= cfg.max_hold_days:
                close_trade(trade, date, float(close), "time stop")
            else:
                continue
            del open_positions[symbol]

        # 3) Mark to market and collect today's signals for tomorrow's open.
        for symbol in by_date[date]:
            _, _, close, dev, sig = bars[symbol][date]
            last_close[symbol] = float(close)
            if symbol not in open_positions and sig:
                pending.append((symbol, float(dev)))
        equity_points[date] = cash + sum(t.shares * last_close[s] for s, t in open_positions.items())

    # Close anything still open at the last available close so stats are complete.
    final_date = calendar[-1]
    for symbol, trade in list(open_positions.items()):
        close_trade(trade, final_date, last_close[symbol], "end of data")
    equity = pd.Series(equity_points).sort_index()
    if open_positions:
        equity.iloc[-1] = cash

    benchmark = None
    if benchmark_close is not None and not benchmark_close.empty:
        benchmark = benchmark_close.loc[(benchmark_close.index >= equity.index[0]) & (benchmark_close.index <= equity.index[-1])]
        if not benchmark.empty:
            benchmark = benchmark / benchmark.iloc[0] * cfg.starting_capital

    trades.sort(key=lambda t: t.entry_date)
    result = BacktestResult(trades=trades, equity=equity, starting_capital=cfg.starting_capital, benchmark=benchmark)
    result.stats = compute_stats(trades, equity, cfg.starting_capital, benchmark)
    return result
