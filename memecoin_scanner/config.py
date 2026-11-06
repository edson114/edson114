"""Central configuration for the meme coin scanner.

fomo (fomo.family) has no public API, so the scan reads the same public
on-chain market data the app trades against: DexScreener pairs for
discovery and price action, RugCheck for contract and holder safety. The
output is a contract address per pick, which you paste into fomo's search
to buy.

Every threshold below is a judgment call over free data, not a backtested
edge. Most meme coins go to zero; the gates are there to throw out the
obvious rugs and dead charts, not to make anything safe.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    # --- Universe ---
    # fomo trades Solana meme coins; DexScreener chain ids. Add "base" etc.
    # here only if your fomo account trades that chain too.
    chains: tuple = ("solana",)

    # DexScreener free-text searches that surface extra active pairs beyond
    # the boosted / profiled / community-takeover lists.
    search_queries: tuple = ("pump", "bonk", "meme", "sol")

    # Quote tokens a pair must trade against (Wrapped SOL, USDC, USDT).
    allowed_quote_symbols: tuple = ("SOL", "WSOL", "USDC", "USDT")

    # Never picked: majors, stables and LSTs that show up in searches.
    excluded_symbols: tuple = (
        "SOL", "WSOL", "USDC", "USDT", "JUP", "JTO", "PYTH", "RAY", "BONK",
        "WIF", "JITOSOL", "MSOL", "BSOL", "ORCA", "W", "TRUMP", "PENGU",
    )

    # --- Market gates (rejects; no extra network calls) ---
    min_liquidity_usd: float = 30_000.0
    min_market_cap: float = 150_000.0
    max_market_cap: float = 50_000_000.0
    min_volume_h24: float = 100_000.0
    min_txns_h24: int = 500
    min_age_hours: float = 1.0
    max_drop_h1_pct: float = -25.0  # dumping right now
    max_drop_h6_pct: float = -40.0

    # --- Safety (RugCheck) ---
    # Only the best-scoring candidates by market data get a RugCheck call.
    max_safety_checks: int = 25
    max_top10_holder_pct: float = 45.0  # reject above this (pools excluded)
    rugcheck_pause_s: float = 0.35

    # --- Penalties ---
    wash_volume_liquidity_ratio: float = 40.0  # 24h volume / liquidity
    late_entry_h24_pct: float = 1500.0  # already ran >15x today
    young_age_hours: float = 3.0
    insider_flag_count: int = 25
    falling_m5_pct: float = -10.0
    # Average 6h trade size (USD). At/below bot level buy pressure counts
    # 30%; at/above organic level it counts in full.
    bot_avg_trade_usd: float = 10.0
    organic_avg_trade_usd: float = 40.0

    # --- Output ---
    top_n: int = 5
    # Picks scoring below this still fill the list but are labeled
    # low conviction, so a thin market doesn't read like a strong one.
    min_conviction_score: float = 60.0
    output_dir: str = "reports/memecoins"

    # --- Trade plan printed under every pick (rules of thumb, not advice) ---
    max_position_pct: float = 2.0  # of total portfolio per coin
    stop_loss_pct: float = 35.0
    take_profit_ladder: tuple = (
        (2.0, "sell 50% (your initial stake is back)"),
        (5.0, "sell another 25%"),
        (10.0, "trail the last 25% with a 30% trailing stop"),
    )

    request_timeout_s: float = 15.0
