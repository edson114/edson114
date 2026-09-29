"""CLI entrypoint: run the small-cap momentum options scan.

Usage:
    python -m smallcap_options.scan
    python -m smallcap_options.scan --no-issue
    python -m smallcap_options.scan --self-test   # offline, synthetic data

Set SMALLCAP_SYMBOLS (comma-separated tickers) to always include specific
symbols in the scan regardless of what the free screener returns.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path
from typing import Optional

from . import data, universe
from .config import Config
from .direction import build_directional_signal
from .gates import evaluate_candidate_gates
from .intraday import build_intraday_snapshot
from .news import find_catalyst_for_symbol
from .report import CandidateReport, render_report
from .strategy import select_contract
from .universe import DeepCandidate


def score_candidate(dc: DeepCandidate, cfg: Config, market_headlines: list) -> CandidateReport:
    symbol = dc.quote.symbol

    intraday_df = data.get_intraday_history(symbol, cfg.intraday_interval, cfg.intraday_period)
    if intraday_df is None or intraday_df.empty:
        return CandidateReport(
            quote=dc.quote,
            float_shares=dc.float_shares,
            avg_dollar_volume=dc.avg_dollar_volume,
            intraday=None,
            signal=None,
            skip_reason="No intraday data available (market closed, delisted, or provider gap).",
        )

    intraday = build_intraday_snapshot(intraday_df)

    ticker_headlines = data.get_ticker_news(symbol, cfg.max_ticker_news_items)
    catalyst_hits = find_catalyst_for_symbol(
        symbol, dc.quote.name, ticker_headlines, market_headlines, cfg.catalyst_keywords
    )

    signal = build_directional_signal(dc.quote, intraday, catalyst_hits, cfg)

    contract = None
    gate = None
    if signal.bias in ("CALL", "PUT"):
        option_type = "call" if signal.bias == "CALL" else "put"
        chain = data.pick_expiration_within_dte(symbol, cfg.max_dte)
        if chain is None:
            skip_reason: Optional[str] = "No option chain listed within the configured DTE window."
        else:
            contract, skip_reason = select_contract(chain, dc.quote.price, option_type, cfg)

        next_earnings = data.get_next_earnings_date(symbol)
        gate = evaluate_candidate_gates(skip_reason, dc.float_shares, next_earnings, bool(catalyst_hits))

    return CandidateReport(
        quote=dc.quote,
        float_shares=dc.float_shares,
        avg_dollar_volume=dc.avg_dollar_volume,
        intraday=intraday,
        signal=signal,
        catalyst_hits=catalyst_hits,
        contract=contract,
        gate=gate,
    )


def run_scan(cfg: Config) -> str:
    extra_symbols = universe.extra_symbols_from_env()
    deep_candidates, universe_stats = universe.build_universe(cfg, extra_symbols=extra_symbols)

    market_headlines = data.get_news(cfg.news_feeds, cfg.max_headlines_per_feed)
    candidates = [score_candidate(dc, cfg, market_headlines) for dc in deep_candidates]

    return render_report(candidates, universe_stats, cfg)


def maybe_create_github_issue(report_md: str, title: str) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        return

    import requests

    try:
        resp = requests.post(
            f"https://api.github.com/repos/{repo}/issues",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
            json={"title": title, "body": report_md, "labels": ["smallcap-scan"]},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        print(f"Warning: could not create GitHub issue ({exc}). Report was still generated and saved.", file=sys.stderr)


def _synthetic_deep_candidate(rng, symbol: str, name: str, bullish: bool) -> DeepCandidate:
    import numpy as np
    import pandas as pd

    from .data import CandidateQuote

    price = round(rng.uniform(2.0, 15.0), 2)
    change_pct = rng.uniform(15.0, 60.0) * (1 if bullish else -1)
    day_volume = int(rng.uniform(2_000_000, 20_000_000))
    avg_volume_3m = day_volume / rng.uniform(4.0, 12.0)

    quote = CandidateQuote(
        symbol=symbol,
        name=name,
        price=price,
        change_pct=change_pct,
        day_volume=day_volume,
        avg_volume_3m=avg_volume_3m,
        market_cap=price * rng.uniform(5_000_000, 40_000_000),
        source_query="synthetic",
    )

    n = 40
    dates = pd.date_range(end=dt.date.today(), periods=n, freq="B")
    hist_price = price / (1 + change_pct / 100.0) + np.cumsum(rng.normal(0, 0.05, n))
    price_history = pd.DataFrame(
        {
            "Open": hist_price,
            "High": hist_price * 1.02,
            "Low": hist_price * 0.98,
            "Close": hist_price,
            "Volume": rng.integers(int(avg_volume_3m * 0.7), int(avg_volume_3m * 1.3), n),
        },
        index=dates,
    )

    return DeepCandidate(
        quote=quote,
        float_shares=rng.uniform(3_000_000, 40_000_000),
        avg_dollar_volume=float((price_history["Close"] * price_history["Volume"]).tail(20).mean()),
        price_history=price_history,
    )


def _synthetic_intraday(rng, quote, bullish: bool, faded: bool):
    """Percent-based (not fixed-dollar) synthetic path so it stays
    plausible across the $1-$20 price range: a smooth run from the prior
    close to the current price when `not faded` (still extending, near
    the session high/low), or a run past the current price to a further
    extreme and back (a failed breakout/bounce) when `faded`."""
    import numpy as np
    import pandas as pd

    n = 60
    times = pd.date_range(end=dt.datetime.now(), periods=n, freq="5min")
    prior_close = quote.price / (1 + quote.change_pct / 100.0)
    direction = 1.0 if bullish else -1.0

    if faded:
        peak_idx = int(n * 0.6)
        extreme = quote.price * (1 + direction * 0.35)
        up_leg = np.linspace(prior_close, extreme, peak_idx)
        down_leg = np.linspace(extreme, quote.price, n - peak_idx)
        path = np.concatenate([up_leg, down_leg])
    else:
        path = np.linspace(prior_close, quote.price, n)

    noise = rng.normal(0.0, prior_close * 0.004, n)
    path = np.clip(path + noise, 0.05, None)
    path[0] = prior_close
    path[-1] = quote.price

    df = pd.DataFrame(
        {
            "Open": path,
            "High": path * 1.006,
            "Low": path * 0.994,
            "Close": path,
            "Volume": rng.integers(50_000, 500_000, n),
        },
        index=times,
    )
    return df


def _self_test_report() -> str:
    """Build a report from synthetic data, exercising every module without
    any network access -- used to smoke-test the pipeline offline."""
    import numpy as np

    from .data import Headline

    rng = np.random.default_rng(7)
    cfg = Config()

    dc_call = _synthetic_deep_candidate(rng, "SYNA", "Runwell Biotech Corp", bullish=True)
    dc_put = _synthetic_deep_candidate(rng, "SYNB", "Faderloop Holdings Inc", bullish=True)  # green day, fading -> PUT
    deep_candidates = [dc_call, dc_put]

    market_headlines = [
        Headline(source="Synthetic", title="Runwell Biotech Corp announces FDA clearance for lead device", link="#"),
    ]

    candidates = []
    for dc, faded in ((dc_call, False), (dc_put, True)):
        intraday_df = _synthetic_intraday(rng, dc.quote, bullish=True, faded=faded)
        intraday = build_intraday_snapshot(intraday_df)
        catalyst_hits = find_catalyst_for_symbol(
            dc.quote.symbol, dc.quote.name, [], market_headlines, cfg.catalyst_keywords
        )
        signal = build_directional_signal(dc.quote, intraday, catalyst_hits, cfg)

        contract = None
        gate = None
        if signal.bias in ("CALL", "PUT"):
            from .options_math import bs_price

            spot = dc.quote.price
            option_type = "call" if signal.bias == "CALL" else "put"
            strikes = np.arange(round(spot * 0.7), round(spot * 1.3) + 1, max(0.5, round(spot * 0.05, 1)))
            iv = 0.9 + rng.uniform(-0.1, 0.1, len(strikes))
            t_years = 5 / 365.0
            fair = np.array([bs_price(spot, k, t_years, cfg.risk_free_rate, v, option_type) for k, v in zip(strikes, iv)])
            half_spread = np.maximum(0.02, fair * 0.08)
            import pandas as pd

            df = pd.DataFrame(
                {
                    "strike": strikes,
                    "bid": np.maximum(0.01, fair - half_spread),
                    "ask": fair + half_spread,
                    "lastPrice": fair,
                    "impliedVolatility": iv,
                    "openInterest": rng.integers(30, 500, len(strikes)),
                }
            )
            from .data import OptionChain

            chain = OptionChain(
                expiration=(dt.date.today() + dt.timedelta(days=5)).isoformat(),
                dte=5,
                calls=df if option_type == "call" else pd.DataFrame(columns=df.columns),
                puts=df if option_type == "put" else pd.DataFrame(columns=df.columns),
            )
            contract, skip_reason = select_contract(chain, spot, option_type, cfg)
            gate = evaluate_candidate_gates(skip_reason, dc.float_shares, None, bool(catalyst_hits))

        candidates.append(
            CandidateReport(
                quote=dc.quote,
                float_shares=dc.float_shares,
                avg_dollar_volume=dc.avg_dollar_volume,
                intraday=intraday,
                signal=signal,
                catalyst_hits=catalyst_hits,
                contract=contract,
                gate=gate,
            )
        )

    universe_stats = {"screened": 200, "prefiltered": 12, "deep_scanned": len(deep_candidates), "manual_symbols": []}
    return render_report(candidates, universe_stats, cfg)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Small-cap momentum options scanner")
    parser.add_argument("--no-issue", action="store_true", help="Skip creating a GitHub issue even if GITHUB_TOKEN is set")
    parser.add_argument("--output-dir", default=None, help="Override the reports output directory")
    parser.add_argument("--self-test", action="store_true", help="Run the full pipeline on synthetic data (no network)")
    args = parser.parse_args(argv)

    cfg = Config()

    try:
        report_md = _self_test_report() if args.self_test else run_scan(cfg)
    except Exception as exc:
        print(f"Scan failed: {exc}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir or cfg.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"{dt.date.today().isoformat()}.md"
    out_path.write_text(report_md)
    print(report_md)
    print(f"\nSaved report to {out_path}", file=sys.stderr)

    if not args.no_issue and not args.self_test:
        maybe_create_github_issue(report_md, f"Small-Cap Momentum Options Scan -- {dt.date.today().isoformat()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
