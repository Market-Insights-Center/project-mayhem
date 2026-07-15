# kronos_command.py
# --- Imports ---
import asyncio
import traceback
import json
import os
import shutil
import re
import pandas as pd
import numpy as np
import sqlite3
import pytz
from tabulate import tabulate
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from prometheus_core import Prometheus # Import Prometheus class
from dateutil.relativedelta import relativedelta
import logging
import random
import statistics
import requests
# --- (Inside kronos_command.py) ---

# --- Add these new imports ---
import aiosqlite
import shlex
from dateutil.relativedelta import relativedelta
# --- (End of new imports) ---

# --- Constants ---
PROMETHEUS_STATE_FILE = 'prometheus_state.json'
KRONOS_SCHEDULE_FILE = 'kronos_schedule.json'
SANDBOX_DIR = 'kronos_sandbox' # Directory for test outputs
OPTIMIZABLE_PARAMS_FILE = 'optimizable_parameters.json'

prometheus_logger = logging.getLogger('PROMETHEUS_CORE') # <-- ADD THIS LINE

# Define cache file paths directly to avoid import issues
SP500_CACHE_FILE = 'sp500_risk_cache.csv'
SP100_CACHE_FILE = 'sp100_risk_cache.csv'

# Define default background task intervals
DEFAULT_CORR_INTERVAL_HOURS = 6
DEFAULT_WORKFLOW_CHANCE = 0.1

# --- Kronos Helper Functions ---

# --- (Inside "Kronos Helper Functions" section) ---

# --- NEW: Test Dimension Definitions ---
MARKET_CONDITIONS = {
    "COVID_Crash": {"start_date": "2020-01-15", "end_date": "2020-04-15"},
    "2021_Bull":   {"start_date": "2021-01-01", "end_date": "2022-01-01"},
    "2022_Bear":   {"start_date": "2022-01-01", "end_date": "2023-01-01"},
    "Current_1Y":  {"start_date": (datetime.now() - relativedelta(years=1)).strftime('%Y-%m-%d'), 
                    "end_date": datetime.now().strftime('%Y-%m-%d')}
    # Add more conditions as needed
}

async def _get_index_tickers(index_name: str) -> List[str]:
    """
    Fetches the current list of tickers for a major index from Wikipedia.
    Supports 'sp500' and 'nasdaq100'.
    """
    url_map = {
        'sp500': 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies',
        'nasdaq100': 'https://en.wikipedia.org/wiki/Nasdaq-100'
    }
    url = url_map.get(index_name.lower())
    if not url:
        return []

    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)
        response.raise_for_status()
        
        # Use pandas to easily parse the first table on the page
        df_list = pd.read_html(response.text)
        
        # Find the correct table and symbol column
        for df in df_list:
            if 'Symbol' in df.columns:
                # S&P 500 format
                symbols = df['Symbol'].dropna().unique().tolist()
                return [str(s).replace('.', '-') for s in symbols]
            elif 'Ticker' in df.columns:
                # NASDAQ 100 format
                symbols = df['Ticker'].dropna().unique().tolist()
                return [str(s).replace('.', '-') for s in symbols]
        return []
    except Exception as e:
        prometheus_logger.warning(f"Failed to fetch index tickers for '{index_name}': {e}")
        return []

AI_SCREENER_DEFINITIONS = {
    "AI_TECH_GROWTH": {
        "rep_ticker": "QQQ",
        "params": {
            "sector_identifiers": ["Technology"],
            "criteria": [{"metric": "growth_score", "operator": ">", "value": 70}],
            "top_n": 25
        }
    },
    "AI_VALUE_STOCKS": {
        "rep_ticker": "SPYV",
        "params": {
            "sector_identifiers": ["Market"],
            "criteria": [{"metric": "fundamental_score", "operator": ">", "value": 80}],
            "top_n": 25
        }
    },
    "AI_STRONG_MOMENTUM": {
        "rep_ticker": "MTUM",
        "params": {
            "sector_identifiers": ["Market"],
            "criteria": [{"metric": "technical_score", "operator": ">", "value": 80}],
            "top_n": 25
        }
    }
}

async def _get_universe(name: str, prometheus_instance: Prometheus) -> Dict[str, Any]:
    """
    Fetches a list of tickers for a universe and identifies its representative ticker.
    NOW DYNAMIC: Uses live index scraping and the real AI screener function.
    """
    print(f"   [Convergence] Loading universe: {name}...")
    name_upper = name.upper()
    
    try:
        if name_upper == "SPY_500":
            tickers = await _get_index_tickers('sp500')
            return {"tickers": tickers, "representative_ticker": "SPY"}
        
        elif name_upper == "QQQ_100":
            tickers = await _get_index_tickers('nasdaq100')
            return {"tickers": tickers, "representative_ticker": "QQQ"}
        
        elif name_upper.startswith("AI_"):
            screener_def = AI_SCREENER_DEFINITIONS.get(name_upper)
            if not screener_def:
                print(f"   [Convergence] Unknown AI Screener recipe: {name}. Defaulting to SPY.")
                return {"tickers": ["SPY"], "representative_ticker": "SPY"}

            print(f"   [Convergence] Running real AI screener: {name}...")
            
            # Call the actual screener function from Prometheus
            results = await prometheus_instance.screener_func(
                args=[], ai_params=screener_def["params"], is_called_by_ai=True
            )

            screener_tickers = []
            if results and results.get("status") == "success":
                screener_tickers = [item['Ticker'] for item in results.get('results', [])]
            else:
                print(f"   [Convergence] AI Screener failed: {results.get('message', 'Unknown error')}")

            if not screener_tickers:
                print(f"   [Convergence] AI Screener '{name}' returned no tickers. Defaulting to rep_ticker.")
                return {"tickers": [screener_def["rep_ticker"]], "representative_ticker": screener_def["rep_ticker"]}

            print(f"   [Convergence] AI Screener found {len(screener_tickers)} tickers.")
            return {"tickers": screener_tickers, "representative_ticker": screener_def["rep_ticker"]}

        else:
            print(f"   [Convergence] Unknown universe: {name}. Using as single ticker.")
            return {"tickers": [name_upper], "representative_ticker": name_upper}
            
    except Exception as e:
        print(f"❌ CRITICAL ERROR in _get_universe '{name}': {e}")
        traceback.print_exc()
        return {"tickers": [], "representative_ticker": None}
             
def _format_trade_frequency(trades_per_day: float) -> str:
    """
    Converts a trades_per_day float into a human-readable string
    with a unit that keeps the number reasonable.
    """
    if trades_per_day == 0:
        return "0.00 trades/yr"

    # Trades per Year (approx 252 trading days)
    trades_per_year = trades_per_day * 252
    if trades_per_year < 10.0:
        return f"{trades_per_year:.2f} trades/yr"
        
    # Trades per Month (approx 21 trading days)
    trades_per_month = trades_per_day * 21
    if trades_per_month < 10.0:
        return f"{trades_per_month:.2f} trades/mo"

    # Trades per Week
    trades_per_week = trades_per_day * 5
    if trades_per_week < 10.0:
        return f"{trades_per_week:.2f} trades/wk"

    # Trades per Day
    if trades_per_day < 10.0:
        return f"{trades_per_day:.2f} trades/day"
        
    # Trades per Hour (assuming 7 trading hours)
    trades_per_hour = trades_per_day / 7
    return f"{trades_per_hour:.2f} trades/hr"

# --- (Inside "--- NEW: Database Helpers for Convergence ---" section) --
# --- NEW: Database Helpers for Convergence ---
async def _log_convergence_run(db_path: str, run_name: str, parent_run_id: Optional[int], run_params_json: str) -> int:
    """Creates a new entry in convergence_runs and returns the new run_id."""
    start_time = datetime.utcnow().isoformat()
    try:
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO convergence_runs (run_name, start_time, status, run_parameters_json, parent_run_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (run_name, start_time, "Running", run_params_json, parent_run_id)
            )
            await db.commit()
            return cursor.lastrowid
    except Exception as e:
        print(f"❌ CRITICAL: Failed to log convergence run to DB: {e}")
        return -1

async def _update_convergence_run_status(db_path: str, run_id: int, status: str):
    """Updates the status and end_time of a convergence run."""
    end_time = datetime.utcnow().isoformat()
    try:
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE convergence_runs SET status = ?, end_time = ? WHERE run_id = ?",
                (status, end_time, run_id)
            )
            await db.commit()
    except Exception as e:
        print(f"❌ CRITICAL: Failed to update convergence run status in DB: {e}")

async def _log_convergence_result(db_path: str, run_id: int, universe: str, condition: str, strategy: str, result: Dict[str, Any], test_duration_days: int):
    """Logs a single permutation's result to the convergence_results table."""
    try:
        async with aiosqlite.connect(db_path) as db:
            # --- START OF MODIFICATION ---
            await db.execute(
                """
                INSERT INTO convergence_results (
                    run_id, universe, market_condition, strategy_name, 
                    best_params_json, best_sharpe_ratio, total_return_pct, 
                    max_drawdown_pct, trade_count, test_duration_days,
                    buy_hold_return_pct
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, universe, condition, strategy,
                    result.get("best_params_json"),
                    result.get("best_sharpe_ratio"),
                    result.get("total_return_pct"),
                    result.get("max_drawdown_pct"),
                    result.get("trade_count"),
                    test_duration_days,
                    result.get("buy_hold_return_pct") # Add this
                )
            )
            # --- END OF MODIFICATION ---
            await db.commit()
    except Exception as e:
        print(f"❌ CRITICAL: Failed to log convergence result to DB: {e}")

async def _get_seed_population(db_path: str, universe: str, condition: str, strategy: str, num_to_seed: int = 10) -> List[Dict[str, Any]]:
    """
    (Evolutionary Memory Feature)
    Fetches the Top N best-performing parameter sets from ALL previous runs
    for this exact scenario to seed the new generation.
    """
    prometheus_logger.debug(f"   [Convergence] Searching all history for Top {num_to_seed} seeds for {strategy} in {condition}...")
    seeds = []
    try:
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                """
                SELECT best_params_json, total_return_pct FROM convergence_results
                WHERE universe = ? 
                  AND market_condition = ? 
                  AND strategy_name = ?
                  AND total_return_pct > -999.0
                  AND best_params_json IS NOT NULL
                ORDER BY total_return_pct DESC
                LIMIT ?
                """,
                (universe, condition, strategy, num_to_seed)
            )
            rows = await cursor.fetchall()
            
            if rows:
                prometheus_logger.info(f"   [Convergence] Found {len(rows)} high-performing seeds from past runs.")
                for row in rows:
                    try:
                        seeds.append(json.loads(row[0]))
                    except Exception:
                        continue # Skip bad JSON
                return seeds
            
    except Exception as e:
        print(f"   [Convergence] Error fetching seed population: {e}")
    
    prometheus_logger.info("   [Convergence] No historical seeds found. Using random population.")
    return []

# --- NEW: Orchestrator Functions ---
async def _run_convergence_matrix(
    run_id: int,
    db_path: str,
    run_name: str,
    universes: List[str],
    conditions: List[str],
    strategies: List[str],
    prometheus_instance: Prometheus
):
    """The main orchestration loop for the convergence command."""
    prometheus_logger.info(f"--- [Convergence Run {run_id}: '{run_name}'] Starting ---")
    print(f"--- [Convergence Run {run_id}: '{run_name}'] Starting ---")
    print(f"   Universes: {universes}")
    print(f"   Conditions: {conditions}")
    print(f"   Strategies: {strategies}")
    
    total_permutations = len(universes) * len(conditions) * len(strategies)
    completed_count = 0
    
    for uni_name in universes:
        prometheus_logger.debug(f"[Convergence {run_id}] Getting universe '{uni_name}'")
        universe_data = await _get_universe(uni_name, prometheus_instance)
        rep_ticker = universe_data.get("representative_ticker")
        if not rep_ticker:
            print(f"   SKIPPING Universe: {uni_name} (no representative ticker found).")
            prometheus_logger.warning(f"[Convergence {run_id}] Skipping Universe {uni_name}, no rep_ticker.")
            continue
            
        for cond_name in conditions:
            condition_dates = MARKET_CONDITIONS.get(cond_name)
            if not condition_dates:
                print(f"   SKIPPING Condition: {cond_name} (not defined in MARKET_CONDITIONS).")
                prometheus_logger.warning(f"[Convergence {run_id}] Skipping Condition {cond_name}, not defined.")
                continue
                
            try:
                start_dt = datetime.strptime(condition_dates['start_date'], '%Y-%m-%d')
                end_dt = datetime.strptime(condition_dates['end_date'], '%Y-%m-%d')
                test_duration_days = (end_dt - start_dt).days
                if test_duration_days <= 0: test_duration_days = 1
            except Exception:
                test_duration_days = 365 
                
            for strat_name in strategies:
                completed_count += 1
                print(f"\n[Convergence Run {run_id} | {completed_count}/{total_permutations}]")
                print(f"   Testing: {strat_name} on {rep_ticker} ({uni_name})")
                print(f"   Condition: {cond_name} ({test_duration_days} days)")
                prometheus_logger.info(f"[Convergence {run_id}] Running {strat_name} on {rep_ticker} ({cond_name})")

                seed_pop = await _get_seed_population(db_path, uni_name, cond_name, strat_name, num_to_seed=10)
                
                try:
                    best_params, best_return_pct, all_metrics = await prometheus_instance.run_parameter_optimization(
                        command_name="/backtest",
                        strategy_name=strat_name,
                        ticker=rep_ticker,
                        period=None, 
                        start_date=condition_dates["start_date"],
                        end_date=condition_dates["end_date"],
                        seed_population=seed_pop,
                        generations=15, 
                        population_size=30 
                    )
                    
                    if best_params and best_return_pct > -float('inf'):
                        prometheus_logger.info(f"[Convergence {run_id}] GA Success for {strat_name}. Best Return: {best_return_pct:.2f}%")
                        
                        all_metrics["best_params_json"] = json.dumps(best_params, sort_keys=True)
                        
                        print(f"   -> SUCCESS: Best Return {best_return_pct:.2f}%")
                        
                        # --- START OF FIX ---
                        bh_return = all_metrics.get("buy_hold_return_pct")
                        prometheus_logger.debug(f"GA run returned metrics: {all_metrics}")
                        if isinstance(bh_return, (int, float)):
                            print(f"   -> vs. Buy & Hold Return: {bh_return:.2f}%")
                        else:
                            print(f"   -> vs. Buy & Hold Return: N/A (Value was: {bh_return})")
                        # --- END OF FIX ---
                        
                        result_to_log = all_metrics
                    else:
                        print(f"   -> FAILED: Optimization did not find a valid result.")
                        prometheus_logger.warning(f"[Convergence {run_id}] GA FAILED for {strat_name}. No valid result.")
                        result_to_log = {"total_return_pct": -float('inf')}

                    await _log_convergence_result(db_path, run_id, uni_name, cond_name, strat_name, result_to_log, test_duration_days)

                except Exception as e:
                    print(f"   -> ❌ CRITICAL ERROR during optimization: {e}")
                    prometheus_logger.error(f"[Convergence {run_id}] CRITICAL GA Error for {strat_name}: {e}", exc_info=True)
                    traceback.print_exc()
                    await _log_convergence_result(db_path, run_id, uni_name, cond_name, strat_name, {"total_return_pct": -float('inf'), "error": str(e)}, test_duration_days)

    print(f"\n--- [Convergence Run {run_id}] Finished All Permutations ---")
    prometheus_logger.info(f"--- [Convergence Run {run_id}] Finished All Permutations ---")
    await _update_convergence_run_status(db_path, run_id, "Completed")
    
    await _generate_convergence_summary(run_id, prometheus_instance)

async def _generate_convergence_summary(run_id: int, prometheus_instance: Prometheus):
    """
    (Phase 4)
    Queries all results for the run, analyzes them with Pandas,
    and calls the AI to generate a full, human-readable report.
    """
    print("\n--- Generating Convergence Summary ---")
    
    db_path = prometheus_instance.db_path
    
    try:
        query = """
            SELECT 
                strategy_name, market_condition, universe,
                total_return_pct, trade_count, test_duration_days, 
                best_sharpe_ratio, best_params_json,
                buy_hold_return_pct
            FROM convergence_results
            WHERE run_id = ? AND total_return_pct > -999.0
        """
        
        rows_data = []
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = sqlite3.Row
            cursor = await db.execute(query, (run_id,))
            rows_data = await cursor.fetchall()

        if not rows_data:
            print("No successful results were logged for this convergence run.")
            return

        df = pd.DataFrame([dict(row) for row in rows_data])

    except Exception as e:
        print(f"❌ Error querying convergence summary data: {e}")
        traceback.print_exc()
        return

    if df.empty:
        print("No successful results were logged for this convergence run.")
        return

    print("   -> Analyzing results...")
    
    # --- START OF FIX: Handle N/A (None/NaN) values correctly ---
    df['trades'] = df['trade_count'].fillna(0)
    df['duration'] = df['test_duration_days'].fillna(365)
    df['return_pct'] = df['total_return_pct'].fillna(0.0)
    
    # Use np.nan for numeric types that are missing
    df['sharpe'] = df['best_sharpe_ratio'].replace([None], np.nan)
    df['buy_hold_pct'] = df['buy_hold_return_pct'].replace([None], np.nan)
    # --- END OF FIX ---
    
    df['trades_per_day'] = df['trades'] / df['duration']
    df['return_per_day_pct'] = df['return_pct'] / df['duration']
    df['profit_time_ratio'] = (df['return_pct'] / (df['trades'] + 1))
    
    df['trade_freq_str'] = df['trades_per_day'].apply(_format_trade_frequency)
    df['alpha_pct'] = df['return_pct'] - df['buy_hold_pct'] # This will correctly result in NaN
    
    best_overall = df.loc[df['return_pct'].idxmax()]
    
    # Handle case where all alpha is NaN
    if not df['alpha_pct'].isnull().all():
        best_alpha = df.loc[df['alpha_pct'].idxmax()]
        most_robust_strategy = df.groupby('strategy_name')['alpha_pct'].mean().idxmax()
    else:
        best_alpha = best_overall # Fallback
        most_robust_strategy = df.groupby('strategy_name')['return_pct'].mean().idxmax()

    
    # --- START OF FIX: Handle NaN in the analysis string ---
    def format_bh_comparison(row):
        if pd.isna(row['buy_hold_pct']):
            return "(vs. Buy&Hold: N/A)"
        return f"(vs. Buy&Hold's {row['buy_hold_pct']:.2f}%)"
    
    def format_alpha(row):
         if pd.isna(row['alpha_pct']):
            return "N/A"
         return f"{row['alpha_pct']:.2f}%"

    analysis_summary = f"""
    **Overall Analysis:**
    - **Most Profitable Run (Raw %):** A '{best_overall['strategy_name']}' strategy returned **{best_overall['return_pct']:.2f}%** {format_bh_comparison(best_overall)} in the '{best_overall['market_condition']}' condition.
    - **Best Outperformance (Alpha):** A '{best_alpha['strategy_name']}' strategy in '{best_alpha['market_condition']}' outperformed Buy&Hold by **{format_alpha(best_alpha)}**.
    - **Most Robust Strategy:** On average, '{most_robust_strategy}' was the best strategy found.
    """
    # --- END OF FIX ---
    
    top_5_df = df.sort_values('return_pct', ascending=False).head(5)
    
    columns_to_show = ['strategy_name', 'market_condition', 'return_pct', 'buy_hold_pct', 'alpha_pct', 'trade_freq_str', 'sharpe', 'best_params_json']
    columns_to_show = [col for col in columns_to_show if col in top_5_df.columns]
    
    # Use floatfmt to handle NaN -> "N/A"
    top_5_results_str = top_5_df[columns_to_show].to_markdown(
        index=False,
        floatfmt=(".s", ".s", ".2f", ".2f", ".2f", ".s", ".3f", ".s"),
        missingval="N/A"
    )
    
    print("   -> Calling AI for final synthesis...")
    prompt = f"""
    You are an expert quantitative analyst. I have just completed a 'Convergence' run, a meta-optimization test of trading strategies.
    
    Your job is to analyze the results and provide a detailed summary. Pay close attention to the strategy's return ('return_pct') versus the benchmark 'buy_hold_pct'. The 'alpha_pct' column shows this difference.
    
    If 'buy_hold_pct', 'alpha_pct', or 'sharpe' are 'N/A', it means the data was not available for that run. You MUST state this limitation. Do not treat 'N/A' and '0.00%' as the same thing.
    
    Here is the high-level analysis of the run:
    {analysis_summary}
    
    Here are the Top 5 most profitable permutations found (sorted by raw 'return_pct'):
    {top_5_results_str}
    
    Based *only* on this data, please provide the following:
    1.  **Executive Summary:** A brief summary of the most important findings. Did the strategies successfully beat Buy & Hold? (Acknowledge if B&H data was 'N/A').
    2.  **Best Overall Strategy:** Identify the single best run. Was it the 'Most Profitable' or the one with the 'Best Outperformance (Alpha)'? Detail its strategy, parameters, and performance (Return %, vs. Buy&Hold %, Alpha %, and trade frequency).
    3.  **Market Condition Insights:** How did market conditions affect performance and the ability to beat the market?
    4.  **Recommendations for Investors:**
        * **For an Aggressive (Max Profit) Investor:** Which strategy/parameter set from this test would you recommend?
        * **For a Conservative (Risk-Averse) Investor:** Which strategy had the best *outperformance* ('alpha_pct') while also having a good Sharpe Ratio? (Acknowledge if Sharpe is 'N/A').
    
    Be clear, concise, and base all conclusions strictly on the data provided.
    """
    
    try:
        model_to_use = prometheus_instance.gemini_model
        response = await model_to_use.generate_content_async(prompt)
        ai_response = response.text
        
        if not ai_response:
            raise Exception("AI returned an empty response.")
            
        print("\n--- 🤖 Convergence AI Summary Report 🤖 ---")
        print(ai_response)
        print("------------------------------------------")
        
    except Exception as e:
        print(f"❌ AI Summary Generation Failed: {e}")
        print("--- Top 5 Results (Manual Fallback) ---")
        print(top_5_df[columns_to_show].to_markdown(index=False, missingval="N/A"))
                        
async def _handle_kronos_convergence(parts: List[str], prometheus_instance: Prometheus):
    """Handles the 'convergence' command in the Kronos shell."""
    if len(parts) < 2:
        # --- MODIFICATION: Updated help text to remove --parent_run ---
        print("Usage: convergence <run_name> --universes=... --conditions=... --strategies=... [--time_limit=1h]")
        print("  --universes: Comma-separated list (e.g., SPY_500,QQQ)")
        print("  --conditions: Comma-separated list (e.g., 2022_Bear,Current_1Y)")
        print("  --strategies: Comma-separated list from optimizable_parameters.json (e.g., rsi,ma_crossover)")
        print("  --time_limit: Optional (e.g., 30m, 2h, 1d)")
        print("  (Evolutionary memory is now automatic and always on)")
        return

    run_name = parts[1]
    args = parts[2:]
    
    parsed_args = {
        "universes": None, "conditions": None, "strategies": None,
        "time_limit": None, "parent_run": None # Keep parent_run for parsing, but don't use it
    }
    for arg in args:
        if arg.startswith("--universes="):
            parsed_args["universes"] = arg.split('=', 1)[1].split(',')
        elif arg.startswith("--conditions="):
            parsed_args["conditions"] = arg.split('=', 1)[1].split(',')
        elif arg.startswith("--strategies="):
            parsed_args["strategies"] = arg.split('=', 1)[1].split(',')
        elif arg.startswith("--time_limit="):
            parsed_args["time_limit"] = arg.split('=', 1)[1]
        elif arg.startswith("--parent_run="):
            # We no longer use this, but we'll parse it to avoid an error
            print("   -> Note: --parent_run is deprecated. Evolutionary memory is now automatic.")
            pass
            
    if not all([parsed_args["universes"], parsed_args["conditions"], parsed_args["strategies"]]):
        print("❌ Error: --universes, --conditions, and --strategies are all required.")
        return

    valid_strategies = prometheus_instance.optimizable_params_config.get("/backtest", {}).keys()
    for s in parsed_args["strategies"]:
        if s not in valid_strategies:
            print(f"❌ Error: Strategy '{s}' is not defined as optimizable in {OPTIMIZABLE_PARAMS_FILE}.")
            return
            
    timeout_seconds = None
    if parsed_args["time_limit"]:
        delta = _parse_interval_to_timedelta(parsed_args["time_limit"])
        if delta:
            timeout_seconds = delta.total_seconds()
            print(f"   -> Time Limit set to {timeout_seconds} seconds ({parsed_args['time_limit']}).")
        else:
            print(f"⚠️ Warning: Invalid time_limit format '{parsed_args['time_limit']}'. Running with no time limit.")
    
    # We still log parent_run=None for schema compatibility, but it's not used
    run_params_json = json.dumps(parsed_args, default=str)
    run_id = await _log_convergence_run(
        prometheus_instance.db_path,
        run_name,
        None, # Pass None for parent_run_id
        run_params_json
    )
    if run_id == -1:
        print("❌ Failed to start convergence run (database error).")
        return

    # Create the main task
    task = _run_convergence_matrix(
        run_id=run_id,
        db_path=prometheus_instance.db_path,
        run_name=run_name,
        universes=parsed_args["universes"],
        conditions=parsed_args["conditions"],
        strategies=parsed_args["strategies"],
        # --- THIS IS THE FIX: 'parent_run_id' argument is removed ---
        prometheus_instance=prometheus_instance
    )
    
    try:
        print(f"Starting Convergence Run {run_id}. This may take a long time...")
        await asyncio.wait_for(task, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        print(f"⌛️ Convergence Run {run_id} ('{run_name}') reached time limit of {parsed_args['time_limit']}.")
        await _update_convergence_run_status(prometheus_instance.db_path, run_id, "Timed-Out")
        await _generate_convergence_summary(run_id, prometheus_instance)
    except Exception as e:
        print(f"❌ Convergence Run {run_id} ('{run_name}') failed with an unexpected error.")
        traceback.print_exc()
        await _update_convergence_run_status(prometheus_instance.db_path, run_id, f"Failed: {e}")

def _load_kronos_config() -> Dict[str, Any]:
    """Loads configurable parameters from the Prometheus state file."""
    config = {
        "correlation_interval_hours": DEFAULT_CORR_INTERVAL_HOURS,
        "workflow_analysis_chance": DEFAULT_WORKFLOW_CHANCE
    }
    try:
        if os.path.exists(PROMETHEUS_STATE_FILE):
            with open(PROMETHEUS_STATE_FILE, 'r') as f:
                state = json.load(f)
                config["correlation_interval_hours"] = state.get("correlation_interval_hours", DEFAULT_CORR_INTERVAL_HOURS)
                config["workflow_analysis_chance"] = state.get("workflow_analysis_chance", DEFAULT_WORKFLOW_CHANCE)
    except (IOError, json.JSONDecodeError):
        pass
    return config

def _save_kronos_config(config_key: str, new_value: Any, prometheus_instance: Prometheus):
    """Saves a specific config key to the state file and updates the instance if active."""
    try:
        state = {}
        if os.path.exists(PROMETHEUS_STATE_FILE):
            with open(PROMETHEUS_STATE_FILE, 'r') as f:
                state = json.load(f)
        
        state[config_key] = new_value
        state["is_active"] = prometheus_instance.is_active 
        
        with open(PROMETHEUS_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=4)
        
        print(f"✅ Config updated: '{config_key}' set to {new_value}.")
        
        if prometheus_instance.is_active:
            if config_key == 'correlation_interval_hours':
                print("   -> Restarting background correlation task to apply new interval...")
                if prometheus_instance.correlation_task and not prometheus_instance.correlation_task.done():
                    prometheus_instance.correlation_task.cancel()
                # Re-check and restart the task
                required_funcs = [
                    prometheus_instance.derivative_func, prometheus_instance.mlforecast_func, 
                    prometheus_instance.sentiment_func, prometheus_instance.fundamentals_func, 
                    prometheus_instance.quickscore_func
                ]
                if all(required_funcs):
                    prometheus_instance.correlation_task = asyncio.create_task(prometheus_instance.background_correlation_analysis())
                    print("   -> Background task restarted with new interval.")
                else:
                    print("   -> Background task not restarted (required functions missing).")
                    
            elif config_key == 'workflow_analysis_chance':
                # This value is read dynamically, so no restart is needed.
                print("   -> Workflow analysis chance updated for next user command.")
                
    except (IOError, json.JSONDecodeError) as e:
        print(f"❌ Error saving Kronos config: {e}")
    except Exception as e:
        print(f"❌ Unexpected error saving config: {e}")

def _load_schedule() -> List[Dict[str, Any]]:
    """Loads the schedule from kronos_schedule.json."""
    if not os.path.exists(KRONOS_SCHEDULE_FILE):
        return []
    try:
        with open(KRONOS_SCHEDULE_FILE, 'r') as f:
            schedule_data = json.load(f)
            # Re-parse next_run times into datetime objects
            for job in schedule_data:
                job['next_run'] = datetime.fromisoformat(job['next_run'])
            return schedule_data
    except (IOError, json.JSONDecodeError, TypeError):
        print("⚠️ Warning: Could not load or parse schedule file. Creating a new one.")
        return []

def _save_schedule(schedule_data: List[Dict[str, Any]]):
    """Saves the schedule to kronos_schedule.json."""
    try:
        # Convert datetime objects to strings for JSON serialization
        serializable_data = []
        for job in schedule_data:
            job_copy = job.copy()
            job_copy['next_run'] = job['next_run'].isoformat()
            serializable_data.append(job_copy)
            
        with open(KRONOS_SCHEDULE_FILE, 'w') as f:
            json.dump(serializable_data, f, indent=4)
    except (IOError, TypeError) as e:
        print(f"❌ Error saving schedule: {e}")

def _parse_interval_to_timedelta(interval_str: str) -> Optional[timedelta]:
    """Converts interval string (e.g., '3h', '1d', '30m') to timedelta."""
    match = re.match(r"(\d+)([mhd])", interval_str.lower())
    if not match:
        return None
    try:
        value = int(match.group(1))
        unit = match.group(2)
        if unit == 'm':
            return timedelta(minutes=value)
        elif unit == 'h':
            return timedelta(hours=value)
        elif unit == 'd':
            return timedelta(days=value)
    except (ValueError, TypeError):
        return None
    return None

def _is_market_open() -> bool:
    """Checks if the US stock market is open (Mon-Fri, 8:30-15:00 CST/CDT)."""
    try:
        # Using US/Central as it's less ambiguous with DST than EST/EDT
        tz = pytz.timezone('US/Central') 
        now_ct = datetime.now(tz)
        
        # Market open 8:30 AM, close 3:00 PM (15:00) Central Time
        market_open = now_ct.time() >= datetime.time(8, 30)
        market_close = now_ct.time() < datetime.time(15, 0)
        is_weekday = now_ct.weekday() < 5 # 0=Monday, 4=Friday
        
        return is_weekday and market_open and market_close
    except Exception:
        # Fail safe: if time check fails, assume market is closed
        return False

# --- Kronos Command Handlers ---

async def _handle_kronos_status(parts: List[str], prometheus_instance: Prometheus):
    """Handles the 'status' command in the Kronos shell."""
    current_status_str = "ACTIVE" if prometheus_instance.is_active else "INACTIVE"
    
    if len(parts) == 1:
        print(f"Prometheus is currently {current_status_str}.")
        print("Usage: status <on|off>")
        return

    new_status = parts[1].lower()
    
    if new_status == "on":
        if prometheus_instance.is_active:
            print("Prometheus is already ACTIVE.")
        else:
            print("Activating Prometheus...")
            prometheus_instance.is_active = True
            
            required_funcs = [
                prometheus_instance.derivative_func, prometheus_instance.mlforecast_func, 
                prometheus_instance.sentiment_func, prometheus_instance.fundamentals_func, 
                prometheus_instance.quickscore_func
            ]
            if all(required_funcs):
                if not prometheus_instance.correlation_task or prometheus_instance.correlation_task.done():
                    print("   -> Starting background correlation task...")
                    prometheus_instance.correlation_task = asyncio.create_task(prometheus_instance.background_correlation_analysis())
                else:
                    print("   -> Background correlation task is already running.")
            else:
                print("   -> Background correlation task NOT started (required functions missing).")
                
            print("   -> Loading synthesized commands...")
            prometheus_instance._load_and_register_synthesized_commands_sync()
            print("   -> Context fetching and workflow analysis enabled.")
            prometheus_instance._save_prometheus_state()
            print("✅ Prometheus is now ACTIVE.")
            
    elif new_status == "off":
        if not prometheus_instance.is_active:
            print("Prometheus is already INACTIVE.")
        else:
            print("Deactivating Prometheus...")
            prometheus_instance.is_active = False
            
            if prometheus_instance.correlation_task and not prometheus_instance.correlation_task.done():
                prometheus_instance.correlation_task.cancel()
                print("   -> Background correlation task cancelled.")
            prometheus_instance.correlation_task = None
            
            prometheus_instance.toolbox = prometheus_instance.base_toolbox.copy()
            prometheus_instance.synthesized_commands.clear()
            print("   -> Synthesized commands unloaded.")
            print("   -> Context fetching and workflow analysis disabled.")
            prometheus_instance._save_prometheus_state()
            print("✅ Prometheus is now INACTIVE.")
    else:
        print(f"Unknown status: '{new_status}'. Use 'on' or 'off'.")

async def _handle_kronos_optimize(parts: List[str], prometheus_instance: Prometheus):
    """Handles the 'optimize' command in the Kronos shell."""
    try:
        if len(parts) < 4:
            print("Usage: optimize <strategy_name> <ticker> <period> [generations] [population]")
            print("Example: optimize rsi SPY 1y 10 20")
            return

        strategy_arg = parts[1].lower()
        ticker_arg = parts[2].upper()
        period_arg = parts[3].lower()
        generations_arg = int(parts[4]) if len(parts) > 4 else 10
        population_size_arg = int(parts[5]) if len(parts) > 5 else 20
        num_parents_arg = population_size_arg // 2

        optimizable_strategies = prometheus_instance.optimizable_params_config.get("/backtest", {}).keys()
        if strategy_arg not in optimizable_strategies:
            print(f"❌ Error: Strategy '{strategy_arg}' is not defined as optimizable for /backtest.")
            print(f"   Available strategies for optimization: {', '.join(optimizable_strategies)}")
            return

        await prometheus_instance.run_parameter_optimization(
            command_name="/backtest",
            strategy_name=strategy_arg,
            ticker=ticker_arg,
            period=period_arg,
            generations=generations_arg,
            population_size=population_size_arg,
            num_parents=num_parents_arg
        )

    except (ValueError, TypeError):
        print("❌ Error: Invalid number for generations or population size.")
    except Exception as e:
        print(f"❌ An error occurred during optimization: {e}")
        traceback.print_exc()

async def _handle_kronos_test(parts: List[str], prometheus_instance: Prometheus):
    """Handles the 'test' command in the Kronos shell."""
    try:
        if len(parts) < 4:
            print("Usage: test <command_file.py> <ticker> <period> [mode:manual|auto]")
            print("Example: test backtest_command.py SPY 2y manual")
            return

        filename_to_improve = parts[1]
        ticker_arg = parts[2].upper()
        period_arg = parts[3].lower()
        mode_arg = parts[4].lower() if len(parts) > 4 else "manual"

        if not filename_to_improve.endswith(".py"):
            print("❌ Error: File must be a .py file.")
            return
        if mode_arg not in ["manual", "auto"]:
            print("❌ Error: Mode must be 'manual' or 'auto'.")
            return
            
        print(f"--- Initiating Automated Test for {filename_to_improve} ---")
        print(f"    Mode: {mode_arg.upper()}, Ticker: {ticker_arg}, Period: {period_arg}")

        # 1. Generate Hypothesis
        hypothesis_result = await prometheus_instance.generate_improvement_hypothesis(filename_to_improve)
        if not (isinstance(hypothesis_result, dict) and hypothesis_result.get("status") == "success"):
            print(f"❌ Skipping test because hypothesis failed: {hypothesis_result.get('message', 'Unknown error')}")
            return

        original_code = hypothesis_result.get("original_code")
        hypothesis_text = hypothesis_result.get("hypothesis")
        if not original_code or not hypothesis_text:
            print("❌ Error: Hypothesis generated, but original code or text missing.")
            return

        # 2. Generate Improved Code (to temporary file)
        temp_filepath = await prometheus_instance._generate_improved_code(
            command_filename=filename_to_improve,
            original_code=original_code,
            improvement_hypothesis=hypothesis_text
        )
        if not temp_filepath:
            print(f"❌ Failed to generate or save improved code for {filename_to_improve}.")
            return

        # 3. Compare Performance (if backtestable)
        print("\n-> Checking if code is backtestable for comparison...")
        # Construct the path to the *original* command file
        original_target_path_check = os.path.join(os.path.dirname(__file__), 'Isolated Commands', filename_to_improve)
        
        OriginalStratClass = prometheus_instance._load_strategy_class_from_file(original_target_path_check)
        ImprovedStratClass = prometheus_instance._load_strategy_class_from_file(temp_filepath)
        
        is_backtestable = (OriginalStratClass and hasattr(OriginalStratClass(pd.DataFrame()), 'generate_signals') and
                           ImprovedStratClass and hasattr(ImprovedStratClass(pd.DataFrame()), 'generate_signals'))

        if not is_backtestable:
            print("-> Files do not appear to be standard backtest strategies. Skipping performance comparison.")
            print(f"   -> Generated code saved temporarily for manual review: {temp_filepath}")
            return

        print(f"-> Files appear backtestable. Running comparison on {ticker_arg} ({period_arg})...")
        comparison_results = await prometheus_instance._compare_command_performance(
            original_filename=filename_to_improve,
            improved_filepath=temp_filepath,
            ticker=ticker_arg,
            period=period_arg
        )

        if not comparison_results:
            print("⚠️ Comparison failed or produced no results. Aborting approval step.")
            print(f"   -> Improved code remains available at: {temp_filepath}")
            return

        # 4. Handle Approval and Overwrite
        original_results, improved_results = comparison_results
        
        is_improved = improved_results.get('sharpe_ratio', -np.inf) > original_results.get('sharpe_ratio', -np.inf)
        
        print("\n--- Confirmation ---")
        original_target_path = os.path.join(os.path.dirname(__file__), 'Isolated Commands', filename_to_improve)
        
        user_approval = "no"
        if mode_arg == "auto":
            if is_improved:
                print(f"   -> AUTO-APPROVE: Improved Sharpe Ratio ({improved_results.get('sharpe_ratio', -np.inf):.3f} > {original_results.get('sharpe_ratio', -np.inf):.3f}).")
                user_approval = "yes"
            else:
                print(f"   -> AUTO-REJECT: No improvement in Sharpe Ratio.")
                user_approval = "no"
        else: # Manual mode
            prompt_message = f"❓ Overwrite original file '{original_target_path}' with the improved version? (yes/no): "
            user_approval = await asyncio.to_thread(input, prompt_message)

        if user_approval.lower() == 'yes':
            try:
                print(f"   -> Overwriting '{original_target_path}'...")
                shutil.move(temp_filepath, original_target_path)
                print(f"✅ Original file overwritten successfully.")
            except Exception as e_move:
                print(f"❌ Error overwriting file: {e_move}")
                print(f"   -> Improved code remains available at: {temp_filepath}")
        else:
            print("   -> Overwrite cancelled/rejected.")
            print(f"   -> Improved code remains available at: {temp_filepath}")

    except Exception as e:
        print(f"❌ An error occurred during the test: {e}")
        traceback.print_exc()

async def _handle_kronos_config(parts: List[str], prometheus_instance: Prometheus):
    """Handles the 'config' command in the Kronos shell."""
    current_config = _load_kronos_config()
    
    if len(parts) == 1:
        print("\n--- Current Kronos Configuration ---")
        print(f"  correlation_interval_hours = {current_config.get('correlation_interval_hours')}")
        print(f"  workflow_analysis_chance   = {current_config.get('workflow_analysis_chance')}")
        print("\nUsage: config <key> <value>")
        print("Example: config correlation_interval_hours 8")
        return
        
    if len(parts) != 3:
        print("Usage: config <key> <value>")
        return

    key, value_str = parts[1].lower(), parts[2]
    
    try:
        if key == "correlation_interval_hours":
            new_value = float(value_str)
            if new_value < 0.1: raise ValueError("Interval must be at least 0.1 hours.")
            _save_kronos_config(key, new_value, prometheus_instance)
            
        elif key == "workflow_analysis_chance":
            new_value = float(value_str)
            if not (0.0 <= new_value <= 1.0): raise ValueError("Chance must be between 0.0 and 1.0.")
            _save_kronos_config(key, new_value, prometheus_instance)
            
        else:
            print(f"❌ Error: Unknown config key '{key}'.")
            print("   Available keys: correlation_interval_hours, workflow_analysis_chance")

    except ValueError as e:
        print(f"❌ Error: Invalid value '{value_str}'. {e}")
    except Exception as e:
        print(f"❌ An error occurred: {e}")

async def _handle_kronos_schedule(parts: List[str], prometheus_instance: Prometheus):
    """Handles the 'schedule' command for managing cron-like tasks."""
    if len(parts) < 2 or parts[1].lower() not in ['add', 'list', 'remove']:
        print("Usage: schedule <add|list|remove> [options]")
        print("  - add <interval> \"<command>\" [--market-hours] : e.g., schedule add 15m \"/risk\" --market-hours")
        print("  - list                                   : Show active scheduled jobs.")
        print("  - remove <job_id>                        : Remove a job by its ID.")
        return

    action = parts[1].lower()
    schedule = _load_schedule()

    if action == 'list':
        if not schedule:
            print("No commands are currently scheduled.")
            return
        print("\n--- Active Command Schedule ---")
        table_data = []
        for i, job in enumerate(schedule):
            table_data.append([
                i + 1,
                job['command_str'],
                job['interval_str'],
                job['next_run'].strftime('%Y-%m-%d %H:%M:%S'),
                "Yes" if job.get('market_hours_only', False) else "No" # Show market hours status
            ])
        print(tabulate(table_data, headers=["Job ID", "Command", "Interval", "Next Run (UTC)", "Market Hours Only"], tablefmt="grid"))

    elif action == 'remove':
        if len(parts) < 3:
            print("Usage: schedule remove <job_id>")
            return
        try:
            job_id_to_remove = int(parts[2])
            if 1 <= job_id_to_remove <= len(schedule):
                removed_job = schedule.pop(job_id_to_remove - 1)
                _save_schedule(schedule)
                print(f"✅ Removed scheduled job: \"{removed_job['command_str']}\"")
            else:
                print(f"❌ Error: Invalid Job ID. Use 'schedule list' to see valid IDs.")
        except ValueError:
            print("❌ Error: Job ID must be a number.")

    elif action == 'add':
        # Re-join all parts to find the quoted command
        full_input_str = " ".join(parts[2:])
        command_match = re.search(r"\"(.*?)\"", full_input_str)
        if not command_match:
            print("Usage: schedule add <interval> \"<command>\" [--market-hours]")
            print("Error: Command must be enclosed in double quotes.")
            return
        
        command_str = command_match.group(1)
        # Get the part *before* the quoted command as the interval
        interval_str = full_input_str[:command_match.start()].strip()
        # Get the part *after* the quoted command for flags
        flags_str = full_input_str[command_match.end():].strip()
        
        market_hours_only = "--market-hours" in flags_str
        
        interval_delta = _parse_interval_to_timedelta(interval_str)
        if not interval_delta or interval_delta.total_seconds() < 60:
            print("❌ Error: Invalid interval. Must be at least '1m' and use 'm', 'h', or 'd'.")
            return
            
        if not command_str.startswith(('/', '#')): # Allow internal commands
            print("❌ Error: Command must be a valid command string starting with '/' (e.g., \"/briefing\").")
            return
            
        # Add the new job
        new_job = {
            "command_str": command_str,
            "interval_str": interval_str,
            "interval_seconds": interval_delta.total_seconds(),
            "next_run": datetime.utcnow() + interval_delta, # Set first run
            "market_hours_only": market_hours_only # Store the new flag
        }
        schedule.append(new_job)
        _save_schedule(schedule)
        print(f"✅ Scheduled job added. Next run at: {new_job['next_run'].strftime('%Y-%m-%d %H:%M:%S')} UTC")
        if market_hours_only:
            print("   -> This job will only run during US market hours (Mon-Fri, 8:30-15:00 US/Central).")

async def _handle_kronos_analyze(parts: List[str], prometheus_instance: Prometheus):
    """Handles the 'analyze' command for querying the log database."""
    if len(parts) < 2 or parts[1].lower() not in ['logs']:
        print("Usage: analyze logs <command_name | errors>")
        print("  - <command_name>: e.g., /backtest. Shows performance evolution.")
        print("  - errors          : Shows most common errors.")
        return

    db_path = prometheus_instance.db_path
    if not os.path.exists(db_path):
        print(f"❌ Error: Prometheus database not found at '{db_path}'.")
        return

    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        target = " ".join(parts[2:])
        
        if target.lower() == 'errors':
            print(f"\n--- Top 10 Errors (Last 7 Days) ---")
            cursor.execute("""
                SELECT output_summary, COUNT(*) as count
                FROM command_log
                WHERE success = 0 AND timestamp >= ?
                GROUP BY output_summary
                ORDER BY count DESC
                LIMIT 10
            """, ((datetime.now() - timedelta(days=7)).isoformat(),))
            rows = cursor.fetchall()
            if not rows:
                print("No errors found in the last 7 days.")
                return
            table_data = [[row[0][:100] + "...", row[1]] for row in rows]
            print(tabulate(table_data, headers=["Error Message", "Count"], tablefmt="grid"))
            
        elif target.startswith('/'):
            command_name = target
            print(f"\n--- Performance Analysis for {command_name} (Last 30 Days) ---")
            
            # Check for backtest metrics
            if command_name == '/backtest':
                cursor.execute("""
                    SELECT 
                        STRFTIME('%Y-%m-%d', timestamp) as day,
                        AVG(backtest_sharpe_ratio),
                        AVG(backtest_return_pct),
                        COUNT(*)
                    FROM command_log
                    WHERE command = ? AND success = 1 AND backtest_sharpe_ratio IS NOT NULL
                          AND timestamp >= ?
                    GROUP BY day
                    ORDER BY day ASC
                """, (command_name, (datetime.now() - timedelta(days=30)).isoformat()))
                
                rows = cursor.fetchall()
                if not rows:
                    print(f"No successful backtest logs with metrics found for {command_name} in the last 30 days.")
                    return
                
                table_data = [[row[0], f"{row[1]:.3f}", f"{row[2]:.2f}%", row[3]] for row in rows]
                print(tabulate(table_data, headers=["Date", "Avg Sharpe", "Avg Return %", "Runs"], tablefmt="grid"))
            else:
                # Generic command analysis
                cursor.execute("""
                    SELECT 
                        STRFTIME('%Y-%m-%d', timestamp) as day,
                        AVG(duration_ms),
                        SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes,
                        SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) as failures
                    FROM command_log
                    WHERE command = ? AND timestamp >= ?
                    GROUP BY day
                    ORDER BY day ASC
                """, (command_name, (datetime.now() - timedelta(days=30)).isoformat()))
                
                rows = cursor.fetchall()
                if not rows:
                    print(f"No logs found for {command_name} in the last 30 days.")
                    return
                
                table_data = [[row[0], f"{row[1]:.0f} ms", row[2], row[3]] for row in rows]
                print(tabulate(table_data, headers=["Date", "Avg Duration", "Successes", "Failures"], tablefmt="grid"))

        else:
            print("Invalid target. Use 'errors' or a command name starting with '/'.")

    except sqlite3.Error as e:
        print(f"❌ Database error: {e}")
    except Exception as e:
        print(f"❌ An error occurred during analysis: {e}")
    finally:
        if conn:
            conn.close()

async def _handle_kronos_cache(parts: List[str]):
    """Handles the 'cache' command for managing data caches."""
    if len(parts) < 2 or parts[1].lower() not in ['list', 'clear']:
        print("Usage: cache <list|clear>")
        return
        
    action = parts[1].lower()
    
    # Define cache files
    cache_files = {
        "S&P 500": SP500_CACHE_FILE,
        "S&P 100": SP100_CACHE_FILE,
        # Add other cache files here as they are created
    }
    
    if action == 'list':
        print("\n--- Data Cache Status ---")
        table_data = []
        for name, filepath in cache_files.items():
            status = "❌ Not Found"
            size_mb = 0
            mod_time = "N/A"
            if os.path.exists(filepath):
                try:
                    stats = os.stat(filepath)
                    size_mb = stats.st_size / (1024 * 1024)
                    mod_time = datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                    status = "✅ Found"
                except Exception:
                    status = "⚠️ Error Reading"
            table_data.append([name, status, f"{size_mb:.2f} MB", mod_time])
        print(tabulate(table_data, headers=["Cache Name", "Status", "Size", "Last Modified"], tablefmt="grid"))

    elif action == 'clear':
        print("\n--- Clearing Data Caches ---")
        for name, filepath in cache_files.items():
            if os.path.exists(filepath):
                try:
                    os.remove(filepath)
                    print(f"   -> ✅ Removed '{name}' cache ({filepath})")
                except Exception as e:
                    print(f"   -> ❌ Failed to remove '{name}' cache: {e}")
            else:
                print(f"   -> ℹ️ '{name}' cache not found, skipping.")
        print("Cache clearing complete.")

def calculate_donchian_channels(data: pd.DataFrame, window: int) -> pd.DataFrame:
    """
    Calculates the Donchian Channels (Upper and Lower) for a given DataFrame.
    This function is now local to Kronos to avoid import errors.
    """
    df = data.copy()
    df['Upper_Channel'] = df['High'].rolling(window=window).max().shift(1)
    df['Lower_Channel'] = df['Low'].rolling(window=window).min().shift(1)
    return df

# --- Phase 5: Workflow Discovery (GA of GAs) ---

WORKFLOW_SEARCH_SPACE = {
    "sources": [
        {"type": "screener", "param": "AI_TECH_GROWTH", "desc": "AI Screener: Tech Growth"},
        {"type": "screener", "param": "AI_VALUE_STOCKS", "desc": "AI Screener: Value Stocks"},
        {"type": "screener", "param": "AI_STRONG_MOMENTUM", "desc": "AI Screener: Strong Momentum"},
        {"type": "sector", "param": "Technology", "desc": "Sector: Technology"},
        {"type": "sector", "param": "Healthcare", "desc": "Sector: Healthcare"},
        {"type": "sector", "param": "Financials", "desc": "Sector: Financials"},
    ],
    "filters": [
        {"type": "breakout", "param": 20, "desc": "Filter: 20-Day Breakout"},
        {"type": "powerscore", "param": 75, "desc": "Filter: PowerScore > 75"},
        {"type": None, "param": None, "desc": "Filter: None"},
    ],
    "actions": [
        {"type": "backtest", "param": "rsi", "desc": "Action: Backtest (RSI)"},
        {"type": "backtest", "param": "ma_crossover", "desc": "Action: Backtest (MA Crossover)"},
        {"type": "backtest", "param": "trend_following", "desc": "Action: Backtest (Trend Following)"},
    ]
}

def _generate_initial_workflow_population(population_size: int) -> List[Dict]:
    """Creates an initial random population of workflow individuals."""
    population = []
    for _ in range(population_size):
        individual = {
            "source": random.choice(WORKFLOW_SEARCH_SPACE["sources"]),
            "filter": random.choice(WORKFLOW_SEARCH_SPACE["filters"]),
            "action": random.choice(WORKFLOW_SEARCH_SPACE["actions"]),
        }
        population.append(individual)
    return population

async def _filter_tickers_by_breakout(tickers: List[str], window: int, prometheus_instance: Prometheus) -> List[str]:
    """
    Internal helper to filter a list of tickers for those currently breaking out.
    """
    if not tickers:
        return []
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=window * 2)
    
    data = await prometheus_instance.get_yf_download_robustly(
        tickers=tickers, start=start_date.strftime('%Y-%m-%d'), end=end_date.strftime('%Y-%m-%d')
    )
    if data.empty:
        return []

    breakout_tickers = []
    for ticker in tickers:
        try:
            if ('High', ticker) not in data.columns or ('Low', ticker) not in data.columns or ('Close', ticker) not in data.columns:
                continue

            ticker_df = data.loc[:, pd.IndexSlice[:, ticker]].copy()
            ticker_df.columns = ticker_df.columns.droplevel(1)
            
            if len(ticker_df.dropna()) < window + 1:
                continue

            ticker_df = calculate_donchian_channels(ticker_df, window)
            
            latest_close = ticker_df['Close'].iloc[-1]
            previous_upper = ticker_df['Upper_Channel'].iloc[-1]

            if pd.notna(latest_close) and pd.notna(previous_upper) and latest_close > previous_upper:
                breakout_tickers.append(ticker)
        except Exception:
            continue
            
    return breakout_tickers

async def _evaluate_workflow_fitness(workflow: Dict, condition: Dict, prometheus_instance: Prometheus) -> float:
    """
    Executes a full workflow and returns its fitness score (average backtest return %).
    """
    MAX_TICKERS_TO_EVALUATE = 5
    
    try:
        source_type = workflow["source"]["type"]
        source_param = workflow["source"]["param"]
        initial_tickers = []

        if source_type == "screener":
            universe_data = await _get_universe(source_param, prometheus_instance)
            initial_tickers = universe_data.get("tickers", [])
        elif source_type == "sector":
            universe_data = await _get_universe(source_param, prometheus_instance)
            initial_tickers = universe_data.get("tickers", [])

        if not initial_tickers: return -999.0

        filter_type = workflow["filter"]["type"]
        filtered_tickers = initial_tickers

        if filter_type == "breakout":
            filter_param = workflow["filter"]["param"]
            filtered_tickers = await _filter_tickers_by_breakout(initial_tickers, filter_param, prometheus_instance)
        elif filter_type == "powerscore":
            if len(initial_tickers) > 10:
                 filtered_tickers = random.sample(initial_tickers, 10)

        if not filtered_tickers: return -998.0

        action_type = workflow["action"]["type"]
        action_param = workflow["action"]["param"]
        
        if action_type == "backtest":
            tickers_to_evaluate = filtered_tickers
            if len(filtered_tickers) > MAX_TICKERS_TO_EVALUATE:
                tickers_to_evaluate = random.sample(filtered_tickers, MAX_TICKERS_TO_EVALUATE)

            tasks = []
            for ticker in tickers_to_evaluate:
                task = prometheus_instance.run_parameter_optimization(
                    command_name="/backtest", strategy_name=action_param,
                    ticker=ticker, start_date=condition["start_date"], end_date=condition["end_date"],
                    generations=5, population_size=10
                )
                tasks.append(task)
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            successful_returns = [res[1] for res in results if isinstance(res, tuple) and len(res) > 1 and isinstance(res[1], (int, float)) and res[1] > -900]

            if not successful_returns: return -997.0

            return statistics.mean(successful_returns)

    except Exception as e:
        prometheus_logger.warning(f"Workflow evaluation failed: {e}")
        return -1000.0
    
    return -1000.0

def _crossover_workflows(parents: List[Dict]) -> List[Dict]:
    parent1, parent2 = random.sample(parents, 2)
    child = {
        "source": parent1["source"] if random.random() < 0.5 else parent2["source"],
        "filter": parent1["filter"] if random.random() < 0.5 else parent2["filter"],
        "action": parent1["action"] if random.random() < 0.5 else parent2["action"],
    }
    return [child]

def _mutate_workflow(individual: Dict, mutation_rate: float) -> Dict:
    mutated_individual = individual.copy()
    if random.random() < mutation_rate:
        mutated_individual["source"] = random.choice(WORKFLOW_SEARCH_SPACE["sources"])
    if random.random() < mutation_rate:
        mutated_individual["filter"] = random.choice(WORKFLOW_SEARCH_SPACE["filters"])
    if random.random() < mutation_rate:
        mutated_individual["action"] = random.choice(WORKFLOW_SEARCH_SPACE["actions"])
    return mutated_individual

def _format_workflow(workflow: Dict) -> str:
    source_str = workflow['source']['desc']
    filter_str = workflow['filter']['desc']
    action_str = workflow['action']['desc']
    return f"{source_str} -> {filter_str} -> {action_str}"

async def _run_workflow_optimization(run_name: str, condition_name: str, generations: int, population_size: int, prometheus_instance: Prometheus):
    """Orchestrator for the workflow discovery GA."""
    print(f"\n--- 🚀 Starting Workflow Discovery Run: '{run_name}' ---")
    
    condition = MARKET_CONDITIONS.get(condition_name)
    if not condition:
        print(f"❌ Error: Market condition '{condition_name}' not found.")
        return

    print(f"   Market Condition: {condition_name} ({condition['start_date']} to {condition['end_date']})")
    print(f"   Generations: {generations}, Population Size: {population_size}")

    # 1. Initialize Population
    current_population = _generate_initial_workflow_population(population_size)
    best_workflow_overall = None
    best_fitness_overall = -float('inf')

    # 2. Run GA Loop
    for gen in range(generations):
        print(f"\n--- [Generation {gen + 1}/{generations}] ---")
        print("   -> Evaluating workflow fitness...")

        tasks = [_evaluate_workflow_fitness(ind, condition, prometheus_instance) for ind in current_population]
        fitness_scores = await asyncio.gather(*tasks)

        population_with_fitness = sorted(
            zip(current_population, fitness_scores),
            key=lambda item: item[1],
            reverse=True
        )

        current_best_workflow, current_best_fitness = population_with_fitness[0]
        
        print(f"   -> Best Fitness (Avg Return %): {current_best_fitness:.2f}%")
        print(f"      Workflow: {_format_workflow(current_best_workflow)}")

        if current_best_fitness > best_fitness_overall:
            best_fitness_overall = current_best_fitness
            best_workflow_overall = current_best_workflow
            print("      ✨ New Overall Best Found! ✨")

        # 3. Evolve Population
        num_parents = population_size // 2
        parents = [ind for ind, fit in population_with_fitness[:num_parents]]
        
        offspring = []
        for _ in range(population_size - num_parents):
            offspring.extend(_crossover_workflows(parents))
            
        mutated_offspring = [_mutate_workflow(ind, mutation_rate=0.3) for ind in offspring]

        current_population = parents + mutated_offspring

    print("\n--- 🏆 Workflow Discovery Finished 🏆 ---")
    if best_workflow_overall:
        print(f"  Best Workflow Found: {_format_workflow(best_workflow_overall)}")
        print(f"  Best Fitness (Avg Return %): {best_fitness_overall:.2f}%")
        
        # --- NEW: Display Screener Parameters ---
        if best_workflow_overall['source']['type'] == 'screener':
            screener_name = best_workflow_overall['source']['param']
            screener_params = AI_SCREENER_DEFINITIONS.get(screener_name)
            if screener_params:
                print("\n  AI Screener Parameters:")
                print(json.dumps(screener_params['params'], indent=2))
        # --- END NEW ---

        print("\n  JSON Definition:")
        print(json.dumps(best_workflow_overall, indent=2))
    else:
        print("  No successful workflow was found.")
        
async def _handle_kronos_discover(parts: List[str], prometheus_instance: Prometheus):
    if len(parts) < 2:
        print("Usage: discover <run_name> --condition=<name> [--generations=5] [--population=10]")
        print("  --condition: A defined market condition (e.g., 2022_Bear, Current_1Y)")
        return

    run_name = parts[1]
    args = parts[2:]
    
    parsed_args = {"condition": None, "generations": 5, "population": 10}
    for arg in args:
        if arg.startswith("--condition="):
            parsed_args["condition"] = arg.split('=', 1)[1]
        elif arg.startswith("--generations="):
            parsed_args["generations"] = int(arg.split('=', 1)[1])
        elif arg.startswith("--population="):
            parsed_args["population"] = int(arg.split('=', 1)[1])
            
    if not parsed_args["condition"]:
        print("❌ Error: --condition is a required argument.")
        return

    await _run_workflow_optimization(
        run_name=run_name, condition_name=parsed_args["condition"],
        generations=parsed_args["generations"], population_size=parsed_args["population"],
        prometheus_instance=prometheus_instance
    )

async def handle_kronos_command(args: List[str], prometheus_instance: Prometheus):
    """
    Opens the interactive Kronos shell to manage the Prometheus instance.
    """
    if not prometheus_instance:
        print("❌ CRITICAL: Kronos command cannot start. The Prometheus instance was not provided.")
        return

    print("\n--- Kronos Meta-Control Shell ---")
    print("Welcome, Kronos. Manage Prometheus's autonomy.")
    print("Type 'help' for commands, 'exit' to return to Singularity.")
    
    while True:
        try:
            active_str = "ACTIVE" if prometheus_instance.is_active else "INACTIVE"
            user_input = await asyncio.to_thread(input, f"Kronos ({active_str})> ")
            
            if not user_input:
                continue
                
            parts = user_input.split()
            cmd = parts[0].lower()
            
            if cmd == 'exit':
                print("Exiting Kronos shell.")
                break
                
            elif cmd == 'help':
                print("\n--- Kronos Commands ---")
                print("  status <on|off>      : Toggle Prometheus autonomous features ON or OFF.")
                print("  discover <name> --condition=... : [Phase 5] Run workflow optimization to find the best trading processes.")
                print("                         (e.g., discover FindAlpha --condition=2022_Bear)")
                print("  convergence <name> --universes=... : [Phase 1-4] Run meta-optimization for strategy parameters.")
                print("                         (e.g., convergence Q4_Test --universes=SPY_500 --conditions=2022_Bear --strategies=rsi)")
                print("  optimize <strat> <t> <p>... : Run GA parameter optimization for a single /backtest strategy.")
                print("                         (e.g., optimize rsi SPY 1y)")
                print("  test <file> <t> <p> [auto|manual] : Run the full 'Hypothesize -> Generate -> Test -> Overwrite' loop.")
                print("                         (e.g., test backtest_command.py SPY 2y manual)")
                print("  schedule <add|list|remove>... : Manage scheduled tasks.")
                print("                         (e.g., schedule add 4h \"/briefing\")")
                print("  analyze logs <cmd|errors> : Analyze the Prometheus command log database.")
                print("                         (e.g., analyze logs /backtest)")
                print("  cache <list|clear>   : View or clear data caches (e.g., for /risk).")
                print("  config [key] [value] : View or set automation parameters.")
                print("                         (e.g., config correlation_interval_hours 8)")
                print("  help                 : Show this help message.")
                print("  exit                 : Return to the main Singularity shell.")
                
            elif cmd == 'status':
                await _handle_kronos_status(parts, prometheus_instance)
                
            elif cmd == 'convergence':
                if not prometheus_instance.is_active:
                    print("   -> Cannot run convergence. Prometheus is INACTIVE.")
                    continue
                await _handle_kronos_convergence(parts, prometheus_instance)

            elif cmd == 'discover':
                if not prometheus_instance.is_active:
                    print("   -> Cannot run discovery. Prometheus is INACTIVE.")
                    continue
                await _handle_kronos_discover(parts, prometheus_instance)

            elif cmd == 'optimize':
                if not prometheus_instance.is_active:
                    print("   -> Cannot optimize. Prometheus is INACTIVE.")
                    continue
                await _handle_kronos_optimize(parts, prometheus_instance)
                
            elif cmd == 'test':
                if not prometheus_instance.is_active:
                    print("   -> Cannot test. Prometheus is INACTIVE.")
                    continue
                await _handle_kronos_test(parts, prometheus_instance)
                
            elif cmd == 'config':
                await _handle_kronos_config(parts, prometheus_instance)
                
            elif cmd == 'schedule':
                await _handle_kronos_schedule(parts, prometheus_instance)
                
            elif cmd == 'analyze':
                await _handle_kronos_analyze(parts, prometheus_instance)
                
            elif cmd == 'cache':
                await _handle_kronos_cache(parts)
                
            else:
                print(f"Unknown Kronos command: '{cmd}'. Type 'help'.")
                
        except EOFError:
            print("\nExiting Kronos shell (EOF).")
            break
        except KeyboardInterrupt:
            print("\nExiting Kronos shell (Interrupt).")
            break
        except Exception as e:
            print(f"❌ An error occurred in the Kronos shell: {e}")
            traceback.print_exc()

async def kronos_scheduler_worker(prometheus_instance: Prometheus):
    """
    The background worker that runs scheduled tasks.
    This should be started as an asyncio.Task in main_singularity.py.
    """
    print("🚀 Kronos Scheduler Worker has been started.")
    while True:
        await asyncio.sleep(60) # Check every minute
        
        # Only run jobs if Prometheus is active
        if not prometheus_instance or not prometheus_instance.is_active:
            continue
            
        schedule = _load_schedule()
        now_utc = datetime.utcnow()
        schedule_updated = False
        
        for job in schedule:
            if now_utc >= job['next_run']:
                print(f"\n[Kronos Scheduler] Triggering job: {job['command_str']}")
                
                # --- NEW: Market Hours Check ---
                is_market_hours_job = job.get('market_hours_only', False)
                if is_market_hours_job and not _is_market_open():
                    print(f"   -> Skipping job: '{job['command_str']}'. Reason: Market is closed.")
                    # Reschedule for the next interval
                    interval_delta = timedelta(seconds=job['interval_seconds'])
                    job['next_run'] = now_utc + interval_delta
                    schedule_updated = True
                    continue # Skip this job
                # --- END NEW ---

                try:
                    # --- FIX: Robust command parsing for sub-shells ---
                    command_str = job['command_str']
                    
                    # Split the command from its arguments
                    parts = command_str.split()
                    command_with_slash = parts[0]
                    args = parts[1:]

                    # Check for meta-commands (commands that are shells themselves)
                    if command_with_slash == "/prometheus":
                        # This is a command *for* the Prometheus shell
                        # Re-map it to the *actual* Singularity command.
                        if args and args[0] == "generate" and args[1] == "memo":
                            print(f"   -> Remapping {command_str} to /memo")
                            await prometheus_instance.execute_and_log("/memo", [], called_by_user=False, internal_call=True)
                        # Add other mappings here as needed
                        # e.g., elif args and args[0] == "analyze" and args[1] == "patterns":
                        #   await prometheus_instance.analyze_workflows()
                        else:
                            print(f"   -> ERROR: Scheduled /prometheus command '{' '.join(args)}' is not recognized by Kronos.")

                    elif command_with_slash == "/kronos":
                        # This is a command *for* the Kronos shell itself
                        if args and args[0] == "test":
                            print(f"   -> Executing Kronos command: test {' '.join(args[1:])}")
                            await _handle_kronos_test(args, prometheus_instance)
                        # Add other internal Kronos commands here if needed
                        else:
                            print(f"   -> ERROR: Scheduled /kronos command '{' '.join(args)}' is not supported for automation.")
                    
                    else:
                        # This is a standard Singularity command (e.g., /risk, /briefing)
                        print(f"   -> Executing standard command: {command_with_slash}")
                        await prometheus_instance.execute_and_log(
                            command_name_with_slash=command_with_slash,
                            args=args,
                            called_by_user=False,
                            internal_call=True
                        )
                    # --- END FIX ---
                    
                except Exception as e:
                    print(f"❌ [Kronos Scheduler] Error running job '{job['command_str']}': {e}")
                
                # Reschedule for the next interval
                interval_delta = timedelta(seconds=job['interval_seconds'])
                job['next_run'] = now_utc + interval_delta
                schedule_updated = True
        
        if schedule_updated:
            _save_schedule(schedule)