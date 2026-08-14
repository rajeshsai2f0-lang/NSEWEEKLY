import os
import csv
import time
import queue
import threading
import datetime
from google import genai
from google.genai import types
from PIL import Image

API_KEYS = [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3"),
    os.getenv("GEMINI_API_KEY_4"),
    os.getenv("GEMINI_API_KEY_5"),
]
API_KEYS = [k for k in API_KEYS if k]

if not API_KEYS:
    raise ValueError("❌ No Gemini API keys found in environment variables!")

MODEL_NAME = "gemini-3.5-flash-lite"
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


PROMPT = """
You are a professional swing trader evaluating an NSE (Indian stock market)
WEEKLY chart to decide whether it is a candidate to BUY at or near the
current price, or needs more time to set up. Prices are in Indian Rupees
(INR). Each candle on this chart represents one week. This is a live,
forward-looking decision about the right edge of the chart - NOT a
historical review of a move that has already completed.

This pipeline runs once a day, but the chart is WEEKLY, so the rightmost
candle very often represents the current, still-open trading week rather
than a closed one. Do not penalize a setup just because the rightmost
candle looks incomplete or short - instead use it to judge whether price
is CURRENTLY trading at, through, or near the pivot level right now. Your
job is to catch the setup as it develops through the week, not only after
the week has closed.

Chart legend: candles are green (up week) / red (down week). The blue line
is the 10-week EMA. The orange line is the 30-week EMA. There is a volume
panel below the price panel with no average-volume reference line drawn -
judge "high" or "low" volume relative to the other bars visible on the
same chart.

Your primary goal is to identify stocks in a strong uptrend with linear
price action, clear institutional footprints, and a good risk/reward
profile. Ruthlessly reject choppy or downtrending stocks, deep bases,
heavy distribution, and post-top rollovers.

Step 0 - VALIDITY GATE: First confirm this image is actually a valid stock
price chart (candles/bars, a price axis, and time axis). If it is NOT a
valid stock chart (e.g. a receipt, screenshot of text, unrelated photo, or
anything you cannot analyze), respond with ONLY this exact line and
nothing else:
INVALID | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | Score 0% | Not a valid stock chart

Do not add any explanation before or after that line. Only proceed to the
steps below if the image IS a valid stock chart.

Step 1: Identify the Stock Symbol/Ticker shown on the chart (it should be
in the chart title). If it is genuinely not visible anywhere on the chart,
use "N/A".

Step 2: Confirm the Chart Timeframe is "Weekly" from the candle spacing and
axis labels. If the chart clearly does NOT look weekly (e.g. it looks like
daily bars), report "Daily" or "Unclear" here instead so the mismatch is
visible downstream, but continue the analysis as best you can.

Step 3 - LINEARITY CHECK: Assess the price action leading into the current
base. Choose strictly either "Linear" or "Choppy".

Step 4 - MOVING AVERAGE (MA) STATUS: Choose one: "Rising (Price > 10 EMA >
30 EMA)", "Coiling", "Price > 10 EMA but < 30 EMA", or "Downtrending
(Price < 10 EMA < 30 EMA, or 30 EMA sloping down)".

Step 5: Identify the base/pattern type at the right edge of the chart.
You MUST choose exactly one label from this fixed list - do not invent
new names, combine names, or use synonyms for something already on the
list:
["VCP", "Flag", "Bull Flag", "Pennant", "Cup with Handle", "Long Base",
"High Tight Flag", "Flat Base", "Wedge", "Ascending Triangle",
"Double Bottom", "Rounding Base", "No Clear Base", "Other"]

If you choose "Other", it must be a pattern genuinely not covered by the
list above - append a short 2-4 word descriptor after it in the Pattern
field (e.g. "Other: Head and Shoulders"). Do not use "Other" just to
rename something that already has a label on the list.

Step 6 - BASE DEPTH: Choose one: "Shallow (< 20%)", "Normal (20% - 35%)",
or "Deep (> 35%)".

Step 7 - DISTRIBUTION / DATA-QUALITY CHECK: Choose one: "Clean" or "Heavy
Distribution". While judging this, also screen for two things and fold
any findings into your Step 15 Reason (do not add new output fields):
- Anomalous candles: a single candle with a range wildly out of proportion
  to the rest of the chart (e.g. price roughly doubling and round-tripping
  within one or two weeks with nothing comparable elsewhere) is likely a
  corporate action (split/bonus/rights issue) or data artifact, not
  genuine volatility - discount it rather than reading it as a clean
  breakout signal, and say so in the Reason.
- Illiquidity: if volume is near-zero for most of the chart and only
  spikes on the most recent week(s), note that the stock is thinly traded
  even if the pattern otherwise qualifies - this raises slippage risk.

Step 8 - INSTITUTIONAL FOOTPRINT CHECK: Look at the price advance that led
INTO this base (the run-up before the current consolidation) and check it
against these criteria for genuine institutional buying:
- Roughly 3-8 weeks of concentrated buying on visibly strong, towering
  volume (clearly above the average volume seen elsewhere on the chart).
- The overall advance should be a strong move, ideally in the 20%-40%+
  range - a real show of buying force, not a weak drift higher.
- Ideally there is at least one standout weekly candle with an unusually
  wide range relative to the rest of the chart somewhere in that move.
- Ideally the move includes a stand-out volume bar - a clear spike
  relative to recent history.
- The base that follows should be shallow - ideally less than 15-20% deep
  at worst - and price should be holding above the 10-week EMA, with the
  30-week EMA flattening out or turning up beneath it.

Rate how many of these are clearly visible on the chart and choose one:
- "Strong": most/all criteria are clearly present.
- "Moderate": some criteria present, others unclear or absent.
- "Weak": few or none of these criteria are visible.
Use "Unclear" only if the chart doesn't show enough of the prior move to judge.

Step 9: Assess READINESS as of the most recent candle. Choose exactly one:
- "Ready Now": the base is tight and complete, price is sitting at, just
  below, or currently pushing through a clear pivot/trigger level this
  week, volume has dried up through the base (or is now expanding on the
  breakout week), and the setup is actionable now.
- "Forming": the setup is developing but not yet tight or complete -
  more price action or time is needed before it becomes tradeable.
- "Extended": the stock has already broken out and moved too far from a
  logical entry to chase.
- "Broken": the pattern has failed - support violated, structure choppy
  or erratic, or a prior breakout has already rolled back below the pivot
  or the 10-week EMA (treat any single up-week inside an established
  downtrend - 10 EMA below 30 EMA, both sloping down - as a bull trap, not
  a new setup).

Step 10: If "Forming", estimate how many more weeks it likely needs to
tighten up before becoming actionable (e.g., "1-2 weeks", "3-4 weeks").
If not "Forming", use "N/A".

Step 11: If "Ready Now" (or close to it), identify the specific
Pivot/Trigger Price visible on the chart - the level that, if broken above,
confirms entry. Read it off the nearest price-axis gridline. Give a plain
INR number only (e.g. 1420.50), no currency symbol or extra text. Use
"N/A" if not applicable.

Step 12 - STOPLEVEL: Identify a logical Stop-Loss reference level - the low
of the last 1-2 candles or the base low, read off the nearest price-axis
gridline. Give a plain INR number only (e.g. 1380.00). Use "N/A" if not
applicable.

Step 13 - STOPLOSS PERCENTAGE: Percentage difference between Pivot and
Stoplevel (e.g., 3.5%). Use "N/A" if not applicable.

Step 14: Convert your conviction into a Score 0-100%, where 90%+ is a
textbook, high-conviction setup worth acting on, and below 40% means
avoid or skip for now. A "Weak" institutional footprint, "Choppy"
linearity, an anomalous/data-quality candle, or a post-top rollover should
generally pull this score down, even if the base pattern itself looks
tight.

Step 15: Give a concise one-sentence Reason covering volume behavior,
the 10/30-week EMA relationship, structural tightness/linearity, and any
data-quality or liquidity flags from Step 7.

Respond ONLY in this exact pipe "|" separated format with 15 parts, and no extra text:
[Symbol] | [Timeframe] | [Linearity] | [MA_Status] | [Pattern] | [BaseDepth] | [DistributionCheck] | [Inst_Footprint] | [Readiness] | [DaysToReady] | [PivotPrice] | [StopLevel] | [StoplossPercent] | Score [0-100]% | [Reason]
"""

FIELDNAMES = [
    "File", "Symbol", "Timeframe", "Linearity", "MA_Status", "Pattern",
    "BaseDepth", "DistributionCheck", "InstitutionalFootprint", "Readiness",
    "DaysToReady", "PivotPrice", "StopLevel", "StoplossPercent", "Score",
    "Reason", "RawResponse"
]


def parse_response(raw_text):
    raw_parts = [p.strip() for p in raw_text.split("|")]

    if len(raw_parts) != 15:
        return {
            "Symbol": "N/A", "Timeframe": "N/A", "Linearity": "N/A",
            "MA_Status": "N/A", "Pattern": "N/A", "BaseDepth": "N/A",
            "DistributionCheck": "N/A", "InstitutionalFootprint": "N/A",
            "Readiness": "PARSE_ERROR", "DaysToReady": "N/A", "PivotPrice": "N/A",
            "StopLevel": "N/A", "StoplossPercent": "N/A", "Score": "N/A",
            "Reason": f"Field count mismatch ({len(raw_parts)}/15). Raw: {raw_text}",
            "RawResponse": raw_text,
        }

    (symbol, timeframe, linearity, ma_status, pattern, base_depth, dist_check,
     footprint, readiness, days_to_ready, pivot, stop_level, stop_percent, score_field, reason) = raw_parts

    score = score_field.replace("Score", "").strip()

    return {
        "Symbol": symbol,
        "Timeframe": timeframe,
        "Linearity": linearity,
        "MA_Status": ma_status,
        "Pattern": pattern,
        "BaseDepth": base_depth,
        "DistributionCheck": dist_check,
        "InstitutionalFootprint": footprint,
        "Readiness": readiness,
        "DaysToReady": days_to_ready,
        "PivotPrice": pivot,
        "StopLevel": stop_level,
        "StoplossPercent": stop_percent,
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
                      f"{row['Symbol']} | Linearity: {row['Linearity']} | "
                      f"Stop: {row['StopLevel']} | Score: {row['Score']}")

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
