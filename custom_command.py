# --- Imports for custom_command ---
import asyncio
import os
import csv
from datetime import datetime
import pytz
import math
import traceback
from typing import List, Dict, Any, Optional
from collections import defaultdict

import pandas as pd
from tabulate import tabulate

# --- Imports from other command modules ---
from invest_command import process_custom_portfolio

# --- Constants (copied for self-containment) ---
PORTFOLIO_DB_FILE = 'portfolio_codes_database.csv'
PORTFOLIO_OUTPUT_DIR = 'portfolio_outputs'
TRACKING_ORIGIN_FILE = 'tracking_origin_data.csv'

# --- Helper Functions (copied or moved for self-containment) ---

def safe_score(value: Any) -> float:
    try:
        if pd.isna(value) or value is None: return 0.0
        if isinstance(value, str): value = value.replace('%', '').replace('$', '').strip()
        return float(value)
    except (ValueError, TypeError): return 0.0

def ensure_portfolio_output_dir():
    if not os.path.exists(PORTFOLIO_OUTPUT_DIR):
        try:
            os.makedirs(PORTFOLIO_OUTPUT_DIR)
        except OSError:
            pass # Fail silently if creation fails in a race condition

def ask_singularity_input(prompt: str, validation_fn=None, error_msg: str = "Invalid input.", default_val=None, is_called_by_ai: bool = False) -> Optional[str]:
    if is_called_by_ai:
        return None
    while True:
        full_prompt = f"{prompt}"
        if default_val is not None:
            full_prompt += f" (default: {default_val if default_val != '' else 'None'}, press Enter to use)"
        full_prompt += ": "
        user_response = input(full_prompt).strip()
        if not user_response and default_val is not None:
            return str(default_val)
        if not user_response and default_val is None:
            print("Input is required.")
            continue
        if validation_fn:
            if validation_fn(user_response):
                return user_response
            else:
                print(error_msg)
                retry = input("Try again? (yes/no, default: yes): ").lower().strip()
                if retry == 'no':
                    return None
        else:
            return user_response

async def collect_portfolio_inputs_singularity(portfolio_code_singularity: str, is_called_by_ai: bool = False) -> Optional[Dict[str, Any]]:
    inputs = {'portfolio_code': portfolio_code_singularity}
    try:
        while True:
            ema_sens_str = input("Enter EMA sensitivity (1: Weekly, 2: Daily, 3: Hourly): ")
            try: inputs['ema_sensitivity'] = str(int(ema_sens_str)); break
            except: print("Invalid input.")
        while True:
            amp_str = input("Enter amplification (e.g., 0.5, 1.0, 2.0): ")
            try: inputs['amplification'] = str(float(amp_str)); break
            except: print("Invalid input.")
        while True:
            num_port_str = input("Enter number of sub-portfolios: ")
            try: inputs['num_portfolios'] = str(int(num_port_str)); break
            except: print("Must be > 0.")
        while True:
            frac_s_str = input("Allow fractional shares? (yes/no): ").lower()
            if frac_s_str in ['yes', 'no']: inputs['frac_shares'] = 'true' if frac_s_str == 'yes' else 'false'; break
        inputs['risk_tolerance'] = '10'; inputs['risk_type'] = 'stock'; inputs['remove_amplification_cap'] = 'true'
        current_total_weight = 0.0
        for i in range(1, int(inputs['num_portfolios']) + 1):
            tickers_in = input(f"Enter tickers for Sub-Portfolio {i} (comma-separated): ").upper()
            inputs[f'tickers_{i}'] = tickers_in
            if i == int(inputs['num_portfolios']):
                weight_val = 100.0 - current_total_weight
            else:
                weight_str = input(f"Enter weight for Sub-Portfolio {i} (%): ")
                weight_val = float(weight_str)
            inputs[f'weight_{i}'] = f"{weight_val:.2f}"; current_total_weight += weight_val
        return inputs
    except (ValueError, Exception):
        return None

async def save_portfolio_to_csv(file_path: str, portfolio_data_to_save: Dict[str, Any], is_called_by_ai: bool = False):
    file_exists = os.path.isfile(file_path)
    fieldnames = ['portfolio_code', 'ema_sensitivity', 'amplification', 'num_portfolios', 'frac_shares', 'risk_tolerance', 'risk_type', 'remove_amplification_cap']
    num_portfolios_val = int(portfolio_data_to_save.get('num_portfolios', 0))
    for i in range(1, num_portfolios_val + 1):
        fieldnames.extend([f'tickers_{i}', f'weight_{i}'])
    try:
        with open(file_path, 'a', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore')
            if not file_exists or os.path.getsize(file_path) == 0: writer.writeheader()
            writer.writerow(portfolio_data_to_save)
    except Exception as e:
        if not is_called_by_ai:
            print(f"Error saving portfolio config: {e}")

def _get_custom_portfolio_run_csv_filepath(portfolio_code: str) -> str:
    return os.path.join(PORTFOLIO_OUTPUT_DIR, f"run_data_portfolio_{portfolio_code.lower().replace(' ','_')}.csv")

# Add this new function anywhere in the file
async def _update_portfolio_origin_data(portfolio_code: str, tailored_stock_holdings: List[Dict[str, Any]]):
    """
    Reads the origin data file and adds any new tickers from the current run.
    This function ensures that the first price/share count for a ticker is saved permanently.
    """
    origin_data = defaultdict(dict)
    if os.path.exists(TRACKING_ORIGIN_FILE):
        try:
            with open(TRACKING_ORIGIN_FILE, 'r', newline='', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row['PortfolioCode'] == portfolio_code:
                        origin_data[row['Ticker']] = {'Shares': row['Shares'], 'Price': row['Price']}
        except (IOError, csv.Error) as e:
            print(f"⚠️ Warning: Could not read origin data file: {e}")
            return # Abort if the file is unreadable

    # Identify new tickers from the current run that are not in our origin data
    new_entries_to_add = []
    for holding in tailored_stock_holdings:
        ticker = holding.get('ticker')
        if ticker and ticker != 'Cash' and ticker not in origin_data:
            new_entries_to_add.append({
                'PortfolioCode': portfolio_code,
                'Ticker': ticker,
                'Shares': holding.get('shares'),
                'Price': holding.get('live_price_at_eval')
            })

    if not new_entries_to_add:
        return # Nothing to do

    # Append only the new entries to the origin file
    try:
        file_exists = os.path.exists(TRACKING_ORIGIN_FILE)
        with open(TRACKING_ORIGIN_FILE, 'a', newline='', encoding='utf-8') as f:
            fieldnames = ['PortfolioCode', 'Ticker', 'Shares', 'Price']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists or os.path.getsize(TRACKING_ORIGIN_FILE) == 0:
                writer.writeheader()
            writer.writerows(new_entries_to_add)
            print(f"💾 Added {len(new_entries_to_add)} new ticker(s) to the permanent tracking origin file for '{portfolio_code}'.")
    except IOError as e:
        print(f"❌ Error: Could not write to origin data file: {e}")

# <<< START: REPLACEMENT FOR _save_custom_portfolio_run_to_csv >>>
async def _save_custom_portfolio_run_to_csv(portfolio_code: str, tailored_stock_holdings: List[Dict[str, Any]], final_cash: float, total_portfolio_value_for_percent_calc: Optional[float] = None, is_called_by_ai: bool = False):
    filepath = _get_custom_portfolio_run_csv_filepath(portfolio_code)
    timestamp_utc_str = datetime.now(pytz.UTC).isoformat()
    data_for_csv = []
    
    # Pre-processing to enforce BYDDY Integer Rounding before saving
    for holding in tailored_stock_holdings:
        ticker = holding.get('ticker')
        if ticker and ticker.upper() == 'BYDDY':
            try:
                shares = float(holding.get('shares', 0.0))
                price = float(holding.get('live_price_at_eval', 0.0))
                # Force round to nearest integer
                new_shares = round(shares)
                holding['shares'] = str(new_shares)
                
                # Adjust allocation values to match new share count if price is valid
                if price > 0:
                    holding['actual_money_allocation'] = str(new_shares * price)
                    if total_portfolio_value_for_percent_calc:
                         holding['actual_percent_allocation'] = str(((new_shares * price) / total_portfolio_value_for_percent_calc) * 100)
            except (ValueError, TypeError):
                pass # Fallback to original if conversion fails

    for holding in tailored_stock_holdings:
        # Format the path list into a user-friendly string
        path_str = ' > '.join(map(str, holding.get('path', [])))
        
        data_for_csv.append({
            'Ticker': holding.get('ticker'),
            'Shares': holding.get('shares'),
            'LivePriceAtEval': holding.get('live_price_at_eval'),
            'ActualMoneyAllocation': holding.get('actual_money_allocation'),
            'ActualPercentAllocation': holding.get('actual_percent_allocation'),
            'RawInvestScore': holding.get('raw_invest_score', 'N/A'),
            'SubPortfolio': holding.get('sub_portfolio_id', 'N/A'),
            'SubPortfolioPath': path_str # <<< ADDED HIERARCHICAL PATH
        })

    cash_percent_alloc_val = 'N/A'
    if total_portfolio_value_for_percent_calc and total_portfolio_value_for_percent_calc > 0:
        cash_percent_alloc_val = (final_cash / total_portfolio_value_for_percent_calc) * 100.0

    data_for_csv.append({
        'Ticker': 'Cash', 'Shares': '-', 'LivePriceAtEval': 1.0,
        'ActualMoneyAllocation': final_cash,
        'ActualPercentAllocation': f"{cash_percent_alloc_val:.2f}" if isinstance(cash_percent_alloc_val, float) else 'N/A',
        'RawInvestScore': 'N/A', 'SubPortfolio': 'N/A', 'SubPortfolioPath': 'Cash'
    })

    # Add the new column to the fieldnames
    fieldnames = ['Ticker', 'Shares', 'LivePriceAtEval', 'ActualMoneyAllocation', 'ActualPercentAllocation', 'RawInvestScore', 'SubPortfolio', 'SubPortfolioPath']
    try:
        ensure_portfolio_output_dir()
        with open(filepath, 'w', newline='', encoding='utf-8') as csvfile:
            csvfile.write(f"# portfolio_code: {portfolio_code}\n# timestamp_utc: {timestamp_utc_str}\n# ---BEGIN_DATA---\n")
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction='ignore') # Use ignore for safety
            writer.writeheader()
            writer.writerows(data_for_csv)
    except Exception as e:
        if not is_called_by_ai:
            print(f"❌ Error saving custom portfolio run CSV: {e}")

    if tailored_stock_holdings:
        await _update_portfolio_origin_data(portfolio_code, tailored_stock_holdings)
# <<< END: REPLACEMENT FOR _save_custom_portfolio_run_to_csv >>>
      
async def save_portfolio_data_singularity(portfolio_code_to_save: str, date_str_to_save: str, is_called_by_ai: bool = False):
    """
    Saves *combined percentage data* for a custom portfolio (original '3725' functionality).
    """
    if not os.path.exists(PORTFOLIO_DB_FILE):
        return
    portfolio_config_data = None
    try:
        df_db = pd.read_csv(PORTFOLIO_DB_FILE)
        portfolio_row = df_db[df_db['portfolio_code'].astype(str).str.lower() == portfolio_code_to_save.lower()]
        if not portfolio_row.empty:
            portfolio_config_data = portfolio_row.iloc[0].to_dict()
    except Exception:
        return

    if portfolio_config_data:
        _, combined_result_for_save, _, _ = await process_custom_portfolio(
            portfolio_data_config=portfolio_config_data, tailor_portfolio_requested=False,
            frac_shares_singularity=str(portfolio_config_data.get('frac_shares')).lower() == 'true',
            total_value_singularity=None, is_custom_command_simplified_output=True, is_called_by_ai=True
        )
        if combined_result_for_save:
            data_to_write_csv = [{'DATE': date_str_to_save, 'TICKER': item.get('ticker', 'ERR'), 'PRICE': f"{safe_score(item.get('live_price')):.2f}", 'COMBINED_ALLOCATION_PERCENT': f"{safe_score(item.get('combined_percent_allocation_adjusted')):.2f}"} for item in combined_result_for_save if item.get('ticker') != 'Cash' and safe_score(item.get('combined_percent_allocation_adjusted', 0)) > 1e-4]
            if data_to_write_csv:
                save_filename = f"portfolio_code_{portfolio_code_to_save}_data.csv"
                file_exists = os.path.isfile(save_filename)
                with open(save_filename, 'a', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=['DATE', 'TICKER', 'PRICE', 'COMBINED_ALLOCATION_PERCENT'])
                    if not file_exists or os.path.getsize(f.name) == 0: writer.writeheader()
                    writer.writerows(sorted(data_to_write_csv, key=lambda x: float(x['COMBINED_ALLOCATION_PERCENT']), reverse=True))

async def _load_custom_portfolio_run_from_csv(portfolio_code: str) -> Dict[str, Any]:
    """
    Loads the last saved detailed run output of a custom portfolio from its CSV file.
    """
    filepath = _get_custom_portfolio_run_csv_filepath(portfolio_code)
    if not os.path.exists(filepath):
        return {"status": "error", "message": f"No saved run data found for portfolio '{portfolio_code}'."}

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            holdings = list(csv.DictReader(f))
        
        # Basic parsing, assuming last row is cash and the rest are stocks
        cash_row = holdings.pop() if holdings and holdings[-1].get('Ticker') == 'Cash' else {}
        final_cash = float(cash_row.get('ActualMoneyAllocation', 0.0))
        
        return {"status": "success", "holdings": holdings, "final_cash": final_cash}
    except Exception as e:
        return {"status": "error", "message": f"Failed to load or parse saved data: {e}"}

async def get_comparison_for_custom_portfolio(ai_params: Optional[Dict] = None, is_called_by_ai: bool = True) -> Dict[str, Any]:
    """
    Loads saved output, runs fresh, returns a comparison, and overwrites the saved output.
    """
    portfolio_code = ai_params.get("portfolio_code") if ai_params else None
    if not portfolio_code:
        return {"status": "error", "message": "Portfolio code not provided."}

    # 1. Load the previously saved ("old") run
    old_run = await _load_custom_portfolio_run_from_csv(portfolio_code)
    old_holdings = {h['Ticker']: float(h.get('Shares', 0.0)) for h in old_run.get('holdings', [])} if old_run['status'] == 'success' else {}

    # 2. Run the portfolio fresh to get the "new" run
    try:
        df_db = pd.read_csv(PORTFOLIO_DB_FILE)
        config_row = df_db[df_db['portfolio_code'].str.lower() == portfolio_code.lower()]
        if config_row.empty:
            return {"status": "error", "message": f"Portfolio configuration for '{portfolio_code}' not found."}
        config = config_row.iloc[0].to_dict()

        tailor = ai_params.get('value_for_assessment') is not None
        value = float(ai_params['value_for_assessment']) if tailor else None
        frac_shares = ai_params.get('use_fractional_shares_override', config.get('frac_shares', 'false').lower() == 'true')

        _, _, new_cash, new_data = await process_custom_portfolio(
            portfolio_data_config=config, tailor_portfolio_requested=tailor,
            frac_shares_singularity=frac_shares, total_value_singularity=value,
            is_custom_command_simplified_output=True, is_called_by_ai=True)
        
        new_holdings = {h['ticker']: float(h.get('shares', 0.0)) for h in new_data}
    except Exception as e:
        return {"status": "error", "message": f"Failed to generate fresh run for comparison: {e}"}

    # 3. Compare old and new holdings
    all_tickers = sorted(list(set(old_holdings.keys()) | set(new_holdings.keys())))
    changes = []
    for ticker in all_tickers:
        old_shares = old_holdings.get(ticker, 0.0)
        new_shares = new_holdings.get(ticker, 0.0)
        if not math.isclose(old_shares, new_shares):
            changes.append(f"{ticker}: {old_shares:.2f} -> {new_shares:.2f} shares")

    summary = f"Comparison for '{portfolio_code}': "
    if not changes:
        summary += "No changes in holdings detected. "
    else:
        summary += "Changes detected: " + ", ".join(changes) + ". "

    # 4. Save the new run, overwriting the old one
    await _save_custom_portfolio_run_to_csv(
        portfolio_code, new_data, new_cash, value, is_called_by_ai=True
    )
    summary += "The new run has been saved as the current baseline."
    
    return {"status": "success", "summary": summary}

async def handle_custom_command(args: List[str], ai_params: Optional[Dict] = None, is_called_by_ai: bool = False):
    if not is_called_by_ai:
        print("\n--- /custom Command ---")
    summary_for_ai = "Custom command initiated."

    if ai_params: # AI Call
        action = ai_params.get("action")
        portfolio_code_input = ai_params.get("portfolio_code")

        if not portfolio_code_input:
            return "Error for AI (/custom): 'portfolio_code' is required."

        if action == "run_existing_portfolio":
            tailor_run_ai = ai_params.get("tailor_to_value", False)
            total_value_ai_float: Optional[float] = None
            # frac_shares_override_ai: Optional[bool] = ai_params.get("use_fractional_shares") if "use_fractional_shares" in ai_params else None
            # The frac_shares_final_run_ai logic below correctly handles the override or config value

            if tailor_run_ai:
                total_value_ai_raw = ai_params.get("total_value")
                if total_value_ai_raw is None:
                    return "Error for AI (/custom run): 'total_value' required when 'tailor_to_value' is true."
                try:
                    total_value_ai_float = float(total_value_ai_raw)
                    if total_value_ai_float <= 0:
                        return "Error for AI (/custom run): 'total_value' must be a positive number."
                except ValueError:
                    return "Error for AI (/custom run): 'total_value' is not a valid number."

            portfolio_config_from_db = None
            if not os.path.exists(PORTFOLIO_DB_FILE):
                return f"Error for AI (/custom run): Portfolio database '{PORTFOLIO_DB_FILE}' not found."
            try:
                with open(PORTFOLIO_DB_FILE, 'r', encoding='utf-8', newline='') as f_db:
                    reader = csv.DictReader(f_db)
                    for row in reader:
                        if row.get('portfolio_code', '').strip().lower() == portfolio_code_input.lower():
                            portfolio_config_from_db = row
                            break
                if not portfolio_config_from_db:
                    return f"Error for AI (/custom run): Portfolio code '{portfolio_code_input}' not found in database."

                frac_shares_override_ai: Optional[bool] = ai_params.get("use_fractional_shares") if "use_fractional_shares" in ai_params else None # Get override
                frac_shares_final_run_ai: bool
                if frac_shares_override_ai is not None:
                    frac_shares_final_run_ai = frac_shares_override_ai
                else:
                    csv_frac_shares_str = portfolio_config_from_db.get('frac_shares', 'false').strip().lower()
                    frac_shares_final_run_ai = csv_frac_shares_str in ['true', 'yes']

                # Process the portfolio
                _, _, final_cash_value_run, tailored_data_run = await process_custom_portfolio(
                    portfolio_data_config=portfolio_config_from_db,
                    tailor_portfolio_requested=tailor_run_ai,
                    frac_shares_singularity=frac_shares_final_run_ai,
                    total_value_singularity=total_value_ai_float,
                    is_custom_command_simplified_output=True, # True for AI calls if tailored
                    is_called_by_ai=True
                )

                # <<< MODIFIED SAVE CALL FOR AI PATH >>>
                await _save_custom_portfolio_run_to_csv(
                    portfolio_code=portfolio_code_input,
                    tailored_stock_holdings=tailored_data_run, # This is tailored_portfolio_structured_data
                    final_cash=final_cash_value_run,
                    total_portfolio_value_for_percent_calc=total_value_ai_float if tailor_run_ai else None,
                    is_called_by_ai=True
                )
                # <<< END MODIFIED SAVE CALL FOR AI PATH >>>

                summary_for_ai = f"Analysis for custom portfolio '{portfolio_code_input}' completed. Detailed run output saved/overwritten to CSV. "
                if tailor_run_ai:
                    summary_for_ai += f"Tailored to ${total_value_ai_float:,.2f} (Fractional Shares: {frac_shares_final_run_ai}). Final cash: ${final_cash_value_run:,.2f}."
                return summary_for_ai
            except Exception as e_ai_run:
                return f"Error processing AI request for /custom run '{portfolio_code_input}': {str(e_ai_run)}"

        elif action == "save_portfolio_data":
            date_to_save_legacy = ai_params.get("date_to_save")
            if not date_to_save_legacy:
                return "Error for AI (/custom save_portfolio_data): 'date_to_save' is required."
            try:
                datetime.strptime(date_to_save_legacy, '%m/%d/%Y')
            except ValueError:
                return f"Error for AI (/custom save_portfolio_data): Invalid date format '{date_to_save_legacy}'. Use MM/DD/YYYY."
            await save_portfolio_data_singularity(portfolio_code_input, date_to_save_legacy, is_called_by_ai=True)
            return f"Legacy combined percentage data for portfolio '{portfolio_code_input}' requested for save on {date_to_save_legacy}."
        else:
            return f"Error for AI (/custom): Unknown or unsupported action '{action}'."

    else: # CLI Path
        # ... (CLI argument parsing and new portfolio creation logic remains the same) ...
        if not args:
            print("Usage: /custom <portfolio_code_or_#> [save_data_code 3725 (for legacy combined % save)]")
            print("Note: Running a portfolio (e.g. /custom MYPORT) now automatically saves/overwrites its detailed run output to CSV.") # Updated note
            return None

        portfolio_code_cli = args[0].strip()
        legacy_save_code_cli = args[1].strip() if len(args) > 1 else None
        is_new_code_auto_cli = False

        if portfolio_code_cli == '#':
            next_code_num = 1
            if os.path.exists(PORTFOLIO_DB_FILE):
                max_code = 0
                try:
                    df_codes_cli = pd.read_csv(PORTFOLIO_DB_FILE)
                    numeric_codes_cli = pd.to_numeric(df_codes_cli['portfolio_code'], errors='coerce').dropna()
                    if not numeric_codes_cli.empty: max_code = int(numeric_codes_cli.max())
                except Exception: pass
                next_code_num = max_code + 1
            portfolio_code_cli = str(next_code_num)
            is_new_code_auto_cli = True
            print(f"CLI: Using next available portfolio code: `{portfolio_code_cli}`")

        if legacy_save_code_cli == "3725":
            # ... (legacy save logic unchanged) ...
            if is_new_code_auto_cli:
                print("CLI Error: Cannot use '#' (auto-generated code) directly with the legacy '3725' save_data_code.")
                return None
            date_to_save_str_cli = input(f"CLI: Enter date (MM/DD/YYYY) to save legacy combined % data for portfolio '{portfolio_code_cli}': ")
            try:
                datetime.strptime(date_to_save_str_cli, '%m/%d/%Y')
                await save_portfolio_data_singularity(portfolio_code_cli, date_to_save_str_cli, is_called_by_ai=False)
            except ValueError: print("CLI: Invalid date format for legacy save. Save operation cancelled.")
            return None


        portfolio_config_from_db_cli = None
        if os.path.exists(PORTFOLIO_DB_FILE) and not is_new_code_auto_cli:
            try:
                with open(PORTFOLIO_DB_FILE, 'r', encoding='utf-8', newline='') as file_cli_db:
                    reader_cli_db = csv.DictReader(file_cli_db)
                    for row_cli_db in reader_cli_db:
                        if row_cli_db.get('portfolio_code', '').strip().lower() == portfolio_code_cli.lower():
                            portfolio_config_from_db_cli = row_cli_db; break
            except Exception as e_read_db_cli: print(f"CLI: Error reading portfolio DB: {e_read_db_cli}")

        if portfolio_config_from_db_cli is None:
            print(f"CLI: Portfolio code '{portfolio_code_cli}' not found or creating new. Starting interactive setup...")
            new_portfolio_config_cli = await collect_portfolio_inputs_singularity(portfolio_code_cli, is_called_by_ai=False)
            if new_portfolio_config_cli:
                await save_portfolio_to_csv(PORTFOLIO_DB_FILE, new_portfolio_config_cli, is_called_by_ai=False)
                portfolio_config_from_db_cli = new_portfolio_config_cli
                print(f"CLI: New portfolio configuration '{portfolio_code_cli}' saved.")
                run_now_cli_str = input(f"Run portfolio '{portfolio_code_cli}' now with this new configuration? (yes/no, default: yes): ").lower().strip()
                if run_now_cli_str == 'no': return None
            else: print("CLI: Portfolio configuration cancelled or incomplete."); return None
        
        # This is the part for running an existing or newly created portfolio via CLI
        if portfolio_config_from_db_cli:
            try:
                csv_frac_shares_str_cli = portfolio_config_from_db_cli.get('frac_shares', 'false').strip().lower()
                frac_shares_setting_from_config_cli = csv_frac_shares_str_cli in ['true', 'yes']
                frac_shares_for_this_run_cli = frac_shares_setting_from_config_cli

                print(f"--- Running Custom Portfolio: {portfolio_code_cli} ---")
                print(f"  Configuration default for fractional shares: {frac_shares_setting_from_config_cli}")

                tailor_this_run_cli = False
                total_value_for_this_run_cli: Optional[float] = None

                tailor_prompt_cli = input(f"CLI: Tailor portfolio '{portfolio_code_cli}' to a value for this run? (yes/no, default: no): ").lower().strip()
                if tailor_prompt_cli == 'yes':
                    tailor_this_run_cli = True
                    val_input_cli = input("CLI: Enter total portfolio value for tailoring: ").strip()
                    try:
                        total_value_for_this_run_cli = float(val_input_cli)
                        if total_value_for_this_run_cli <= 0:
                            print("CLI: Portfolio value must be positive. Proceeding without tailoring.")
                            tailor_this_run_cli = False
                        else: # Value is positive, ask about fractional shares for this run
                            override_frac_s_cli = input(f"CLI: Override fractional shares for this run? (current config: {frac_shares_setting_from_config_cli}) (yes/no/config, default: config): ").lower().strip()
                            if override_frac_s_cli == 'yes': frac_shares_for_this_run_cli = True
                            elif override_frac_s_cli == 'no': frac_shares_for_this_run_cli = False
                            # If 'config' or empty, frac_shares_for_this_run_cli remains as frac_shares_setting_from_config_cli
                    except ValueError:
                        print("CLI: Invalid portfolio value. Proceeding without tailoring.")
                        tailor_this_run_cli = False
                
                print(f"  For this run, using fractional shares: {frac_shares_for_this_run_cli}")
                if tailor_this_run_cli: print(f"  Tailoring to value: ${total_value_for_this_run_cli:,.2f}")
                else: print("  Not tailoring to a specific value (will show percentages if not tailored).")

                # Process the portfolio
                _, _, final_cash_cli_run, tailored_data_cli_run = await process_custom_portfolio(
                    portfolio_data_config=portfolio_config_from_db_cli,
                    tailor_portfolio_requested=tailor_this_run_cli,
                    frac_shares_singularity=frac_shares_for_this_run_cli,
                    total_value_singularity=total_value_for_this_run_cli,
                    is_custom_command_simplified_output=tailor_this_run_cli,
                    is_called_by_ai=False
                )

                # <<< MODIFIED SAVE CALL FOR CLI PATH >>>
                await _save_custom_portfolio_run_to_csv(
                    portfolio_code=portfolio_code_cli,
                    tailored_stock_holdings=tailored_data_cli_run, # This is tailored_portfolio_structured_data
                    final_cash=final_cash_cli_run,
                    total_portfolio_value_for_percent_calc=total_value_for_this_run_cli if tailor_this_run_cli else None,
                    is_called_by_ai=False
                )
                print(f"\nCLI: Custom portfolio analysis for `{portfolio_code_cli}` complete. Detailed run output saved/overwritten to CSV.")
                # <<< END MODIFIED SAVE CALL FOR CLI PATH >>>

            except Exception as e_custom_cli_run:
                print(f"CLI Error processing portfolio '{portfolio_code_cli}': {e_custom_cli_run}")
                traceback.print_exc()
        return None
    
async def load_portfolio_config(portfolio_code: str) -> Optional[Dict[str, Any]]:
    """Robustly loads a specific portfolio's configuration from the database CSV."""
    if not os.path.exists(PORTFOLIO_DB_FILE):
        print(f"❌ Error: Portfolio database file '{PORTFOLIO_DB_FILE}' not found.")
        return None
        
    try:
        with open(PORTFOLIO_DB_FILE, mode='r', encoding='utf-8') as infile:
            # Use skipinitialspace=True to handle whitespace in data rows
            reader = csv.reader(infile, skipinitialspace=True)
            try:
                header = [h.strip() for h in next(reader)]
            except StopIteration:
                return None # Empty file

            try:
                code_index = header.index('portfolio_code')
            except ValueError:
                print(f"❌ Error: 'portfolio_code' column not found in '{PORTFOLIO_DB_FILE}'.")
                return None

            for row in reader:
                if len(row) > code_index and str(row[code_index]).lower() == portfolio_code.lower():
                    padded_row = row + [None] * (len(header) - len(row))
                    return dict(zip(header, padded_row))
        
        print(f"❌ Error: Portfolio configuration for '{portfolio_code}' not found in database.")
        return None
    except Exception as e:
        print(f"❌ Error loading portfolio configuration: {e}")
        return None