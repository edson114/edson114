"""CLI entrypoint: the daily options signal.

Usage:
    python -m options_signal.scan
    python -m options_signal.scan --symbols QQQ,TSLA,NVDA
    python -m options_signal.scan --watchlist trader/watchlist.json --watchlist-top 10
    python -m options_signal.scan --json
    python -m options_signal.scan --self-test   # offline, synthetic data

Run it after 9:50 AM ET (the 15-minute opening range plus one bar). It
writes reports/options_signals/<stamp>.md and .json and prints the report.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import sys
from dataclasses import replace
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from .config import Config
from .market import LiveMarket
from .pipeline import run
from .report import render_markdown, to_json
from .rules import load_trader_config

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None


def new_york_now() -> dt.datetime:
    return dt.datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None, second=0, microsecond=0)


def parse_now(text: str) -> dt.datetime:
    return dt.datetime.strptime(text.replace("T", " "), "%Y-%m-%d %H:%M")


def watchlist_symbols(path: str, top: int) -> list:
    try:
        hits = json.loads(Path(path).read_text()).get("hits", [])
    except (OSError, ValueError):
        return []
    return [h["symbol"] for h in hits[:top] if h.get("symbol")]


def _clean(obj):
    """NaN/inf -> None so the JSON is strict."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


class SyntheticMarket:
    """Deterministic fake data for --self-test: 40 past sessions of a random
    walk and a today that breaks out above its opening range."""

    def __init__(self, now: dt.datetime, seed: int = 7):
        self.now = now
        self.today = now.date()
        rng = np.random.default_rng(seed)
        rows, index = [], []
        day, price = self.today - dt.timedelta(days=60), 100.0
        while day <= self.today:
            if day.weekday() < 5:
                for slot in range(78):
                    ts = dt.datetime.combine(day, dt.time(9, 30)) + dt.timedelta(minutes=5 * slot)
                    drift = 0.0012 if (day == self.today and 3 <= slot <= 8) else 0.0
                    o = price
                    c = o * (1 + drift + rng.normal(0, 0.0015))
                    h = max(o, c) * (1 + abs(rng.normal(0, 0.0007)))
                    l = min(o, c) * (1 - abs(rng.normal(0, 0.0007)))
                    rows.append((o, h, l, c))
                    index.append(ts)
                    price = c
            day += dt.timedelta(days=1)
        self.df = pd.DataFrame(rows, index=pd.DatetimeIndex(index), columns=["Open", "High", "Low", "Close"])

    def bars(self, symbol: str, period: str) -> pd.DataFrame:
        return self.df

    def expirations(self, symbol: str) -> list:
        return [(self.today + dt.timedelta(days=d)).isoformat() for d in (0, 4, 11, 32)]

    def chain(self, symbol: str, expiration: str, option_type: str) -> pd.DataFrame:
        from qqq_iron_condor.options_math import bs_price

        closed = self.df[self.df.index + dt.timedelta(minutes=5) <= self.now]
        spot = float(closed["Close"].iloc[-1])
        exp = dt.date.fromisoformat(expiration)
        t = max((exp - self.today).days, 1) / 365.0
        strikes = np.arange(round(spot) - 10, round(spot) + 11, 1.0)
        mids = [bs_price(spot, k, t, 0.045, 0.30, option_type) for k in strikes]
        return pd.DataFrame({
            "strike": strikes,
            "bid": [round(m * 0.98, 2) for m in mids],
            "ask": [round(m * 1.02, 2) for m in mids],
            "openInterest": [1000] * len(strikes),
            "impliedVolatility": [0.30] * len(strikes),
        })

    def vix(self) -> Optional[float]:
        return 16.0

    def earnings(self, symbol: str) -> Optional[dt.date]:
        return self.today + dt.timedelta(days=40)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Daily options signal (calls/puts on opening-range breakouts)")
    parser.add_argument("--symbols", help="comma-separated tickers (default: the configured universe)")
    parser.add_argument("--watchlist", help="also scan the first --watchlist-top hits of this trader-scanner watchlist")
    parser.add_argument("--watchlist-top", type=int, default=10)
    parser.add_argument("--target", type=float, help="profit target on the premium, percent (default 30)")
    parser.add_argument("--replays", type=int, help="Monte Carlo replays (default 10,000)")
    parser.add_argument("--seed", type=int, help="random seed (default 19)")
    parser.add_argument("--now", help='pin the clock, New York time, today only: "2026-10-26 10:15"')
    parser.add_argument("--output-dir", help="override the report directory")
    parser.add_argument("--no-write", action="store_true", help="print only, write no files")
    parser.add_argument("--json", action="store_true", help="print the JSON instead of the markdown")
    parser.add_argument("--self-test", action="store_true", help="run on synthetic data (no network)")
    args = parser.parse_args(argv)

    cfg = Config()
    overrides = {k: v for k, v in (("profit_target_pct", args.target), ("replays", args.replays), ("seed", args.seed)) if v is not None}
    if overrides:
        cfg = replace(cfg, **overrides)
    trader = load_trader_config(cfg.trader_config_path)

    if args.self_test:
        now = dt.datetime.combine(dt.date(2026, 10, 26), dt.time(10, 20))
        market = SyntheticMarket(now)
        symbols = ["TEST"]
    else:
        now = parse_now(args.now) if args.now else new_york_now()
        market = LiveMarket()
        symbols = [s.strip().upper() for s in args.symbols.split(",")] if args.symbols else list(cfg.symbols)
        if args.watchlist:
            symbols += [s for s in watchlist_symbols(args.watchlist, args.watchlist_top) if s not in symbols]

    result = run(symbols, market, now, cfg, trader)
    md = render_markdown(result)
    doc = _clean(to_json(result))
    print(json.dumps(doc, indent=2) if args.json else md)

    if not args.no_write and not args.self_test:
        out_dir = Path(args.output_dir or cfg.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y-%m-%d-%H%M")
        (out_dir / f"{stamp}.md").write_text(md)
        (out_dir / f"{stamp}.json").write_text(json.dumps(doc, indent=2) + "\n")
        print(f"Saved report to {out_dir / stamp}.md and .json", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
