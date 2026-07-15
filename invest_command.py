# invest_command.py

# --- Imports for Invest Command ---
import yfinance as yf
import pandas as pd
import math
from tabulate import tabulate
import os
import uuid
import matplotlib
matplotlib.use('Agg')
import asyncio
import matplotlib.pyplot as plt
import numpy as np
from typing import Optional, List, Dict, Any
import traceback
import csv
from collections import defaultdict

# --- Global Dependencies & Constants ---
YFINANCE_API_SEMAPHORE = asyncio.Semaphore(8)
RISK_CSV_FILE = 'market_data.csv'
PORTFOLIO_DB_FILE = 'portfolio_codes_database.csv'

# --- Helper Functions ---

def safe_score(value: Any) -> float:
    """Safely converts a value to a float, returning 0.0 on failure."""
    try:
        if pd.isna(value) or value is None: return 0.0
        if isinstance(value, str): value = value.replace('%', '').replace('$', '').strip()
        return float(value)
    except (ValueError, TypeError): return 0.0

def get_allocation_score(is_called_by_ai: bool = False) -> tuple[float, float, float]:
    """
    Robustly reads the last line of the market data CSV to get risk scores.
    This version is resilient to column name variations (case, space, underscore, suffixes).
    """
    avg_s, gen_s, mkt_inv_s = 50.0, 50.0, 50.0 # Defaults
    if not os.path.exists(RISK_CSV_FILE):
        if not is_called_by_ai:
            print(f"Warning: Market data file '{RISK_CSV_FILE}' not found. Using defaults (50.0).")
        return avg_s, gen_s, mkt_inv_s

    try:
        with open(RISK_CSV_FILE, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            header = [h.strip() for h in next(reader)]
            last_line = None
            for line in reader:
                if line:
                    last_line = line
        
        if not last_line:
            if not is_called_by_ai: print(f"Warning: '{RISK_CSV_FILE}' is empty. Using defaults (50.0).")
            return avg_s, gen_s, mkt_inv_s

        latest_data = dict(zip(header, last_line))

        gen_score_key = next((k for k in latest_data if k.lower().strip().startswith('general market score')), None)
        inv_score_key = next((k for k in latest_data if k.lower().strip().startswith('raw market invest score')), None)

        if not (gen_score_key and inv_score_key):
            if not is_called_by_ai: 
                print(f"Warning: '{RISK_CSV_FILE}' missing required columns ('General Market Score', 'Raw Market Invest Score'). Using defaults (50.0).")
                print(f"DEBUG: Found columns: {list(latest_data.keys())}")
            return avg_s, gen_s, mkt_inv_s

        gs_val = safe_score(latest_data.get(gen_score_key))
        mis_val = safe_score(latest_data.get(inv_score_key))
        
        avg_s_calc = (gs_val + (2 * mis_val)) / 3.0
        avg_s = max(0, min(100, avg_s_calc))
        gen_s = max(0, min(100, gs_val))
        mkt_inv_s = max(0, min(100, mis_val))
        
        if not is_called_by_ai: print(f"  get_allocation_score: Using scores: Avg(Sigma)={avg_s:.2f}, Gen={gen_s:.2f}, MktInv={mkt_inv_s:.2f}")
        return avg_s, gen_s, mkt_inv_s

    except Exception as e:
        if not is_called_by_ai: print(f"Error in get_allocation_score: {e}. Using defaults (50.0).")
        return avg_s, gen_s, mkt_inv_s

async def _load_all_portfolio_configs(is_called_by_ai: bool = False) -> Dict[str, Dict[str, Any]]:
    """
    Loads all portfolio configurations from the database CSV into a dictionary
    keyed by a lowercase portfolio_code for efficient lookup.
    """
    configs = {}
    if not os.path.exists(PORTFOLIO_DB_FILE):
        if not is_called_by_ai:
            print(f"Warning: Portfolio database file '{PORTFOLIO_DB_FILE}' not found.")
        return configs
    try:
        with open(PORTFOLIO_DB_FILE, mode='r', encoding='utf-8') as infile:
            reader = csv.DictReader(infile)
            for row in reader:
                code = row.get('portfolio_code')
                if code:
                    configs[code.lower().strip()] = {k.strip(): v for k, v in row.items()}
        return configs
    except Exception as e:
        if not is_called_by_ai:
            print(f"Error loading all portfolio configurations: {e}")
        return configs

async def calculate_ema_invest(ticker: str, ema_interval: int, is_called_by_ai: bool = False) -> tuple[Optional[float], Optional[float]]:
    async with YFINANCE_API_SEMAPHORE:
        stock = yf.Ticker(ticker.replace('.', '-'))
        interval_map = {1: "1wk", 2: "1d", 3: "1h"}
        period_map = {1: "max", 2: "10y", 3: "2y"}
        try:
            await asyncio.sleep(np.random.uniform(0.1, 0.3))
            data = await asyncio.to_thread(stock.history, period=period_map.get(ema_interval, "2y"), interval=interval_map.get(ema_interval, "1h"))
            if data.empty or 'Close' not in data.columns: return None, None
            data['EMA_8'] = data['Close'].ewm(span=8, adjust=False).mean()
            data['EMA_55'] = data['Close'].ewm(span=55, adjust=False).mean()
            if data.empty or data.iloc[-1][['Close', 'EMA_8', 'EMA_55']].isna().any():
                return (data['Close'].iloc[-1] if not data.empty and pd.notna(data['Close'].iloc[-1]) else None), None
            latest = data.iloc[-1]
            live_price, ema_8, ema_55 = latest['Close'], latest['EMA_8'], latest['EMA_55']
            if pd.isna(live_price) or pd.isna(ema_8) or pd.isna(ema_55) or ema_55 == 0: return live_price, None
            ema_invest_score = (((ema_8 - ema_55) / ema_55) * 4 + 0.5) * 100
            return float(live_price), float(ema_invest_score)
        except Exception:
            return None, None

async def calculate_one_year_invest(ticker: str, is_called_by_ai: bool = False) -> tuple[float, float]:
    async with YFINANCE_API_SEMAPHORE:
        try:
            await asyncio.sleep(np.random.uniform(0.1, 0.3))
            data = await asyncio.to_thread(yf.Ticker(ticker.replace('.', '-')).history, period="1y")
            if data.empty or len(data) < 2 or 'Close' not in data.columns: return 0.0, 50.0
            start_price, end_price = data['Close'].iloc[0], data['Close'].iloc[-1]
            if pd.isna(start_price) or pd.isna(end_price) or start_price == 0: return 0.0, 50.0
            one_year_change = ((end_price - start_price) / start_price) * 100
            invest_per = (one_year_change / 2) + 50 if one_year_change < 0 else math.sqrt(max(0, one_year_change * 5)) + 50
            return float(one_year_change), float(max(0, min(invest_per, 100)))
        except Exception:
            return 0.0, 50.0
        
def plot_ticker_graph(ticker: str, ema_interval: int, is_called_by_ai: bool = False) -> Optional[str]:
    ticker_yf_format = ticker.replace('.', '-')
    stock = yf.Ticker(ticker_yf_format)
    interval_map = {1: "1wk", 2: "1d", 3: "1h"}
    period_map = {1: "5y", 2: "1y", 3: "6mo"}
    interval_str = interval_map.get(ema_interval, "1h")
    period_str = period_map.get(ema_interval, "1y")
    try:
        data = stock.history(period=period_str, interval=interval_str)
        if data.empty or 'Close' not in data.columns: raise ValueError("No data")
        data['EMA_55'] = data['Close'].ewm(span=55, adjust=False).mean()
        data['EMA_8'] = data['Close'].ewm(span=8, adjust=False).mean()
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.plot(data.index, data['Close'], color='grey', label='Price', linewidth=1.0)
        ax.plot(data.index, data['EMA_55'], color='darkgreen', label='EMA 55', linewidth=1.5)
        ax.plot(data.index, data['EMA_8'], color='firebrick', label='EMA 8', linewidth=1.5)
        ax.set_title(f"{ticker} Price and EMAs ({interval_str})", color='white')
        ax.set_xlabel('Date', color='white'); ax.set_ylabel('Price', color='white')
        ax.legend(facecolor='black', edgecolor='white', labelcolor='white')
        ax.grid(True, color='dimgray', linestyle='--', linewidth=0.5, alpha=0.5)
        ax.tick_params(axis='x', colors='white'); ax.tick_params(axis='y', colors='white')
        fig.tight_layout()
        filename = f"{ticker}_graph_{uuid.uuid4().hex[:6]}.png"
        plt.savefig(filename, facecolor='black', edgecolor='black')
        plt.close(fig)
        if not is_called_by_ai: print(f"📂 Graph saved: {filename}")
        return filename
    except Exception as e:
        if not is_called_by_ai: print(f"❌ Error plotting graph for {ticker}: {e}")
        if 'fig' in locals() and plt.fignum_exists(fig.number): plt.close(fig)
        return None

def generate_portfolio_pie_chart(portfolio_allocations: List[Dict[str, Any]], chart_title: str, filename_prefix: str = "portfolio_pie", is_called_by_ai: bool = False) -> Optional[str]:
    if not portfolio_allocations:
        if not is_called_by_ai: print("Pie Chart Error: No allocation data.")
        return None
    valid_data = [{'ticker': item['ticker'], 'value': item['value']} for item in portfolio_allocations if item.get('value', 0) > 1e-9]
    if not valid_data:
        if not is_called_by_ai: print("Pie Chart Error: No positive allocations.")
        return None

    labels = [item['ticker'] for item in valid_data]
    sizes = [item['value'] for item in valid_data]
    total_value_chart = sum(sizes)

    threshold_percentage = 1.5
    max_individual_slices = 14
    if len(labels) > max_individual_slices + 1:
        sorted_allocs = sorted(zip(sizes, labels), reverse=True)
        display_labels, display_sizes, other_value = [], [], 0.0
        for i, (size, label) in enumerate(sorted_allocs):
            if i < max_individual_slices: display_labels.append(label); display_sizes.append(size)
            else: other_value += size
        if other_value > 1e-9: display_labels.append("Others"); display_sizes.append(other_value)
        labels, sizes = display_labels, display_sizes
        if not labels: return None

    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(12, 8))
    if not labels: plt.close(fig); return None

    custom_colors = ['#4E79A7', '#F28E2B', '#E15759', '#76B7B2', '#59A14F', '#EDC948', '#B07AA1', '#FF9DA7', '#9C755F', '#BAB0AC', '#A0CBE8', '#FFBE7D', '#F4ADA8', '#B5D9D0', '#8CD17D']
    colors_to_use = custom_colors[:len(labels)] if len(labels) <= len(custom_colors) else [plt.cm.get_cmap('viridis', len(labels))(i) for i in range(len(labels))]
    explode_values = [0.05 if i == 0 and len(labels) > 0 else 0 for i in range(len(labels))]

    wedges, _, autotexts = ax.pie(
        sizes, explode=explode_values, labels=None,
        autopct=lambda pct: f"{pct:.1f}%" if pct > threshold_percentage else '',
        startangle=90, colors=colors_to_use, pctdistance=0.80,
        wedgeprops={'edgecolor': '#2c2f33', 'linewidth': 1}
    )
    for autotext in autotexts: autotext.set_color('white'); autotext.set_fontsize(9); autotext.set_fontweight('bold')
    ax.set_title(chart_title, fontsize=18, color='white', pad=25, fontweight='bold')
    ax.axis('equal')
    legend_labels = [f'{l} ({s/total_value_chart*100:.1f}%)' for l, s in zip(labels, sizes)]
    ax.legend(wedges, legend_labels, title="Holdings", loc="center left", bbox_to_anchor=(1.05, 0, 0.5, 1),
              fontsize='medium', labelcolor='lightgrey', title_fontsize='large', facecolor='#36393f', edgecolor='grey')
    plt.tight_layout(rect=[0, 0, 0.85, 1])
    filename = f"{filename_prefix}_{uuid.uuid4().hex[:8]}.png"
    try:
        plt.savefig(filename, facecolor=fig.get_facecolor(), edgecolor='none', bbox_inches='tight')
        if not is_called_by_ai: print(f"Pie chart saved: {filename}")
    except Exception as e:
        if not is_called_by_ai: print(f"Error saving pie chart: {e}")
        plt.close(fig); return None
    plt.close(fig)
    return filename

# --- Core Logic for /invest and /custom ---
# <<< START: REPLACEMENT FOR process_custom_portfolio >>>
# <<< REPLACEMENT FOR process_custom_portfolio >>>
async def process_custom_portfolio(
    portfolio_data_config: Dict[str, Any],
    tailor_portfolio_requested: bool,
    frac_shares_singularity: bool,
    total_value_singularity: Optional[float] = None,
    is_custom_command_simplified_output: bool = False,
    is_called_by_ai: bool = False,
    names_map: Optional[Dict[str, str]] = None,
    all_portfolio_configs_passed: Optional[Dict[str, Dict[str, Any]]] = None,
    parent_path: Optional[List[str]] = None
) -> tuple[List[str], List[Dict[str, Any]], float, List[Dict[str, Any]]]:
    
    is_top_level_call = all_portfolio_configs_passed is None
    suppress_prints = (is_custom_command_simplified_output or is_called_by_ai) and is_top_level_call

    if is_top_level_call:
        all_portfolio_configs = await _load_all_portfolio_configs(is_called_by_ai=suppress_prints)
    else:
        all_portfolio_configs = all_portfolio_configs_passed

    sell_to_cash_active = False
    if is_top_level_call:
        avg_score, _, _ = get_allocation_score(is_called_by_ai=suppress_prints)
        if avg_score is not None and avg_score < 50.0:
            sell_to_cash_active = True
            if not suppress_prints:
                print(f"\n:warning: **Sell-to-Cash Feature Active!** (Avg Market Score: {avg_score:.2f} < 50).")
                print("  -> Stocks with a raw score below 50% will receive 0% allocation within their sub-portfolio.")

    if isinstance(portfolio_data_config, pd.Series):
        portfolio_data_config = portfolio_data_config.to_dict()

    cleaned_config = {str(k).strip(): v for k, v in portfolio_data_config.items()}
    
    current_portfolio_code = str(cleaned_config.get('portfolio_code', ''))
    current_names_map = names_map if names_map is not None else {}
    
    ema_sensitivity = int(safe_score(cleaned_config.get('ema_sensitivity', 3)))
    amplification = float(safe_score(cleaned_config.get('amplification', 1.0)))
    num_portfolios = int(safe_score(cleaned_config.get('num_portfolios', 0)))

    final_combined_portfolio_data_calc = []
    all_entries_for_graphs_plotting = []

    for i in range(num_portfolios):
        portfolio_index = i + 1
        sub_portfolio_id = f'Sub-Portfolio {i+1}'
        weight = safe_score(cleaned_config.get(f'weight_{portfolio_index}', '0'))
        if weight <= 0: continue

        tickers_str = cleaned_config.get(f'tickers_{portfolio_index}', '')
        items_in_sub = [item.strip().upper() for item in tickers_str.split(',') if item.strip()]
        
        # Build path for display and tracking
        parent_portfolio_name = current_names_map.get(current_portfolio_code.lower(), current_portfolio_code)
        sub_portfolio_name = current_names_map.get(f"{current_portfolio_code.lower()}|{sub_portfolio_id}", sub_portfolio_id)
        current_path = (parent_path or []) + [parent_portfolio_name, sub_portfolio_name]

        # This sub-portfolio contains only one item, and it's another portfolio
        if len(items_in_sub) == 1 and items_in_sub[0].lower() in all_portfolio_configs:
            nested_code = items_in_sub[0].lower()
            nested_config = all_portfolio_configs[nested_code]
            
            # RECURSIVE CALL: Process the nested portfolio as a block
            _, nested_combined_data, _, _ = await process_custom_portfolio(
                portfolio_data_config=nested_config,
                tailor_portfolio_requested=False, # Always false for nested calls
                frac_shares_singularity=False,
                is_custom_command_simplified_output=True, # Suppress prints in recursion
                is_called_by_ai=True,
                all_portfolio_configs_passed=all_portfolio_configs,
                names_map=current_names_map,
                parent_path=current_path
            )
            
            # Scale the results of the nested portfolio by the sub-portfolio's weight
            for nested_entry in nested_combined_data:
                scaled_allocation = nested_entry.get('combined_percent_allocation', 0.0) * (weight / 100.0)
                nested_entry['combined_percent_allocation'] = scaled_allocation
                final_combined_portfolio_data_calc.append(nested_entry)
                if nested_entry.get('live_price', 0) > 0:
                    all_entries_for_graphs_plotting.append({'ticker': nested_entry['ticker'], 'ema_sensitivity': ema_sensitivity})

        # This sub-portfolio contains regular tickers
        else:
            current_portfolio_list_calc = []
            tasks = [calculate_ema_invest(ticker, ema_sensitivity, is_called_by_ai=True) for ticker in items_in_sub]
            results = await asyncio.gather(*tasks)
            
            for ticker, (live_price, ema_invest) in zip(items_in_sub, results):
                if live_price is None and ema_invest is None: continue
                
                ema_invest_score = 50.0 if ema_invest is None else ema_invest
                live_price_val = 0.0 if live_price is None else live_price
                raw_score = safe_score(ema_invest_score)
                
                score_for_alloc = raw_score
                if sell_to_cash_active and raw_score < 50.0:
                    score_for_alloc = 0.0
                
                amplified_score = max(0, safe_score((score_for_alloc * amplification) - (amplification - 1) * 50))
                
                entry_data = {
                    'ticker': ticker, 'sub_portfolio_id': sub_portfolio_id,
                    'live_price': live_price_val, 'raw_invest_score': raw_score,
                    'amplified_score_adjusted': amplified_score,
                    'path': current_path + [ticker]
                }
                current_portfolio_list_calc.append(entry_data)
                if live_price_val > 0:
                    all_entries_for_graphs_plotting.append({'ticker': ticker, 'ema_sensitivity': ema_sensitivity})

            # Normalize within this ticker-based sub-portfolio
            total_amplified_score_in_sub = sum(e['amplified_score_adjusted'] for e in current_portfolio_list_calc)
            for entry in current_portfolio_list_calc:
                internal_alloc = (entry['amplified_score_adjusted'] / total_amplified_score_in_sub) * 100 if total_amplified_score_in_sub > 0 else 0
                final_alloc = (internal_alloc * weight) / 100.0
                entry['combined_percent_allocation'] = final_alloc
                final_combined_portfolio_data_calc.append(entry)

    # --- Post-processing and Display (only for top-level call) ---
    if not is_top_level_call:
        # For recursive calls, just return the calculated data
        return [], final_combined_portfolio_data_calc, 0.0, []

    # --- Graph plotting and detailed sub-portfolio display logic ---
    sent_graphs = set()
    if not suppress_prints:
        for graph_entry in all_entries_for_graphs_plotting:
            ticker_key_graph = graph_entry.get('ticker')
            if not ticker_key_graph or ticker_key_graph in sent_graphs: continue
            await asyncio.to_thread(plot_ticker_graph, ticker_key_graph, graph_entry['ema_sensitivity'], is_called_by_ai=suppress_prints)
            sent_graphs.add(ticker_key_graph)

    final_combined_portfolio_data_calc.sort(key=lambda x: x.get('raw_invest_score', -float('inf')), reverse=True)
    
    if not suppress_prints:
        print("\n**--- Final Combined Portfolio (Sorted by Raw Score)---**")
        combined_data_display_final = []
        for entry_disp_final in final_combined_portfolio_data_calc:
            comb_alloc_f_disp = f"{round(entry_disp_final.get('combined_percent_allocation', 0), 2):.2f}%"
            combined_data_display_final.append([
                entry_disp_final.get('ticker', 'ERR'),
                f"${entry_disp_final.get('live_price', 0):.2f}",
                f"{safe_score(entry_disp_final.get('raw_invest_score')):.2f}%",
                f"{safe_score(entry_disp_final.get('amplified_score_adjusted', 0)):.2f}%",
                comb_alloc_f_disp
            ])
        if not combined_data_display_final: print("No valid data for the combined portfolio.")
        else: print(tabulate(combined_data_display_final, headers=["Ticker", "Live Price", "Raw Score", "Basis Amplified %", "Final % Alloc"], tablefmt="pretty"))

    # --- Tailoring logic ---
    tailored_portfolio_output_list_final = []
    tailored_portfolio_structured_data = []
    final_cash_value_tailored = 0.0

    if tailor_portfolio_requested:
        total_value_float_for_tailor = safe_score(total_value_singularity)
        if total_value_float_for_tailor <= 0:
            if not suppress_prints: print("Error: Tailored portfolio requested but total value is zero or negative.")
            return [], final_combined_portfolio_data_calc, total_value_float_for_tailor, []
        
        current_tailored_entries_for_calc = []
        total_actual_money_spent_on_stocks = 0.0
        
        # --- MODIFIED ROUNDING LOGIC START ---
        # Determine rounding precision
        active_assets_count = sum(1 for entry in final_combined_portfolio_data_calc if safe_score(entry.get('combined_percent_allocation', 0.0)) > 1e-9 and safe_score(entry.get('live_price', 0.0)) > 0)
        avg_allocation = total_value_float_for_tailor / active_assets_count if active_assets_count > 0 else 0.0
        
        # Rule: If Value < 1000 OR Avg Alloc < 250 -> Round down to 2 decimals (hundredths)
        # Else -> Round down to 1 decimal (tenths)
        use_hundredths = (total_value_float_for_tailor < 1000) or (avg_allocation < 250)
        # --- MODIFIED ROUNDING LOGIC END ---

        for entry_tailoring in final_combined_portfolio_data_calc:
            final_stock_alloc_pct_tailor = safe_score(entry_tailoring.get('combined_percent_allocation', 0.0))
            live_price_for_tailor = safe_score(entry_tailoring.get('live_price', 0.0))
            if final_stock_alloc_pct_tailor > 1e-9 and live_price_for_tailor > 0:
                ideal_dollar_allocation = total_value_float_for_tailor * (final_stock_alloc_pct_tailor / 100.0)
                
                if frac_shares_singularity:
                    raw_shares = ideal_dollar_allocation / live_price_for_tailor
                    if use_hundredths:
                        # Round DOWN to 2 decimal places (hundredths)
                        shares_to_buy = math.floor(raw_shares * 100) / 100.0
                    else:
                        # Round DOWN to 1 decimal place (tenths)
                        shares_to_buy = math.floor(raw_shares * 10) / 10.0
                else:
                    # Whole shares, always floor
                    shares_to_buy = float(math.floor(ideal_dollar_allocation / live_price_for_tailor))
                
                actual_money_allocated = shares_to_buy * live_price_for_tailor
                
                if actual_money_allocated > 0:
                    actual_percent_of_total = (actual_money_allocated / total_value_float_for_tailor) * 100.0
                    current_tailored_entries_for_calc.append({
                        'ticker': entry_tailoring.get('ticker','ERR'),
                        'shares': shares_to_buy,
                        'live_price_at_eval': live_price_for_tailor,
                        'actual_money_allocation': actual_money_allocated,
                        'actual_percent_allocation': actual_percent_of_total,
                        'sub_portfolio_id': entry_tailoring.get('sub_portfolio_id'),
                        'path': entry_tailoring.get('path'),
                        'raw_invest_score': entry_tailoring.get('raw_invest_score')
                    })
                    total_actual_money_spent_on_stocks += actual_money_allocated

        final_cash_value_tailored = total_value_float_for_tailor - total_actual_money_spent_on_stocks
        tailored_portfolio_structured_data = sorted(current_tailored_entries_for_calc, key=lambda x: x['ticker'])
        
        if not suppress_prints:
            print("\n--- Tailored Portfolio (Shares) ---")
            
            if frac_shares_singularity:
                share_format = "{:.2f}" if use_hundredths else "{:.1f}"
            else:
                share_format = "{:.0f}"
                
            tailored_portfolio_output_list_final = [f"{item['ticker']} - {share_format.format(item['shares'])} shares" for item in tailored_portfolio_structured_data]
            print("\n".join(tailored_portfolio_output_list_final))
            print(f"Final Cash Value: ${final_cash_value_tailored:,.2f}")

            print("\n--- Tailored Portfolio (Full Details) ---")
            table_data = []
            for item in tailored_portfolio_structured_data:
                table_data.append([item['ticker'], share_format.format(item['shares']), f"${item['actual_money_allocation']:,.2f}", f"{item['actual_percent_allocation']:.2f}%"])
            cash_percent = (final_cash_value_tailored / total_value_float_for_tailor) * 100.0 if total_value_float_for_tailor > 0 else 0
            table_data.append(['Cash', '-', f"${final_cash_value_tailored:,.2f}", f"{cash_percent:.2f}%"])
            print(tabulate(table_data, headers=["Ticker", "Shares", "Actual $ Allocation", "Actual % Allocation"], tablefmt="pretty"))
            
            pie_data = [{'ticker': item['ticker'], 'value': item['actual_money_allocation']} for item in tailored_portfolio_structured_data]
            if final_cash_value_tailored > 1e-9:
                pie_data.append({'ticker': 'Cash', 'value': final_cash_value_tailored})
            
            chart_title = f"{current_portfolio_code} Allocation (Value: ${total_value_float_for_tailor:,.0f})"
            generate_portfolio_pie_chart(pie_data, chart_title, "portfolio_pie", is_called_by_ai=suppress_prints)
    
    return tailored_portfolio_output_list_final, final_combined_portfolio_data_calc, final_cash_value_tailored, tailored_portfolio_structured_data
# <<< END: REPLACEMENT FOR process_custom_portfolio >>>
# # <<< END: REPLACEMENT FOR process_custom_portfolio >>>

# --- Main Handler for /invest ---
async def handle_invest_command(args: List[str], ai_params: Optional[Dict] = None, is_called_by_ai: bool = False, return_structured_data: bool = False):
    if not is_called_by_ai: print("\n--- /invest Command ---")
    portfolio_data_config_invest = {'risk_type': 'stock', 'risk_tolerance': '10', 'remove_amplification_cap': 'true'}
    tailor_run, total_val_run, frac_s_run = False, None, False

    if ai_params:
        try:
            ema_sens = int(ai_params.get("ema_sensitivity"))
            if ema_sens not in [1,2,3]: return "Error (AI /invest): Invalid EMA sensitivity."
            portfolio_data_config_invest['ema_sensitivity'] = str(ema_sens)
            portfolio_data_config_invest['amplification'] = str(float(ai_params.get("amplification")))
            sub_portfolios = ai_params.get("sub_portfolios")
            if not sub_portfolios: return "Error (AI /invest): 'sub_portfolios' required."
            portfolio_data_config_invest['num_portfolios'] = str(len(sub_portfolios))
            total_weight_ai = 0
            for i, sub_p in enumerate(sub_portfolios, 1):
                tickers, weight = sub_p.get("tickers"), sub_p.get("weight")
                if not tickers or weight is None: return f"Error (AI /invest): Sub-portfolio {i} missing tickers/weight."
                weight_val = float(weight)
                if not (0 <= weight_val <= 100): return f"Error (AI /invest): Weight for sub-portfolio {i} out of range."
                total_weight_ai += weight_val
                portfolio_data_config_invest[f'tickers_{i}'] = str(tickers).upper()
                portfolio_data_config_invest[f'weight_{i}'] = f"{weight_val:.2f}"
            if not math.isclose(total_weight_ai, 100.0, abs_tol=1.0): return f"Error (AI /invest): Weights must sum to ~100. Got {total_weight_ai:.2f}."

            tailor_run = ai_params.get("tailor_to_value", False)
            if tailor_run:
                total_val_run = ai_params.get("total_value")
                if total_val_run is None or float(total_val_run) <= 0: return "Error (AI /invest): Positive 'total_value' required for tailoring."
                total_val_run = float(total_val_run)
            frac_s_run = ai_params.get("use_fractional_shares", False)
            portfolio_data_config_invest['frac_shares'] = str(frac_s_run).lower()
        except (KeyError, ValueError) as e: return f"Error (AI /invest): Parameter issue: {e}"
        
        tailored_list_str, combined_data, final_cash, tailored_structured_data = await process_custom_portfolio(
            portfolio_data_config=portfolio_data_config_invest, tailor_portfolio_requested=tailor_run,
            frac_shares_singularity=frac_s_run, total_value_singularity=total_val_run,
            is_custom_command_simplified_output=tailor_run, is_called_by_ai=True
        )
        
        if return_structured_data:
            return tailored_list_str, combined_data, final_cash, tailored_structured_data

        summary = f"/invest analysis completed (EMA Sens: {portfolio_data_config_invest['ema_sensitivity']}, Amp: {portfolio_data_config_invest['amplification']}). "
        if tailor_run:
            summary += f"Tailored to ${total_val_run:,.2f} (FracShares: {frac_s_run}). Final Cash: ${final_cash:,.2f}. "
            summary += "Top holdings: " + (", ".join(tailored_list_str[:3]) + "..." if len(tailored_list_str)>3 else ", ".join(tailored_list_str)) if tailored_list_str else "No stock holdings."
        else:
            summary += "Top combined allocations: " + (", ".join([f"{d['ticker']}({d.get('combined_percent_allocation',0):.1f}%)" for d in combined_data[:3] if 'ticker' in d])) if combined_data else "No combined allocation data."
        return summary
    else: # CLI Path
        try:
            ema_sens_str_cli = input("Enter EMA sensitivity (1: Weekly, 2: Daily, 3: Hourly): ")
            ema_sensitivity_cli = int(ema_sens_str_cli)
            if ema_sensitivity_cli not in [1, 2, 3]:
                print("Invalid EMA sensitivity. Must be 1, 2, or 3.")
                return None

            amp_str_cli = input("Enter amplification factor (e.g., 0.25, 0.5, 1, 2, 3, 4, 5): ")
            amplification_cli = float(amp_str_cli)
            
            num_port_str_cli = input("How many portfolios would you like to calculate? (e.g., 2): ")
            num_portfolios_cli = int(num_port_str_cli)
            if num_portfolios_cli <= 0:
                print("Number of portfolios must be greater than 0.")
                return None

            portfolio_data_config_invest['ema_sensitivity'] = str(ema_sensitivity_cli)
            portfolio_data_config_invest['amplification'] = str(amplification_cli)
            portfolio_data_config_invest['num_portfolios'] = str(num_portfolios_cli)

            current_total_weight_cli = 0.0
            for i in range(1, num_portfolios_cli + 1):
                print(f"\n--- Portfolio {i} ---")
                tickers_input_cli = input(f"Enter tickers for Portfolio {i} (comma-separated, e.g., AAPL,MSFT): ").upper()
                if not tickers_input_cli.strip():
                    print("Tickers cannot be empty. Please start over or provide valid tickers.")
                    return None
                portfolio_data_config_invest[f'tickers_{i}'] = tickers_input_cli

                weight_val_cli = 0.0
                if i == num_portfolios_cli:
                    weight_val_cli = 100.0 - current_total_weight_cli
                    if weight_val_cli < -0.01:
                        print(f"Error: Previous weights ({current_total_weight_cli}%) exceed 100%. Cannot set weight for final portfolio.")
                        return None
                    weight_val_cli = max(0, weight_val_cli)
                    print(f"Weight for Portfolio {i} automatically set to: {weight_val_cli:.2f}%")
                else:
                    remaining_weight_cli = 100.0 - current_total_weight_cli
                    weight_str_cli = input(f"Enter weight for Portfolio {i} (0-{remaining_weight_cli:.2f}%): ")
                    weight_val_cli = float(weight_str_cli)
                    if not (-0.01 < weight_val_cli < remaining_weight_cli + 0.01):
                        print(f"Invalid weight. Must be between 0 and {remaining_weight_cli:.2f}%.")
                        return None
                portfolio_data_config_invest[f'weight_{i}'] = f"{weight_val_cli:.2f}"
                current_total_weight_cli += weight_val_cli
            
            if not math.isclose(current_total_weight_cli, 100.0, abs_tol=0.1):
                print(f"Warning: Total weights sum to {current_total_weight_cli:.2f}%, not 100%. Results might be skewed.")

            tailor_portfolio_for_run = False
            total_value_for_tailoring_run = None
            frac_shares_for_tailoring_run = False
            
            tailor_str_cli = input("Tailor the table to your portfolio value? (yes/no): ").lower()
            if tailor_str_cli == 'yes':
                tailor_portfolio_for_run = True
                val_str_cli = input("Enter the total value for the combined portfolio (e.g., 10000): ")
                total_value_for_tailoring_run = float(val_str_cli)
                if total_value_for_tailoring_run <= 0:
                    print("Portfolio value must be positive.")
                    return None
                
                frac_s_str_cli = input("Tailor using fractional shares? (yes/no): ").lower()
                frac_shares_for_tailoring_run = frac_s_str_cli == 'yes'
                portfolio_data_config_invest['frac_shares'] = str(frac_shares_for_tailoring_run).lower()

            print("\nCLI: Processing /invest request...")
            await process_custom_portfolio(
                portfolio_data_config=portfolio_data_config_invest,
                tailor_portfolio_requested=tailor_portfolio_for_run,
                frac_shares_singularity=frac_shares_for_tailoring_run,
                total_value_singularity=total_value_for_tailoring_run,
                is_custom_command_simplified_output=False
            )
            print("\n/invest analysis complete.")
            return None

        except ValueError:
            print("CLI Error: Invalid input. Please enter numbers where expected.")
            return None
        except Exception as e_invest_cli:
            print(f"CLI Error occurred during /invest: {e_invest_cli}")
            traceback.print_exc()
            return None