"""Central configuration for the QQQ iron condor scanner."""

from dataclasses import dataclass, field


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

    # DTE (calendar days) windows to scan and label.
    expiration_targets: tuple = (
        ("Weekly", 5, 10),
        ("Monthly", 28, 45),
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
