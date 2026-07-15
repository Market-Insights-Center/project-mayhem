# nexus_command.py

import asyncio
import os
import csv
import json
import math
import traceback
import re
import configparser
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict

import pandas as pd
from tabulate import tabulate
import pytz

# --- Imports from other command modules ---
from invest_command import process_custom_portfolio, calculate_ema_invest
from market_command import calculate_market_invest_scores_singularity, get_sp500_symbols_singularity
from breakout_command import run_breakout_analysis_singularity
from cultivate_command import run_cultivate_analysis_singularity
# Ensure load_portfolio_config is imported from custom_command
from custom_command import PORTFOLIO_DB_FILE, load_portfolio_config 
from tracking_command import (
    _load_portfolio_run, _save_custom_portfolio_run_to_csv,
    generate_allocation_comparison_chart, send_notification,
    _get_custom_portfolio_run_csv_filepath
)

try:
    from execution_command import execute_portfolio_rebalance, get_robinhood_equity, get_robinhood_holdings
except ImportError:
    def execute_portfolio_rebalance(trades): print("Execution module not found.")
    def get_robinhood_equity(): return 0.0
    def get_robinhood_holdings(): return {}

# --- Constants ---
NEXUS_DB_FILE = 'nexus_portfolios.csv'
PORTFOLIO_OUTPUT_DIR = 'portfolio_outputs'

# --- Helper Functions ---

def _get_nexus_run_csv_filepath(nexus_code: str) -> str:
    """Returns the filepath for saving/loading a Nexus run."""
    return os.path.join(PORTFOLIO_OUTPUT_DIR, f"run_data_nexus_{nexus_code.lower().replace(' ', '_')}.csv")

async def _load_nexus_config(nexus_code: str) -> Optional[Dict[str, Any]]:
    """Loads a Nexus portfolio configuration from the CSV database."""
    if not os.path.exists(NEXUS_DB_FILE):
        return None
    try:
        with open(NEXUS_DB_FILE, mode='r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get('nexus_code', '').strip().lower() == nexus_code.lower():
                    return row
    except Exception as e:
        print(f"❌ Error loading Nexus config: {e}")
    return None

async def _save_nexus_config(config_data: Dict[str, Any]):
    """Saves a new Nexus configuration to the CSV database."""
    file_exists = os.path.exists(NEXUS_DB_FILE)
    fieldnames = list(config_data.keys())
    
    if file_exists:
        with open(NEXUS_DB_FILE, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            file_fieldnames = reader.fieldnames
        for k in fieldnames:
            if k not in file_fieldnames:
                file_fieldnames.append(k)
        fieldnames = file_fieldnames

    try:
        with open(NEXUS_DB_FILE, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            if not file_exists or os.path.getsize(NEXUS_DB_FILE) == 0:
                writer.writeheader()
            writer.writerow(config_data)
        print(f"✔ Nexus configuration for '{config_data['nexus_code']}' saved.")
    except Exception as e:
        print(f"❌ Error saving Nexus config: {e}")

async def _create_new_nexus_portfolio(nexus_code: str) -> Optional[Dict[str, Any]]:
    """Interactive CLI to create a new Nexus portfolio."""
    print(f"\n--- Creating New Nexus Portfolio: {nexus_code} ---")
    
    try:
        num_components = int(input("Enter number of sub-components (Portfolios or Commands): "))
        if num_components <= 0: raise ValueError
    except ValueError:
        print("Invalid number.")
        return None

    config_data = {
        'nexus_code': nexus_code,
        'num_components': num_components,
        'frac_shares': 'true'
    }

    total_weight = 0.0
    
    for i in range(1, num_components + 1):
        print(f"\n--- Component {i} ---")
        user_input = input("Type (Portfolio/Command) OR Enter Code/Name: ").strip()
        
        comp_type_lower = user_input.lower()
        
        # 1. Explicit Type: Portfolio
        if 'portfolio' in comp_type_lower or comp_type_lower == 'p':
            config_data[f'component_{i}_type'] = 'Portfolio'
            value = input("Enter Portfolio Code (from database): ").strip()
            if not await load_portfolio_config(value):
                print(f"⚠️ Warning: Portfolio '{value}' not found in DB. Proceeding anyway.")
            config_data[f'component_{i}_value'] = value
            
        # 2. Explicit Type: Command
        elif 'command' in comp_type_lower or comp_type_lower == 'c':
            config_data[f'component_{i}_type'] = 'Command'
            print("Available Commands: Market, Breakout, Cultivate")
            cmd_val = input("Enter Command Name: ").strip().title()
            
            if "Cultivate" in cmd_val:
                if "A" not in cmd_val and "B" not in cmd_val:
                    variant = input("Cultivate Code (A or B): ").strip().upper()
                    cmd_val = f"Cultivate {variant}"
            
            config_data[f'component_{i}_value'] = cmd_val

        # 3. Smart Detection (Handle direct input like 'iquantum' or 'Market')
        else:
            # A. Check for Command Keywords first
            if any(x in comp_type_lower for x in ['market', 'breakout', 'cultivate']):
                 print(f"   -> Identified '{user_input}' as a Command.")
                 config_data[f'component_{i}_type'] = 'Command'
                 
                 cmd_val = user_input.title()
                 if "Cultivate" in cmd_val and "A" not in cmd_val and "B" not in cmd_val:
                      variant = input("Cultivate Code (A or B): ").strip().upper()
                      cmd_val = f"Cultivate {variant}"
                 config_data[f'component_{i}_value'] = cmd_val

            # B. Check for Portfolio existence in DB
            elif await load_portfolio_config(user_input):
                print(f"   -> Identified '{user_input}' as a saved Portfolio.")
                config_data[f'component_{i}_type'] = 'Portfolio'
                config_data[f'component_{i}_value'] = user_input
                
            # C. Default / Fallback
            else:
                print(f"Unrecognized input '{user_input}'. Defaulting to Command 'Market'.")
                config_data[f'component_{i}_type'] = 'Command'
                config_data[f'component_{i}_value'] = 'Market'

        try:
            weight = float(input(f"Enter Weight for {config_data[f'component_{i}_value']} (%): "))
        except ValueError:
            weight = 0.0
        
        config_data[f'component_{i}_weight'] = weight
        total_weight += weight

    if not math.isclose(total_weight, 100.0, abs_tol=0.1):
        print(f"⚠️ Warning: Total weight is {total_weight}%, not 100%. Allocations will be scaled.")

    await _save_nexus_config(config_data)
    return config_data

async def _resolve_nexus_component(
    comp_type: str, 
    comp_value: str, 
    allocated_value: float,
    parent_path: str,
    allow_fractional: bool = True
) -> List[Dict[str, Any]]:
    """Executes a single component of the Nexus portfolio."""
    holdings = []
    
    # --- 1. Sub-Portfolio ---
    if comp_type.lower() == 'portfolio':
        sub_config = await load_portfolio_config(comp_value)
        if not sub_config:
            print(f"   -> ❌ Error: Sub-portfolio '{comp_value}' not found.")
            return []
            
        print(f"   -> Processing Sub-Portfolio '{comp_value}' (${allocated_value:,.2f})...")
        
        # Sub-portfolios handle their own rounding via process_custom_portfolio parameters
        _, _, _, sub_holdings = await process_custom_portfolio(
            portfolio_data_config=sub_config,
            tailor_portfolio_requested=True,
            total_value_singularity=allocated_value,
            frac_shares_singularity=allow_fractional, 
            is_custom_command_simplified_output=True,
            is_called_by_ai=True
        )
        
        for h in sub_holdings:
            h['sub_portfolio_id'] = comp_value
            h['path'] = [parent_path, comp_value] + h.get('path', [])
            # Standardize ticker case
            if 'ticker' in h: h['ticker'] = str(h['ticker']).upper().strip()
            holdings.append(h)

    # --- 2. Commands ---
    elif comp_type.lower() == 'command':
        print(f"   -> Running Command '{comp_value}' (${allocated_value:,.2f})...")
        tickers = []
        
        if "Market" in comp_value:
            sp500 = await asyncio.to_thread(get_sp500_symbols_singularity)
            if sp500:
                scores = await calculate_market_invest_scores_singularity(sp500, 2)
                valid_scores = [s for s in scores if s.get('score') is not None]
                tickers = [s['ticker'] for s in valid_scores[:10]]
        
        elif "Breakout" in comp_value:
            res = await run_breakout_analysis_singularity(is_called_by_ai=True)
            if res and res.get('status') == 'success':
                tickers = [item['Ticker'] for item in res.get('current_breakout_stocks', [])[:10]]
        
        elif "Cultivate" in comp_value:
            code = "A" if "A" in comp_value else "B"
            _, tailored, _, _, _, _, err = await run_cultivate_analysis_singularity(
                portfolio_value=allocated_value,
                frac_shares=allow_fractional,
                cultivate_code_str=code,
                is_called_by_ai=True
            )
            if not err:
                for h in tailored:
                    h['sub_portfolio_id'] = comp_value
                    h['path'] = [parent_path, comp_value]
                    if 'ticker' in h: h['ticker'] = str(h['ticker']).upper().strip()
                    holdings.append(h)
                return holdings
        
        # Process generated tickers for Market/Breakout
        if tickers:
            weight_per_stock = allocated_value / len(tickers)
            tasks = [calculate_ema_invest(t, 2, is_called_by_ai=True) for t in tickers]
            prices = await asyncio.gather(*tasks)
            
            for i, t in enumerate(tickers):
                t_clean = str(t).upper().strip() # Ensure clean ticker
                price = prices[i][0]
                score = prices[i][1]
                if price and price > 0:
                    shares = weight_per_stock / price
                    
                    # Apply Fractional Rule here for generated commands
                    if not allow_fractional:
                        shares = math.floor(shares) # Floor to be safe on budget
                    
                    if shares > 0:
                        holdings.append({
                            'ticker': t_clean,
                            'shares': shares,
                            'live_price_at_eval': price,
                            'actual_money_allocation': shares * price,
                            'actual_percent_allocation': ((shares * price)/allocated_value)*100,
                            'raw_invest_score': score,
                            'sub_portfolio_id': comp_value,
                            'path': [parent_path, comp_value]
                        })
        else:
            print(f"      (No tickers found for {comp_value})")

    return holdings

async def process_nexus_portfolio(
    nexus_config: Dict[str, Any],
    total_value: float,
    nexus_code: str
) -> Tuple[List[Dict[str, Any]], float]:
    """Orchestrates the Nexus calculation."""
    all_holdings = []
    num_components = int(nexus_config.get('num_components', 0))
    
    # Determine global fractional setting
    allow_fractional = str(nexus_config.get('frac_shares', 'true')).lower() == 'true'
    
    for i in range(1, num_components + 1):
        c_type = nexus_config.get(f'component_{i}_type')
        c_value = nexus_config.get(f'component_{i}_value')
        c_weight = float(nexus_config.get(f'component_{i}_weight', 0))
        
        if c_weight <= 0: continue
        
        allocated_money = total_value * (c_weight / 100.0)
        component_holdings = await _resolve_nexus_component(
            c_type, c_value, allocated_money, nexus_code, allow_fractional
        )
        all_holdings.extend(component_holdings)

    # --- AGGREGATION LOGIC ---
    # Combines same-ticker allocations from different sub-portfolios/commands
    aggregated_map = defaultdict(lambda: {
        'ticker': '', 'shares': 0.0, 'actual_money_allocation': 0.0, 
        'paths': set(), 'live_price': 0.0, 'score': 0.0
    })
    
    for h in all_holdings:
        t = str(h['ticker']).upper().strip() # Enforce Upper Case for Key
        aggregated_map[t]['ticker'] = t
        aggregated_map[t]['shares'] += float(h['shares'])
        aggregated_map[t]['actual_money_allocation'] += float(h['actual_money_allocation'])
        
        # Keep the latest price seen (should be similar/identical)
        if h.get('live_price_at_eval'):
            aggregated_map[t]['live_price'] = h['live_price_at_eval']
            
        aggregated_map[t]['score'] = h.get('raw_invest_score', 0)
        
        path_str = " > ".join([str(p) for p in h.get('path', [])])
        aggregated_map[t]['paths'].add(path_str)

    final_holdings = []
    total_spent = 0.0
    
    for t, data in aggregated_map.items():
        if data['shares'] > 0:
            
            # --- BYDDY Rounding Logic (Final Check) ---
            final_shares = data['shares']
            final_money = data['actual_money_allocation']
            
            if t == 'BYDDY':
                final_shares = round(final_shares)
                if data['live_price'] > 0:
                    final_money = final_shares * data['live_price']
            
            final_holdings.append({
                'ticker': t,
                'shares': final_shares,
                'live_price_at_eval': data['live_price'],
                'actual_money_allocation': final_money,
                'actual_percent_allocation': (final_money / total_value) * 100,
                'raw_invest_score': data['score'],
                'sub_portfolio_id': "Mixed" if len(data['paths']) > 1 else list(data['paths'])[0].split(' > ')[-1],
                'path': list(data['paths'])
            })
            total_spent += final_money
            
    final_cash = total_value - total_spent
    return final_holdings, final_cash

async def handle_nexus_command(args: List[str]):
    """Main entry point for the /nexus command."""
    print("\n--- /nexus Meta-Portfolio Command ---")
    if not args:
        print("Usage: /nexus <nexus_code>")
        return

    nexus_code = args[0]
    config = await _load_nexus_config(nexus_code)
    if not config:
        print(f"Nexus portfolio '{nexus_code}' not found.")
        create = input("Create new Nexus portfolio? (yes/no): ").lower()
        if create == 'yes':
            config = await _create_new_nexus_portfolio(nexus_code)
            if not config: return
        else:
            return

    print("⏳ Connecting to Robinhood to fetch current portfolio value...")
    rh_equity = await asyncio.to_thread(get_robinhood_equity)
    
    suggested_value = None
    if rh_equity > 0:
        suggested_value = math.floor(rh_equity * 0.98)
        print(f"✔ Robinhood Portfolio Value Fetched: ${rh_equity:,.2f}")
        print(f"  -> Suggested Tailoring Value (98%): ${suggested_value:,.2f}")
    else:
        print("⚠️ Could not fetch Robinhood value (or login failed). Proceeding with manual input.")

    val_prompt = "Enter total portfolio value"
    if suggested_value: val_prompt += f" (default: {suggested_value})"
    val_prompt += ": "
    val_input = input(val_prompt).strip()
    
    total_value = 0.0
    if not val_input and suggested_value:
        total_value = float(suggested_value)
    else:
        try:
            total_value = float(val_input)
            if total_value <= 0: raise ValueError
        except ValueError:
            print("Invalid value.")
            return

    print(f"\n🚀 Running Nexus '{nexus_code}' with ${total_value:,.2f}...")
    new_holdings, new_cash = await process_nexus_portfolio(config, total_value, nexus_code)

    print(f"\n--- Nexus Allocation: {nexus_code} ---")
    table_data = []
    for h in sorted(new_holdings, key=lambda x: x['actual_money_allocation'], reverse=True):
        paths = "\n".join(h['path']) if isinstance(h['path'], list) else str(h['path'])
        table_data.append([
            h['ticker'], f"{h['shares']:.2f}", f"${h['actual_money_allocation']:,.2f}", 
            f"{h['actual_percent_allocation']:.1f}%", paths
        ])
    cash_pct = (new_cash / total_value) * 100
    table_data.append(["CASH", "-", f"${new_cash:,.2f}", f"{cash_pct:.1f}%", "-"])
    print(tabulate(table_data, headers=["Ticker", "Shares", "$ Value", "% Alloc", "Source(s)"], tablefmt="grid"))

    # --- Trade Generation Logic (Live vs Saved) ---
    print("\n⏳ Fetching live holdings for comparison...")
    live_holdings = await asyncio.to_thread(get_robinhood_holdings)
    old_holdings_map = {}
    
    if live_holdings:
        print("ℹ️ Using LIVE Robinhood holdings for trade recommendations.")
        old_holdings_map = live_holdings
    else:
        print("⚠️ Live holdings unavailable. Falling back to last saved CSV run.")
        old_run_filepath = _get_custom_portfolio_run_csv_filepath(f"nexus_{nexus_code}")
        if os.path.exists(old_run_filepath):
            try:
                with open(old_run_filepath, 'r') as f:
                    lines = f.readlines()
                    data_lines = [l for l in lines if not l.startswith('#')]
                    reader = csv.DictReader(data_lines)
                    for row in reader:
                        if row['Ticker'] != 'Cash':
                            old_holdings_map[row['Ticker']] = float(row['Shares'])
            except Exception: pass
        else:
             print("ℹ️ No saved run found. Assuming all positions are new.")

    # --- CONSTRAINT VERIFICATION & TRADE CALCULATION ---
    print("⚙️ Verifying Order Minimums & Constraints...")
    
    # Nexus global fractional setting (needed for adjustments)
    use_frac_shares_new = str(config.get('frac_shares', 'true')).lower() == 'true'
    
    adjusted_count = 0
    # Apply pre-check adjustments to new_holdings in place
    for h in new_holdings:
        ticker = h['ticker']
        if not ticker or ticker == 'Cash': continue
        
        price = h.get('live_price_at_eval', 0.0)
        if not price or price <= 0: continue
        
        raw_target_shares = float(h.get('shares', 0))
        
        # 1. BYDDY Rule (Redundant but safe)
        if ticker == 'BYDDY':
            raw_target_shares = round(raw_target_shares)
            h['shares'] = raw_target_shares # Store as float/int
            h['actual_money_allocation'] = raw_target_shares * price
            
        current_shares = old_holdings_map.get(ticker, 0.0)
        diff = raw_target_shares - current_shares
        
        # 2. Minimum $1.00 Rule for Buys
        if diff > 0:
            trade_value = diff * price
            if trade_value < 1.00:
                is_fractional = use_frac_shares_new and (ticker != 'BYDDY')
                step = 0.01 if is_fractional else 1.0
                
                # Bump shares until trade value >= $1.00
                while (diff * price) < 1.00:
                    diff += step
                    
                new_target_shares = current_shares + diff
                h['shares'] = new_target_shares
                h['actual_money_allocation'] = new_target_shares * price
                adjusted_count += 1
                
    if adjusted_count > 0:
        print(f"-> Adjusted {adjusted_count} Nexus orders to meet minimum $1.00 execution size.")

    trades_to_execute = []
    comparison_table = []
    
    # Recalculate maps after adjustments
    new_holdings_map = {h['ticker']: float(h['shares']) for h in new_holdings}
    all_tickers = sorted(list(set(old_holdings_map.keys()) | set(new_holdings_map.keys())))
    
    for ticker in all_tickers:
        old_s = old_holdings_map.get(ticker, 0.0)
        new_s = new_holdings_map.get(ticker, 0.0)
        change = new_s - old_s
        status = ""
        
        if old_s == 0 and new_s > 0: status = "New"
        elif new_s == 0 and old_s > 0: status = "Removed"
        elif not math.isclose(change, 0, abs_tol=1e-4): status = "Modified"
        
        if status:
            comparison_table.append([ticker, f"{old_s:.2f}", f"{new_s:.2f}", f"{change:+.2f}", status])
            if change > 0: trades_to_execute.append({'ticker': ticker, 'side': 'buy', 'quantity': abs(change)})
            elif change < 0: trades_to_execute.append({'ticker': ticker, 'side': 'sell', 'quantity': abs(change)})

    if comparison_table:
        print("\n--- Recommended Changes ---")
        print(tabulate(comparison_table, headers=["Ticker", "Old Shares", "New Shares", "Change", "Status"], tablefmt="pretty"))
    else:
        print("\n--- No Changes Recommended ---")

    print("\n--- 📧 Trade Recommendation Email ---")
    if input("Send email? (yes/no): ").lower() == 'yes':
        email_body = f"Nexus Report: {nexus_code}\nTotal Value: ${total_value:,.2f}\n\n"
        if comparison_table:
            email_body += tabulate(comparison_table, headers=["Ticker", "Old", "New", "Change", "Status"])
        else:
            email_body += "No trades recommended."
        await send_notification(f"Nexus Update: {nexus_code}", email_body)

    executed = False
    if trades_to_execute:
        print(f"\n🚀 Detected {len(trades_to_execute)} potential trades.")
        if input(">>> Execute on Robinhood? (yes/no): ").lower() == 'yes':
            # Pass live_holdings if we have them so execution doesn't fail blind checks
            await asyncio.to_thread(execute_portfolio_rebalance, trades_to_execute, old_holdings_map if live_holdings else None)
            executed = True

    if executed or input("\nSave this run as the new baseline? (yes/no): ").lower() == 'yes':
        for h in new_holdings:
            if isinstance(h.get('path'), list): h['path'] = " > ".join(h['path'])
        await _save_custom_portfolio_run_to_csv(
            portfolio_code=f"nexus_{nexus_code}",
            tailored_stock_holdings=new_holdings,
            final_cash=new_cash,
            total_portfolio_value_for_percent_calc=total_value
        )
        print("✔ Nexus run saved.")
    print("\n/nexus command complete.")