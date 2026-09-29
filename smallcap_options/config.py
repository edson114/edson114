"""Central configuration for the small-cap momentum options scanner.

The thresholds below formalize a *style* of trading publicly associated
with small-cap momentum traders such as Alex Temiz: low-float,
low-to-mid-priced stocks that gap or spike on a catalyst with unusually
heavy volume, traded with cheap, short-dated (weekly) calls on
continuation or puts on a failed-breakout/fade -- not a reproduction of
any specific person's actual trades, rules, or track record, which are
not publicly documented in enough detail to replicate. This is a
rules-based checklist over free data, not a backtested or verified edge.
"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Config:
    # --- Universe ---
    # Predefined yfinance screener queries pulled each run (see
    # yfinance.screener.screener.PREDEFINED_SCREENER_QUERIES). "small_cap_gainers"
    # already filters to sub-$2B market cap; "aggressive_small_caps" and
    # "most_shorted_stocks" widen the net to names that aren't necessarily
    # green on the day (useful for the PUT/fade side).
    universe_queries: tuple = ("small_cap_gainers", "aggressive_small_caps", "most_shorted_stocks")
    universe_size_per_query: int = 100

    # Extra tickers always included regardless of what the screener returns
    # (e.g. from your own watchlist). Populated from the SMALLCAP_SYMBOLS
    # env var (comma-separated) by universe.py, not hardcoded here.
    extra_symbols: tuple = ()

    # --- Small-cap / momentum filters (cheap prefilter over screener fields,
    # no extra network calls) ---
    min_price: float = 1.0
    max_price: float = 20.0
    max_market_cap: float = 2_000_000_000.0
    min_day_volume: int = 300_000
    min_relative_volume: float = 3.0  # today's volume vs trailing 3-month average
    min_abs_change_pct: float = 8.0  # abs(% change) floor -- gap/spike magnitude

    # --- Deeper per-candidate filters (require one extra call per symbol,
    # so only applied after the cheap prefilter above narrows the field) ---
    max_float_shares: float = 50_000_000.0
    min_avg_dollar_volume: float = 2_000_000.0  # 20d average $ volume floor, liquidity
    max_candidates_deep_scanned: int = 20  # cap on per-symbol calls per run

    # --- News catalyst ---
    news_feeds: tuple = (
        ("Yahoo Finance - Markets", "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US"),
        ("CNBC Markets", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
        ("MarketWatch Top Stories", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
        ("PR Newswire", "https://www.prnewswire.com/rss/news-releases-list.rss"),
        ("GlobeNewswire", "https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20News%20Room"),
    )
    # Per-ticker catalyst search also checks yfinance's own per-symbol news
    # (data.get_ticker_news), which is far more precise than keyword-matching
    # broad market feeds; these generic keywords are a fallback/backstop only.
    catalyst_keywords: tuple = (
        "fda", "approval", "clearance", "clinical", "phase 1", "phase 2", "phase 3",
        "contract", "acquisition", "acquire", "merger", "buyout", "partnership",
        "collaboration", "licensing", "patent", "upgrade", "downgrade", "guidance",
        "earnings", "beat", "miss", "offering", "dilution", "reverse split",
        "halt", "resumes trading", "lawsuit", "settlement", "bankruptcy",
        "short interest", "squeeze", "recall", "data breach", "hack",
    )
    max_headlines_per_feed: int = 15
    max_ticker_news_items: int = 10

    # --- Directional signal ---
    intraday_interval: str = "5m"
    intraday_period: str = "1d"

    # Composite score (-1..+1) must reach this magnitude for a CALL/PUT
    # bias; smaller is reported as NO TRADE (no edge). Deliberately higher
    # than the QQQ tool's 0.30 -- small caps are noisier, so more evidence
    # is required before implying a directional edge exists.
    signal_score_threshold: float = 0.40

    # Always normalized to sum to 1.0 (see direction._normalized_weights).
    component_weights: dict = field(
        default_factory=lambda: {
            "gap": 0.30,
            "vwap": 0.20,
            "extension": 0.20,
            "relative_volume": 0.15,
            "catalyst": 0.15,
        }
    )

    # --- Options selection (cheap, short-dated, slightly OTM -- the
    # "lottery ticket" contract shape this style is known for, not a
    # risk-managed near-ATM position) ---
    max_dte: int = 10
    target_otm_pct: float = 0.07  # ~7% out of the money
    max_bid_ask_spread_pct: float = 0.50  # hard-skip if (ask-bid)/mid exceeds this
    min_option_open_interest: int = 25

    risk_free_rate: float = 0.045

    max_candidates_in_report: int = 8
    output_dir: str = "reports/smallcap"
