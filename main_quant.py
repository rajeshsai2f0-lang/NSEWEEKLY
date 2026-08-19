"""
Separate, parallel entry point to main.py. Same Chartink screener step,
but scores tickers with quant_scorer.py (pure OHLCV math) instead of
generating chart images and calling Gemini. Meant to be run alongside
main.py against the same day's screener output so the two CSVs
(nse_setups_analyzed_results_*.csv vs nse_setups_quant_results_*.csv)
can be diffed on Symbol/Score to compare the LLM-vision scorer against
the deterministic one.

Does not import or modify vision_engine.py, fetchimages_nse.py, or
main.py — safe to run independently or in its own workflow.
"""
import os
import time
import smtplib
from email.message import EmailMessage
import datetime

import requests

from chartink_screener import SCREENERS, PAUSE_BETWEEN, fetch_chartink, build_excel
from quant_scorer import run_quant_analysis


def _quant_excel_path():
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        f"Chartink_Screener_Quant_{datetime.date.today().strftime('%Y-%m-%d')}.xlsx",
    )


def send_email_report(csv_file_path):
    print("📧 Preparing to send email report...")

    sender_email = os.getenv("SMTP_EMAIL")
    sender_password = os.getenv("SMTP_PASSWORD")
    receiver_email = os.getenv("SMTP_EMAIL")

    if not sender_email or not sender_password:
        print("⚠️ Email credentials not found in environment. Skipping email.")
        return

    msg = EmailMessage()
    msg['Subject'] = f"📐 NSE Weekly Quant Score (No-LLM): {datetime.date.today()}"
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg.set_content(
        "Your automated NSE Weekly Quant pipeline has finished running.\n\n"
        "This is the deterministic, rules-based counterpart to the Gemini vision "
        "pipeline — same 10/30-week EMA breakout framework, same output columns, "
        "but every value is computed straight from the weekly OHLCV numbers "
        "(no chart images, no LLM calls). Compare its 'Score' column against "
        "the vision pipeline's CSV for the same day to see where the two agree "
        "or diverge.\n\n"
        "Quick guide to the key columns:\n"
        "  - Stage: Weinstein Stage 1 (Basing) / 2 (Advancing) / 3 (Topping) / 4 (Declining)\n"
        "  - BreakoutStatus: Pre-Breakout (Basing) / Breaking Out This Week / Already Extended / No Setup\n"
        "  - BaseStructure / BaseTightness / Contractions: shape and quality of the base "
        "(detected via swing-high/low pullback legs, not visual pattern-matching)\n"
        "  - TriggerCandleQuality: close position within this week's range if price is at/through the pivot\n"
        "  - PivotPrice / StopLevel / StoplossPercent / Target1: base high, base/recent low, "
        "risk %, and measured-move target (all in INR, read directly off the data)\n\n"
        "Sort by 'Score' (highest first) for the top setups.\n\n"
        "Happy Trading!"
    )

    if csv_file_path and os.path.exists(csv_file_path):
        with open(csv_file_path, 'rb') as f:
            csv_data = f.read()
            msg.add_attachment(csv_data, maintype='text', subtype='csv', filename=os.path.basename(csv_file_path))
    else:
        msg.set_content("⚠️ Pipeline ran, but no quant scoring CSV output was found.")

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
        print("✅ Email sent successfully!")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")


if __name__ == "__main__":
    print("🤖 STARTING NSE QUANT (NO-LLM) AUTOMATION PIPELINE...")

    session = requests.Session()
    results = []

    for name, mode, value in SCREENERS:
        if mode.lower() != "clause":
            print(f"⚠️ Skipping '{name}' — only 'clause' mode supported")
            continue
        print(f"\n📡 Running: {name}")
        df = fetch_chartink(session, value)
        results.append((name, df))
        time.sleep(PAUSE_BETWEEN)

    total_rows = sum(len(df) for _, df in results)

    if results and total_rows > 0:
        print(f"\n📊 Building Excel report...")
        # Own filename (Chartink_Screener_Quant_*) so a same-day run of
        # main.py doesn't collide with this one when comparing both.
        excel_path = _quant_excel_path()
        unique_tickers = build_excel(results, excel_path)

        print(f"\n📐 Running deterministic quant scoring on {len(unique_tickers)} tickers "
              f"(no images, no LLM)...")
        output_csv = run_quant_analysis(unique_tickers)

        send_email_report(output_csv)
    else:
        print("❌ No Chartink results retrieved today (0 tickers across all screeners). Pipeline halted.")
