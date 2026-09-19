"""CLI entrypoint: run the full daily QQQ iron condor scan.

Usage:
    python -m qqq_iron_condor.scan
    python -m qqq_iron_condor.scan --no-issue
    python -m qqq_iron_condor.scan --self-test   # offline, synthetic data
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

from .analysis import build_snapshot
from .config import Config
from .data import get_gap_info, get_news, get_price_history, get_spot_price, get_vix_history, pick_expirations_for_targets
from .gates import evaluate_gates
from .news import flag_catalysts
from .report import render_report
from .strategy import build_iron_condor


def run_scan(cfg: Config) -> str:
    price_history = get_price_history(cfg.symbol, cfg.price_history_period)
    vix_history = get_vix_history(cfg.vix_history_period)
    spot = get_spot_price(price_history)

    snapshot = build_snapshot(price_history, vix_history, cfg.adx_trend_threshold)

    chains = pick_expirations_for_targets(cfg.symbol, cfg.expiration_targets)
    condors = {}
    for target in cfg.expiration_targets:
        chain = chains.get(target.label)
        if chain is None:
            condors[target.label] = None
            continue
        condors[target.label] = build_iron_condor(
            label=target.label,
            chain=chain,
            spot=spot,
            rate=cfg.risk_free_rate,
            target_delta=target.short_delta_target or cfg.short_delta_target,
            wing_width=target.wing_width or cfg.wing_width,
            reference_hv=snapshot.hv20_pct / 100.0,
        )

    headlines = get_news(cfg.news_feeds, cfg.max_headlines_per_feed)
    catalyst_hits = flag_catalysts(headlines, cfg.catalyst_keywords)

    gap_pct, gap_confirmed = get_gap_info(cfg.symbol, price_history)
    gate = evaluate_gates(
        today=dt.date.today(),
        macro_event_dates=cfg.macro_event_dates,
        gap_pct=gap_pct,
        gap_confirmed=gap_confirmed,
        gap_threshold_pct=cfg.gap_threshold_pct,
        vix_level=snapshot.vix_level,
        vix_spike_threshold=cfg.vix_spike_threshold,
        snapshot=snapshot,
        adx_trend_threshold=cfg.adx_trend_threshold,
        volume_ratio_threshold=cfg.volume_ratio_threshold,
        catalyst_hits=catalyst_hits,
    )

    return render_report(cfg.symbol, snapshot, condors, headlines, catalyst_hits, gate=gate)


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
            json={"title": title, "body": report_md, "labels": ["qqq-scan"]},
            timeout=30,
        )
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        # Non-fatal: the report was already generated and saved to disk.
        # A 410 here typically means Issues are disabled for this repo.
        print(f"Warning: could not create GitHub issue ({exc}). Report was still generated and saved.", file=sys.stderr)


def _self_test_report() -> str:
    """Build a report from synthetic data, exercising every module without
    any network access -- used to smoke-test the pipeline in CI-less/offline
    environments."""
    import numpy as np
    import pandas as pd

    from .data import OptionChain, is_today_bar_present
    from .gates import evaluate_gates
    from .strategy import build_iron_condor

    rng = np.random.default_rng(42)
    n = 260
    dates = pd.date_range(end=dt.date.today(), periods=n, freq="B")
    price = 400 + np.cumsum(rng.normal(0.3, 3.0, n))
    high = price + rng.uniform(0.5, 2.5, n)
    low = price - rng.uniform(0.5, 2.5, n)
    price_history = pd.DataFrame(
        {"Open": price, "High": high, "Low": low, "Close": price, "Volume": rng.integers(1_000_000, 5_000_000, n)},
        index=dates,
    )

    vix_price = 16 + np.cumsum(rng.normal(0, 0.4, n))
    vix_price = np.clip(vix_price, 9, 40)
    vix_history = pd.DataFrame({"Open": vix_price, "High": vix_price, "Low": vix_price, "Close": vix_price}, index=dates)

    cfg = Config()
    spot = float(price_history["Close"].iloc[-1])
    snapshot = build_snapshot(price_history, vix_history, cfg.adx_trend_threshold)

    def synth_chain(label: str, dte: int) -> OptionChain:
        from .options_math import bs_price, time_to_expiration_years

        strikes = np.arange(round(spot) - 40, round(spot) + 40, 1.0)
        iv = 0.18 + rng.uniform(-0.02, 0.02, len(strikes))
        t_years = time_to_expiration_years(dte)

        call_fair = np.array([bs_price(spot, k, t_years, cfg.risk_free_rate, v, "call") for k, v in zip(strikes, iv)])
        put_fair = np.array([bs_price(spot, k, t_years, cfg.risk_free_rate, v, "put") for k, v in zip(strikes, iv)])
        half_spread = 0.02

        calls = pd.DataFrame({
            "strike": strikes,
            "bid": np.maximum(0.01, call_fair - half_spread),
            "ask": call_fair + half_spread,
            "lastPrice": call_fair,
            "impliedVolatility": iv,
        })
        puts = pd.DataFrame({
            "strike": strikes,
            "bid": np.maximum(0.01, put_fair - half_spread),
            "ask": put_fair + half_spread,
            "lastPrice": put_fair,
            "impliedVolatility": iv,
        })
        expiration = (dt.date.today() + dt.timedelta(days=dte)).isoformat()
        return OptionChain(expiration=expiration, dte=dte, calls=calls, puts=puts)

    condors = {}
    for target in cfg.expiration_targets:
        rep_dte = 0 if (target.min_dte == 0 and target.max_dte == 0) else round((target.min_dte + target.max_dte) / 2)
        condors[target.label] = build_iron_condor(
            target.label,
            synth_chain(target.label, rep_dte),
            spot,
            cfg.risk_free_rate,
            target.short_delta_target or cfg.short_delta_target,
            target.wing_width or cfg.wing_width,
            reference_hv=snapshot.hv20_pct / 100.0,
        )

    from .data import Headline
    headlines = [
        Headline(source="Synthetic", title="Fed holds rates steady, Powell signals data-dependent path", link="#"),
        Headline(source="Synthetic", title="Nvidia rallies on AI chip demand outlook", link="#"),
    ]
    catalyst_hits = flag_catalysts(headlines, cfg.catalyst_keywords)

    gap_confirmed = is_today_bar_present(price_history)
    gap_pct = (
        (float(price_history["Close"].iloc[-1]) / float(price_history["Close"].iloc[-2]) - 1.0) * 100.0
        if gap_confirmed
        else None
    )
    gate = evaluate_gates(
        today=dt.date.today(),
        macro_event_dates=cfg.macro_event_dates,
        gap_pct=gap_pct,
        gap_confirmed=gap_confirmed,
        gap_threshold_pct=cfg.gap_threshold_pct,
        vix_level=snapshot.vix_level,
        vix_spike_threshold=cfg.vix_spike_threshold,
        snapshot=snapshot,
        adx_trend_threshold=cfg.adx_trend_threshold,
        volume_ratio_threshold=cfg.volume_ratio_threshold,
        catalyst_hits=catalyst_hits,
    )

    return render_report(cfg.symbol, snapshot, condors, headlines, catalyst_hits, gate=gate)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="QQQ iron condor daily scanner")
    parser.add_argument("--no-issue", action="store_true", help="Skip creating a GitHub issue even if GITHUB_TOKEN is set")
    parser.add_argument("--output-dir", default=None, help="Override the reports output directory")
    parser.add_argument("--self-test", action="store_true", help="Run the full pipeline on synthetic data (no network)")
    args = parser.parse_args(argv)

    cfg = Config()

    try:
        if args.self_test:
            report_md = _self_test_report()
        else:
            report_md = run_scan(cfg)
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
        maybe_create_github_issue(report_md, f"QQQ Iron Condor Scan -- {dt.date.today().isoformat()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
