# --- Imports ---
import yfinance as yf
import pandas as pd
import math
from math import sqrt
from tabulate import tabulate
import os
import uuid
import matplotlib
matplotlib.use('Agg')
import asyncio
import matplotlib.pyplot as plt
import numpy as np
from tradingview_screener import Query, Column # Keep if used elsewhere, maybe not needed directly now
import csv
from datetime import datetime, timedelta
import pytz
from typing import Optional, List, Dict, Any, Tuple, Callable # Added Callable
import time as py_time
import traceback
import logging
import json
import google.generativeai as genai
from google.generativeai.types import FunctionDeclaration, Tool
import fear_and_greed # Keep if used
import humanize
from nltk.tokenize import sent_tokenize
import nltk
import glob # Keep if used
import random
from bs4 import BeautifulSoup # Keep if used
import requests # Keep if used
from fpdf import FPDF # Keep if used
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle # Keep if used
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle # Keep if used
from reportlab.lib.units import inch # Keep if used
from reportlab.lib import colors # Keep if used
from reportlab.lib.enums import TA_CENTER, TA_LEFT # Keep if used
import configparser
from scipy.stats import norm # Keep if used
from urllib.parse import quote_plus # Keep if used
from sklearn.model_selection import train_test_split # Keep if used elsewhere
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor # Keep if used elsewhere
from sklearn.metrics import accuracy_score # Keep if used elsewhere
from scipy.interpolate import CubicSpline # Keep if used elsewhere
from pypfopt import EfficientFrontier # Keep if used elsewhere
from pypfopt import risk_models # Keep if used elsewhere
from pypfopt import expected_returns # Keep if used elsewhere
import seaborn as sns # Keep if used elsewhere
import speech_recognition as sr # Keep if used
from gtts import gTTS # Keep if used elsewhere, maybe not needed now
import playsound # Keep if used elsewhere, maybe not needed now
import pyaudio # Keep if used elsewhere, maybe not needed now
from scipy.stats import percentileofscore # Keep if used elsewhere
import urllib3
import smtplib # Keep if used elsewhere
from email.mime.multipart import MIMEMultipart # Keep if used elsewhere
from email.mime.text import MIMEText # Keep if used elsewhere
from io import StringIO
import sys
import re
import networkx as nx # Keep if used elsewhere
import time
from dateutil.relativedelta import relativedelta
import string
from contextlib import contextmanager
import io

# For Web: cd "C:\Users\victo\Downloads\MIC CODE FILES\portfolio-lab"
# >> npm run dev

# --- Prometheus Core Import ---
from prometheus_core import Prometheus # Import the new class
from kronos_command import handle_kronos_command, kronos_scheduler_worker # <<< ADD THIS LINE

# --- Command Module Imports ---
# Import all command handlers
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'Isolated Commands'))

from ai_command import handle_ai_prompt, handle_voice_command, initialize_ai_components # type: ignore
from invest_command import ( # type: ignore
    handle_invest_command,
    process_custom_portfolio,
    calculate_ema_invest,
    calculate_one_year_invest,
    plot_ticker_graph,
    get_allocation_score,
    generate_portfolio_pie_chart
) # type: ignore
from quickscore_command import handle_quickscore_command # type: ignore
from sentiment_command import handle_sentiment_command, get_ai_sentiment_analysis # type: ignore
from optimize_command import handle_optimize_command # type: ignore
from dev_command import handle_dev_command #type: ignore
from backtest_command import handle_backtest_command # type: ignore
from compare_command import handle_compare_command # type: ignore
from custom_command import handle_custom_command, get_comparison_for_custom_portfolio # type: ignore
from cultivate_command import handle_cultivate_command, run_cultivate_analysis_singularity # type: ignore
from powerscore_command import handle_powerscore_command # type: ignore
from breakout_command import handle_breakout_command # type: ignore
from counter_command import initialize_counter_files, increment_command_count, handle_counter_command # type: ignore
from risk_command import handle_risk_command # type: ignore
from assess_command import handle_assess_command # type: ignore
from macdforecast_command import handle_macd_forecast_command # type: ignore
from heatmap_command import handle_heatmap_command # type: ignore
from market_command import handle_market_command # type: ignore
from strategies_command import handle_strategies_command # type: ignore
from futures_command import handle_futures_command # type: ignore
from favorites_command import handle_favorites_command # type: ignore
from fundamentals_command import handle_fundamentals_command # type: ignore
from briefing_command import handle_briefing_command # type: ignore
# Adjusted import for report generation functions
from reportgeneration_command import handle_report_generation, generate_ai_driven_report, create_dynamic_investment_plan # type: ignore
from help_command import handle_help_command, load_command_states # type: ignore
from spear_command import handle_spear_command # type: ignore
from sector_command import handle_sector_command # type: ignore
from options_command import handle_options_command # type: ignore
from simulation_command import handle_simulation_command # type: ignore
from mlforecast_command import handle_mlforecast_command # type: ignore
from history_command import handle_history_command # type: ignore
from web_command import handle_web_command # type: ignore
from monitor_command import handle_monitor_command, load_alerts_from_csv, alert_worker, persistent_alert_worker # type: ignore
from tracking_command import handle_tracking_command # type: ignore
from fairvalue_command import handle_fairvalue_command # type: ignore
from derivative_command import handle_derivative_command # type: ignore
from nexus_command import handle_nexus_command # type: ignore
# prometheus_core import is already done above

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

@contextmanager
def suppress_output():
    """A context manager to temporarily suppress stdout."""
    original_stdout = sys.stdout
    sys.stdout = io.StringIO() # Redirect to a dummy stream
    try:
        yield
    finally:
        sys.stdout = original_stdout # Restore original stdout

try:
    nltk.data.find('tokenizers/punkt')
except LookupError:
    print("📂 Downloading 'punkt' for nltk...")
    nltk.download('punkt', quiet=True)

# --- Configuration File Setup ---
config = configparser.ConfigParser()
config_file = 'config.ini'

COMMAND_USAGE_TIMESTAMPS = {}
COMMAND_STATES_CACHE = {}

if not os.path.exists(config_file):
    raise FileNotFoundError(f"Error: The configuration file '{config_file}' was not found. Please create it.")

try:
    config.read(config_file)

    # --- Load API Keys ---
    GEMINI_API_KEY = config.get('API_KEYS', 'GEMINI_API_KEY', fallback=None) # Changed fallback to None

    # --- Load File Paths ---
    file_paths = config['FILE_PATHS']
    PORTFOLIO_DB_FILE = file_paths.get('PORTFOLIO_DB_FILE', 'portfolio_codes_database.csv')
    PORTFOLIO_OUTPUT_DIR = file_paths.get('PORTFOLIO_OUTPUT_DIR', 'portfolio_outputs')
    BREAKOUT_TICKERS_FILE = file_paths.get('BREAKOUT_TICKERS_FILE', 'breakout_tickers.csv')
    BREAKOUT_HISTORICAL_DB_FILE = file_paths.get('BREAKOUT_HISTORICAL_DB_FILE', 'breakout_historical_database.csv')
    MARKET_FULL_SENS_DATA_FILE_PREFIX = file_paths.get('MARKET_FULL_SENS_DATA_FILE_PREFIX', 'market_full_sens_')
    CULTIVATE_INITIAL_METRICS_FILE = file_paths.get('CULTIVATE_INITIAL_METRICS_FILE', 'cultivate_initial_metrics.csv')
    CULTIVATE_T1_FILE = file_paths.get('CULTIVATE_T1_FILE', 'cultivate_ticker_list_one.csv')
    CULTIVATE_T_MINUS_1_FILE = file_paths.get('CULTIVATE_T_MINUS_1_FILE', 'cultivate_ticker_list_negative_one.csv')
    CULTIVATE_TF_FINAL_FILE = file_paths.get('CULTIVATE_TF_FINAL_FILE', 'cultivate_ticker_list_final.csv')
    CULTIVATE_COMBINED_DATA_FILE_PREFIX = file_paths.get('CULTIVATE_COMBINED_DATA_FILE_PREFIX', 'cultivate_combined_')
    USERS_FAVORITES_FILE = file_paths.get('USERS_FAVORITES_FILE', 'users_favorites.txt') # Likely deprecated if using JSON prefs
    USER_PREFERENCES_FILE = file_paths.get('USER_PREFERENCES_FILE', 'user_preferences.json')
    RISK_CSV_FILE = file_paths.get('RISK_CSV_FILE', 'market_data.csv')
    RISK_EOD_CSV_FILE = file_paths.get('RISK_EOD_CSV_FILE', 'risk_eod_data.csv')
    RISK_LOG_FILE = file_paths.get('RISK_LOG_FILE', 'risk_calculations.log')

    # --- Load App Settings ---
    app_settings = config['APP_SETTINGS']
    EST_TIMEZONE = pytz.timezone(app_settings.get('TIMEZONE', 'US/Eastern'))
    MARKET_HEDGING_TICKERS = [t.strip().upper() for t in app_settings.get('MARKET_HEDGING_TICKERS', 'SPY,DIA,QQQ').split(',')]
    RESOURCE_HEDGING_TICKERS = [t.strip().upper() for t in app_settings.get('RESOURCE_HEDGING_TICKERS', 'GLD,SLV').split(',')]
    HEDGING_TICKERS = MARKET_HEDGING_TICKERS + RESOURCE_HEDGING_TICKERS

except (configparser.Error, pytz.exceptions.UnknownTimeZoneError, KeyError) as e:
    print(f"❌ Error reading configuration file '{config_file}': {e}. Using default fallbacks.")
    # Provide safe defaults if config reading fails
    GEMINI_API_KEY = None # Default to None if not found
    EST_TIMEZONE = pytz.timezone('US/Eastern')
    MARKET_HEDGING_TICKERS = ['SPY', 'DIA', 'QQQ']
    RESOURCE_HEDGING_TICKERS = ['GLD', 'SLV']
    HEDGING_TICKERS = MARKET_HEDGING_TICKERS + RESOURCE_HEDGING_TICKERS
    PORTFOLIO_DB_FILE = 'portfolio_codes_database.csv'
    PORTFOLIO_OUTPUT_DIR = 'portfolio_outputs'
    # Define other paths with defaults
    BREAKOUT_TICKERS_FILE = 'breakout_tickers.csv'
    BREAKOUT_HISTORICAL_DB_FILE = 'breakout_historical_database.csv'
    MARKET_FULL_SENS_DATA_FILE_PREFIX = 'market_full_sens_'
    CULTIVATE_INITIAL_METRICS_FILE = 'cultivate_initial_metrics.csv'
    CULTIVATE_T1_FILE = 'cultivate_ticker_list_one.csv'
    CULTIVATE_T_MINUS_1_FILE = 'cultivate_ticker_list_negative_one.csv'
    CULTIVATE_TF_FINAL_FILE = 'cultivate_ticker_list_final.csv'
    CULTIVATE_COMBINED_DATA_FILE_PREFIX = 'cultivate_combined_'
    USERS_FAVORITES_FILE = 'users_favorites.txt'
    USER_PREFERENCES_FILE = 'user_preferences.json'
    RISK_CSV_FILE = 'market_data.csv'
    RISK_EOD_CSV_FILE = 'risk_eod_data.csv'
    RISK_LOG_FILE = 'risk_calculations.log'

# --- Global State Variables ---
gemini_model, tts_engine, AVAILABLE_PYTHON_FUNCTIONS = None, None, {}
GEMINI_CHAT_SESSION = None # Potentially deprecated if manage history differently
AI_CONVERSATION_HISTORY = []
CURRENT_AI_SESSION_ORIGINAL_REQUEST = None
AI_INTERNAL_STEP_COUNT = 0 # Potentially deprecated
GEMINI_API_LOCK = asyncio.Lock()
YFINANCE_API_SEMAPHORE = asyncio.Semaphore(8) # Keep for yfinance limiting
YFINANCE_LOCK = asyncio.Lock() # Keep if still needed for specific yfinance operations
API_TASK_SEMAPHORE = asyncio.Semaphore(8) # Keep if limiting other API calls
risk_persistent_signal = "Hold" # Keep if used
risk_signal_day = None # Keep if used

# --- Logger Setup ---
# Setup for the main risk logger (as it was)
risk_logger = logging.getLogger('RISK_MODULE')
risk_logger.setLevel(logging.INFO)
risk_logger.propagate = False
if not risk_logger.hasHandlers():
    try:
        risk_file_handler = logging.FileHandler(RISK_LOG_FILE)
        risk_formatter = logging.Formatter('%(asctime)s - %(levelname)s - Module:%(module)s - Func:%(funcName)s - %(message)s')
        risk_file_handler.setFormatter(risk_formatter)
        risk_logger.addHandler(risk_file_handler)
    except Exception as e_log:
        print(f"❌ Error setting up risk logger: {e_log}")


# --- All Helper functions from original main_singularity.py go here ---
# (make_hashable, safe_get, safe_score, get_yfinance_info_robustly, etc.)
# --- IMPORTANT: Ensure find_and_screen_stocks is defined or imported here ---
async def find_and_screen_stocks(args: List[str], ai_params: Dict[str, Any], is_called_by_ai: bool = False) -> Dict[str, Any]:
    """
    Placeholder for the find_and_screen_stocks function.
    Ensure the actual implementation is present here or correctly imported.
    This version is just a stub returning an error.
    """
    # NOTE: You MUST replace this with the *actual implementation* of
    # find_and_screen_stocks from your original main_singularity file.
    # It involves calling the external 'screentest.py' script.
    print(f"--- [DEBUG] find_and_screen_stocks called (AI: {is_called_by_ai}) ---")
    print(f"   -> Args: {args}")
    print(f"   -> AI Params: {ai_params}")
    # --- Replace below with the actual logic ---
    if not is_called_by_ai:
        return {"status": "error", "message": "This function is primarily for AI use via Prometheus."}
    # Example logic (replace with your actual subprocess call)
    script_path = os.path.join(os.path.dirname(__file__), 'screentest.py') # Assuming screentest.py exists
    if not os.path.exists(script_path):
         return {"status": "error_tool_exception", "message": f"Screener script '{script_path}' not found."}
    # Dummy result simulation - replace with actual run_external_script call
    print("   -> (Simulating screener call - replace with actual implementation)")
    await asyncio.sleep(1) # Simulate work
    # Dummy success response
    # return {"status": "success", "results": [{'Ticker': 'AAPL', 'Reason': 'Matches criteria'}], "message": "Screening complete."}
    # Dummy error response
    return {"status": "error_subprocess", "message": "Simulated screener subprocess failure."}
    # --- End of replacement section ---

# --- Add other necessary helper functions from main_singularity.py ---
# e.g., get_yf_download_robustly, get_yf_data_singularity, get_gics_map,
# load_user_preferences, update_user_preference_tool, get_treasury_yield_data,
# filter_stocks_by_gics, pre_screen_stocks_by_sensitivity, build_gics_database_file etc.
# Make sure they don't rely on global variables that aren't defined here.

# --- Placeholder/Example Helper Functions (ensure real ones are present) ---
def make_hashable(obj): # Keep as is
    if "MapComposite" in str(type(obj)): obj = dict(obj)
    elif "RepeatedComposite" in str(type(obj)): obj = list(obj)
    if isinstance(obj, dict): return tuple((k, make_hashable(v)) for k, v in sorted(obj.items()))
    if isinstance(obj, list): return tuple(make_hashable(e) for e in obj)
    return obj
def safe_get(data_dict, key, default=None): # Keep as is
    value = data_dict.get(key, default)
    if value is None or value == 'None': return default
    return value
def safe_score(value: Any) -> float: # Keep as is
    try:
        if pd.isna(value) or value is None: return 0.0
        if isinstance(value, str): value = value.replace('%', '').replace('$', '').strip()
        return float(value)
    except (ValueError, TypeError): return 0.0
async def get_yfinance_info_robustly(ticker: str) -> Optional[Dict[str, Any]]: # Keep as is
    async with YFINANCE_API_SEMAPHORE:
        for attempt in range(3):
            try:
                await asyncio.sleep(random.uniform(0.2, 0.5))
                stock_info = await asyncio.to_thread(lambda: yf.Ticker(ticker).info)
                # More robust check for valid info
                if stock_info and ('regularMarketPrice' in stock_info or 'currentPrice' in stock_info):
                    return stock_info
                else:
                     raise ValueError(f"Incomplete data received for {ticker}")
            except Exception as e:
                if attempt < 2: await asyncio.sleep((attempt + 1) * 2)
                else: print(f"   -> ❌ ERROR: All attempts to fetch .info for {ticker} failed. Last error: {type(e).__name__}")
    return None
async def get_yf_download_robustly(tickers: list, **kwargs) -> pd.DataFrame: # Keep as is
    max_retries = 3
    for attempt in range(max_retries):
        try:
            await asyncio.sleep(random.uniform(0.3, 0.7)) # Stagger requests
            # Ensure progress=False is explicitly passed if not in kwargs
            kwargs.setdefault('progress', False)
            data = await asyncio.to_thread( yf.download, tickers=tickers, **kwargs )
            if data.empty and len(tickers) == 1:
                 # yfinance sometimes returns empty for single valid tickers temporarily
                 raise IOError(f"yf.download returned empty DataFrame for single ticker: {tickers[0]}")
            # Do not raise error for empty on multi-ticker downloads immediately
            # Check later if *all* failed.
            return data # Success (even if partially empty for multi-ticker)
        except Exception as e:
            if attempt < max_retries - 1:
                delay = (attempt + 1) * 3 # Backoff: 3s, 6s
                print(f"   -> WARNING: yf.download failed (Attempt {attempt+1}/{max_retries}). Retrying in {delay}s...")
                await asyncio.sleep(delay)
            else:
                print(f"   -> ❌ ERROR: All yfinance download attempts failed for {tickers}. Last error: {type(e).__name__}")
                return pd.DataFrame()
    return pd.DataFrame()
# --- (Ensure all other required helpers are here) ---
# --- Needed for Prometheus Background Task ---
def get_sp500_symbols_singularity(is_called_by_ai: bool = False) -> List[str]:
    """Fetches S&P 500 symbols from Wikipedia using the requests library for reliability."""
    # This function is duplicated here because Prometheus needs it, and importing
    # directly from main_singularity inside Prometheus can be problematic.
    # Ensure this implementation is kept in sync with any changes in other files.
    try:
        sp500_url = 'https://en.wikipedia.org/wiki/List_of_S%26P_500_companies'
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(sp500_url, headers=headers, timeout=15)
        response.raise_for_status()
        dfs = pd.read_html(StringIO(response.text))
        if not dfs: return []
        sp500_df = dfs[0]
        if 'Symbol' not in sp500_df.columns: return []
        symbols = [str(s).replace('.', '-') for s in sp500_df['Symbol'].tolist() if isinstance(s, str)]
        return sorted(list(set(s for s in symbols if s)))
    except Exception as e:
        print(f"-> Error fetching S&P 500 symbols (in main): {e}")
        return []

# --- NEW: Memo Command Handler ---
async def handle_memo_command(args: List[str], ai_params: Optional[Dict] = None, is_called_by_ai: bool = False, prometheus_instance: Prometheus = None, gemini_model_obj: Any = None):
    """Wrapper function to call the memo generation within the Prometheus instance."""
    if not prometheus_instance:
        print("Error: Prometheus instance not available for memo generation.")
        return {"status": "error", "message": "Prometheus instance missing."}
    # Pass necessary dependencies (Prometheus instance, Gemini model)
    return await prometheus_instance.generate_market_memo(args, ai_params, is_called_by_ai, prometheus_instance, gemini_model_obj)


async def handle_strategy_recipe_command(args: List[str], ai_params: Optional[Dict] = None, is_called_by_ai: bool = False, prometheus_instance: Prometheus = None, gemini_model_obj: Any = None):
    """Wrapper function to call the strategy recipe generation within the Prometheus instance."""
    if not prometheus_instance:
        print("Error: Prometheus instance not available for recipe generation.")
        return {"status": "error", "message": "Prometheus instance missing."}
    # Pass necessary dependencies (Prometheus instance, Gemini model)
    return await prometheus_instance.generate_strategy_recipe(args, ai_params, is_called_by_ai, prometheus_instance, gemini_model_obj)

# --- NEW: Improvement Proposal Command Handler ---
async def handle_propose_improvement_command(args: List[str], ai_params: Optional[Dict] = None, is_called_by_ai: bool = False, prometheus_instance: Prometheus = None, gemini_model_obj: Any = None):
    """Wrapper function to call the improvement proposal logic within the Prometheus instance."""
    if not prometheus_instance:
        print("Error: Prometheus instance not available for improvement proposal.")
        return {"status": "error", "message": "Prometheus instance missing."}
    # Pass necessary dependencies (Prometheus instance, Gemini model)
    # Note: 'called_by_user' will be set correctly by Prometheus.execute_and_log
    return await prometheus_instance.analyze_and_propose_improvement(args=args, ai_params=ai_params, is_called_by_ai=is_called_by_ai, prometheus_instance=prometheus_instance, gemini_model_obj=gemini_model_obj)

# --- Tool Box Map Definition ---
TOOLBOX_MAP: Dict[str, Callable] = {
    "briefing": handle_briefing_command,
    "breakout": handle_breakout_command,
    "quickscore": handle_quickscore_command,
    "market": handle_market_command,
    "risk": handle_risk_command,
    "history": handle_history_command,
    "custom": handle_custom_command,
    "cultivate": handle_cultivate_command,
    "invest": handle_invest_command,
    "assess": handle_assess_command,
    "comparison": get_comparison_for_custom_portfolio,
    "spear": handle_spear_command,
    "macdforecast": handle_macd_forecast_command,
    "favorites": handle_favorites_command,
    "fundamentals": handle_fundamentals_command,
    "report": handle_report_generation,
    "sentiment": handle_sentiment_command,
    "powerscore": handle_powerscore_command,
    "backtest": handle_backtest_command,
    "fairvalue": handle_fairvalue_command,
    "heatmap": handle_heatmap_command,
    "optimize": handle_optimize_command,
    "sector": handle_sector_command,
    "strategies": handle_strategies_command,
    "futures": handle_futures_command,
    "compare": handle_compare_command,
    "derivative": handle_derivative_command,
    "mlforecast": handle_mlforecast_command,
    "options": handle_options_command,
    "simulation": handle_simulation_command,
    "web": handle_web_command,
    "monitor": handle_monitor_command,
    "tracking": handle_tracking_command,
    "counter": handle_counter_command,
    "dev": handle_dev_command,
    "help": handle_help_command,
    "memo": handle_memo_command,
    "strategy_recipe": handle_strategy_recipe_command,
    "propose_improvement": handle_propose_improvement_command, # <<< Added improvement proposal handler
    "kronos": handle_kronos_command, # <<< ADD THIS LINE,
    "nexus": handle_nexus_command # <<< ADD THIS LINE
}

# --- Core Application Logic ---
async def main_singularity():
    global AI_CONVERSATION_HISTORY, CURRENT_AI_SESSION_ORIGINAL_REQUEST, AI_INTERNAL_STEP_COUNT
    global gemini_model, tts_engine, AVAILABLE_PYTHON_FUNCTIONS
    global COMMAND_STATES_CACHE

    # ... (Initialization code remains the same up to func_to_command_map) ...
    # Initialize AI Components
    with suppress_output():
        gemini_model, tts_engine, AVAILABLE_PYTHON_FUNCTIONS = initialize_ai_components(GEMINI_API_KEY, globals())

    # Initialize Counter and Alerts
    await initialize_counter_files()
    await load_alerts_from_csv()

    # Initialize Prometheus Core
    print("-> Initializing Project Prometheus Core...")
    prometheus = Prometheus(
        gemini_api_key=GEMINI_API_KEY,
        toolbox_map=TOOLBOX_MAP,
        risk_command_func=handle_risk_command,
        derivative_func=handle_derivative_command,
        mlforecast_func=handle_mlforecast_command,
        screener_func=find_and_screen_stocks,
        powerscore_func=handle_powerscore_command,
        sentiment_func=handle_sentiment_command,
        fundamentals_func=handle_fundamentals_command,
        quickscore_func=handle_quickscore_command
    )
    print("   -> Prometheus Core is active.")

    # Post-Prometheus Initial Setup
    gics_db_path = os.path.join(os.path.dirname(__file__), 'gics_database.txt')
    if not os.path.exists(gics_db_path):
         print(f"Warning: GICS database '{gics_db_path}' not found. Screener functionality might be affected.")

    command_states = load_command_states()
    COMMAND_STATES_CACHE = command_states

    display_welcome_message(command_states)
    display_utility_commands_only()
    alert_task = asyncio.create_task(alert_worker())
    persistent_alert_task = asyncio.create_task(persistent_alert_worker())
    kronos_task = asyncio.create_task(kronos_scheduler_worker(prometheus))

    func_to_command_map = {func.__name__: f"/{cmd_name}" for cmd_name, func in TOOLBOX_MAP.items()} # Use full command path
    for py_func_name, py_func_obj in AVAILABLE_PYTHON_FUNCTIONS.items():
        if py_func_name not in func_to_command_map:
             derived_cmd_name = py_func_name.replace("handle_", "").replace("_command", "").replace("_tool","")
             # Ensure func_to_command_map stores the command *with* the slash if that's how Prometheus expects it
             func_to_command_map[py_func_name] = f"/{derived_cmd_name}"


    # --- Main Input Loop ---
    while True:
        try:
            prompt_text = "[AI Chat Active] > " if AI_CONVERSATION_HISTORY else "SINGULARITY: "
            user_input_full_line = await asyncio.to_thread(input, prompt_text)
            user_input_full_line = user_input_full_line.strip()

            if not user_input_full_line:
                continue

            # --- AI Chat Session Management ---
            if user_input_full_line.lower() == "end chat":
                if AI_CONVERSATION_HISTORY:
                    print("AI chat session ended by user. History cleared.")
                    AI_CONVERSATION_HISTORY.clear()
                    CURRENT_AI_SESSION_ORIGINAL_REQUEST = None
                    AI_INTERNAL_STEP_COUNT = 0
                else:
                    print("No active AI chat session to end.")
                continue

            # --- Command Parsing ---
            command_parts = user_input_full_line.split()
            command_with_slash = command_parts[0].lower()
            command_name_no_slash = command_with_slash.lstrip('/')
            args = command_parts[1:]

            # --- Alias '/reportgeneration' to '/report' ---
            if command_with_slash == "/reportgeneration":
                print("Info: Alias '/reportgeneration' mapped to '/report' for Prometheus.")
                command_with_slash = "/report"
                command_name_no_slash = "report"

            # --- Special Commands (Not Routed Through Prometheus Toolbox) ---
            if command_with_slash == "/exit":
                print("Exiting Market Insights Center Singularity. Goodbye!")
                alert_task.cancel()
                if prometheus.correlation_task:
                     prometheus.correlation_task.cancel()
                break

            elif command_with_slash == "/prometheus":
                await prometheus.start_interactive_session()
                continue # Continue loop after shell exits

            elif command_with_slash == "/ai":
                 user_natural_prompt = " ".join(args)
                 if not user_natural_prompt:
                     print("Usage: /ai <your request or question for the AI assistant>")
                 else:
                     print("[DEBUG] Routing to explicit /ai handler...") # DEBUG
                     await increment_command_count(command_with_slash)
                     await handle_ai_prompt(
                         user_new_message=user_natural_prompt,
                         is_new_session=True,
                         original_session_request=user_natural_prompt,
                         conversation_history=AI_CONVERSATION_HISTORY,
                         gemini_model_obj=gemini_model,
                         available_functions=AVAILABLE_PYTHON_FUNCTIONS,
                         session_request_obj={'value': CURRENT_AI_SESSION_ORIGINAL_REQUEST},
                         step_count_obj={'value': AI_INTERNAL_STEP_COUNT},
                         prometheus_obj=prometheus,
                         func_to_command_map=func_to_command_map
                     )
                 continue

            elif command_with_slash == "/voice":
                 print("[DEBUG] Routing to /voice handler...") # DEBUG
                 await increment_command_count(command_with_slash)
                 await handle_voice_command(
                     conversation_history=AI_CONVERSATION_HISTORY,
                     gemini_model_obj=gemini_model,
                     available_functions=AVAILABLE_PYTHON_FUNCTIONS,
                     tts_engine_obj=tts_engine,
                     session_request_obj={'value': CURRENT_AI_SESSION_ORIGINAL_REQUEST},
                     step_count_obj={'value': AI_INTERNAL_STEP_COUNT},
                     prometheus_obj=prometheus,
                     func_to_command_map=func_to_command_map
                 )
                 continue
            
            elif command_with_slash == "/kronos":
                print("[DEBUG] Routing to /kronos handler...")
                await increment_command_count(command_with_slash) # Log the command usage
                # Call the handler directly, passing the live Prometheus instance
                await handle_kronos_command(args, prometheus_instance=prometheus)
                continue # Skip the default Prometheus routing

            # --- Command Routing Through Prometheus ---
            else:
                 # --- *** FIX: Check ONLY for '/' prefix here *** ---
                 if user_input_full_line.startswith("/"):
                     print(f"[DEBUG] Input starts with '/', routing to Prometheus: {command_with_slash}...") # DEBUG
                     # Check Usage Limit and Command State FIRST
                     if not check_usage_limit(command_with_slash):
                         continue

                     core_commands = ['help', 'exit', 'dev', 'prometheus', 'ai', 'voice'] # Updated core list
                     if not COMMAND_STATES_CACHE:
                          COMMAND_STATES_CACHE = load_command_states()
                     enabled_commands = COMMAND_STATES_CACHE.get('commands', {})

                     # Check if command is enabled
                     if command_name_no_slash not in core_commands and not enabled_commands.get(command_name_no_slash, True):
                          disabled_message_template = COMMAND_STATES_CACHE.get('disabled_command_message', "Command '/{command}' is disabled.")
                          print(disabled_message_template.format(command=command_name_no_slash))
                          continue

                     # Determine command to log (handle /assess subcommands)
                     command_to_log = command_with_slash
                     if command_with_slash == "/assess" and args:
                         sub_command = args[0].upper()
                         if sub_command in ['A', 'B', 'C', 'D', 'E']:
                             command_to_log = f"/assess {sub_command}"

                     # Increment Count
                     await increment_command_count(command_to_log)

                     # Execute via Prometheus if it's in the toolbox
                     if command_name_no_slash in prometheus.toolbox:
                         print(f"[DEBUG] Calling Prometheus execute_and_log for: {command_with_slash}...") # DEBUG
                         await prometheus.execute_and_log(command_with_slash, args, called_by_user=True)
                     else:
                          print(f"Unknown command: {command_with_slash}. Type /help for available commands.")

                 # --- Implicit AI Chat (No "/" prefix) ---
                 else:
                     print("[DEBUG] Input does not start with '/', routing to implicit AI handler...") # DEBUG
                     is_new_chat = not AI_CONVERSATION_HISTORY
                     await increment_command_count("/ai_implicit")
                     await handle_ai_prompt(
                         user_new_message=user_input_full_line,
                         is_new_session=is_new_chat,
                         original_session_request=user_input_full_line if is_new_chat else CURRENT_AI_SESSION_ORIGINAL_REQUEST,
                         conversation_history=AI_CONVERSATION_HISTORY,
                         gemini_model_obj=gemini_model,
                         available_functions=AVAILABLE_PYTHON_FUNCTIONS,
                         session_request_obj={'value': CURRENT_AI_SESSION_ORIGINAL_REQUEST},
                         step_count_obj={'value': AI_INTERNAL_STEP_COUNT},
                         prometheus_obj=prometheus,
                         func_to_command_map=func_to_command_map
                     )

        # ... (rest of the main_singularity function remains the same: exception handling, cleanup) ...
        except KeyboardInterrupt:
            print("\nExiting Market Insights Center Singularity (KeyboardInterrupt). Goodbye!")
            alert_task.cancel()
            if prometheus.correlation_task:
                 prometheus.correlation_task.cancel()
            break
        except Exception as e_main_loop:
            print(f"An unexpected error occurred in the main loop: {e_main_loop}")
            traceback.print_exc()
            if 'prometheus' in locals():
                 try:
                     await prometheus._log_command(datetime.now(), "/main_loop_error", [], {}, f"Critical Error: {e_main_loop}", success=False, duration_ms=0)
                 except: pass # Avoid errors during error logging

    # --- Cleanup after loop exit ---
    print("Shutting down background tasks...")
    if not alert_task.done():
        alert_task.cancel()
    if 'kronos_task' in locals() and not kronos_task.done(): # <<< This line is from your file
        kronos_task.cancel()                               # <<< This line is from your file
    if 'persistent_alert_task' in locals() and not persistent_alert_task.done(): # <<< ADD THIS
        persistent_alert_task.cancel()                                           # <<< ADD THIS
    if prometheus.correlation_task and not prometheus.correlation_task.done():
         prometheus.correlation_task.cancel()

    await asyncio.sleep(0.5)
    try: await alert_task
    except asyncio.CancelledError: print("Alert worker successfully shut down.")
    try:                                                        # <<< This line is from your file
        if 'kronos_task' in locals(): await kronos_task         # <<< This line is from your file
    except asyncio.CancelledError: print("Kronos scheduler successfully shut down.") # <<< This line is from your file
    try: # <<< ADD THIS BLOCK
        if 'persistent_alert_task' in locals(): await persistent_alert_task
    except asyncio.CancelledError: print("Persistent alert worker successfully shut down.") # <<< ADD THIS
    try:
         if prometheus.correlation_task: await prometheus.correlation_task
    except asyncio.CancelledError: print("Prometheus background task successfully shut down.")

    print("Application shutdown complete.")

# --- Functions for Usage Limits, Welcome Message, etc. (Keep As Is) ---
def check_usage_limit(command: str) -> bool:
    """Checks if a command is within its usage limits. Returns True if allowed, False if blocked."""
    global COMMAND_USAGE_TIMESTAMPS, COMMAND_STATES_CACHE

    if not COMMAND_STATES_CACHE: # Load if empty
        COMMAND_STATES_CACHE = load_command_states()

    limits = COMMAND_STATES_CACHE.get('usage_limits', {})
    command_key = command.lstrip('/') # Use name without slash
    if command_key not in limits:
        return True # No limit set for this command

    limit_info = limits[command_key]
    limit_count = limit_info.get('limit')
    limit_period = limit_info.get('period')

    # Validate limit info structure
    if not all([isinstance(limit_count, int), isinstance(limit_period, str)]) or limit_count <= 0:
        # Invalid limit definition, treat as no limit
        return True

    period_map = {
        'minute': timedelta(minutes=1), 'hour': timedelta(hours=1), 'day': timedelta(days=1),
        'week': timedelta(weeks=1), 'month': relativedelta(months=1) # Use relativedelta for month
    }

    if limit_period not in period_map:
        print(f"Warning: Invalid limit period '{limit_period}' defined for command '{command}'. Ignoring limit.")
        return True # Invalid period, treat as no limit

    now = datetime.now()
    cutoff_time = now - period_map[limit_period]

    # Get timestamps for this command, filtering out old ones
    command_timestamps = COMMAND_USAGE_TIMESTAMPS.get(command_key, [])
    recent_timestamps = [t for t in command_timestamps if t > cutoff_time]

    # Check if the limit has been reached
    if len(recent_timestamps) >= limit_count:
        limit_message_template = COMMAND_STATES_CACHE.get('limit_reached_message', "Limit reached for /{command}.")
        # Ensure the message template exists and format it safely
        try:
            print(limit_message_template.format(command=command_key, limit_count=limit_count, period=limit_period))
        except KeyError:
            # Fallback if the template is malformed
            print(f"Usage limit reached for /{command_key}.")
        return False # Block command

    # Record the current usage time and update the global dictionary
    recent_timestamps.append(now)
    COMMAND_USAGE_TIMESTAMPS[command_key] = recent_timestamps

    return True # Allow command


def display_welcome_message(command_states):
    """Displays the welcome message, conditionally showing animation and ASCII art."""
    print("Initializing Market Insights Center Singularity...")
    show_animation = command_states.get("startup_animation_enabled", True)

    # Full message as defined in the original file
    full_ascii_message = r"""
 _____ ______       ___      ________
|\   _ \  _   \    |\  \    |\   ____\
\ \  \\\__\ \  \   \ \  \   \ \  \___|
 \ \  \\|__| \  \   \ \  \   \ \  \
  \ \  \    \ \  \ __\ \  \ __\ \  \____
   \ \__\    \ \__\\__\ \__\\__\ \_______\
    \|__|     \|__\|__|\|__\|__|\|_______|

Stage History:
First Stage: Pilot - 07/05/2025 to 29/05/25
Second Stage: Eidos - 01/06/25 to 29/07/25
Third Stage: Cognis - 02/08/25 to 22/09/25
Fourth Stage: Nexus - 27/09/25 to 29/10/25
Fifth Stage: Prometheus - 30/10/25 to Present

Presenting:
__| |___________________________________________________________________________________________________________________________________| |__
__   ___________________________________________________________________________________________________________________________________   __
  | |                                                                                                                                   | |  
  | |         _          _           _            _   _         _          _            _       _    _      _                  _        | |  
  | |        /\ \       /\ \        /\ \         /\_\/\_\ _    /\ \       /\ \         / /\    / /\ /\ \   /\_\               / /\      | |  
  | |       /  \ \     /  \ \      /  \ \       / / / / //\_\ /  \ \      \_\ \       / / /   / / //  \ \ / / /         _    / /  \     | |  
  | |      / /\ \ \   / /\ \ \    / /\ \ \     /\ \/ \ \/ / // /\ \ \     /\__ \     / /_/   / / // /\ \ \\ \ \__      /\_\ / / /\ \__  | |  
  | |     / / /\ \_\ / / /\ \_\  / / /\ \ \   /  \____\__/ // / /\ \_\   / /_ \ \   / /\ \__/ / // / /\ \_\\ \___\    / / // / /\ \___\ | |  
  | |    / / /_/ / // / /_/ / / / / /  \ \_\ / /\/________// /_/_ \/_/  / / /\ \ \ / /\ \___\/ // /_/_ \/_/ \__  /   / / / \ \ \ \/___/ | |  
  | |   / / /__\/ // / /__\/ / / / /   / / // / /\/_// / // /____/\    / / /  \/_// / /\/___/ // /____/\    / / /   / / /   \ \ \       | |  
  | |  / / /_____// / /_____/ / / /   / / // / /    / / // /\____\/   / / /      / / /   / / // /\____\/   / / /   / / /_    \ \ \      | |  
  | | / / /      / / /\ \ \  / / /___/ / // / /    / / // / /______  / / /      / / /   / / // / /______  / / /___/ / //_/\__/ / /      | |  
  | |/ / /      / / /  \ \ \/ / /____\/ / \/_/    / / // / /_______\/_/ /      / / /   / / // / /_______\/ / /____\/ / \ \/___/ /       | |  
  | |\/_/       \/_/    \_\/\/_________/          \/_/ \/__________/\_\/       \/_/    \/_/ \/__________/\/_________/   \_____\/        | |  
  | |                                                                                                                                   | |  
__| |___________________________________________________________________________________________________________________________________| |__
__   ___________________________________________________________________________________________________________________________________   __
  | |                                                                                                                                   | |  
"""

    # Find the split point
    split_marker = "Presenting:"
    split_index = full_ascii_message.find(split_marker)

    static_ascii_part = full_ascii_message[:split_index]
    animated_ascii_part = full_ascii_message[split_index:]

    if show_animation:
        animation_chars = ["|", "/", "-", "\\"]
        for _ in range(10): # Shortened animation
            for char in animation_chars:
                print(f"\rLoading... {char}", end="", flush=True)
                py_time.sleep(0.1) # Faster sleep
        print("\rLoading... Done!          ")
        print("\n" + "="*45)
        target_message = "Singularity, Awake! Open... Your... Eyes..."
        char_set = string.ascii_letters + string.digits + "!@#$%^&*()_+-=[]{}|;:,.<>/?~` "
        revealed_message = ""
        for char in target_message:
            if char == ' ':
                revealed_message += ' '
                print(f"\r{revealed_message}", end="", flush=True)
                continue
            for _ in range(2): # Fewer random chars
                random_char = random.choice(char_set)
                print(f"\r{revealed_message}{random_char}", end="", flush=True)
                py_time.sleep(0.05) # Faster sleep
            revealed_message += char
            print(f"\r{revealed_message}", end="", flush=True)
            py_time.sleep(0.1) # Faster sleep
        print()
        print("="*45)

        # --- MODIFIED ANIMATION LOGIC ---
        print("\nLaunching Singularity:")
        # 1. Print the static part (Singularity logo + History) instantly
        print(static_ascii_part, end="", flush=True)

        # 2. Print the animated part (Prometheus) with a fast typing effect
        for char in animated_ascii_part:
            print(char, end="", flush=True)
            # Adjust sleep time for desired speed. 0.001 is very fast.
            py_time.sleep(0.001) 

        print("\n\n") # Add newlines at the end
        # --- END OF MODIFIED LOGIC ---

    else: # No animation
        print("Loading... Done!")
        print("\n" + "="*45)
        print("Singularity, Awake! Open... Your... Eyes...")
        print("="*45)
        print("\nLaunching Singularity:")
        # Print the *entire* message at once
        print(full_ascii_message)
        print("\n\n")

def display_utility_commands_only(): # Keep as is
    """Displays only the essential utility commands at startup."""
    print("\nUtility Commands")
    print("-------------------")
    print("/help - Display the full list of commands.")
    print("/exit - Close the Market Insights Center Singularity.")
    print("/prometheus - Enter the Prometheus meta-AI shell.") # Added Prometheus command
    print("-------------------\n")

def ensure_portfolio_output_dir(): # Keep as is
    """Ensures the directory for saving portfolio outputs exists."""
    if not os.path.exists(PORTFOLIO_OUTPUT_DIR):
        try:
            os.makedirs(PORTFOLIO_OUTPUT_DIR)
        except OSError as e:
            print(f"❌ Error creating directory {PORTFOLIO_OUTPUT_DIR}: {e}. Please create it manually.")
ensure_portfolio_output_dir() # Ensure it runs at startup

def load_user_preferences() -> Dict[str, Any]: # Keep as is
    """Loads user preferences from the JSON file."""
    if not os.path.exists(USER_PREFERENCES_FILE):
        return {} # Return empty dict if file doesn't exist
    try:
        with open(USER_PREFERENCES_FILE, 'r', encoding='utf-8') as f:
            # Handle empty file case
            content = f.read()
            if not content:
                return {}
            return json.loads(content)
    except (json.JSONDecodeError, IOError) as e:
        print(f"⚠️ Warning: Could not read user preferences file '{USER_PREFERENCES_FILE}': {e}")
        return {} # Return empty dict on error


# --- AI Function Mapping & Tool Definitions ---
# These are largely handled within initialize_ai_components now,
# but keep the AVAILABLE_PYTHON_FUNCTIONS mapping if used directly by handle_ai_prompt
# The AVAILABLE_PYTHON_FUNCTIONS dict is now populated inside initialize_ai_components

# --- Script Execution ---
if __name__ == "__main__":
    # --- One-time Setup ---
    # Global model initialization is handled in initialize_ai_components

    # Configure risk_logger (should happen once at module level, already done above)

    # Initialize AI Conversation History etc. (already defined globally)
    if 'AI_CONVERSATION_HISTORY' not in globals(): AI_CONVERSATION_HISTORY = []
    if 'CURRENT_AI_SESSION_ORIGINAL_REQUEST' not in globals(): CURRENT_AI_SESSION_ORIGINAL_REQUEST = None
    if 'AI_INTERNAL_STEP_COUNT' not in globals(): AI_INTERNAL_STEP_COUNT = 0


    try:
        # Run the main async function
        asyncio.run(main_singularity())

    except KeyboardInterrupt:
        print("\nApplication terminated by user (main execution scope).")
        if 'risk_logger' in globals() and risk_logger: risk_logger.info("Application terminated by user from main execution scope.")
    except Exception as e_run_critical:
        print(f"Critical error running application: {e_run_critical}")
        traceback.print_exc()
        # Log critical errors using the risk logger if available
        if 'risk_logger' in globals() and risk_logger:
            risk_logger.critical(f"Application crashed with an unexpected error: {e_run_critical}", exc_info=True)
        else: # Fallback basic logging if risk_logger itself failed
            try:
                logging.basicConfig(filename=RISK_LOG_FILE, level=logging.ERROR) # Basic config
                logging.critical(f"Application CRASH (logger might have failed): {e_run_critical}", exc_info=True)
            except NameError: # If RISK_LOG_FILE wasn't defined
                logging.basicConfig(filename='critical_error.log', level=logging.ERROR)
                logging.critical(f"Application CRASH (logger failed, no config): {e_run_critical}", exc_info=True)