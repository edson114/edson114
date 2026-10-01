"""CLI entrypoint for the Kotegawa-style 25-day MA reversion tool.

Usage:
    python -m kotegawa_reversion.scan                     # today's buy list
    python -m kotegawa_reversion.scan --backtest --period 5y
    python -m kotegawa_reversion.scan --symbols AAPL,NVDA,TSLA --entry-deviation 0.15
    python -m kotegawa_reversion.scan --self-test         # offline, synthetic data

Set KOTEGAWA_SYMBOLS (comma-separated) to replace the default universe.
"""

from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import replace
from pathlib import Path

from . import data
from .backtest import run_backtest
from .config import Config
from .report import render_backtest, render_scan
from .signals import evaluate_symbol


def build_scan(histories: dict, cfg: Config, universe_size: int) -> str:
    signals = [s for s in (evaluate_symbol(sym, df, cfg) for sym, df in histories.items()) if s is not None]
    as_of = max((s.date for s in signals), default=dt.datetime.today()).date()
    return render_scan(signals, cfg, as_of, universe_size)


def _write(text: str, cfg: Config, name: str) -> Path:
    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    path.write_text(text)
    return path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--backtest", action="store_true", help="run a historical backtest instead of today's scan")
    p.add_argument("--period", default=None, help="yfinance period, e.g. 1y, 5y, 10y (default: 1y scan, 5y backtest)")
    p.add_argument("--symbols", default=None, help="comma-separated universe override")
    p.add_argument("--benchmark", default="SPY")
    p.add_argument("--entry-deviation", type=float, default=None)
    p.add_argument("--exit-deviation", type=float, default=None)
    p.add_argument("--stop-loss", type=float, default=None)
    p.add_argument("--max-hold", type=int, default=None)
    p.add_argument("--self-test", action="store_true", help="offline run on synthetic data (no network)")
    p.add_argument("--no-write", action="store_true", help="print only; don't write a report file")
    args = p.parse_args(argv)

    cfg = Config()
    overrides = {
        "entry_deviation": args.entry_deviation,
        "exit_deviation": args.exit_deviation,
        "stop_loss_pct": args.stop_loss,
        "max_hold_days": args.max_hold,
    }
    cfg = replace(cfg, **{k: v for k, v in overrides.items() if v is not None})

    if args.self_test:
        histories = data.synthetic_histories()
        cfg = replace(cfg, entry_deviation=0.08)
        print(build_scan(histories, cfg, len(histories)))
        result = run_backtest(histories, cfg)
        print(render_backtest(result, cfg, len(histories), "synthetic"))
        return 0

    universe = data.load_universe(args.symbols)
    period = args.period or ("5y" if args.backtest else cfg.history_period)
    histories = data.get_histories(universe, period)
    if not histories:
        print("No price data retrieved -- check your network connection.")
        return 1

    if args.backtest:
        benchmark = data.get_benchmark(args.benchmark, period) if args.benchmark else None
        text = render_backtest(run_backtest(histories, cfg, benchmark), cfg, len(universe), period)
        name = f"backtest-{dt.date.today().isoformat()}.md"
    else:
        text = build_scan(histories, cfg, len(universe))
        name = f"{dt.date.today().isoformat()}.md"

    print(text)
    if not args.no_write:
        print(f"Report written to {_write(text, cfg, name)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
