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


def vol_index_for_symbol(symbol: str) -> tuple[str, str, float]:
    """Pick the CBOE volatility index that actually tracks the underlying
    being scanned, rather than always defaulting to VIX: VXN (Nasdaq-100)
    for QQQ, VIX (S&P 500) for everything else (SPX, SPY, ...). Returns
    (yfinance_symbol, tradier_symbol, spike_threshold).

    VXN structurally trades richer than VIX -- about 30% higher on
    average, confirmed against a year of live data -- so a flat 20.0
    spike threshold carried over from VIX would fire on nearly every scan.
    27.0 is VXN's equivalent: both thresholds sit at roughly the 80th
    percentile of each index's own trailing 1-year range.

    This intentionally does not read `Config.vix_symbol` /
    `tradier_vix_symbol` / `vix_spike_threshold` -- those remain VIX-only
    and are relied on by the sibling Directional Signal / Daily $ Target
    tools (`daily_target_iv_vix_multiple` is calibrated specifically
    against VIX's level, not VXN's), which stay out of scope here.
    """
    if symbol.lstrip("^").upper() == "QQQ":
        return "^VXN", "VXN", 27.0
    return "^VIX", "VIX", 20.0


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
    # "^SPX" is the yfinance ticker for full-size S&P 500 index options --
    # unlike QQQ, it serves both price history AND a real option chain
    # (verified live; Yahoo's "^GSPC" has the former but not the latter).
    # Tradier's convention drops the caret ("SPX"); providers.py strips it
    # automatically when dispatching to Tradier, so this field never needs
    # a separate tradier_symbol the way VIX does below.
    symbol: str = "^SPX"
    vix_symbol: str = "^VIX"
    # Symbol convention differs between providers: yfinance wants the
    # caret-prefixed Yahoo ticker, Tradier's market data generally uses the
    # bare index symbol. Not verified against a live Tradier account in
    # this codebase -- adjust if VIX history calls fail with a 4xx.
    tradier_vix_symbol: str = "VIX"

    # Risk-free rate used in the Black-Scholes delta approximation.
    # Approximate short-term T-bill yield; not fetched live to avoid
    # another network dependency, close enough for delta estimation.
    risk_free_rate: float = 0.045

    # Target absolute delta for the short strikes of the iron condor.
    # ~0.16 delta is roughly a 1 standard deviation move (~84% POP per side).
    # Delta targeting is underlying-agnostic by design, so this didn't need
    # to change for the QQQ -> SPX switch -- only the dollar-denominated
    # wing widths below did, since SPX trades ~10x QQQ's price.
    short_delta_target: float = 0.16

    # Wing width in dollars for the long (protective) legs. SPX lists
    # strikes every $5 near the money (confirmed live), vs QQQ's $1 --
    # scaled roughly with the ~10x spot-price ratio between the two
    # (QQQ's old default was $5) and rounded to a real $5 strike increment.
    wing_width: float = 50.0

    # DTE (calendar days) windows to scan and label. 0DTE uses a tighter
    # wing and a lower delta target: intraday gamma risk is much higher,
    # and a full weekly/monthly-sized wing would be disproportionately wide
    # relative to a same-day expected move. Wing width here is likewise
    # scaled for SPX's ~$5 near-the-money strike spacing (QQQ's old default
    # was $2).
    expiration_targets: tuple = (
        ExpirationTarget("0DTE", 0, 0, short_delta_target=0.10, wing_width=20.0, allow_fallback=False),
        ExpirationTarget("Weekly", 5, 10),
        ExpirationTarget("Monthly", 28, 45),
    )

    # Trend strength threshold above which the market is considered
    # "trending" and less favorable for range-bound premium selling.
    adx_trend_threshold: float = 25.0

    # Lookback windows
    price_history_period: str = "1y"
    vix_history_period: str = "1y"

    # News. Swapped from QQQ/Nasdaq (^IXIC) feeds to SPY/S&P 500 (^GSPC)
    # feeds to match the broad-index underlying -- QQQ-specific headlines
    # skew tech-heavy and are less relevant once the scan trades SPX.
    news_feeds: tuple = (
        ("Yahoo Finance - SPY", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY&region=US&lang=en-US"),
        ("Yahoo Finance - S&P 500", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US"),
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

    # QQQ intraday fetch window used to build the relative-volume (RVOL)
    # profile: how busy today is, bar-for-bar, vs. the historical average
    # at the same point in the session -- used to scale down the
    # opening-range-breakout component when a "breakout" isn't backed by
    # real participation (see direction.py's _orb_component). Longer than
    # intraday_period so there are prior days to compare against; capped
    # by yfinance at 60d for a 5m bar size.
    volume_profile_period: str = "20d"

    # Width of the opening range (from the first bar of the session) used
    # for the opening-range-breakout component and, when triggered, as the
    # underlying stop-loss level.
    opening_range_minutes: int = 15

    # Target absolute delta for the single-leg call/put suggested to buy.
    # Near-ATM (unlike the 0.16 short-strike target for the iron condor)
    # so the contract is responsive to an intraday move rather than mostly
    # extrinsic value.
    directional_delta_target: float = 0.45

    # Underlying stop-loss distance (in ATR14 multiples), used as a cap on
    # the opening-range stop and as the fallback when no ORB level is
    # available. Empirically tightened from an initial 0.75: at that
    # wider distance, a `directional_backtest.py` run over real 5-min bars
    # found 80% of trades never actually reached their stop or target --
    # they just drifted to an arbitrary end-of-day price instead of being
    # managed by the plan. 0.35 leaves less room, so more trades actually
    # resolve one way or the other within the session; re-validate this
    # against the backtest if you change it.
    stop_atr_multiple: float = 0.35

    # Target = entry + this multiple of the stop distance (risk), i.e. a
    # 1.5:1 reward:risk underlying target.
    reward_risk_ratio: float = 1.5

    # Composite score (-1..+1) must reach this magnitude to produce a
    # CALL/PUT bias; anything smaller is reported as NO TRADE (no edge).
    signal_score_threshold: float = 0.30

    # Per-component weights for the directional score. Always normalized
    # to sum to 1.0 before use (direction.py's _normalized_weights) -- the
    # raw numbers below don't need to sum to exactly 1.0 themselves, so
    # zeroing a component redistributes its share to the rest rather than
    # just shrinking the max possible score -- e.g. for an experiment,
    # override via dataclasses.replace(cfg, component_weights={**cfg.
    # component_weights, "orb": 0.0}). A `directional_backtest.py` run
    # found the "orb" (opening-range breakout) component's contribution
    # anti-correlated with trade returns (r ~ -0.3 to -0.4 across two
    # windows) -- i.e. trading the breakout tended to be the wrong side of
    # it -- which is why it's worth being able to zero out and re-test
    # rather than only tuning the score threshold.
    #
    # "options_flow" (call-vs-put volume skew from the live option chain)
    # is NOT backtestable with free data -- yfinance only serves a
    # current snapshot, not historical per-contract volume -- so it's
    # always neutral (0.0 contribution) in directional_backtest.py. Only
    # the live signal exercises it for real.
    component_weights: dict = field(
        default_factory=lambda: {
            "daily_trend": 0.20,
            "macd": 0.10,
            "rsi": 0.10,
            "vwap": 0.20,
            "ema": 0.15,
            "orb": 0.15,
            "relative_strength": 0.10,
            "options_flow": 0.15,
        }
    )

    signals_output_dir: str = "reports/signals"

    # --- Daily $ target (0.80-delta ~60 DTE calls/puts, daily_target.py) ---
    # Deep-ITM single-leg buy: at 0.80 delta the option moves ~$0.80 per $1
    # QQQ move, with far less theta/IV sensitivity than near-ATM short-dated
    # contracts. ~60 DTE keeps daily theta small relative to the target.
    daily_target_delta: float = 0.80
    # Contract selection with live quotes: among strikes whose |delta| is in
    # [min, max] and whose bid-ask spread is at most daily_target_max_spread
    # (per share), pick the one closest to daily_target_delta. Deep-ITM QQQ
    # strikes often quote $2+ wide while slightly shallower ones are
    # $0.15-0.40 wide, so the pick is often a bit below 0.80 delta. If
    # nothing passes the spread cap, the plan flags the day as not tradeable
    # (showing the strike needing the smallest QQQ move to net the target
    # after the spread) instead of quietly suggesting an expensive contract.
    daily_target_min_delta: float = 0.65
    daily_target_max_delta: float = 0.90
    daily_target_max_spread: float = 0.20
    daily_target_expiration: ExpirationTarget = ExpirationTarget("60DTE", 50, 70)
    daily_target_contracts: int = 10

    # Which read picks CALL vs PUT: "first_hour" (buy calls if QQQ is above
    # its open after the first `daily_target_first_hour_minutes`, puts if
    # below -- the only rule that made money in daily_target_experiments.py,
    # and fragile: negative at 2x slippage, so paper-trade it) or "signal"
    # (the directional signal's score). Hard skip-day gates apply to both.
    daily_target_direction_source: str = "first_hour"
    daily_target_first_hour_minutes: int = 60

    # Close the whole position as soon as it is up this much (10 contracts x
    # $100 = $1,000 means the option has to gain $1.00/share)...
    daily_target_profit_usd: float = 1000.0
    # ...or down this much. 1:1 with the target by default -- with a
    # coin-flip direction call that's a negative-expectancy game once
    # spreads are paid, so check `daily_target_backtest.py` before
    # loosening it. There is deliberately no "no stop" option.
    daily_target_stop_usd: float = 1000.0
    # Time stop: close at the end of this many trading sessions if neither
    # level was hit. Only one position is open at a time -- no new daily
    # entry is suggested while one is still open, so positions can't stack.
    daily_target_max_hold_days: int = 5

    # Backtest-only assumptions (no free historical option chains exist):
    # QQQ IV is approximated as VIX x this multiple, and every fill pays
    # this much per share vs. the Black-Scholes mid on each side.
    daily_target_iv_vix_multiple: float = 1.15
    daily_target_slippage: float = 0.05

    daily_target_output_dir: str = "reports/daily_target"
