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
   prompt built specifically for this weekly/10-30-EMA chart format
   (Weinstein stage, prior thrust, VCP-style base contractions, trigger
   candle quality, EMA support, and a measured-move target), returns a
   16-field pipe-delimited verdict per ticker, and writes the results to
   CSV. Reads up to 20 `GEMINI_API_KEY_*` env vars and runs one worker
   thread per key in parallel (each independently paced to stay under its
   own per-minute quota) — you only need to set as many keys as you have.
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

## No-LLM alternative (`quant_scorer.py` / `main_quant.py`)

A separate, parallel pipeline that scores the same Chartink ticker list
against the same 10/30-week EMA breakout framework and produces the same
16-field verdict — but computes every field directly from the weekly
OHLCV numbers instead of rendering a chart image and asking Gemini to
read it. No images, no LLM calls, no Gemini API keys required.

- **`quant_scorer.py`** — pulls weekly OHLCV via `yfinance` (no image is
  ever generated), detects Stage/EMA alignment from the EMA10/EMA30
  series directly, finds the current base window via swing-high/low
  pullback detection, counts contraction legs and checks whether they're
  tightening (VCP), reads trigger-candle quality and volume signature off
  the raw bars, and computes Pivot/Stop/Target1 as exact numbers (base
  high/low + measured move) rather than an estimate read off a gridline.
  Score is an explicit, traceable weighted sum — see `SCORE_WEIGHTS` at
  the top of the file.
- **`main_quant.py`** — same Chartink screener step as `main.py`, but
  calls `quant_scorer.run_quant_analysis()` instead of generating charts
  and calling `vision_engine.py`. Writes its own dated Excel
  (`Chartink_Screener_Quant_*.xlsx`) and CSV
  (`nse_setups_quant_results_*.csv`) so it never collides with `main.py`'s
  output when both are run on the same day, and emails its own report.
- **`.github/workflows/PipelineQuant.yaml`** — a separate, independently
  dispatchable workflow. Only needs `SMTP_EMAIL`/`SMTP_PASSWORD` — no
  Gemini secrets.

Run it the same way as the main pipeline:

```bash
python main_quant.py
```

Because it writes to a different CSV/Excel filename pattern than
`main.py`, you can run both on the same day and diff the two CSVs on
`Symbol`/`Score` to see where the deterministic scorer and the vision
model agree or diverge. It does not import from or modify `vision_engine.py`,
`fetchimages_nse.py`, or `main.py` — the two pipelines are fully
independent.

Where it differs from the vision pipeline, by design:
- Pivot/Stop/Target1 are exact (computed from the data), not estimates.
- No corporate-action/data-artifact judgment beyond a simple >35%
  single-week move flag in the Reason column — Gemini's visual read of
  "this looks like a split" is fuzzier but can catch things a fixed
  threshold misses.
- The VCP/contraction read uses a fixed fractal swing-detection rule
  (`SWING_ORDER` bars on each side); it won't have the same tolerance for
  messy, near-but-not-quite-textbook bases that a vision model has. Worth
  spot-checking its high scorers against the vision pipeline's or your
  own chart library before trusting either exclusively.
