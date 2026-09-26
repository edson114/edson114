"""Options positioning read: call-vs-put volume skew from the live option
chain(s) the app already fetches for contract selection.

This is the honest, freely-available proxy for "where the flow is
going" -- real institutional order flow (block trades, dark pool prints,
sweep detection) is a paid data product (FlowAlgo, Unusual Whales, and
similar) this app has no access to, and there is no free source of it.
Put/call volume mixes retail and institutional activity together; it is
not literal whale/dark-pool tracking, a skewed read here is the same
public per-contract volume those paid services are themselves built
from, just without their added block-trade detection or historical
baselining. Treat it as one more input, not a smart-money leak.
"""

from __future__ import annotations

from typing import Optional

from .data import OptionChain


def compute_call_put_skew(chains: dict) -> tuple[float, int, int]:
    """Returns (skew, total_call_volume, total_put_volume).

    skew is in [-1, +1]: +1 is all call volume, -1 is all put volume, 0
    is either a perfectly balanced tape or -- more commonly -- no usable
    volume data (chain unavailable/untraded), which is scored as neutral
    rather than guessed in either direction.
    """
    total_call = 0
    total_put = 0
    for chain in chains.values():
        if not isinstance(chain, OptionChain):
            continue
        total_call += int(chain.calls["volume"].fillna(0).sum())
        total_put += int(chain.puts["volume"].fillna(0).sum())

    total = total_call + total_put
    if total <= 0:
        return 0.0, total_call, total_put
    skew = (total_call - total_put) / total
    return skew, total_call, total_put
