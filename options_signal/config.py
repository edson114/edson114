"""Central configuration for the daily options signal.

The rules mirror the trader skills: trader-scanner / trader-entry (15-minute
opening range on 5-minute bars, first close beyond it), trader-exit (stop at
the opening-range midpoint, flat by 15:55), trader-timemachine (10,000
bootstrap replays from the last ~60 days of 5-minute bars, seed 19, +-2 bar
window) and trader-budget / trader-risk (max loss per trade, position cap,
cash buffer, entry window from trader/config.json). The one change is the
target: a percentage gain on the option's premium instead of an R multiple
on the stock.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # --- Universe: liquid names with deep, tight option chains. The scan
    # can add the trader-scanner watchlist on top (--watchlist).
    symbols: tuple = (
        "QQQ", "SPY", "IWM", "NVDA", "TSLA", "AAPL", "MSFT",
        "AMZN", "META", "GOOGL", "AMD", "AVGO", "NFLX", "PLTR",
    )
    # Funds have no earnings date to check.
    etfs: tuple = ("QQQ", "SPY", "IWM", "DIA")

    # --- The setup (trader-entry / trader-exit) ---
    or_minutes: int = 15
    bar_minutes: int = 5
    flat_by: str = "15:55"
    no_entries_before: str = "09:45"
    no_entries_after: str = "15:30"
    # A trigger older than this many bars is still evaluated (from the
    # latest close, what you would actually pay now) but flagged as late.
    late_after_bars: int = 6

    # --- The target ---
    profit_target_pct: float = 30.0

    # --- Contract selection ---
    # Weekly-ish, near the money: 0DTE quotes are the least reliable on free
    # data (the SPX report has shown a 0.000 delta on a 0DTE strike), and a
    # same-day exit doesn't need the extra gamma.
    min_dte: int = 1
    max_dte: int = 10
    preferred_dte: int = 7
    strikes_to_try: int = 6
    max_spread_pct: float = 10.0  # (ask - bid) / mid, percent
    min_open_interest: int = 500
    risk_free_rate: float = 0.045

    # --- The time machine ---
    history_period: str = "60d"
    min_full_sessions: int = 20
    replays: int = 10_000
    seed: int = 19
    window: int = 2

    # --- Output ---
    top_n: int = 3
    output_dir: str = "reports/options_signals"
    trader_config_path: str = "trader/config.json"
