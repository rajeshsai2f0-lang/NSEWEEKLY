import os
import csv
import time
import queue
import threading
import datetime
from google import genai
from google.genai import types
from PIL import Image

API_KEYS = [os.getenv(f"GEMINI_API_KEY_{i}") for i in range(1, 21)]
API_KEYS = [k for k in API_KEYS if k]

if not API_KEYS:
    raise ValueError("❌ No Gemini API keys found in environment variables!")

MODEL_NAME = "gemini-3.7-flash"
THINKING_LEVEL = "low"

# Bound every single API call so a hung/slow request can never stall a worker
# for minutes. If it doesn't respond in this window, treat it as an error and
# move on (with retry/backoff) instead of sitting there silently.
REQUEST_TIMEOUT_MS = 45_000

# Minimum seconds between two calls made on the SAME key. Keeps each key
# comfortably under its own per-minute quota regardless of how many other
# keys are running concurrently. Tune down (e.g. 3.0) once you confirm your
# tier's actual RPM limit; tune up if you still see 429s.
PER_KEY_MIN_INTERVAL = 4.5

MAX_RETRIES_PER_IMAGE = 3


# ══════════════════════════════════════════════════════════════════════════════
#  PROMPT — NSE Weekly 10/30 EMA Breakout Scanner
#
#  Chart source: fetchimages_nse.py plots 2y of weekly OHLCV via yfinance,
#  with the 10-week EMA in BLUE and the 30-week EMA in ORANGE (see
#  generate_charts_from_excel()). Prices are in INR. This prompt combines
#  that NSE-specific chart format with a Weinstein-stage + VCP-contraction
#  breakout anatomy so the run can flag names that are "basing and
#  tightening" as well as names breaking out this week:
#
#   1. STAGE CONTEXT (Weinstein): is this a Stage 2 advance (what we want),
#      a Stage 1 base still forming, a Stage 3 top rolling over, or a
#      Stage 4 decline? EMA alignment alone can look "bullish" late into a
#      topping process, so stage is checked as its own, prior gate.
#   2. PRIOR THRUST: an earlier strong up-leg (or a golden-cross reclaim off
#      a base) that already lifted price well off its lows and put it above
#      a rising 10 EMA, itself at/crossing above the rising 30 EMA.
#   3. TIGHT BASE / FLAG with CONTRACTIONS: several weeks of small-range,
#      sideways-to-up candles hugging the blue 10 EMA, printing a
#      flat/rising shelf. The highest-quality bases show 2-3 progressively
#      TIGHTER pullbacks (each contraction shallower than the last — VCP),
#      not just one flat range. Volume dries up during this base.
#   4. THE TRIGGER: a wide-range green weekly candle that CLOSES NEAR ITS
#      HIGH (upper third of the candle's range), breaking/closing above the
#      shelf resistance, on volume that expands sharply vs. the base. A
#      breakout candle with a big upper wick / weak close is a same-week
#      fakeout risk even if it technically pierces resistance.
#   5. The orange 30 EMA stays below and rising underneath the blue 10
#      EMA/price the whole time -- both EMAs support the move rather than
#      cap it.
#   6. REWARD SIDE: a measured-move target (pivot + base height) gives an
#      immediate reward/risk gut-check against the stop distance.
#
#  This pipeline runs once a day, but the chart is WEEKLY, so the rightmost
#  candle very often represents the current, still-open trading week -- see
#  the note in the prompt body telling the model to judge that candle live
#  rather than penalize it for looking incomplete.
# ══════════════════════════════════════════════════════════════════════════════
PROMPT = """
You are a veteran swing trader and market wizard evaluating an NSE (Indian
stock market) WEEKLY chart to decide whether it is a candidate to BUY at or
near the current price, or needs more time to set up. Prices are in Indian
Rupees (INR). Each image is a WEEKLY candlestick chart with two moving
averages plotted: the BLUE line is the 10-week EMA, the ORANGE line is the
30-week EMA. You are scanning for stocks that are basing and about to break
out, or are breaking out right now, on the weekly timeframe -- NOT daily
noise.

This pipeline runs once a day, but the chart is WEEKLY, so the rightmost
candle very often represents the current, still-open trading week rather
than a closed one. Do not penalize a setup just because the rightmost
candle looks incomplete or short -- instead use it to judge whether price
is CURRENTLY trading at, through, or near the pivot level right now. Your
job is to catch the setup as it develops through the week, not only after
the week has closed.

Chart legend: candles are green (up week) / red (down week). There is a
volume panel below the price panel with no average-volume reference line
drawn -- judge "high" or "low" volume relative to the other bars visible
on the same chart.

Your job is to recognize this repeatable anatomy of a weekly breakout:
0. STAGE CONTEXT — before anything else, classify the overall stage of the
   move (Weinstein-style): is price in a Stage 1 base (flat, coiling, no
   trend), a Stage 2 advance (trending up, EMAs rising and supportive --
   this is what we want), a Stage 3 top (advance stalling, EMAs flattening,
   choppy overlapping candles after a big run), or a Stage 4 decline (EMAs
   falling, price below both)? Do this classification FIRST, independent of
   EMA alignment alone -- a stock can look "10>30 rising" while already
   deep into a Stage 3 top.
1. PRIOR THRUST — an earlier strong up-leg (or a golden-cross reclaim) that
   already lifted price above a rising blue 10 EMA, with the blue 10 EMA at
   or above the rising orange 30 EMA.
2. TIGHT BASE / FLAG WITH CONTRACTIONS — several weeks of small-range,
   sideways-to-up candles hugging the blue 10 EMA, forming a flat or gently
   rising shelf with a clear horizontal resistance level. Count how many
   distinct pullback legs ("contractions") make up the base. The strongest
   bases show multiple contractions that get progressively TIGHTER/
   shallower each time (classic VCP); a single flat range counts as one
   contraction. Volume should be drying up / below average during this
   base.
3. THE TRIGGER — a wide-range green weekly candle, on volume that expands
   sharply above the base's average volume, pushing through or closing
   above the shelf resistance. Critically, judge WHERE this candle closes
   within its own high-low range: a close in the upper third is a strong,
   convicted trigger; a close in the lower half (long upper wick) is a weak
   trigger even if the high pierced resistance -- flag it as such rather
   than treating any green candle above the shelf as confirmation.
4. EMA SUPPORT — the orange 30 EMA stays below price and rising throughout,
   acting as support under the blue 10 EMA, never capping the move from
   above.
5. REWARD SIDE — once you have a pivot and stop, project a first target
   using a measured move: target = pivot + (height of the base, i.e.
   base high minus base low). This gives a quick reward-vs-risk read
   alongside the stop distance.

Ruthlessly reject: Stage 1 names with no evidence of a prior thrust yet,
Stage 3/4 names (topping or declining) even if short-term EMA alignment
still looks bullish, choppy overlapping price action with no identifiable
base, deep/loose bases (>35% range), climactic/parabolic candles already
far extended above both EMAs with no nearby support, and heavy top-down
distribution (long red candles on above-average volume near highs). Also
screen for two data-quality issues and fold any findings into the Reason:
- Anomalous candles: a single candle with a range wildly out of proportion
  to the rest of the chart (e.g. price roughly doubling and round-tripping
  within one or two weeks with nothing comparable elsewhere) is likely a
  corporate action (split/bonus/rights issue) or data artifact, not
  genuine volatility -- discount it rather than reading it as a clean
  breakout signal.
- Illiquidity: if volume is near-zero for most of the chart and only
  spikes on the most recent week(s), note that the stock is thinly traded
  even if the pattern otherwise qualifies -- this raises slippage risk.

Step 0 — VALIDITY GATE: First confirm this image is a valid weekly stock
chart with visible EMAs. If not, respond ONLY with:
INVALID | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | Score 0% | Not a valid weekly chart

Step 1: Identify the Stock Symbol/Ticker (it should be in the chart
title). Use "N/A" if not visible.
Step 2 — STAGE: Choose one: "Stage 1 (Basing)", "Stage 2 (Advancing)", "Stage 3 (Topping)", or "Stage 4 (Declining)".
Step 3 — EMA ALIGNMENT: Choose one: "10>30 Rising (Aligned)", "10 Crossing Above 30", "10<30 (Downtrend)", or "Flat/Coiling".
Step 4 — BASE STRUCTURE: Identify the pattern (e.g., "Flag", "Shelf/Flat Base", "VCP", "Cup with Handle", "Golden-Cross Reclaim"). If none, use "No Clear Base".
Step 5 — BASE TIGHTNESS: Choose one: "Tight (< 15%)", "Normal (15% - 30%)", or "Loose (> 30%)".
Step 6 — CONTRACTIONS: Count of distinct pullback legs in the base (integer, e.g. "1", "2", "3"), noting whether each is tighter than the last, or "N/A" if no base.
Step 7 — VOLUME SIGNATURE: Choose one: "Drying Up in Base", "Expanding on Breakout", "Climactic/Blow-off", or "Average/No Signal".
Step 8 — TRIGGER CANDLE QUALITY: Choose one: "Strong Close (Upper Third)", "Mid-Range Close", "Weak Close (Lower Third/Long Wick)", or "N/A" if no trigger candle yet.
Step 9 — BREAKOUT STATUS: Choose one: "Pre-Breakout (Basing)", "Breaking Out This Week", "Already Extended", or "No Setup / Downtrend".
Step 10: Weeks spent basing/tightening so far (integer, e.g. "6") or "N/A".
Step 11: Pivot/Trigger Price — the shelf/base resistance level to buy above. Read it off the nearest price-axis gridline. Give a plain INR number only (e.g. 1420.50), no currency symbol or extra text. Use "N/A" if not applicable.
Step 12 — STOPLEVEL: Plain INR number just below the 10 EMA or the low of the base (e.g., 1380.00). Use "N/A" if not applicable.
Step 13 — STOPLOSS PERCENTAGE: Percentage difference between Pivot and Stoplevel (e.g., 6.5%). Use "N/A" if not applicable.
Step 14 — TARGET1: Measured-move first target = Pivot + base height (high minus low of the base). Plain INR number (e.g., 1650.00) or "N/A".
Step 15: Score 0-100% for breakout quality/readiness, where 90%+ is a textbook, high-conviction setup worth acting on, and below 40% means avoid or skip for now (e.g., Score 85%).
Step 16: One-sentence Reason referencing the specific anatomy observed (stage, EMA alignment, base/contractions, trigger candle quality, and any data-quality/liquidity flags).

Respond ONLY in this exact pipe "|" separated format with 16 parts, and no extra text:
[Symbol] | [Stage] | [EMA_Alignment] | [BaseStructure] | [BaseTightness] | [Contractions] | [VolumeSignature] | [TriggerCandleQuality] | [BreakoutStatus] | [WeeksBasing] | [PivotPrice] | [StopLevel] | [StoplossPercent] | [Target1] | Score [0-100]% | [Reason]
"""

FIELDNAMES = [
    "File", "Symbol", "Stage", "EMA_Alignment", "BaseStructure", "BaseTightness",
    "Contractions", "VolumeSignature", "TriggerCandleQuality", "BreakoutStatus",
    "WeeksBasing", "PivotPrice", "StopLevel", "StoplossPercent", "Target1",
    "Score", "Reason", "RawResponse"
]


def parse_response(raw_text):
    raw_parts = [p.strip() for p in raw_text.split("|")]

    if len(raw_parts) != 16:
        return {
            "Symbol": "N/A", "Stage": "N/A", "EMA_Alignment": "N/A", "BaseStructure": "N/A",
            "BaseTightness": "N/A", "Contractions": "N/A", "VolumeSignature": "N/A",
            "TriggerCandleQuality": "N/A", "BreakoutStatus": "PARSE_ERROR", "WeeksBasing": "N/A",
            "PivotPrice": "N/A", "StopLevel": "N/A", "StoplossPercent": "N/A", "Target1": "N/A",
            "Score": "N/A",
            "Reason": f"Field count mismatch ({len(raw_parts)}/16). Raw: {raw_text}",
            "RawResponse": raw_text,
        }

    (symbol, stage, ema_alignment, base_structure, base_tightness, contractions,
     vol_signature, trigger_quality, breakout_status, weeks_basing, pivot,
     stop_level, stop_percent, target1, score_field, reason) = raw_parts

    score = score_field.replace("Score", "").strip()

    return {
        "Symbol": symbol,
        "Stage": stage,
        "EMA_Alignment": ema_alignment,
        "BaseStructure": base_structure,
        "BaseTightness": base_tightness,
        "Contractions": contractions,
        "VolumeSignature": vol_signature,
        "TriggerCandleQuality": trigger_quality,
        "BreakoutStatus": breakout_status,
        "WeeksBasing": weeks_basing,
        "PivotPrice": pivot,
        "StopLevel": stop_level,
        "StoplossPercent": stop_percent,
        "Target1": target1,
        "Score": score,
        "Reason": reason,
        "RawResponse": raw_text,
    }


def is_quota_error(e):
    msg = str(e).lower()
    return "429" in msg or "quota" in msg or "resource_exhausted" in msg or "rate limit" in msg


class KeyWorker:
    """
    Owns exactly one API key and one Client. Used from exactly one dedicated
    thread, so there is no shared/mutable state between workers and no risk
    of two threads racing on the same client or the same key's rate limit.
    """

    def __init__(self, label, api_key):
        self.label = label
        self.client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_MS),
        )
        self._last_call = 0.0

    def _pace(self):
        elapsed = time.monotonic() - self._last_call
        if elapsed < PER_KEY_MIN_INTERVAL:
            time.sleep(PER_KEY_MIN_INTERVAL - elapsed)
        self._last_call = time.monotonic()

    def classify(self, img):
        last_err = None
        for attempt in range(MAX_RETRIES_PER_IMAGE):
            self._pace()
            try:
                response = self.client.models.generate_content(
                    model=MODEL_NAME,
                    contents=[PROMPT, img],
                    config=types.GenerateContentConfig(
                        thinking_config=types.ThinkingConfig(thinking_level=THINKING_LEVEL)
                    ),
                )
                return response.text.strip()
            except Exception as e:
                last_err = e
                if is_quota_error(e):
                    # Back off harder — this key is rate-limited, not dead.
                    time.sleep(15 * (attempt + 1))
                else:
                    time.sleep(2)
        raise last_err


def run_vision_analysis(folder_path="Tomorrow_Setups_NSE", csv_filename=None):
    if not os.path.exists(folder_path):
        print(f"❌ Folder '{folder_path}' not found.")
        return None

    if not csv_filename:
        timestamp = datetime.date.today().strftime('%Y-%m-%d')
        csv_filename = f"nse_setups_analyzed_results_{timestamp}.csv"

    all_files = [f for f in os.listdir(folder_path) if f.endswith(('.png', '.jpg', '.jpeg'))]

    processed_files = set()
    file_exists = os.path.exists(csv_filename)
    if file_exists:
        with open(csv_filename, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                processed_files.add(row["File"])

    remaining_files = [f for f in all_files if f not in processed_files]
    print(f"Total charts found: {len(all_files)} | Left to process: {len(remaining_files)}")

    if len(remaining_files) == 0:
        print("All charts have already been processed!")
        return csv_filename

    print(f"🔑 Running with {len(API_KEYS)} Gemini API key(s) in parallel "
          f"(~{PER_KEY_MIN_INTERVAL}s pace per key).")

    work_queue = queue.Queue()
    for filename in remaining_files:
        work_queue.put(filename)

    write_lock = threading.Lock()
    progress_lock = threading.Lock()
    progress = {"done": 0}
    total = len(remaining_files)

    # Append mode + flush after every row: if this run gets cancelled or
    # times out, everything completed so far is already safely on disk.
    csv_file = open(csv_filename, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(csv_file, fieldnames=FIELDNAMES)
    if not file_exists:
        writer.writeheader()
        csv_file.flush()

    def worker_loop(worker):
        while True:
            try:
                filename = work_queue.get_nowait()
            except queue.Empty:
                return

            img_path = os.path.join(folder_path, filename)
            try:
                img = Image.open(img_path)
                raw_text = worker.classify(img)
                row = {"File": filename, **parse_response(raw_text)}

                with write_lock:
                    writer.writerow(row)
                    csv_file.flush()

                with progress_lock:
                    progress["done"] += 1
                    n = progress["done"]

                print(f"[{n}/{total}] (key {worker.label}) {filename}: "
                      f"{row['Symbol']} | {row['Stage']} | {row['BreakoutStatus']} | "
                      f"Base: {row['BaseStructure']} | Score: {row['Score']}")

            except Exception as e:
                with progress_lock:
                    progress["done"] += 1
                    n = progress["done"]
                print(f"[{n}/{total}] (key {worker.label}) ❌ {filename}: {e}")
            finally:
                work_queue.task_done()

    workers = [KeyWorker(i + 1, key) for i, key in enumerate(API_KEYS)]
    threads = [threading.Thread(target=worker_loop, args=(w,), daemon=True) for w in workers]

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    csv_file.close()

    print(f"\n✅ Vision analysis complete! Processed {progress['done']}/{total} charts -> {csv_filename}")
    return csv_filename
