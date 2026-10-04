# QQQ Daily $ Target Backtest -- 2026-10-04

Window: 2023-11-03 to 2026-10-02. Rules: 10 contracts, 0.80 delta, 60 DTE, +$1,000 target / -$1,000 stop / 5-session time stop, one position at a time, $50 slippage per side.
Intraday path: hourly bars; when one hour touched both levels, the OHLC path convention decides (up bar: low first, down bar: high first). "Worst-case win rate" instead scores every such tie as a stop.

| Direction | Trades | Target | Stop | Time | Win rate | Worst-case win rate | Total P&L | Avg/trade | Avg win | Avg loss | Max DD | Target same day | Avg capital |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| signal | 467 | 213 | 254 | 0 | 46% | 37% | -$40,258 | -$86 | $1,003 | -$1,000 | $44,258 | 211 of 730 days | $37,706 |
| call | 466 | 203 | 263 | 0 | 44% | 35% | -$56,227 | -$121 | $1,019 | -$1,000 | $66,227 | 199 of 730 days | $35,427 |
| put | 467 | 226 | 241 | 0 | 48% | 41% | -$15,784 | -$34 | $1,000 | -$1,003 | $23,784 | 226 of 730 days | $40,180 |

### Stop size (signal direction, same +$1,000 target)

| Stop | Trades | Target | Stop | Time | Win rate | Total P&L | Avg/trade | Avg loss | Max DD |
|---|---|---|---|---|---|---|---|---|---|
| -$500 | 470 | 146 | 324 | 0 | 31% | -$16,000 | -$34 | -$500 | $24,500 |
| -$1,000 | 467 | 213 | 254 | 0 | 46% | -$40,258 | -$86 | -$1,000 | $44,258 |
| -$2,000 | 441 | 265 | 176 | 0 | 60% | -$83,132 | -$189 | -$2,021 | $89,915 |
| -$3,000 | 421 | 291 | 130 | 0 | 69% | -$94,780 | -$225 | -$3,090 | $110,099 |
| none (5-session time stop only) | 275 | 239 | 0 | 36 | 87% | -$78,794 | -$287 | -$9,650 | $107,023 |

A wider stop (or none) raises the win rate because QQQ usually comes back far enough to tag +$1,000 -- but the losses that do happen get bigger. The total P&L column is what decides whether that trade-off pays.

**How to read this:** `signal` uses the daily half of the directional score; `call` / `put` are always-long / always-short baselines. Over a strong uptrend, `call` will look good simply because QQQ went up -- the question is whether `signal` holds up in both directions. "Target same day" counts sessions that actually closed at +$1,000 out of all trading days in the window -- that's the honest answer to how often the daily goal was met.

**Not captured:** real option quotes/IV (Black-Scholes at VIX x 1.15), the intraday half of the live signal, news, and the true intraday order of highs and lows (a day spanning both levels counts as a stop). Past results don't predict future ones.
