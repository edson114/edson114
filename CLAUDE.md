# Notes for Claude

## "run scan"

When the user types **`run scan`** (on its own), they mean the meme coin
scan, not the SPX/QQQ or small-cap scans:

1. From the repo root, run `run-scan` (the console command from
   `pyproject.toml`; if it isn't installed, run `pip install -e .` or use
   `python -m memecoin_scanner`). It saves a report to
   `reports/memecoins/`.
2. Reply with the 5 picks: ticker, contract address (they paste it into
   the fomo app), score, the entry action (BUY NOW / WAIT) with the buy
   zone and entry price, the stop and take-profit levels, market cap,
   liquidity, 1h/24h change, the main reasons and every risk flag. Say
   which picks are labeled low conviction or use estimated levels (no
   candles), and keep the not-financial-advice warning short.
3. Commit the new report and push to the session's branch.

## "run fast scan"

**`run fast scan`** means `run-scan --fast`: short-term rules (coins under
48h whose volume is speeding up, 1h swing, 50% sold at 1.5x, 4h time
stop). Reply the same way as for "run scan" and include each pick's time
stop. Say it's inspired by fast traders like Kimchi, never that it is his
strategy: his rules aren't public. Reports land in
`reports/memecoins/fast/`.

## Logging the user's trades

When the user says they bought or sold a coin, log it with `trade-log`
(or `python -m memecoin_scanner.journal`): `buy --symbol --usd --price
--contract`, `sell --id --price [--fraction 0.5] [--proceeds]`. If they
don't give the fill price, log the latest quoted price with `--estimated`
and ask for the real fill, then fix it with `correct --id --proceeds` (or
`--price`). Show `trade-log stats` after each close, then
commit `journal/memecoin_trades.csv` and push. Phase 1 of their plan is
50 closed trades at a fixed $50-100 size before scaling.

The fomo app has no API; don't claim the picks came from inside fomo.
