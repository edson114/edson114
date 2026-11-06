# Notes for Claude

## "run scan"

When the user types **`run scan`** (on its own), they mean the meme coin
scan, not the SPX/QQQ or small-cap scans:

1. Run `python -m memecoin_scanner.scan` (install `requirements.txt`
   first if `requests` is missing). It saves a report to
   `reports/memecoins/`.
2. Reply with the 5 picks: ticker, contract address (they paste it into
   the fomo app), score, market cap, liquidity, 1h/24h change, the main
   reasons and every risk flag, and the stop / take-profit levels. Say
   which picks are labeled low conviction, and keep the not-financial-advice
   warning short.
3. Commit the new report and push to the session's branch.

The fomo app has no API; don't claim the picks came from inside fomo.
