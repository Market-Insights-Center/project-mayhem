# --- Imports for breakout_command ---
import asyncio
import os
import csv
from datetime import datetime
from typing import List, Dict, Any, Optional
import traceback

import pandas as pd
from tabulate import tabulate
from tradingview_screener import Query, Column

# --- Imports from other command modules ---
from invest_command import calculate_ema_invest, calculate_one_year_invest

# --- Constants ---
BREAKOUT_TICKERS_FILE = 'breakout_tickers.csv'
BREAKOUT_HISTORICAL_DB_FILE = 'breakout_historical_database.csv'

# --- Helper Functions ---
def safe_score(value: Any) -> float:
    try:
        if pd.isna(value) or value is None: return 0.0
        if isinstance(value, str): value = value.replace('%', '').replace('$', '').strip()
        return float(value)
    except (ValueError, TypeError): return 0.0

# --- Core Logic Functions (moved from breakout.py) ---
async def run_breakout_analysis_singularity(is_called_by_ai: bool = False) -> dict:
    """Runs breakout analysis and returns results, handling errors."""
    if not is_called_by_ai:
        print("\n--- Running Breakout Analysis ---")

    existing_tickers_data = {}
    if os.path.exists(BREAKOUT_TICKERS_FILE):
        try:
            df_existing = pd.read_csv(BREAKOUT_TICKERS_FILE)
            if not df_existing.empty:
                # ... (existing data loading logic remains the same) ...
                for col in ["Highest Invest Score", "Lowest Invest Score", "Live Price", "1Y% Change", "Invest Score"]:
                    if col in df_existing.columns:
                        if df_existing[col].dtype == 'object': df_existing[col] = df_existing[col].astype(str).str.replace('%', '', regex=False).str.replace('$', '', regex=False).str.strip()
                        df_existing[col] = pd.to_numeric(df_existing[col], errors='coerce')
                existing_tickers_data = df_existing.set_index('Ticker').to_dict('index')
        except Exception as e:
            msg = f"Info: Could not read existing breakout file '{BREAKOUT_TICKERS_FILE}': {e}"
            if not is_called_by_ai: print(f"  -> {msg}")
            # Don't return error here, just proceed without existing data

    existing_tickers_set = set(existing_tickers_data.keys())
    new_tickers_from_screener = []
    screener_error_msg = None
    try:
        if not is_called_by_ai: print("  -> Running TradingView Screener...")
        query = Query().select('name').where(Column('market_cap_basic') >= 1_000_000_000, Column('volume') >= 1_000_000, Column('change|1W') >= 20, Column('close') >= 1, Column('average_volume_90d_calc') >= 1_000_000).order_by('change', ascending=False).limit(100)
        _, new_tickers_df = await asyncio.to_thread(query.get_scanner_data, timeout=60)
        if new_tickers_df is not None and 'name' in new_tickers_df.columns:
            new_tickers_from_screener = sorted(list(set([str(t).split(':')[-1].replace('.', '-') for t in new_tickers_df['name'].tolist() if pd.notna(t)])))
            if not is_called_by_ai: print(f"     ... Screener found {len(new_tickers_from_screener)} potential new tickers.")
        else:
            if not is_called_by_ai: print("     ... Screener returned no data.")
    except Exception as e:
        screener_error_msg = f"TradingView screener failed: {type(e).__name__}"
        if not is_called_by_ai: print(f"  -> ⚠️ Warning: {screener_error_msg}")
        # Don't return error yet, proceed with existing tickers if any

    all_tickers_to_process = sorted(list(set(list(existing_tickers_data.keys()) + new_tickers_from_screener)))
    if not all_tickers_to_process:
        msg = "No existing or new tickers found to process."
        if not is_called_by_ai: print(f"  -> {msg}")
        return {"status": "error", "message": screener_error_msg or msg, "current_breakout_stocks": []} # Return error structure

    temp_updated_data = []
    processing_errors = []
    if not is_called_by_ai: print(f"  -> Processing {len(all_tickers_to_process)} total tickers for scores and filtering...")
    process_count = 0
    for ticker_b in all_tickers_to_process:
        process_count += 1
        if not is_called_by_ai and process_count % 10 == 0:
            print(f"\r     ... processing {process_count}/{len(all_tickers_to_process)}", end="")
        try:
            # Setting sensitivity to 2 (Daily) as default for breakout
            live_price, current_invest_score = await calculate_ema_invest(ticker_b, 2, is_called_by_ai=True)
            one_year_change, _ = await calculate_one_year_invest(ticker_b, is_called_by_ai=True) # Assuming 1Y Invest Score isn't needed here

            # Skip if score calculation failed
            if current_invest_score is None:
                 processing_errors.append(f"{ticker_b}: Score calc failed")
                 continue

            existing_entry = existing_tickers_data.get(ticker_b, {})
            # Use safe_score for robust comparison, provide default for lowest if missing
            highest_score = max(safe_score(existing_entry.get("Highest Invest Score")), current_invest_score)
            lowest_score = min(safe_score(existing_entry.get("Lowest Invest Score", current_invest_score)), current_invest_score) # Default lowest to current

            # Apply filtering logic
            if not (current_invest_score > 600 or current_invest_score < 100.0 or current_invest_score < (3.0/4.0) * highest_score):
                status = "Repeat" if ticker_b in existing_tickers_set else "New"
                temp_updated_data.append({
                    "Ticker": ticker_b,
                    "Live Price": f"{live_price:.2f}" if live_price is not None else "N/A", # Handle potential None price
                    "Invest Score": f"{current_invest_score:.2f}%",
                    "Highest Invest Score": f"{highest_score:.2f}%",
                    "Lowest Invest Score": f"{lowest_score:.2f}%",
                    "1Y% Change": f"{one_year_change:.2f}%" if one_year_change is not None else "N/A",
                    "Status": status,
                    "_sort_score": current_invest_score # Keep for sorting
                })
        except Exception as e:
            err_str = f"{ticker_b}: {type(e).__name__}"
            processing_errors.append(err_str)
            continue # Skip this ticker on error

    if not is_called_by_ai: print(f"\r     ... processing complete.                     ") # Clear progress line
    if processing_errors and not is_called_by_ai:
        print(f"  -> ⚠️ Warnings during ticker processing ({len(processing_errors)}): {', '.join(processing_errors[:3])}{'...' if len(processing_errors)>3 else ''}")

    # Sort and remove helper key
    temp_updated_data.sort(key=lambda x: x['_sort_score'], reverse=True)
    final_data = [{k: v for k, v in item.items() if k != '_sort_score'} for item in temp_updated_data]

    # --- Always return a dict with status ---
    if not final_data and screener_error_msg:
         # If screener failed AND no stocks passed filter, report screener error
         return {"status": "error", "message": screener_error_msg, "current_breakout_stocks": []}
    elif not final_data:
        # If screener worked but no stocks passed filter
        return {"status": "success", "message": "No breakout stocks met the criteria after filtering.", "current_breakout_stocks": []}
    else:
        # Success, return the found stocks
        return {"status": "success", "message": f"Found {len(final_data)} breakout stocks.", "current_breakout_stocks": final_data}
    
async def save_breakout_data_singularity(date_str: str, is_called_by_ai: bool = False) -> str:
    """
    Saves the current breakout data from BREAKOUT_TICKERS_FILE to
    BREAKOUT_HISTORICAL_DB_FILE for a given date.
    Returns a summary string.
    """
    if not is_called_by_ai:
        print(f"\n--- Saving Breakout Data for Date: {date_str} ---")

    if not os.path.exists(BREAKOUT_TICKERS_FILE):
        msg = f"Error: Current breakout data file '{BREAKOUT_TICKERS_FILE}' not found. Cannot save historical data."
        if not is_called_by_ai:
            print(msg)
        return msg

    save_count = 0
    try:
        df_current_breakout = pd.read_csv(BREAKOUT_TICKERS_FILE)
        if df_current_breakout.empty:
            msg = f"Info: Current breakout file '{BREAKOUT_TICKERS_FILE}' is empty. Nothing to save to historical DB."
            if not is_called_by_ai:
                print(msg)
            return msg

        historical_data_to_save = []
        for _, row in df_current_breakout.iterrows():
            price_str = str(row.get('Live Price', 'N/A')).replace('$', '').strip()
            score_str = str(row.get('Invest Score', 'N/A')).replace('%', '').strip()
            price_val = safe_score(price_str)
            score_val = safe_score(score_str)

            historical_data_to_save.append({
                'DATE': date_str,
                'TICKER': row.get('Ticker', 'ERR'),
                'PRICE': f"{price_val:.2f}" if price_val is not None and not pd.isna(price_val) else "N/A",
                'INVEST_SCORE': f"{score_val:.2f}" if score_val is not None and not pd.isna(score_val) else "N/A"
            })

        if not historical_data_to_save:
            msg = "No valid breakout data rows to save after processing."
            if not is_called_by_ai: print(msg)
            return msg

        file_exists_hist = os.path.isfile(BREAKOUT_HISTORICAL_DB_FILE)
        headers_hist = ['DATE', 'TICKER', 'PRICE', 'INVEST_SCORE']

        with open(BREAKOUT_HISTORICAL_DB_FILE, 'a', newline='', encoding='utf-8') as f_hist:
            writer_hist = csv.DictWriter(f_hist, fieldnames=headers_hist)
            if not file_exists_hist or os.path.getsize(f_hist.name) == 0:
                writer_hist.writeheader()
            for data_row_hist in historical_data_to_save:
                writer_hist.writerow(data_row_hist)
                save_count += 1
        msg = f"Successfully saved {save_count} breakout records to '{BREAKOUT_HISTORICAL_DB_FILE}' for date {date_str}."
        if not is_called_by_ai:
            print(msg)
        return msg

    except Exception as e_save_hist:
        msg = f"An unexpected error occurred processing/saving historical breakout data: {e_save_hist}"
        if not is_called_by_ai:
            print(msg)
            traceback.print_exc()
        return msg
   
async def handle_breakout_command(args: List[str], ai_params: Optional[Dict] = None, is_called_by_ai: bool = False):
    """ Handles breakout stock analysis by running analysis or saving data. """
    action_to_perform = "run"; date_str_for_save = None
    if ai_params:
        action_to_perform = ai_params.get("action", "run")
        if action_to_perform == "save": date_str_for_save = ai_params.get("date_to_save", datetime.now().strftime('%m/%d/%Y'))
    elif args and args[0] == "3725":
        action_to_perform = "save"; date_str_for_save = input("Enter date (MM/DD/YYYY) to save breakout data: ")

    if action_to_perform == "save":
        # --- MODIFICATION: Removed emojis ---
        if not date_str_for_save: msg = "Error: Date is required for saving."; print(f"[Error] {msg}"); return msg if is_called_by_ai else None
        try: datetime.strptime(date_str_for_save, '%m/%d/%Y') # Validate date format
        except ValueError: msg = "Error: Invalid date format. Use MM/DD/YYYY."; print(f"[Error] {msg}"); return msg if is_called_by_ai else None
        save_summary = await save_breakout_data_singularity(date_str_for_save, is_called_by_ai=is_called_by_ai)
        return save_summary if is_called_by_ai else None
    
    # --- MODIFICATION: Refactored this entire block ---
    elif action_to_perform == "run":
        # 1. Get the analysis result dictionary
        analysis_result = await run_breakout_analysis_singularity(is_called_by_ai=is_called_by_ai)
        
        # 2. Extract data for local processing
        breakout_stocks = analysis_result.get("current_breakout_stocks", [])
        status = analysis_result.get("status", "error")
        message = analysis_result.get("message", "Analysis failed.")

        # 3. Handle CLI (user) output
        if not is_called_by_ai:
             print("\n--- Breakout Stocks Analysis ---")
             if breakout_stocks:
                 print(tabulate(breakout_stocks, headers="keys", tablefmt="pretty"))
             else:
                 print(message) # Print the message (e.g., "No stocks found", "Screener failed")

        # 4. Handle file saving (only on success with stocks)
        if status == "success" and breakout_stocks:
            try:
                df_to_save = pd.DataFrame(breakout_stocks)
                df_to_save.to_csv(BREAKOUT_TICKERS_FILE, index=False)
                # Removed emoji from save_msg
                save_msg = f"Saved {len(breakout_stocks)} records to {BREAKOUT_TICKERS_FILE}"
                if not is_called_by_ai:
                    print(f"\n{save_msg}")
            except Exception as e:
                save_err_msg = f"Error saving breakout results: {e}"
                if not is_called_by_ai:
                    # Removed emoji from print
                    print(f"\n[Error] {save_err_msg}")
                # Don't alter the analysis_result here, the workflow needs the original dict

        # 5. Return the correct type based on caller
        if is_called_by_ai:
            # AI (and synthesized workflows) get the raw dictionary
            return analysis_result
        else:
            # CLI user call returns None (as output was already printed)
            return None
    # --- END MODIFICATION ---
