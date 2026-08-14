# NSE Weekly 10/30 EMA Breakout Pipeline

Daily automation that finds NSE stocks setting up on the **weekly** timeframe
against a 10-week / 30-week EMA framework, charts them, and runs a Gemini
vision pass to score and rank the setups.

## Pipeline

1. **`chartink_screener.py`** — runs two Chartink clause screeners:
   - **Weekly 10-30 EMA Breakout - Ready Now**: price above a rising 10-week
     EMA which is itself above a rising 30-week EMA, tagging a fresh ~10-week
     high, on volume expansion, and not yet extended too far above the 10 EMA
     to chase.
   - **Weekly 10-30 EMA Basing - Forming**: same EMA-stack condition, but for
     stocks trading between 85-99% of their 10-week high — an early watchlist
     feed for setups a few days ahead of a possible trigger.
2. **`fetchimages_nse.py`** — pulls 2 years of weekly OHLCV per ticker via
   `yfinance`, plots candles with the 10-week EMA (blue) and 30-week EMA
   (orange), titled with the ticker so it's readable off the image itself.
3. **`vision_engine.py`** — sends each chart to Gemini with a structured
   prompt built specifically for this weekly/10-30-EMA chart format (base
   structure, EMA relationship, volume dry-up/expansion, data-quality and
   bull-trap checks), returns a pipe-delimited verdict per ticker, and
   writes the results to CSV.
4. **`main.py`** — orchestrates all of the above end to end and emails the
   resulting CSV (sort by `Score` for the top setups).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in SMTP + Gemini API keys
python main.py
```

For unattended runs, `.github/workflows/Pipeline.yaml` runs the same
pipeline on a schedule via GitHub Actions, reading the same env vars from
repo Secrets instead of `.env`.

## Notes / caveats

- The Chartink clause syntax for the two screeners (in particular the
  `1 week ago weekly ema(...)` slope check) is modeled on working patterns
  from other Chartink clauses but hasn't been independently verified against
  the live Chartink engine — paste-test in Chartink's scanner console before
  changing thresholds.
- The Gemini prompt assumes the rightmost weekly candle may still be
  in-progress (since this runs daily against weekly bars) and is written to
  judge live setups accordingly rather than only fully-closed weeks.
