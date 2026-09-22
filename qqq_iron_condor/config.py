"""Central configuration for the QQQ iron condor scanner."""

from dataclasses import dataclass, field
from typing import Optional


# 2026 FOMC decision days (the announcement day of each 2-day meeting,
# 2:00pm ET) and CPI release days, sourced from federalreserve.gov's FOMC
# calendar and BLS's CPI release schedule (cross-referenced against
# multiple independent sources and BLS's own archived-release URL naming,
# e.g. cpi_05122026.htm = published May 12, 2026, covering April data).
# CPI dates from Feb 2026 onward reflect the schedule as revised after the
# 2025 government shutdown ("lapse in appropriations") pushed some
# releases back. Only the FOMC *decision* day is included, not the
# (lower-impact) first day of each two-day meeting -- add those too if
# you want extra caution. Re-verify closer to each date in case of further
# revisions, and extend this table when 2027 dates are published.
FOMC_2026_DECISION_DAYS = {
    "2026-01-28": "FOMC decision",
    "2026-03-18": "FOMC decision",
    "2026-04-29": "FOMC decision",
    "2026-06-17": "FOMC decision",
    "2026-07-29": "FOMC decision",
    "2026-09-16": "FOMC decision",
    "2026-10-28": "FOMC decision",
    "2026-12-09": "FOMC decision",
}

CPI_2026_RELEASE_DAYS = {
    "2026-01-13": "CPI release (December 2025 data)",
    "2026-02-13": "CPI release (January 2026 data)",
    "2026-03-11": "CPI release (February 2026 data)",
    "2026-04-10": "CPI release (March 2026 data)",
    "2026-05-12": "CPI release (April 2026 data)",
    "2026-06-10": "CPI release (May 2026 data)",
    "2026-07-14": "CPI release (June 2026 data)",
    "2026-08-12": "CPI release (July 2026 data)",
    "2026-09-11": "CPI release (August 2026 data)",
    "2026-10-14": "CPI release (September 2026 data)",
    "2026-11-10": "CPI release (October 2026 data)",
    "2026-12-10": "CPI release (November 2026 data)",
}

DEFAULT_MACRO_EVENT_DATES = {**FOMC_2026_DECISION_DAYS, **CPI_2026_RELEASE_DAYS}


@dataclass(frozen=True)
class ExpirationTarget:
    label: str
    min_dte: int
    max_dte: int
    # Per-target overrides; fall back to Config.short_delta_target /
    # Config.wing_width when left as None.
    short_delta_target: Optional[float] = None
    wing_width: Optional[float] = None
    # 0DTE has no "closest available expiration" fallback: if QQQ doesn't
    # list a same-day expiration today, skip it rather than silently
    # substitute a multi-day one under the "0DTE" label.
    allow_fallback: bool = True


@dataclass(frozen=True)
class Config:
    symbol: str = "QQQ"
    vix_symbol: str = "^VIX"

    # Risk-free rate used in the Black-Scholes delta approximation.
    # Approximate short-term T-bill yield; not fetched live to avoid
    # another network dependency, close enough for delta estimation.
    risk_free_rate: float = 0.045

    # Target absolute delta for the short strikes of the iron condor.
    # ~0.16 delta is roughly a 1 standard deviation move (~84% POP per side).
    short_delta_target: float = 0.16

    # Wing width in dollars for the long (protective) legs.
    wing_width: float = 5.0

    # DTE (calendar days) windows to scan and label. 0DTE uses a tighter
    # wing and a lower delta target: intraday gamma risk is much higher,
    # and QQQ's $1 strike spacing makes a $5 wing disproportionately wide
    # relative to a same-day expected move.
    expiration_targets: tuple = (
        ExpirationTarget("0DTE", 0, 0, short_delta_target=0.10, wing_width=2.0, allow_fallback=False),
        ExpirationTarget("Weekly", 5, 10),
        ExpirationTarget("Monthly", 28, 45),
    )

    # Trend strength threshold above which the market is considered
    # "trending" and less favorable for range-bound premium selling.
    adx_trend_threshold: float = 25.0

    # Lookback windows
    price_history_period: str = "1y"
    vix_history_period: str = "1y"

    # News
    news_feeds: tuple = (
        ("Yahoo Finance - QQQ", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=QQQ&region=US&lang=en-US"),
        ("Yahoo Finance - Nasdaq", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EIXIC&region=US&lang=en-US"),
        ("CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
        ("MarketWatch Top Stories", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    )
    catalyst_keywords: tuple = (
        "fomc", "fed ", "federal reserve", "rate decision", "rate cut", "rate hike",
        "cpi", "pce", "inflation", "jobs report", "nonfarm", "payrolls",
        "gdp", "powell", "earnings", "guidance", "antitrust", "tariff",
        "nvidia", "apple", "microsoft", "amazon", "google", "alphabet", "meta",
        "tesla", "broadcom", "semiconductor", "chip ", "opec", "war", "shutdown",
    )
    max_headlines_per_feed: int = 8

    output_dir: str = "reports"

    # --- Trade gate: skip-day rules ---
    # Known scheduled macro events (FOMC decisions, CPI prints) to
    # hard-skip. Pre-populated with 2026 dates (see DEFAULT_MACRO_EVENT_DATES
    # above) -- there is no live paid economic-calendar feed wired into this
    # app, so this is a point-in-time snapshot, not a self-updating source.
    # Keep it current and extend it for 2027+ from official sources:
    #   FOMC: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
    #   CPI:  https://www.bls.gov/schedule/news_release/cpi.htm
    # Keys are "YYYY-MM-DD" (date the scan runs, i.e. the event date).
    macro_event_dates: dict = field(default_factory=lambda: dict(DEFAULT_MACRO_EVENT_DATES))

    # Hard-skip if the opening (or, pre-market, the indicated) gap vs the
    # prior close is at least this many percentage points in magnitude.
    gap_threshold_pct: float = 0.8

    # Hard-skip if VIX is at or above this level at scan time. This checks
    # the level *at the time the scan runs* -- it cannot detect a spike
    # that develops intraday after the report has already been generated.
    vix_spike_threshold: float = 20.0

    # Soft "trending + above-average volume" flag: fires when ADX(14) is
    # at/above adx_trend_threshold AND the most recently completed
    # session's volume is at least this multiple of its trailing 20-day
    # average. This is a leading-indicator proxy from the last completed
    # session, not a live intraday volume read -- a single morning scan
    # can't yet know today's full-day volume.
    volume_ratio_threshold: float = 1.3

    # --- Directional (buy calls/puts) signal ---
    # Ticker used for the intraday relative-strength read (QQQ performance
    # vs. the broad market today) -- outperformance points to tech-specific
    # strength rather than a market-wide move.
    spy_symbol: str = "SPY"

    # Intraday bar size/lookback used for VWAP, opening-range, and
    # short-window EMA/RSI reads. "1d" period with a 5m interval is what
    # yfinance reliably serves for the current session.
    intraday_interval: str = "5m"
    intraday_period: str = "1d"

    # Width of the opening range (from the first bar of the session) used
    # for the opening-range-breakout component and, when triggered, as the
    # underlying stop-loss level.
    opening_range_minutes: int = 15

    # Target absolute delta for the single-leg call/put suggested to buy.
    # Near-ATM (unlike the 0.16 short-strike target for the iron condor)
    # so the contract is responsive to an intraday move rather than mostly
    # extrinsic value.
    directional_delta_target: float = 0.45

    # Underlying stop-loss distance (in ATR14 multiples) used when no
    # opening-range level is available to stop against.
    stop_atr_multiple: float = 0.75

    # Target = entry + this multiple of the stop distance (risk), i.e. a
    # 1.5:1 reward:risk underlying target.
    reward_risk_ratio: float = 1.5

    # Composite score (-1..+1) must reach this magnitude to produce a
    # CALL/PUT bias; anything smaller is reported as NO TRADE (no edge).
    signal_score_threshold: float = 0.30

    signals_output_dir: str = "reports/signals"
