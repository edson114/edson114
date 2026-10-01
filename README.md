# Trading Tools

Automated, decision-support tools for options trading, off free market
data:

1. **[QQQ Iron Condor Scanner](#qqq-iron-condor-scanner)** — a daily,
   range-bound premium-selling scan.
2. **[QQQ Directional Signal](#qqq-directional-signal-buy-callsputs)** — an
   on-demand (re-runnable through the session) CALL/PUT/NO-TRADE signal
   for buying calls or puts.
3. **[Small-Cap Momentum Options Scanner](#small-cap-momentum-options-scanner-buy-callsputs)**
   — a daily scan of low-float, high-relative-volume small caps, scored
   CALL/PUT/NO-TRADE with a suggested short-dated contract.
4. **[Kotegawa-Style 25-Day MA Reversion](#kotegawa-style-25-day-ma-reversion-stocks)**
   — a stock (not options) buy-the-panic scanner plus a portfolio backtester.

> **Not financial advice.** All four are decision-support tools, not
> auto-traders — none of them ever places orders. Always verify strikes,
> prices, and greeks against your broker's live quotes before trading, and
> see each tool's own limitations section below. **No signal in this repo
> is, or claims to be, close to "99% profitable" — no legitimate
> day-trading system is.** These structure the same inputs a discretionary
> trader would check, consistently; they don't promise a win rate.

## QQQ Iron Condor Scanner

An automated daily scanner for trading **QQQ iron condors**. Every trading
day (scheduled around market open), it pulls price action, technical
indicators, volatility data, and news, then proposes concrete iron condor
strikes for a same-day (0DTE), a weekly, and a monthly expiration.

## What it does

Each run:

1. **Market snapshot** — QQQ price action, trend classification (trend vs.
   range via ADX), RSI(14), SMA20/50/200, EMA9/21, MACD, Bollinger Bands,
   ATR(14), 20-day historical volatility, and 20/50-day support & resistance.
2. **Volatility regime** — VIX level and its percentile rank over the
   trailing year, used as a market-wide IV proxy (cheap vs. rich premium).
3. **News & catalysts** — pulls headlines from free RSS feeds (Yahoo
   Finance, CNBC, MarketWatch) and flags ones mentioning known
   market-moving topics (FOMC, CPI, jobs data, mega-cap earnings,
   geopolitics) so you know when to sit out or size down.
4. **Iron condor construction** — for a same-day (0DTE), weekly (~5-10 DTE),
   and monthly (~28-45 DTE) expiration, it selects short strikes closest to
   a target delta (default ~0.16 for weekly/monthly, ~0.10 for 0DTE),
   adds protective long strikes ($5 wide for weekly/monthly, $2 wide for
   0DTE), and reports net credit, max profit/loss, breakevens, return on
   risk, and an approximate probability of profit. 0DTE only appears if
   QQQ actually lists a same-day expiration when the scan runs; it is
   never approximated with a later expiration under the "0DTE" label.
   Delta comes from a real broker-computed greek when `TRADIER_TOKEN` is
   set (see [Data providers](#data-providers) below), otherwise from a
   Black-Scholes approximation whose time-to-expiry for 0DTE is computed
   from actual clock time remaining until the 4:00pm ET close, not a
   fraction of a calendar day.
5. **Trade gate (skip-day rules)** — a SKIP/OK verdict at the top of the
   report, evaluating: a scheduled macro event (FOMC/CPI, from a
   user-maintained calendar), an opening/pre-market gap beyond a threshold,
   and whether VIX is at/above a spike level at scan time (hard gates --
   any one triggers SKIP), plus a trending-day-with-above-average-volume
   proxy and headline catalyst hits (soft flags -- shown, not forced). See
   [How the trade gate works](#how-the-trade-gate-works) for what each
   condition can and can't actually detect from a single morning scan.
6. **Risk management notes** — position sizing, profit-taking, and
   adjustment guidance, plus a reminder to avoid new positions right before
   flagged catalysts.

The full report is written to `reports/YYYY-MM-DD.md` and, when
`GITHUB_TOKEN` is available, also posted as a GitHub Issue (labeled
`qqq-scan`) so you get a notification without configuring any secrets.

## Running it

```bash
pip install -r requirements.txt

# Live scan (requires internet access to Yahoo Finance / RSS feeds)
python -m qqq_iron_condor.scan

# Offline smoke test with synthetic data (no network needed)
python -m qqq_iron_condor.scan --self-test

# Skip GitHub issue creation
python -m qqq_iron_condor.scan --no-issue
```

Run the test suite with:

```bash
pip install pytest
python -m pytest tests/ -q
```

## Automation

`.github/workflows/qqq-iron-condor-scan.yml` runs the scan on a cron
schedule (weekdays, ~9:30am ET) via GitHub Actions, commits the generated
report into `reports/`, and opens a GitHub Issue with the full analysis
using the repo's built-in `GITHUB_TOKEN` — no extra secrets required. You
can also trigger it manually from the Actions tab (`workflow_dispatch`).

To get notified, watch this repository (or just the `qqq-scan` label) so
new issues land in your GitHub notifications/email.

## Data providers

By default the scan uses **yfinance** (free, no signup) for price history
and option chains. Delta is then a Black-Scholes approximation derived from
the chain's own implied volatility field -- which is exactly the thing that
caused this project's worst bugs: in the first several minutes after the
open, per-contract IV on free data often hasn't populated reliably yet,
producing deltas near 0.000 or picking strikes far from any sane target.
Two sanity checks (`strategy.build_iron_condor`) catch and flag this when
it happens, but they're a safety net, not a fix for the root cause.

Set the `TRADIER_TOKEN` environment variable (or repo secret, for the
GitHub Actions workflow) to switch to **Tradier** instead:

```bash
export TRADIER_TOKEN=your-token-here
python -m qqq_iron_condor.scan
```

Tradier returns real bid/ask and **broker-computed greeks** (delta, gamma,
theta, vega -- via ORATS) with every quote, so the app uses that delta
directly instead of re-deriving it from IV. This removes the whole class
of "IV hasn't populated yet" artifacts, not just the ones the sanity
checks happen to catch.

Getting a token:
- A free **developer sandbox** account (no funded brokerage required) at
  [tradier.com](https://tradier.com) gives 15-minute-delayed data --
  plenty for a once-daily scan. Generate a token at
  [web.tradier.com/user/api](https://web.tradier.com/user/api) and set
  `TRADIER_BASE_URL=https://sandbox.tradier.com/v1` alongside it.
- A funded/linked brokerage account gives real-time data against the
  default production URL (`https://api.tradier.com/v1`) with no extra
  configuration needed.

Every report states which provider produced it. The Tradier integration
(`qqq_iron_condor/tradier.py`) is built from Tradier's documented response
shapes and covered by parsing tests, but hasn't been exercised against a
live account from this codebase -- if `get_vix_history()` fails, the VIX
symbol convention (`Config.tradier_vix_symbol`, default `"VIX"`) is the
first thing to check.

## How strikes are chosen

- **Short strikes**: closest available strike to a target absolute delta
  (`Config.short_delta_target`, default `0.16`) on each side -- a real
  broker-computed delta when Tradier is active, otherwise a Black-Scholes
  approximation from the option chain's own implied volatility.
- **Long strikes**: `Config.wing_width` (default `$5`) beyond each short
  strike, snapped to the nearest listed strike — this caps max loss
  (defined risk).
- **Credit / max loss / max profit / breakevens**: computed from mid
  prices (`(bid+ask)/2`, falling back to last price) of all four legs.
- **Probability of profit**: approximated as `1 - (|short put delta| +
  short call delta)` — a common rule-of-thumb, not an exact calculation.

Tune `qqq_iron_condor/config.py` to change the delta target, wing width,
DTE windows, risk-free rate, or news sources/keywords. Each entry in
`expiration_targets` is an `ExpirationTarget` and can override the delta
target and wing width per expiration (0DTE already does this).

## How the trade gate works

Every report opens with a `🛑 SKIP TODAY` or `✅ OK to trade` verdict
(`qqq_iron_condor/gates.py`), implementing: skip on FOMC days, CPI prints,
surprise macro news, a strong pre-market gap, a trending day with
above-average volume, or a VIX spike. Not all of those are equally
checkable from one point-in-time morning scan, so the gate splits them:

**Hard gates** (any one → SKIP):
- **Scheduled macro event** — today's date matches an entry in
  `Config.macro_event_dates` (a plain `{"YYYY-MM-DD": "label"}` dict you
  maintain yourself). There is no live paid economic-calendar API wired
  in; keep this updated from
  [federalreserve.gov's FOMC calendar](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)
  and [bls.gov's CPI release schedule](https://www.bls.gov/schedule/news_release/cpi.htm).
  Left empty by default — until you fill it in, this check never fires.
- **Gap vs. prior close** — `Config.gap_threshold_pct` (default `0.8`).
  Once the market has opened and today's daily bar exists, this is a
  confirmed move from the prior close; before the open, it falls back to
  a live quote for an indicative (unconfirmed) pre-market gap.
- **VIX spike** — `Config.vix_spike_threshold` (default `20.0`), checked
  against VIX's level **at scan time**. This cannot detect a spike that
  develops intraday after the report has already run.

**Soft flags** (shown, don't force a skip alone):
- **Trending + above-average volume** — `Config.adx_trend_threshold` and
  `Config.volume_ratio_threshold` (default `1.3x`), based on the most
  recently *completed* session's ADX and volume vs. its 20-day average.
  This is a leading-indicator proxy, not live intraday volume — a single
  morning scan can't yet know today's full-day volume or how the trend
  develops.
- **Catalyst headlines** — any keyword hit from the existing news scan.
  "Surprise macro news" is unscheduled by definition; this is the best
  available real-time proxy, not a guarantee of catching one.

If you want the trending/volume or catalyst signals to force a hard skip
too, that's a one-line change in `gates.evaluate_gates` — they're kept
soft by default because the catalyst keyword list is broad enough (mentions
of "earnings", "fed", individual mega-caps, etc.) to fire on most days,
which would make a hard skip on any hit too aggressive to be useful.

## Trade Journal

The scanner and backtest both recommend/simulate -- neither ever knows
what you actually traded. `qqq_iron_condor/journal.py` is a lightweight,
git-diffable record (a CSV at `journal/trades.csv`) of the iron condors
you actually opened, so realized win rate/expectancy/drawdown can be
tracked over time against your real fills, not a theoretical strike.
Nothing is logged automatically -- you (or a chat session helping you)
add a trade when you place it and close it when you exit it:

```bash
# Log a trade you just opened (use your actual broker fill numbers)
python -m qqq_iron_condor.journal add --label Weekly --expiration 2026-10-09 --dte 8 \
    --short-call 760 --long-call 765 --short-put 720 --long-put 715 \
    --credit 1.35 --entry-underlying 740.50 --vix 15.2

# Close it out early (paid a debit to buy back the condor)
python -m qqq_iron_condor.journal close --id 1 --exit-debit 0.35

# ...or let it expire and settle against the underlying's price at expiration
python -m qqq_iron_condor.journal close --id 1 --settle-underlying 742.10

python -m qqq_iron_condor.journal list      # open positions
python -m qqq_iron_condor.journal report    # realized win rate / expectancy / drawdown, by label
```

Realized P&L is computed the same way the backtest settles a simulated
trade (intrinsic value of each spread at the given price, capped at its
wing width), so real and simulated performance stay directly comparable.

## Backtesting

Before scaling size on this rule, it's worth knowing its actual historical
edge rather than trusting the delta-target's textbook ~84% theoretical POP.
`qqq_iron_condor/backtest.py` runs the exact same `strategy.build_iron_condor`
the live scanner uses, mechanically, over years of history:

```bash
# 3 years, all configured expiration labels (0DTE/Weekly/Monthly)
python -m qqq_iron_condor.backtest --years 3

# One label only, gates disabled (see every mechanical trade, gated or not)
python -m qqq_iron_condor.backtest --years 2 --labels Weekly --no-gates
```

It reports, per expiration label: win rate, average win/loss, expectancy
(average P&L per trade), total P&L, profit factor, max drawdown on the
resulting equity curve, and a breakdown by VIX-percentile regime (Low/
Normal/High IV) so you can see whether the edge holds up in calm markets
and falls apart in stressed ones, or vice versa. The report is printed and
saved to `reports/backtests/YYYY-MM-DD.md`.

**Why it's a simulation, not a replay:** there is no free source of
historical QQQ option-chain data (strike-level bid/ask/IV by date) — that
requires a paid feed (CBOE DataShop, ORATS historical, Polygon.io). Free
data only covers historical *underlying* prices and VIX. So the backtest
instead prices a full synthetic chain at every entry/exit with
Black-Scholes, using VIX as a flat (no skew, no term structure)
implied-vol proxy — the same technique the app's own `--self-test` uses.
Concretely, this means:
- It's a real test of the delta-targeting *mechanics* against real
  historical price/vol regimes, including real historical gap days and
  VIX spikes (both hard gates are backtested for real).
- It does **not** capture bid/ask slippage, commissions, early assignment,
  or real strike-level liquidity/skew — actual fills will run worse than
  these simulated mid-price settlements.
- The scheduled-macro-event hard gate only fires for dates present in
  `Config.macro_event_dates`, which today only covers 2026 — it won't
  suppress entries around, say, a 2023 FOMC day.
- Trades are non-overlapping per label (a new one only opens after the
  prior one of that label has settled), so drawdown reflects one strategy
  "slot" run sequentially, not several simultaneously open positions.

Treat the output as a bound on the strategy's mechanical edge under
simplified volatility assumptions, not a prediction of live results.

## Limitations & caveats

- Uses free Yahoo Finance data via `yfinance`; option chain liquidity and
  quote freshness vary, especially for far-dated or wide-strike contracts.
  Per-contract implied volatility can be unreliable both before the open
  (stale/untraded quotes) and for the first several minutes after it
  (many strikes haven't traded yet), which can make the delta-targeted
  strike search land far from its target. `build_iron_condor` has two
  sanity checks for this -- ATM IV implausibly low vs. trailing realized
  volatility, and selected short strikes landing far below the delta
  target -- both surface as an explicit warning on the trade rather than
  a silently wrong recommendation, but neither guarantees clean data; if
  you see a warning, re-pull quotes before trusting the strikes.
- Without `TRADIER_TOKEN` set, delta/greeks are Black-Scholes approximations
  from chain IV, not live broker greeks -- see [Data providers](#data-providers).
  Even with real greeks, this is a bigger caveat for 0DTE, where real-world
  intraday gamma/pin risk near the short strikes is severe and not fully
  captured by a static delta snapshot from when the scan ran.
- News/catalyst detection is a keyword scan over a handful of free RSS
  feeds — it is **not** a substitute for checking an economic calendar
  (FOMC/CPI/NFP dates, earnings dates for QQQ's mega-cap holdings) before
  trading.
- IV rank is approximated via the VIX's 1-year percentile as a market-wide
  proxy; it is not QQQ-specific historical IV.
- This tool never places trades — it only produces an analysis/report.
- The [backtest](#backtesting) is a Black-Scholes simulation driven by real
  historical underlying/VIX data, not a replay of real historical option
  quotes — see that section for exactly what it can and can't capture.
- The [trade journal](#trade-journal) only knows what's logged into it —
  a trade you forget to record or close simply isn't reflected in its
  realized-performance stats.

## QQQ Directional Signal (Buy Calls/Puts)

An on-demand CALL / PUT / NO TRADE signal for buying (not selling) QQQ
options intraday. Unlike the once-daily iron condor scan, it's meant to be
re-run through the session — each run reads the *current* intraday bars, so
the read can change as price action develops.

Each run:

1. **Reuses the same daily market snapshot** as the iron condor scan
   (trend, RSI, MACD, ADX, ATR, VIX regime) for the higher-timeframe read.
2. **Pulls today's intraday bars** (5-minute, via `yfinance`) for QQQ and
   SPY and computes VWAP, an opening-range breakout/breakdown/inside read,
   a short-window EMA9/EMA21 trend, intraday RSI(14), and QQQ's
   relative strength vs. SPY today (tech-specific strength/weakness vs. a
   broad market move).
3. **Runs the same hard skip-day gate** as the iron condor scan (scheduled
   FOMC/CPI, a confirmed/indicated gap beyond threshold, or a VIX spike) —
   any of those forces **NO TRADE** regardless of how bullish/bearish the
   technicals look.
4. **Scores seven weighted signals** (daily trend, MACD histogram, daily
   RSI, VWAP position, intraday EMA trend, opening-range breakout, and
   QQQ-vs-SPY relative strength) into a single **-1..+1 composite score**.
   At/above `Config.signal_score_threshold` (default `+0.30`) → **CALL**;
   at/below `-0.30` → **PUT**; otherwise **NO TRADE** (no edge / choppy).
5. **Selects a near-ATM contract** (target absolute delta
   `Config.directional_delta_target`, default `0.45`, vs. the iron condor's
   0.16 short-strike target) for both a 0DTE and a weekly expiration when
   actionable — near-ATM so the premium is responsive to the move rather
   than mostly extrinsic value.
6. **Proposes a trade plan**: an underlying stop-loss level, and a target
   at `Config.reward_risk_ratio` × the stop distance. The stop is the
   opening-range extreme *only when that's at least as tight as*
   `Config.stop_atr_multiple` × ATR14 (default `0.35`) — otherwise it
   falls back to the ATR-based distance, so a wide early range can't push
   the stop (and, scaled off it, the target) so far away that a single
   session has no realistic room to reach either.

The full report is written to `reports/signals/YYYY-MM-DD-HHMM.md` and,
when `GITHUB_TOKEN` is available, also posted as a GitHub Issue — but
**only when the signal is actionable** (CALL or PUT), to avoid spamming an
issue every time it's re-run and lands on NO TRADE.

### Running it

```bash
# Live signal (requires internet access to Yahoo Finance / RSS feeds)
python -m qqq_iron_condor.signal

# Offline smoke test with synthetic data (no network needed)
python -m qqq_iron_condor.signal --self-test

# Skip GitHub issue creation
python -m qqq_iron_condor.signal --no-issue
```

### How the score is built

Each of the eight components below contributes `weight × value` (value in
`[-1, +1]`) to the composite score; weights are renormalized to sum to 1.0
(raw defaults shown don't need to sum to 1.0 themselves):

| Component | Raw weight | Bullish when... |
|---|---|---|
| Daily trend | 0.20 | Uptrend (spot > SMA50 > SMA200, MACD hist > 0) |
| MACD histogram | 0.10 | Positive |
| Daily RSI(14) | 0.10 | Above 50, scaled (extremes noted, not flipped) |
| VWAP position | 0.20 | Price above VWAP |
| Intraday EMA9/21 | 0.15 | EMA9 > EMA21 |
| Opening-range breakout | 0.15 | Price above the opening-range high, **scaled by relative volume** |
| QQQ vs. SPY relative strength | 0.10 | QQQ outperforming SPY today |
| Options call/put volume skew | 0.15 | More call volume than put volume across the fetched chains |

**Options flow is a real, freely-available proxy — not literal "whale" or
dark-pool tracking.** Genuine institutional order-flow data (block trades,
dark-pool prints, sweep detection) is a paid product (Unusual Whales,
FlowAlgo, and similar); this app has no access to that and doesn't
pretend to. What it *does* compute is `call_volume - put_volume` divided
by their sum, summed across every option chain already fetched for
contract selection (`options_flow.compute_call_put_skew`) — the same
public per-contract volume those paid services are themselves built
from, minus their block-trade detection and historical baselining. It's
one more legitimate input, not a leak into institutional positioning.
**It cannot be backtested with free data** — yfinance only ever serves a
current option-chain snapshot, never historical per-contract volume — so
`directional_backtest.py` always scores it neutral (0.0); only the live
signal exercises it for real.

**Opening-range breakout is volume-scaled, not just a raw breakout read.**
A backtest found the raw signal anti-correlated with returns (see
[Backtesting](#backtesting-1)) — a breakout without real participation
behind it is usually a fakeout. Each session's cumulative volume-so-far is
compared to the historical average at the same point in the session
(same bar count elapsed, not clock time) over the trailing
`Config.volume_profile_period` (default 20 trading days): at or above
that average pace, the breakout keeps full strength; below it, the
component is scaled down (floored at 20% strength, never fully zeroed).
See `intraday.compute_relative_volume` and `direction._orb_component`.

Tune the weights via `Config.component_weights` (always renormalized to
sum to 1.0 before use, so zeroing one redistributes its share to the rest
rather than just shrinking the max possible score), and the delta target,
stop/target multiples, and score threshold, all in
`qqq_iron_condor/config.py`.

### Backtesting

`qqq_iron_condor/directional_backtest.py` replays the exact, unmodified
`direction.build_directional_signal` (and `analysis.build_snapshot`,
`intraday.build_intraday_snapshot`, `gates.evaluate_gates`) over real
historical bars, so this is not a separate re-implementation of the rule
that could silently drift from what the live signal does:

```bash
# Backtest the last 59 days of real 5-minute QQQ/SPY bars (yfinance's cap)
python -m qqq_iron_condor.directional_backtest

# Fewer days, custom output location
python -m qqq_iron_condor.directional_backtest --lookback-days 30 --output-dir /tmp/bt

# Try a stricter score threshold than the live default (0.30) and compare
python -m qqq_iron_condor.directional_backtest --score-threshold 0.5

# Zero out one or more components (redistributes their weight to the
# rest) to test whether dropping them helps
python -m qqq_iron_condor.directional_backtest --zero-components orb
```

Or trigger it on a GitHub-hosted runner (useful if you don't have a local
Python/network setup): `.github/workflows/qqq-directional-backtest.yml`,
`workflow_dispatch` only, with `lookback_days`, `score_threshold`, and
`zero_components` inputs — run it manually from the Actions tab.

It reports trades taken (calls vs. puts), win rate, average win/loss,
expectancy, total return, profit factor, max drawdown, and a breakdown by
confidence level (High vs. Medium). Trades are scored on the **underlying's**
stop/target, not simulated option premium — there's no free source of
historical QQQ option chains, intraday or otherwise. Report saved to
`reports/backtests/directional-YYYY-MM-DD.md`, and every trade's entry/exit,
outcome, and each score component's contribution at entry is saved
alongside it as `reports/backtests/directional-trades-YYYY-MM-DD.csv` —
useful for diagnosing *why* a run came out the way it did (e.g. does one
component correlate with the losing trades?) rather than only seeing the
aggregate.

**What backtesting this has already found, and what changed as a result**
(each finding came from a real run over the last available ~59 days, not a
synthetic test):

1. A first run at the default 0.30 threshold found a *negative*
   expectancy (profit factor 0.44). Raising the threshold to 0.50
   narrowed the loss (profit factor 0.90) but didn't flip it positive.
2. Exporting and reading the per-trade CSV found the real culprit for
   most of that: **80% of trades never reached their stop or target at
   all** — they drifted to an arbitrary end-of-day price instead of being
   managed by the plan, because the ATR-scaled stop/target distance was
   wide enough that a single session rarely had room to reach either.
   `stop_atr_multiple` was tightened `0.75` → `0.35`, and the
   opening-range stop is now capped at that ATR-based distance instead of
   being used unconditionally. Re-running at the *same* 0.30 threshold
   after that fix: eod-exits dropped from ~80% to 52%, and profit factor
   improved 0.44 → 0.57 — a real, verified mechanical fix, but still not
   a profitable system on its own.
3. Correlating each component's contribution (sign-flipped for puts, so
   positive always means "agreed with the trade") against realized
   returns, pooled across the two backtest runs above (n=74 trades): the
   **opening-range breakout (`orb`) component came out anti-correlated**
   with returns (r ≈ -0.3 to -0.4, the strongest and most consistent
   signal of any component, in the *wrong* direction) — trades where ORB
   agreed with the trade direction tended to do *worse*. `vwap` showed a
   smaller version of the same pattern; `rsi` and `relative_strength`
   were mildly positive. Zeroing `orb` out entirely nudged profit factor
   up a little more (0.57 → 0.61) but stayed short of breakeven, and win
   rate actually dipped slightly — a marginal, inconclusive result on its
   own, not a fix.
4. Rather than just drop the component, `orb`'s raw signal is now scaled
   by relative volume (`intraday.compute_relative_volume`,
   `direction._orb_component`): a breakout backed by at/above the
   historical average volume for that point in the session keeps full
   strength, a breakout on unusually light volume is faded down (floored
   at 20%, never fully zeroed). This targets the likely *mechanism* behind
   finding 3 — a fakeout breakout typically lacks participation — rather
   than discarding the signal outright.

5. Re-running the backtest with volume-scaling live: profit factor came
   out at 0.59 — almost exactly between the raw-`orb` baseline (0.57) and
   zeroing it entirely (0.61). **No detectable improvement over either.**
   The fakeout-lacks-volume hypothesis was reasonable and worth testing;
   the data says it isn't the fix.

**Where this leaves the directional signal, honestly:** across five
consecutive experiments on the same ~59-day window — threshold, stop/target
sizing, `--zero-components`, and volume-scaling — exactly one produced a
clear, mechanically-verified improvement (the stop/target fix, since it
addressed a real bug: trades weren't being managed by their plan at all).
Everything downstream of that has moved profit factor within a narrow
0.57–0.61 band, never crossing the 1.0 breakeven line, and the differences
between those attempts are smaller than you'd expect from noise alone at
n≈50-54 trades. Further tuning against this same window is likely to keep
producing similarly inconclusive nudges rather than a real answer — the
more informative next step is a fresh backtest window once more trading
days accumulate, or a genuinely different data source (see below), not
another reweight of these same seven inputs.

**Why this is a ~60-day check, not a multi-year backtest:** the iron
condor backtest above only needs daily bars, so it can run over years.
This signal is fundamentally intraday (VWAP, opening range, a 5-minute
EMA9/21), and free intraday history from yfinance is capped at the last
~60 days for 5-minute bars — there's no way around that with free data.
Concretely:
- A positive expectancy/profit-factor here is weak evidence of an edge
  over one short window in one volatility regime — not proof. Re-run
  periodically and look for consistency across several independent
  windows before trusting it with size.
- The soft catalyst/news flag can't be backtested (historical RSS content
  can't be replayed), so it's always empty here — only the hard gates
  (scheduled macro event, gap, VIX spike) are tested for real.
- VIX is daily-resolution only, so the gate's "current" VIX level at any
  intraday timestamp is the prior day's close, not a live intraday print.
- A bar whose range spans both the stop and the target is conservatively
  resolved as the stop hitting first — real intrabar order is unknown.

See the module docstring in `qqq_iron_condor/directional_backtest.py` for
the full list of what this can and can't capture.

### Limitations & caveats

- This is a **rules-based checklist**, not a backtested-for-edge or
  machine-learned model. The score is not a probability of profit — treat
  it the way you'd treat a discretionary trader's structured opinion, one
  input among several. See [Backtesting](#backtesting-1) for a first,
  honest read on whether it has any measured edge at all.
- Same caveats as the iron condor scan apply: free `yfinance` data can be
  stale/illiquid (especially right after the open), the macro calendar is
  a user-maintained snapshot (not a live paid feed), and news/catalyst
  detection is a keyword scan over a handful of free RSS feeds.
- **0DTE prices move fast.** If you're reading a saved report more than a
  few minutes after it ran, re-pull the underlying price, VWAP, and option
  quotes before acting on it — don't trade a stale signal.
- Delta/greeks are Black-Scholes approximations from chain IV, not live
  broker greeks.
- The options-flow component is put/call **volume**, not confirmed
  institutional positioning — it mixes retail and institutional activity
  indiscriminately, has no block-trade or dark-pool detection, and can't
  be backtested with free data (see [How the score is built](#how-the-score-is-built)).
  Don't read a skewed reading as "the whales are buying."
- This tool never places trades — it only produces an analysis/report.

## Small-Cap Momentum Options Scanner (Buy Calls/Puts)

A daily scan that formalizes a *style* of small-cap options trading
publicly associated with traders like Alex Temiz — low-float, low-to-mid
priced stocks that gap or spike on a catalyst with unusually heavy volume,
traded with cheap, short-dated (weekly) OTM calls on continuation or puts
on a failed breakout/fade — into an objective checklist over free data.

> **This is not a reproduction of any specific trader's actual rules, risk
> management, or track record** — those aren't publicly documented in
> enough detail to replicate. It structures the same *kind* of inputs
> (float, relative volume, gap size, VWAP position, news catalyst) a
> discretionary small-cap momentum trader would look at, consistently, and
> nothing more. Low-float small caps are extremely volatile and often
> illiquid; short-dated OTM options on them routinely lose their entire
> premium in minutes and can be hard to exit at a fair price. This tool
> never places orders.

### What it does

Each run:

1. **Universe** — pulls Yahoo Finance's free screener (`small_cap_gainers`,
   `aggressive_small_caps`, `most_shorted_stocks` by default), plus any
   tickers listed in the `SMALLCAP_SYMBOLS` env var (comma-separated),
   always included regardless of what the screener returns.
2. **Cheap prefilter** — price band ($1–$20 by default), market cap cap,
   minimum day volume, minimum relative volume vs. the 3-month average,
   and a minimum absolute %-move — all from fields the screener already
   returned, no extra network calls.
3. **Deep scan** (capped to a bounded number of symbols per run) — pulls
   float shares and 20-day average dollar volume per surviving candidate,
   filtering out large-float or illiquid names.
4. **Directional score** — blends today's move, position vs. intraday
   VWAP, whether the move is still extending or fading off the session
   high/low, relative volume, and a news catalyst hit into a single
   -1..+1 score → CALL / PUT / NO TRADE. Critically, the *sign* of the
   gap/relative-volume/catalyst components follows the current
   extension/VWAP lean, not the raw day's %change — so a stock that's
   still up 40% on the day but visibly rolling over scores as a PUT setup,
   not a CALL.
5. **Contract selection** — the nearest listed expiration at/under
   `Config.max_dte` (short-dated, weekly-style), nearest strike to a
   target ~7% out-of-the-money, skipped if nothing in the chain clears a
   minimum open-interest and maximum bid/ask-spread bar.
6. **Per-candidate gates** — hard-skips a CALL/PUT score with no tradable
   contract; soft-flags unknown float, earnings within 5 days (IV
   crush/gap risk on a short-dated contract), and no catalyst found.
7. **Report** — ranked candidate table, detailed score breakdown and
   suggested contract for the top setups, and risk-management notes
   (position sizing as a small, defined-risk bet; planning the exit/taking
   profit quickly; respecting bid/ask spread as a real cost).

### Running it

```bash
pip install -r requirements.txt

# Live scan (requires internet access to Yahoo Finance / RSS feeds)
python -m smallcap_options.scan

# Offline smoke test with synthetic data (no network needed)
python -m smallcap_options.scan --self-test

# Skip GitHub issue creation
python -m smallcap_options.scan --no-issue

# Always include specific tickers regardless of the screener
SMALLCAP_SYMBOLS=ABCD,WXYZ python -m smallcap_options.scan
```

`.github/workflows/smallcap-options-scan.yml` runs this on a cron schedule
(weekdays, ~10:05am ET — deliberately after the QQQ scan's open-time slot,
since this style needs the first chunk of the session to show real
relative volume and a gap hold/fade before it's worth scoring), commits
the generated report into `reports/smallcap/`, and opens a GitHub Issue
(labeled `smallcap-scan`) the same way the other two scans do. An optional
repository variable, `SMALLCAP_SYMBOLS`, is passed through the same way.

### Limitations & caveats

- The universe comes from Yahoo Finance's free, unofficial screener
  endpoint — it can rate-limit, change shape, or simply miss a name a
  paid real-time scanner (Trade Ideas, Benzinga Pro, etc.) would have
  caught. This is not a substitute for a real-time Level 2/scanner feed,
  which is what this style of trading is normally built around.
  Individual per-symbol calls in the deep scan are similarly best-effort;
  a symbol that fails any one of them is dropped from that run rather
  than aborting the whole scan.
- Float shares and news catalysts come from free, best-effort sources and
  are frequently stale, missing, or wrong for thinly-covered small caps —
  always verify float manually before trading.
- The directional score is a rules-based checklist, not backtested
  against real historical fills (unlike the QQQ directional signal, which
  has a `directional_backtest.py` — see [Backtesting](#backtesting-1));
  it does not claim a win rate.
- Contract selection is entirely mechanical (nearest strike to a target %
  OTM, nearest expiration under a DTE cap) — it does not try to identify
  the "best" contract, and IV/greeks are Black-Scholes approximations
  from the chain's own quoted IV, not live broker greeks.
- This tool never places trades — it only produces an analysis/report.

## Kotegawa-Style 25-Day MA Reversion (Stocks)

Takashi Kotegawa ("BNF") is publicly known for one idea: buy stocks that
panic-sell far below their **25-day moving average** (a big negative
*kairi*, or deviation rate), sell fast on the rebound toward that average,
and cut anything that keeps falling without hesitation. This tool turns
that idea into fixed rules over free daily bars. It is a reconstruction of
a publicly described *style*, not his actual trades or thresholds.

**Rules (all in `kotegawa_reversion/config.py`):**

| | Default |
|---|---|
| Entry | close ≤ **-12%** vs 25-day MA, green candle (close > open), not below -45% (likely a fundamental break), price ≥ $5, 20d avg $ volume ≥ $50M |
| Fill | next day's open (no look-ahead), deepest deviation first |
| Take profit | close back within 4% of the MA |
| Stop loss | 7% below the fill (gap-downs fill at the open) |
| Time stop | exit at the close after 7 trading days |
| Sizing | 5 slots × 20% of equity, 10 bps cost per side |

The daily scan also reports **market breadth**, the share of the universe
at or below the entry deviation. Kotegawa's biggest wins came in
market-wide panics, when many names were oversold at the same time.

### Running it

```bash
# Today's buy list (default: 72 liquid US large caps)
python -m kotegawa_reversion.scan

# Portfolio backtest vs SPY buy & hold
python -m kotegawa_reversion.scan --backtest --period 10y

# Your own universe / thresholds
python -m kotegawa_reversion.scan --symbols AAPL,NVDA,TSLA --entry-deviation 0.15 --stop-loss 0.10

# Offline smoke test with synthetic data (results are meaningless)
python -m kotegawa_reversion.scan --self-test
```

Reports are written to `reports/kotegawa/`. Set `KOTEGAWA_SYMBOLS` to
replace the default universe.

### What the backtest actually shows (read this)

On the default 72-stock universe (as of 2026-10-01):

| Period | Strategy (defaults) | Max DD | SPY buy & hold | SPY max DD |
|---|---:|---:|---:|---:|
| 5y | +13.2% | -27.8% | +87.8% | -24.5% |
| 10y | +12.7% | -36.3% | +313.9% | -33.7% |

Across a sweep of 96 parameter combinations over 10 years, **none beat
holding SPY**. The best combination (-10% entry, no stop, 15-day hold, no
green-candle filter) made +252% with a -60% drawdown, against SPY's +314%
and -34%. That best result is also cherry-picked after the fact. The
defaults were deliberately **not** re-tuned to fit this history.

Why it struggles here:

- **Different market.** Kotegawa traded early-2000s Japanese stocks with
  retail-driven panics and much less algorithmic competition. US large-cap
  mean reversion at this horizon is heavily arbitraged.
- **Survivorship bias flatters even these numbers.** The universe is
  *today's* winners, which by definition recovered from every past drop.
- **Cash drag.** The strategy is usually flat, waiting for a panic, while
  the index compounds.

Use it as a disciplined watchlist for panic dips with built-in exits, not
as a replacement for an index fund.

## Project layout

```
qqq_iron_condor/
  config.py               # all tunable parameters
  data.py                 # yfinance-backed price/intraday history, option chains, news
  tradier.py              # Tradier-backed price history, option chains (real greeks)
  providers.py            # picks data.py or tradier.py based on TRADIER_TOKEN
  news.py                 # catalyst keyword flagging
  gates.py                # trade gate: skip-day rules (hard gates + soft flags)
  indicators.py           # RSI, EMA/SMA, MACD, Bollinger Bands, ATR, ADX, HV
  intraday.py             # VWAP, opening-range, intraday EMA/RSI, relative strength
  options_math.py         # Black-Scholes delta/price helpers (fallback provider)
  analysis.py             # turns raw data into the daily market snapshot
  strategy.py             # iron condor strike selection & trade math
  backtest.py             # historical backtest: replays the delta-target rule via simulated BS chains
  journal.py              # trade journal: logs real fills, computes realized performance
  direction.py            # directional (buy calls/puts) scoring & contract selection
  options_flow.py         # call/put volume skew read (not backtestable -- live-only)
  directional_backtest.py # replays the directional signal over real 5-min bars (~60d, yfinance's cap)
  report.py               # iron condor Markdown report rendering
  signal_report.py        # directional signal Markdown report rendering
  scan.py                 # iron condor CLI entrypoint / orchestration
  signal.py               # directional signal CLI entrypoint / orchestration
tests/
  test_pipeline.py        # iron condor offline smoke test (synthetic data)
  test_signal_pipeline.py # directional signal offline smoke test (synthetic data)
  test_direction.py       # directional scoring unit tests
  test_intraday.py        # VWAP/opening-range/EMA unit tests
  test_options_flow.py    # call/put volume skew unit tests
  test_strategy.py        # strike selection, sanity checks, real-vs-BS delta
  test_gates.py           # trade gate unit tests
  test_tradier.py         # Tradier response-parsing tests (mocked HTTP)
  test_backtest.py        # backtest simulation mechanics (settlement math, gating, non-overlap)
  test_directional_backtest.py  # directional backtest mechanics (touch detection, gating, aggregation)
  test_journal.py         # trade journal CSV persistence & realized-performance stats
smallcap_options/
  config.py               # all tunable parameters (universe filters, score weights, contract selection)
  data.py                 # yfinance-backed screener, price/intraday history, option chains, float, news
  universe.py             # cheap prefilter + deep (float/liquidity) scan over the screener universe
  news.py                 # per-symbol + keyword-matched catalyst detection
  intraday.py             # VWAP, session high/low, extension-off-high/low
  direction.py            # CALL/PUT/NO-TRADE scoring (gap, VWAP, extension, relative volume, catalyst)
  strategy.py             # short-dated OTM contract selection with a liquidity/spread gate
  gates.py                # per-candidate trade gate (no tradable contract, float unknown, earnings-soon, no catalyst)
  options_math.py         # Black-Scholes delta/price helpers
  report.py               # Markdown report rendering
  scan.py                 # CLI entrypoint / orchestration
tests/
  test_smallcap_universe.py  # cheap prefilter/dedup/ranking unit tests
  test_smallcap_direction.py # directional scoring unit tests (incl. the "faded gapper" PUT case)
  test_smallcap_strategy.py # contract selection unit tests
  test_smallcap_gates.py    # per-candidate gate unit tests
  test_smallcap_scan.py     # offline smoke test (synthetic data) + GitHub issue error handling
kotegawa_reversion/
  config.py               # rules, thresholds, default universe
  data.py                 # yfinance daily bars + synthetic data for --self-test
  signals.py              # 25-day MA deviation and entry rule
  backtest.py             # portfolio backtest (next-open fills, stop/target/time exits)
  report.py               # Markdown scan and backtest reports
  scan.py                 # CLI entrypoint
tests/
  test_kotegawa_reversion.py # signal rules, fill mechanics, offline smoke test
reports/                  # daily iron condor reports land here
reports/signals/          # directional signal reports land here
reports/backtests/        # backtest reports land here (iron condor + directional)
reports/smallcap/         # daily small-cap momentum options scan reports land here
journal/                  # trade journal CSV (trades.csv) lands here
.github/workflows/
  qqq-iron-condor-scan.yml
  qqq-directional-signal.yml
  smallcap-options-scan.yml
```
