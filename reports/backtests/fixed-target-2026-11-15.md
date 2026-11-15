# Fixed $500 Target at 5 Contracts -- 2026-11-15

QQQ hourly bars; last year = 2025-11-13 to 2026-11-13, two years = 2024-11-13 to 2026-11-13. Options are Black-Scholes at the prior day's VIX x 1.15 with $0.05/share slippage per fill, not real quotes.

## 1. RSI calls: buy 5 calls at 0.85 delta, ~60 DTE, at the open if RSI(14) < 70; sell at +$500

One position at a time; the next opens the session after an exit. A position still open with 5 days to expiration is sold at that close (an assumption: the original rules had none). "Open" is the position still held at the end, marked at the last close.

| Stop | Last year: trades | Win rate | Closed P&L | Still open | Net | Worst trade | Two years: trades | Win rate | Closed P&L | Net |
|---|---|---|---|---|---|---|---|---|---|---|
| none | 61 | 98% | $11,086 | -$24,721 | **-$13,634** | -$23,078 | 136 | 98% | $36,792 | **$12,071** |
| -$500 | 220 | 45% | -$10,000 | $0 | **-$10,000** | -$500 | 445 | 45% | -$21,500 | **-$21,500** |
| -$1,000 | 219 | 62% | -$16,126 | $0 | **-$16,126** | -$2,126 | 439 | 62% | -$28,444 | **-$28,444** |
| -$2,000 | 210 | 79% | -$5,126 | $0 | **-$5,126** | -$2,126 | 418 | 77% | -$22,095 | **-$22,095** |
| -$3,000 | 198 | 84% | -$17,482 | $0 | **-$17,482** | -$6,327 | 387 | 83% | -$43,610 | **-$43,610** |

## 2. The daily plan's rules at 5 contracts: +$500 target, -$2,000 hard stop

Skip-day rules (gap >= 0.8%, prior VIX >= 20, FOMC/CPI), first-hour direction at 10:30 (calls and puts), level stop on an hourly close past the first hour's low/high, 5-session time stop.

| Variant | Last year: trades | Win rate | Total | Worst | Max DD | 1st half | 2nd half | Two years: trades | Total | 1st half | 2nd half |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Daily plan rules (calls + puts, 0.80 delta) | 115 | 75% | **$4,795** | -$2,000 | $11,821 | $6,189 | -$1,395 | 255 | **$1,991** | -$2,804 | $4,795 |
| ...with 0.85 delta | 117 | 75% | **$5,219** | -$2,000 | $8,740 | $5,548 | -$329 | 260 | **$6,738** | $1,520 | $5,219 |
| ...same-hour ties scored as stops | 115 | 73% | **$672** | -$2,000 | $14,321 | $4,566 | -$3,895 | 255 | **-$6,298** | -$6,969 | $672 |
| ...2x slippage | 114 | 74% | **$191** | -$2,000 | $14,771 | $4,536 | -$4,345 | 251 | **-$9,027** | -$9,218 | $191 |
| ...3x slippage | 114 | 69% | **-$13,331** | -$2,000 | $18,812 | -$7,138 | -$6,194 | 249 | **-$21,265** | -$7,934 | -$13,331 |
| ...plus RSI < 70 for calls | 106 | 75% | **$4,464** | -$2,000 | $12,152 | $7,359 | -$2,895 | 239 | **$1,109** | -$3,355 | $4,464 |
| ...calls only | 60 | 77% | **$104** | -$2,000 | $7,919 | $4,023 | -$3,919 | 131 | **-$5,712** | -$5,816 | $104 |

**Caveats:** option prices are modeled, not quoted; real spreads on deep in-the-money QQQ options are often wider than the $0.05-$0.15/share tested here, and the daily plan's rules turn negative at 3x slippage. About two years of hourly data only. Simulated history, not a prediction.
