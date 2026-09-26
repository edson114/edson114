"""CLI entrypoint: on-demand QQQ directional (buy calls/puts) signal.

Unlike the once-daily iron condor scan, this is meant to be re-run through
the session as price action develops (it reads the current intraday bars
each time it runs).

Usage:
    python -m qqq_iron_condor.signal
    python -m qqq_iron_condor.signal --no-issue
    python -m qqq_iron_condor.signal --self-test   # offline, synthetic data
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from .analysis import build_snapshot
from .config import Config
from .data import (
    get_gap_info,
    get_intraday_history,
    get_news,
    get_price_history,
    get_spot_price,
    get_vix_history,
    pick_expirations_for_targets,
)
from .direction import DirectionalSignal, build_directional_signal
from .gates import evaluate_gates
from .intraday import build_intraday_snapshot, compute_relative_strength, compute_relative_volume, interval_minutes
from .news import flag_catalysts
from .scan import maybe_create_github_issue
from .signal_report import render_signal_report


def _directional_targets(cfg: Config) -> tuple:
    # 0DTE/Weekly only -- Monthly is too slow-theta to be a useful buy for
    # an intraday directional call/put idea.
    return tuple(t for t in cfg.expiration_targets if t.label in ("0DTE", "Weekly"))


def run_signal(cfg: Config) -> tuple[str, DirectionalSignal]:
    price_history = get_price_history(cfg.symbol, cfg.price_history_period)
    vix_history = get_vix_history(cfg.vix_history_period)
    daily_snapshot = build_snapshot(price_history, vix_history, cfg.adx_trend_threshold)

    qqq_intraday_all = get_intraday_history(cfg.symbol, cfg.intraday_interval, cfg.volume_profile_period)
    today = dt.date.today()
    qqq_intraday = qqq_intraday_all[qqq_intraday_all.index.date == today]
    qqq_intraday_historical = qqq_intraday_all[qqq_intraday_all.index.date < today]
    spy_intraday = get_intraday_history(cfg.spy_symbol, cfg.intraday_interval, cfg.intraday_period)
    intraday_snapshot = build_intraday_snapshot(
        qqq_intraday, cfg.opening_range_minutes, interval_minutes(cfg.intraday_interval)
    )
    relative_strength_pct = compute_relative_strength(qqq_intraday, spy_intraday)
    relative_volume = compute_relative_volume(qqq_intraday, qqq_intraday_historical)

    chains = pick_expirations_for_targets(cfg.symbol, _directional_targets(cfg))

    headlines = get_news(cfg.news_feeds, cfg.max_headlines_per_feed)
    catalyst_hits = flag_catalysts(headlines, cfg.catalyst_keywords)

    gap_pct, gap_confirmed = get_gap_info(cfg.symbol, price_history)
    gate = evaluate_gates(
        today=dt.date.today(),
        macro_event_dates=cfg.macro_event_dates,
        gap_pct=gap_pct,
        gap_confirmed=gap_confirmed,
        gap_threshold_pct=cfg.gap_threshold_pct,
        vix_level=daily_snapshot.vix_level,
        vix_spike_threshold=cfg.vix_spike_threshold,
        snapshot=daily_snapshot,
        adx_trend_threshold=cfg.adx_trend_threshold,
        volume_ratio_threshold=cfg.volume_ratio_threshold,
        catalyst_hits=catalyst_hits,
    )

    signal = build_directional_signal(
        daily_snapshot, intraday_snapshot, relative_strength_pct, gate, chains, cfg, relative_volume=relative_volume
    )
    report_md = render_signal_report(
        cfg.symbol, daily_snapshot, intraday_snapshot, signal, headlines, catalyst_hits, gate
    )
    return report_md, signal


def _self_test_report() -> tuple[str, DirectionalSignal]:
    """Build a report from synthetic data, exercising every module without
    any network access."""
    import numpy as np
    import pandas as pd

    from .data import Headline, OptionChain, is_today_bar_present
    from .options_math import bs_price, time_to_expiration_years

    rng = np.random.default_rng(7)

    n = 260
    dates = pd.date_range(end=dt.date.today(), periods=n, freq="B")
    price = 400 + np.cumsum(rng.normal(0.3, 3.0, n))
    high = price + rng.uniform(0.5, 2.5, n)
    low = price - rng.uniform(0.5, 2.5, n)
    price_history = pd.DataFrame(
        {"Open": price, "High": high, "Low": low, "Close": price, "Volume": rng.integers(1_000_000, 5_000_000, n)},
        index=dates,
    )
    vix_price = np.clip(16 + np.cumsum(rng.normal(0, 0.4, n)), 9, 40)
    vix_history = pd.DataFrame(
        {"Open": vix_price, "High": vix_price, "Low": vix_price, "Close": vix_price}, index=dates
    )

    cfg = Config()
    spot = float(price_history["Close"].iloc[-1])
    daily_snapshot = build_snapshot(price_history, vix_history, cfg.adx_trend_threshold)

    def synth_intraday(start_price: float, drift: float, n_bars: int = 60, end: dt.datetime = None) -> pd.DataFrame:
        steps = rng.normal(drift, 0.15, n_bars)
        close = start_price + np.cumsum(steps)
        open_ = np.concatenate([[start_price], close[:-1]])
        high = np.maximum(open_, close) + rng.uniform(0.01, 0.1, n_bars)
        low = np.minimum(open_, close) - rng.uniform(0.01, 0.1, n_bars)
        idx = pd.date_range(end=end or dt.datetime.now(), periods=n_bars, freq="5min")
        return pd.DataFrame(
            {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": rng.integers(50_000, 300_000, n_bars)},
            index=idx,
        )

    qqq_intraday = synth_intraday(spot - 1.5, 0.06)
    spy_intraday = synth_intraday(spot / 8.0, 0.01)
    qqq_intraday_historical = pd.concat(
        [synth_intraday(spot - 2.0, 0.05, end=dt.datetime.now() - dt.timedelta(days=d)) for d in range(1, 6)]
    )

    intraday_snapshot = build_intraday_snapshot(
        qqq_intraday, cfg.opening_range_minutes, interval_minutes(cfg.intraday_interval)
    )
    relative_strength_pct = compute_relative_strength(qqq_intraday, spy_intraday)
    relative_volume = compute_relative_volume(qqq_intraday, qqq_intraday_historical)

    def synth_chain(label: str, dte: int) -> OptionChain:
        strikes = np.arange(round(spot) - 40, round(spot) + 40, 1.0)
        iv = 0.18 + rng.uniform(-0.02, 0.02, len(strikes))
        t_years = time_to_expiration_years(dte)
        call_fair = np.array([bs_price(spot, k, t_years, cfg.risk_free_rate, v, "call") for k, v in zip(strikes, iv)])
        put_fair = np.array([bs_price(spot, k, t_years, cfg.risk_free_rate, v, "put") for k, v in zip(strikes, iv)])
        half_spread = 0.02
        calls = pd.DataFrame(
            {
                "strike": strikes,
                "bid": np.maximum(0.01, call_fair - half_spread),
                "ask": call_fair + half_spread,
                "lastPrice": call_fair,
                "impliedVolatility": iv,
            }
        )
        puts = pd.DataFrame(
            {
                "strike": strikes,
                "bid": np.maximum(0.01, put_fair - half_spread),
                "ask": put_fair + half_spread,
                "lastPrice": put_fair,
                "impliedVolatility": iv,
            }
        )
        expiration = (dt.date.today() + dt.timedelta(days=dte)).isoformat()
        return OptionChain(expiration=expiration, dte=dte, calls=calls, puts=puts)

    chains = {"0DTE": synth_chain("0DTE", 0), "Weekly": synth_chain("Weekly", 7)}

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
        vix_level=daily_snapshot.vix_level,
        vix_spike_threshold=cfg.vix_spike_threshold,
        snapshot=daily_snapshot,
        adx_trend_threshold=cfg.adx_trend_threshold,
        volume_ratio_threshold=cfg.volume_ratio_threshold,
        catalyst_hits=catalyst_hits,
    )

    signal = build_directional_signal(
        daily_snapshot, intraday_snapshot, relative_strength_pct, gate, chains, cfg, relative_volume=relative_volume
    )
    report_md = render_signal_report(
        cfg.symbol, daily_snapshot, intraday_snapshot, signal, headlines, catalyst_hits, gate
    )
    return report_md, signal


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="QQQ directional (buy calls/puts) signal")
    parser.add_argument(
        "--no-issue",
        action="store_true",
        help="Skip creating a GitHub issue even if GITHUB_TOKEN is set and the signal is actionable",
    )
    parser.add_argument("--output-dir", default=None, help="Override the signals output directory")
    parser.add_argument("--self-test", action="store_true", help="Run the full pipeline on synthetic data (no network)")
    args = parser.parse_args(argv)

    cfg = Config()

    try:
        if args.self_test:
            report_md, signal = _self_test_report()
        else:
            report_md, signal = run_signal(cfg)
    except Exception as exc:
        print(f"Signal run failed: {exc}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir or cfg.signals_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d-%H%M")
    out_path = output_dir / f"{stamp}.md"
    out_path.write_text(report_md)
    print(report_md)
    print(f"\nSaved report to {out_path}", file=sys.stderr)

    if not args.no_issue and not args.self_test and signal.bias in ("CALL", "PUT"):
        maybe_create_github_issue(report_md, f"QQQ Directional Signal -- {signal.bias} ({stamp})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
