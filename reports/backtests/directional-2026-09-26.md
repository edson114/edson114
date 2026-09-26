# QQQ Directional Signal Backtest -- 2026-09-26 07:28

> Simulated over the last 59 days of real 5-minute QQQ/SPY bars -- the longest window free intraday data allows. **This is a short out-of-sample check, not a multi-year, multi-regime backtest.** Scored on the underlying's stop/target only, not simulated option premium. See the module docstring in `qqq_iron_condor/directional_backtest.py` for exactly what this can and can't capture. Not financial advice.

_Score threshold used for CALL/PUT: +-0.30 (`Config.signal_score_threshold`, possibly overridden via `--score-threshold`)._

_Component(s) zeroed out for this run (via `--zero-components`): orb._

- **Trades:** 53 (27 calls / 26 puts)
- **Win rate:** 30.2%
- **Avg win / avg loss:** 0.54% / -0.38%
- **Expectancy (avg return per trade):** -0.10%
- **Total return (sum of per-trade %, non-compounded):** -5.50%
- **Profit factor (gross win / gross loss):** 0.61
- **Max drawdown (equity curve, percentage points):** 7.76%

| Confidence | Trades | Win rate | Expectancy |
|---|---|---|---|
| High | 1 | 0.0% | -0.46% |
| Medium | 52 | 30.8% | -0.10% |

_A profit factor and expectancy above zero here is weak evidence of an edge over a short, single-regime window -- not proof. Re-run this periodically as more history accumulates, and treat a single run's numbers with real skepticism until they're consistent across several independent windows._
