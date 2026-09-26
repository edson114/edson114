# QQQ Directional Signal Backtest -- 2026-09-26 06:52

> Simulated over the last 59 days of real 5-minute QQQ/SPY bars -- the longest window free intraday data allows. **This is a short out-of-sample check, not a multi-year, multi-regime backtest.** Scored on the underlying's stop/target only, not simulated option premium. See the module docstring in `qqq_iron_condor/directional_backtest.py` for exactly what this can and can't capture. Not financial advice.

- **Trades:** 46 (21 calls / 25 puts)
- **Win rate:** 37.0%
- **Avg win / avg loss:** 0.34% / -0.44%
- **Expectancy (avg return per trade):** -0.16%
- **Total return (sum of per-trade %, non-compounded):** -7.15%
- **Profit factor (gross win / gross loss):** 0.44
- **Max drawdown (equity curve, percentage points):** 8.28%

| Confidence | Trades | Win rate | Expectancy |
|---|---|---|---|
| High | 1 | 100.0% | 0.13% |
| Medium | 45 | 35.6% | -0.16% |

_A profit factor and expectancy above zero here is weak evidence of an edge over a short, single-regime window -- not proof. Re-run this periodically as more history accumulates, and treat a single run's numbers with real skepticism until they're consistent across several independent windows._
