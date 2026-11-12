# Live-tracked 0DTE SPX Iron Condor Tally (30pt vs 40pt OTM, $20-wide, 2 contracts)

Rebuilt 2026-11-12 after discovering the running tally had only ever lived in
chat replies (never persisted), which made it unrecoverable across a context
reset. Rebuilt deterministically from real OHLC (`providers.get_price_history`)
and VIX-at-open (`providers.get_vix_history`), using the exact rule documented
in the project: strikes = nearest $5 to Open +/- 30 or 40 points, $20-wide
wings; model entry credit from `backtest.simulate_chain` priced at the
**9:30am market open** (not current wall-clock time -- `time_to_expiration_years(0)`
defaults to `now=datetime.now()`, which silently went near-zero when this
script was first run near the close; always pass
`now=_market_open_datetime(trade_date)` explicitly). Settlement:
`call_loss = min(max(close - short_call, 0), 20)`,
`put_loss = min(max(short_put - close, 0), 20)`,
`pnl_per_contract = credit - call_loss - put_loss`.

This reconstruction uses the Open-based fallback for every day (not each
morning's actual live 9:32am broker-ish quote), so it may differ slightly
from what was reported live in chat on days the live quote was usable --
but it is internally consistent and reproducible, and is the authoritative
record going forward. Append one row per day at each 4:05pm EOD check
instead of rebuilding from memory.

| Date | Open | Close | 30pt strikes (call/put) | 30pt win | 30pt P&L (2 ctr) | 40pt strikes (call/put) | 40pt win | 40pt P&L (2 ctr) |
|---|---|---|---|---|---|---|---|---|
| 2026-10-15 | 7687.31 | 7744.06 | 7715/7660 | loss | -$2,866.92 | 7725/7650 | loss | -$3,092.51 |
| 2026-10-16 | 7745.32 | 7659.44 | 7775/7715 | loss | -$3,026.18 | 7785/7705 | loss | -$3,420.63 |
| 2026-10-19 | 7702.77 | 7690.73 | 7735/7670 | WIN | +$1,222.08 | 7745/7660 | WIN | +$800.85 |
| 2026-10-20 | 7679.01 | 7712.57 | 7710/7650 | loss | +$704.95 | 7720/7640 | WIN | +$797.99 |
| 2026-10-21 | 7736.43 | 7757.17 | 7765/7705 | WIN | +$1,110.36 | 7775/7695 | WIN | +$698.97 |
| 2026-10-22 | 7694.62 | 7663.43 | 7725/7665 | loss | +$894.90 | 7735/7655 | WIN | +$788.70 |
| 2026-10-23 | 7637.18 | 7623.68 | 7665/7605 | WIN | +$1,287.45 | 7675/7595 | WIN | +$862.56 |
| 2026-10-26 | 7639.70 | 7676.77 | 7670/7610 | loss | -$21.90 | 7680/7600 | WIN | +$905.03 |
| 2026-10-27 | 7699.47 | 7722.60 | 7730/7670 | WIN | +$1,224.55 | 7740/7660 | WIN | +$803.27 |
| 2026-10-28 | 7735.56 | 7743.98 | 7765/7705 | WIN | +$1,132.59 | 7775/7695 | WIN | +$718.94 |
| 2026-10-29 | 7699.61 | 7720.39 | 7730/7670 | WIN | +$1,089.00 | 7740/7660 | WIN | +$679.75 |
| 2026-10-30 | 7631.07 | 7580.07 | 7660/7600 | loss | -$2,777.71 | 7670/7590 | loss | -$1,197.73 |
| 2026-11-02 | 7610.83 | 7541.40 | 7640/7580 | loss | -$2,734.43 | 7650/7570 | loss | -$3,158.21 |
| 2026-11-03 | 7552.92 | 7568.92 | 7585/7525 | WIN | +$1,495.71 | 7595/7515 | WIN | +$1,065.43 |
| 2026-11-04 | 7635.81 | 7688.62 | 7665/7605 | loss | -$2,713.39 | 7675/7595 | loss | -$1,862.34 |
| 2026-11-05 | 7678.72 | 7353.73 | 7710/7650 | loss | -$2,916.19 | 7720/7640 | loss | -$3,324.93 |
| 2026-11-06 | 7377.65 | 7376.86 | 7410/7345 | WIN | +$1,899.78 | 7420/7335 | WIN | +$1,487.20 |
| 2026-11-09 | 7422.24 | 7549.94 | 7450/7390 | loss | -$2,265.88 | 7460/7380 | loss | -$2,689.49 |
| 2026-11-10 | 7513.09 | 7520.93 | 7545/7480 | WIN | +$1,312.09 | 7555/7470 | WIN | +$885.75 |
| 2026-11-11 | 7586.65 | 7619.38 | 7615/7555 | loss | +$360.89 | 7625/7545 | WIN | +$814.90 |
| 2026-11-12 | 7550.84 | 7592.40 | 7580/7520 | loss | -$1,299.08 | 7590/7510 | loss | +$283.00 |

**Totals over 21 trading days:**
- **30pt/$20:** 9 wins / 12 losses, net P&L (2 contracts) = **-$6,887.32**
- **40pt/$20:** 13 wins / 8 losses, net P&L (2 contracts) = **-$7,153.52**

Note: "win"/"loss" marks whether a short strike was breached by the close, not
the sign of that day's P&L -- a small breach can still net positive if the
day's credit comfortably covers it (e.g. 2026-10-20 and 2026-11-11 for 30pt,
2026-11-12 for 40pt).

**Takeaway so far:** both structures are net losers across this 21-day
window, dragged down by a cluster of high-volatility days (10/30, 11/2, 11/4,
11/5, 11/9) where the day's range blew through the $20 wing on one side
regardless of starting distance. The 40pt structure wins more often (13 vs 9)
because the extra 10 points of initial distance avoids some marginal
breaches, but its lower entry credit means it doesn't actually net out ahead
of the 30pt structure over the full sample -- consistent with the standing
finding that extra OTM distance helps on borderline days but provides little
protection once a move exceeds ~1-2%.
