# QQQ Directional Signal Backtest -- 2026-09-26 06:59

> Simulated over the last 59 days of real 5-minute QQQ/SPY bars -- the longest window free intraday data allows. **This is a short out-of-sample check, not a multi-year, multi-regime backtest.** Scored on the underlying's stop/target only, not simulated option premium. See the module docstring in `qqq_iron_condor/directional_backtest.py` for exactly what this can and can't capture. Not financial advice.

_Score threshold used for CALL/PUT: +-0.50 (`Config.signal_score_threshold`, possibly overridden via `--score-threshold`)._

- **Trades:** 20 (7 calls / 13 puts)
- **Win rate:** 35.0%
- **Avg win / avg loss:** 0.45% / -0.27%
- **Expectancy (avg return per trade):** -0.02%
- **Total return (sum of per-trade %, non-compounded):** -0.35%
- **Profit factor (gross win / gross loss):** 0.90
- **Max drawdown (equity curve, percentage points):** 2.97%

| Confidence | Trades | Win rate | Expectancy |
|---|---|---|---|
| High | 4 | 25.0% | -0.09% |
| Medium | 16 | 37.5% | 0.00% |

_A profit factor and expectancy above zero here is weak evidence of an edge over a short, single-regime window -- not proof. Re-run this periodically as more history accumulates, and treat a single run's numbers with real skepticism until they're consistent across several independent windows._
