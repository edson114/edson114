"""Central configuration for the Kotegawa-style mean-reversion scanner.

Takashi Kotegawa ("BNF") is publicly known for one core idea, described in
his own interviews: buy stocks that have fallen far below their 25-day
moving average (a large negative "kairi" / deviation rate) during panic
selling, and sell quickly on the rebound toward that average -- cutting
any position that keeps going against him without hesitation. Holding
periods were days, not months.

This module formalizes that idea as rules over free daily bars. It is a
reconstruction of a publicly described *style*, not his actual trades or
thresholds (which he tuned per sector and per market regime by feel, on
the Japanese market of the early 2000s). Nothing here is a verified edge
-- run the backtest on your own universe before trusting any setting.
"""

from dataclasses import dataclass


# Liquid US large caps across sectors. Mean reversion off a moving average
# needs names that panic and recover, not ones that can go to zero
# overnight, so the default universe is deliberately big, liquid companies.
# Override with KOTEGAWA_SYMBOLS (comma-separated) -- see data.load_universe.
DEFAULT_UNIVERSE = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "AMD", "INTC",
    "QCOM", "MU", "ORCL", "CRM", "ADBE", "NFLX", "CSCO", "IBM", "TXN", "AMAT",
    "JPM", "BAC", "WFC", "C", "GS", "MS", "SCHW", "AXP", "V", "MA",
    "UNH", "JNJ", "PFE", "MRK", "ABBV", "LLY", "BMY", "CVS", "TMO", "AMGN",
    "XOM", "CVX", "COP", "SLB", "OXY", "HAL",
    "WMT", "COST", "HD", "LOW", "TGT", "NKE", "SBUX", "MCD", "DIS", "KO", "PEP",
    "BA", "CAT", "DE", "GE", "HON", "UPS", "FDX", "LMT", "RTX",
    "F", "GM", "UBER", "PYPL", "SHOP", "XYZ",
)


@dataclass(frozen=True)
class Config:
    # --- Moving average & deviation ("kairi") ---
    ma_window: int = 25  # the 25-day MA Kotegawa is known for

    # Entry when close is at least this far BELOW the 25-day MA. Kotegawa
    # is often quoted using roughly -20% (and deeper for volatile sectors)
    # on 2000s Japanese small/mid caps; US large caps rarely get that far
    # from their 25-day MA outside a crash, so the default is shallower.
    # Backtest before changing.
    entry_deviation: float = 0.12

    # Skip anything that has fallen absurdly far below the MA -- at that
    # point it is usually a fundamental break (fraud, failed trial,
    # bankruptcy risk), not a panic to buy into.
    max_entry_deviation: float = 0.45

    # Require the signal day to close above its open (a first sign buyers
    # stepped in) instead of buying a candle that is still collapsing.
    require_green_close: bool = True

    # Liquidity floor: 20-day average dollar volume.
    min_avg_dollar_volume: float = 50_000_000.0
    min_price: float = 5.0

    # --- Exits ---
    # Take profit once the close has recovered to within this distance of
    # the 25-day MA (0.0 = full reversion to the MA). Kotegawa took
    # rebounds quickly rather than waiting for a complete round trip.
    exit_deviation: float = 0.04
    # Hard stop below the entry price. Cutting losers fast was the other
    # half of his method; without this, mean reversion is a falling-knife
    # strategy.
    stop_loss_pct: float = 0.07
    # Time stop: if the rebound hasn't come within this many trading days,
    # the thesis (a short-term panic) was wrong -- exit at the close.
    max_hold_days: int = 7

    # --- Portfolio / risk ---
    starting_capital: float = 100_000.0
    max_positions: int = 5
    # Fraction of current equity committed per new position.
    position_fraction: float = 0.20
    # Per-side cost (commission + slippage) in basis points.
    cost_bps: float = 10.0

    # --- Market breadth (panic) read ---
    # Share of the universe at/below the entry deviation. Kotegawa's
    # biggest wins came on market-wide panics, when many names were
    # oversold at once; this is shown in the report, not used as a gate.
    panic_breadth_threshold: float = 0.15

    history_period: str = "1y"
    max_candidates_in_report: int = 15
    output_dir: str = "reports/kotegawa"
