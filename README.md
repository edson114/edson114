# QQQ Iron Condor Scanner

An automated daily scanner for trading **QQQ iron condors**. Every trading
day (scheduled around market open), it pulls price action, technical
indicators, volatility data, and news, then proposes concrete iron condor
strikes for a same-day (0DTE), a weekly, and a monthly expiration.

> **Not financial advice.** This is a decision-support tool, not an
> auto-trader — it never places orders. Always verify strikes, prices, and
> greeks against your broker's live quotes before trading.

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
   a target delta (default ~0.16 for weekly/monthly, ~0.10 for 0DTE) using
   a Black-Scholes delta computed from each contract's implied volatility,
   adds protective long strikes ($5 wide for weekly/monthly, $2 wide for
   0DTE), and reports net credit, max profit/loss, breakevens, return on
   risk, and an approximate probability of profit. 0DTE only appears if
   QQQ actually lists a same-day expiration when the scan runs; it is
   never approximated with a later expiration under the "0DTE" label.
   0DTE's Black-Scholes time-to-expiry is computed from actual clock time
   remaining until the 4:00pm ET close, not a fraction of a calendar day.
5. **Risk management notes** — position sizing, profit-taking, and
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

## How strikes are chosen

- **Short strikes**: closest available strike to a target absolute delta
  (`Config.short_delta_target`, default `0.16`) on each side, computed via
  Black-Scholes from the option chain's implied volatility (the data
  provider doesn't supply broker greeks directly).
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

## Limitations & caveats

- Uses free Yahoo Finance data via `yfinance`; option chain liquidity and
  quote freshness vary, especially for far-dated or wide-strike contracts.
  0DTE quotes in particular can be stale or zero-bid if the scan runs
  before the market has actually opened.
- Delta/greeks are Black-Scholes approximations from chain IV, not live
  broker greeks. This is a bigger caveat for 0DTE, where real-world
  intraday gamma/pin risk near the short strikes is severe and not fully
  captured by a static delta snapshot from when the scan ran.
- News/catalyst detection is a keyword scan over a handful of free RSS
  feeds — it is **not** a substitute for checking an economic calendar
  (FOMC/CPI/NFP dates, earnings dates for QQQ's mega-cap holdings) before
  trading.
- IV rank is approximated via the VIX's 1-year percentile as a market-wide
  proxy; it is not QQQ-specific historical IV.
- This tool never places trades — it only produces an analysis/report.

## Project layout

```
qqq_iron_condor/
  config.py        # all tunable parameters
  data.py           # price history, option chains, VIX, news (network I/O)
  news.py           # catalyst keyword flagging
  indicators.py     # RSI, EMA/SMA, MACD, Bollinger Bands, ATR, ADX, HV
  options_math.py   # Black-Scholes delta/price helpers
  analysis.py       # turns raw data into the market snapshot
  strategy.py       # iron condor strike selection & trade math
  report.py         # Markdown report rendering
  scan.py           # CLI entrypoint / orchestration
tests/
  test_pipeline.py  # offline smoke test (synthetic data)
reports/            # daily generated reports land here
.github/workflows/
  qqq-iron-condor-scan.yml
```
