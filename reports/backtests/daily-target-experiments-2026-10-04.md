# QQQ Daily $ Target Experiments -- 2026-10-04

Window: 2023-11-03 to 2026-10-02 (first half ends 2025-04-22). All variants: 10 contracts, 0.80 delta, 60 DTE, Black-Scholes prices at VIX x 1.15, $50 slippage per side, same skip-day gate, one position at a time.

A variant is a **candidate** (✅) only if it made money in **both** halves. With this many variants, one will look good by chance, so a single good total isn't enough.


### Baseline

| Variant | Trades | Win rate | Total P&L | Avg/trade | Max DD | 1st half | 2nd half | $ goal hit same day | |
|---|---|---|---|---|---|---|---|---|---|
| Live rules: daily signal, 9:30 entry, +$1,000 / -$1,000 | 467 | 46% | -$40,258 | -$86 | $44,258 | -$13,258 | -$27,000 | 211 |  |

### Entry timing

| Variant | Trades | Win rate | Total P&L | Avg/trade | Max DD | 1st half | 2nd half | $ goal hit same day | |
|---|---|---|---|---|---|---|---|---|---|
| Daily signal, 10:30 entry | 456 | 45% | -$46,870 | -$103 | $67,463 | -$42,681 | -$4,188 | 198 |  |

### Direction

| Variant | Trades | Win rate | Total P&L | Avg/trade | Max DD | 1st half | 2nd half | $ goal hit same day | |
|---|---|---|---|---|---|---|---|---|---|
| Strong daily signal only, 10:30 entry | 87 | 48% | -$2,856 | -$33 | $13,856 | -$4,856 | $2,000 | 39 |  |
| First-hour momentum, 10:30 entry | 455 | 54% | $30,068 | $66 | $13,309 | $16,519 | $13,549 | 237 | ✅ |
| First-hour fade, 10:30 entry | 459 | 39% | -$98,495 | -$215 | $105,495 | -$59,104 | -$39,391 | 173 |  |
| Daily + first hour agree, 10:30 entry | 225 | 52% | $8,643 | $38 | $11,357 | -$4,331 | $12,974 | 114 |  |

### Target / stop

| Variant | Trades | Win rate | Total P&L | Avg/trade | Max DD | 1st half | 2nd half | $ goal hit same day | |
|---|---|---|---|---|---|---|---|---|---|
| +$1,000 / -$500 | 470 | 31% | -$16,000 | -$34 | $24,500 | -$13,000 | -$3,000 | 146 |  |
| +$1,000 / -$600 | 470 | 33% | -$30,800 | -$66 | $34,000 | -$19,800 | -$11,000 | 157 |  |
| +$1,000 / -$750 | 470 | 39% | -$32,250 | -$69 | $37,000 | -$14,250 | -$18,000 | 183 |  |
| +$1,500 / -$750 | 466 | 32% | -$14,250 | -$31 | $30,750 | $1,500 | -$15,750 | 146 |  |
| +$500 / -$500 | 470 | 43% | -$31,000 | -$66 | $37,500 | -$26,500 | -$4,500 | 204 |  |

### ATR-scaled exits

| Variant | Trades | Win rate | Total P&L | Avg/trade | Max DD | 1st half | 2nd half | $ goal hit same day | |
|---|---|---|---|---|---|---|---|---|---|
| Target 0.15 x ATR / stop 0.10 x ATR | 470 | 41% | -$43,266 | -$92 | $51,731 | -$21,432 | -$21,834 | -- |  |
| Target 0.25 x ATR / stop 0.15 x ATR | 466 | 39% | -$22,454 | -$48 | $32,656 | -$3,164 | -$19,290 | -- |  |
| Target 0.30 x ATR / stop 0.30 x ATR | 430 | 50% | -$64,780 | -$151 | $102,702 | $19,236 | -$84,016 | -- |  |
| Target 0.50 x ATR / stop 0.25 x ATR | 407 | 33% | -$44,190 | -$109 | $63,578 | -$2,841 | -$41,349 | -- |  |

### Hold period

| Variant | Trades | Win rate | Total P&L | Avg/trade | Max DD | 1st half | 2nd half | $ goal hit same day | |
|---|---|---|---|---|---|---|---|---|---|
| Always calls, close -> next open | 729 | 54% | $72,828 | $100 | $87,287 | -$9,839 | $82,667 | -- |  |
| Always calls, open -> close | 730 | 52% | $32,329 | $44 | $86,744 | -$20,218 | $52,547 | -- |  |
| Always puts, close -> next open | 729 | 39% | -$340,074 | -$466 | $340,074 | -$110,425 | -$229,649 | -- |  |
| Always puts, open -> close | 730 | 45% | -$59,800 | -$82 | $141,413 | $9,342 | -$69,142 | -- |  |

### Stress test: First-hour momentum, 10:30 entry

| Variant | Trades | Win rate | Total P&L | Avg/trade | Max DD | 1st half | 2nd half | $ goal hit same day | |
|---|---|---|---|---|---|---|---|---|---|
| First-hour momentum, 10:30 entry -- same-hour ties scored as stops | 455 | 49% | -$11,932 | -$26 | $23,932 | $2,519 | -$14,451 | 216 |  |
| First-hour momentum, 10:30 entry -- 2x slippage | 452 | 50% | -$10,914 | -$24 | $23,752 | -$3,525 | -$7,389 | 212 |  |
| First-hour momentum, 10:30 entry -- 3x slippage | 455 | 45% | -$41,872 | -$92 | $46,490 | -$28,733 | -$13,140 | 195 |  |

## Verdict

- **Direction: First-hour momentum, 10:30 entry** -- $30,068 over 455 trades ($66/trade): **fragile** -- turns negative with same-hour ties scored as stops, 2x slippage, 3x slippage.

**Not captured:** real option quotes and IV changes, the order of moves inside one hourly bar (the OHLC path convention decides), news, and fills worse than the modelled slippage. Past results don't predict future ones.
