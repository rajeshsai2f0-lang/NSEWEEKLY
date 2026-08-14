import os
import glob
import pandas as pd
import yfinance as yf
import mplfinance as mpf

# NSE tickers on yfinance need a ".NS" suffix (BSE would be ".BO").
YF_SUFFIX = ".NS"


def _read_tickers_from_excel(excel_filename):
    """
    Read tickers from the consolidated "All Tickers" sheet that
    chartink_screener.build_excel() writes. That sheet holds one
    comma-separated string of tickers in a single cell -- it is NOT a
    normal one-ticker-per-row table, so it must be parsed accordingly.

    Only used as a fallback when generate_charts_from_excel() is called
    without an explicit `tickers` list (e.g. run standalone).
    """
    xls = pd.ExcelFile(excel_filename)
    sheet_name = "All Tickers" if "All Tickers" in xls.sheet_names else xls.sheet_names[-1]
    raw = pd.read_excel(excel_filename, sheet_name=sheet_name, header=None)

    tickers = []
    for val in raw.values.flatten():
        if not isinstance(val, str):
            continue
        # The consolidated row looks like "TCS, INFY, RELIANCE, ..."; skip
        # the header/label row (which contains "COPY-PASTE").
        if "COPY-PASTE" in val or "," not in val:
            continue
        tickers.extend(part.strip().upper() for part in val.split(",") if part.strip())
    return sorted(set(tickers))


def generate_charts_from_excel(tickers=None, excel_filename=None, output_folder="Tomorrow_Setups_NSE"):
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
        print(f"📁 Created directory: {output_folder}")

    if tickers is None:
        # Fallback path: no ticker list was passed in directly, so recover
        # one from a Chartink Excel report on disk.
        if not excel_filename or not os.path.exists(excel_filename):
            files = glob.glob("Chartink_Screener_*.xlsx")
            if files:
                excel_filename = files[0]
            else:
                print("❌ No Chartink Excel sheets found.")
                return

        print(f"📊 Reading tickers from: {excel_filename}")
        try:
            tickers = _read_tickers_from_excel(excel_filename)
        except Exception as e:
            print(f"❌ Error reading ticker file: {e}")
            return
    else:
        tickers = sorted(set(t.strip().upper() for t in tickers if t and str(t).strip()))

    print(f"📊 Processing {len(tickers)} tickers. Generating chart images...")

    mc = mpf.make_marketcolors(up='green', down='red', wick='inherit', volume='in', ohlc='black')
    s = mpf.make_mpf_style(marketcolors=mc, gridstyle='--', y_on_right=True)

    success_count = 0
    for ticker in tickers:
        # Chartink tickers already come in bare NSE-symbol form (e.g. "TCS").
        # Strip a stray ".NS"/".BO" if present so we don't double-suffix.
        base_symbol = ticker.split(".")[0]
        yf_symbol = base_symbol + YF_SUFFIX

        try:
            # 2y of weekly bars gives ~100 candles - enough lead-in for the
            # 30-week EMA to stabilize plus a full base + breakout history
            # visible on the right edge.
            data = yf.download(yf_symbol, period="2y", interval="1wk", progress=False)
            if data.empty or len(data) < 40:
                continue

            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)

            data['EMA10'] = data['Close'].ewm(span=10, adjust=False).mean()
            data['EMA30'] = data['Close'].ewm(span=30, adjust=False).mean()

            add_plots = [
                mpf.make_addplot(data['EMA10'], color='blue', width=0.9),
                mpf.make_addplot(data['EMA30'], color='orange', width=0.9),
            ]

            # Save chart under the bare NSE symbol (no ".NS") so filenames
            # match what the vision engine and downstream CSV expect.
            chart_path = os.path.join(output_folder, f"{base_symbol}.png")
            mpf.plot(
                data, type='candle', style=s, volume=True, addplot=add_plots,
                title=f"\n{base_symbol} - Weekly",
                savefig=dict(fname=chart_path, dpi=150, bbox_inches='tight'),
                figratio=(10, 6), figscale=0.9
            )
            success_count += 1
        except Exception:
            pass

    print(f"✅ Successfully generated {success_count} charts in '{output_folder}'.")


if __name__ == "__main__":
    generate_charts_from_excel()
