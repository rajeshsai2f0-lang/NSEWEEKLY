import os
import time
import smtplib
from email.message import EmailMessage
import datetime

import requests

from chartink_screener import OUTPUT_FILE, SCREENERS, PAUSE_BETWEEN, fetch_chartink, build_excel
import fetchimages_nse
from vision_engine import run_vision_analysis


def send_email_report(csv_file_path):
    print("📧 Preparing to send email report...")

    sender_email = os.getenv("SMTP_EMAIL")
    sender_password = os.getenv("SMTP_PASSWORD")
    receiver_email = os.getenv("SMTP_EMAIL")

    if not sender_email or not sender_password:
        print("⚠️ Email credentials not found in environment. Skipping email.")
        return

    msg = EmailMessage()
    msg['Subject'] = f"🚀 NSE Weekly 10/30 EMA Breakout Scan: {datetime.date.today()}"
    msg['From'] = sender_email
    msg['To'] = receiver_email
    msg.set_content(
        "Your automated NSE Weekly 10/30 EMA Breakout pipeline has finished running.\n\n"
        "Attached is the full CSV of every weekly chart scanned. Open it and sort by "
        "'Score' (highest first) to see the best setups.\n\n"
        "Quick guide to the key columns:\n"
        "  - Stage: Weinstein Stage 1 (Basing) / 2 (Advancing) / 3 (Topping) / 4 (Declining)\n"
        "  - BreakoutStatus: Pre-Breakout (Basing) / Breaking Out This Week / Already Extended / No Setup\n"
        "  - BaseStructure / BaseTightness / Contractions: shape and quality of the base (VCP-style contractions are the highest quality)\n"
        "  - TriggerCandleQuality: how convincingly this week's candle closed through the pivot\n"
        "  - PivotPrice / StopLevel / StoplossPercent / Target1: entry trigger, stop, risk %, and first measured-move target (all in INR)\n\n"
        "Focus first on Stage 2 names that are 'Breaking Out This Week' or tightly basing "
        "and 'Pre-Breakout', with a Strong Close trigger candle.\n\n"
        "Happy Trading!"
    )

    if csv_file_path and os.path.exists(csv_file_path):
        with open(csv_file_path, 'rb') as f:
            csv_data = f.read()
            msg.add_attachment(csv_data, maintype='text', subtype='csv', filename=os.path.basename(csv_file_path))
    else:
        msg.set_content("⚠️ Pipeline ran, but no classification CSV output was found.")

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(sender_email, sender_password)
            smtp.send_message(msg)
        print("✅ Email sent successfully!")
    except Exception as e:
        print(f"❌ Failed to send email: {e}")


if __name__ == "__main__":
    print("🤖 STARTING FULL NSE AUTOMATION PIPELINE...")

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
        excel_path = OUTPUT_FILE
        unique_tickers = build_excel(results, excel_path)

        print(f"\n📊 Successfully extracted {len(unique_tickers)} unique tickers. Generating chart images...")
        # Pass the ticker list straight through instead of re-reading it back
        # out of the Excel file, same reasoning as the US pipeline.
        fetchimages_nse.generate_charts_from_excel(
            tickers=unique_tickers, output_folder="Tomorrow_Setups_NSE"
        )

        print("\n🔍 Running Gemini Vision AI Analysis...")
        output_csv = run_vision_analysis(folder_path="Tomorrow_Setups_NSE")

        send_email_report(output_csv)
    else:
        print("❌ No Chartink results retrieved today (0 tickers across all screeners). Pipeline halted.")
