# --- Imports for backtest_command ---
import asyncio
import uuid
from typing import List, Dict, Any, Optional

import yfinance as yf
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from tabulate import tabulate
import json
import logging

prometheus_logger = logging.getLogger('PROMETHEUS_CORE')

# --- Helper Functions (from strategies_command) ---

def calculate_adx(data: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculates the Average Directional Index (ADX)."""
    df = data.copy()
    alpha = 1 / period
    df['H-L'] = df['High'] - df['Low']
    df['H-PC'] = abs(df['High'] - df['Close'].shift(1))
    df['L-PC'] = abs(df['Low'] - df['Close'].shift(1))
    df['TR'] = df[['H-L', 'H-PC', 'L-PC']].max(axis=1)
    df['+DM'] = np.where((df['High'].diff() > df['Low'].diff()) & (df['High'].diff() > 0), df['High'].diff(), 0)
    df['-DM'] = np.where((df['Low'].diff() > df['High'].diff()) & (df['Low'].diff() > 0), df['Low'].diff(), 0)
    # Ensure ATR calculation handles potential division by zero if TR is zero
    df['ATR'] = df['TR'].ewm(alpha=alpha, adjust=False).mean().replace(0, 1e-9) # Replace 0 ATR with small value
    df['+DI'] = (df['+DM'].ewm(alpha=alpha, adjust=False).mean() / df['ATR']) * 100
    df['-DI'] = (df['-DM'].ewm(alpha=alpha, adjust=False).mean() / df['ATR']) * 100
    # Ensure DX calculation handles potential division by zero
    di_sum = df['+DI'] + df['-DI']
    df['DX'] = (abs(df['+DI'] - df['-DI']) / di_sum.replace(0, 1e-9) * 100).fillna(0) # Replace 0 sum with small value
    df['ADX'] = df['DX'].ewm(alpha=alpha, adjust=False).mean()
    return df['ADX']

def calculate_rsi(data: pd.DataFrame, period: int = 14) -> pd.Series:
    """Calculates the Relative Strength Index (RSI)."""
    delta = data['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    # Handle potential division by zero if loss is zero
    rs = gain / loss.replace(0, 1e-9) # Replace 0 loss with small value
    rs.replace([np.inf, -np.inf], 0, inplace=True) # Handle potential inf values after division
    return 100 - (100 / (1 + rs))

# --- Core Backtest Logic ---

async def run_strategy_backtest(
    ticker: str, 
    strategy: str, 
    params: Dict[str, Any], 
    is_cli_call: bool = True,
    period: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Core logic for running a strategy backtest. Fetches data, implements strategy
    logic, simulates trades, calculates metrics, and optionally plots results.
    Returns a dictionary with results or an error dictionary on failure.
    """
    
    fetch_kwargs = {"interval": "1d", "auto_adjust": False, "progress": False}
    if start and end:
        fetch_kwargs["start"] = start
        fetch_kwargs["end"] = end
        period_display = f"{start} to {end}"
    elif period:
        fetch_kwargs["period"] = period
        period_display = period
    else:
        fetch_kwargs["period"] = "1y" # Default fallback
        period_display = "1y (default)"

    if is_cli_call: print(f"   -> Fetching historical data ({period_display})...")
    
    data_download = await asyncio.to_thread(
        yf.download, ticker, **fetch_kwargs
    )

    if data_download.empty:
        err_msg = f"Error: No data downloaded for {ticker}. The ticker may be invalid or delisted."
        if is_cli_call: print(f"❌ {err_msg}")
        return {"status": "error", "message": err_msg}

    hist_data = data_download.copy()
    if isinstance(hist_data.columns, pd.MultiIndex):
        hist_data.columns = hist_data.columns.get_level_values(0)

    price_col = 'Adj Close'
    if price_col not in hist_data.columns or hist_data[price_col].isnull().all():
        price_col = 'Close'
        if price_col not in hist_data.columns or hist_data[price_col].isnull().all():
            err_msg = f"Error: Required 'Adj Close' or 'Close' column not found or is all NaN."
            if is_cli_call: print(f"❌ {err_msg}")
            return {"status": "error", "message": err_msg}
        elif is_cli_call:
            print(f"   -> Warning: Using 'Close' prices as 'Adj Close' was unavailable.")

    if is_cli_call: print(f"   -> Applying '{strategy}' logic...")
    hist_data['signal'] = 0 

    # --- Strategy Signal Generation (REMAINS THE SAME) ---
    try:
        if strategy == 'ma_crossover':
            short_ma = params['short_ma']
            long_ma = params['long_ma']
            hist_data[f'SMA{short_ma}'] = hist_data[price_col].rolling(window=short_ma).mean()
            hist_data[f'SMA{long_ma}'] = hist_data[price_col].rolling(window=long_ma).mean()
            hist_data['position'] = np.where(hist_data[f'SMA{short_ma}'] > hist_data[f'SMA{long_ma}'], 1, -1)
            hist_data['signal'] = hist_data['position'].diff().fillna(0)

        elif strategy == 'rsi':
            rsi_period = params['rsi_period']
            buy_level = params['rsi_buy']
            sell_level = params['rsi_sell']
            hist_data['RSI'] = calculate_rsi(hist_data, period=rsi_period)
            buy_cond = (hist_data['RSI'].shift(1) >= buy_level) & (hist_data['RSI'] < buy_level)
            sell_cond = (hist_data['RSI'].shift(1) <= sell_level) & (hist_data['RSI'] > sell_level)
            hist_data.loc[buy_cond, 'signal'] = 1 # Buy Signal
            hist_data.loc[sell_cond, 'signal'] = -1 # Sell Signal

        elif strategy == 'busd':
            if 'Close' not in hist_data.columns or 'Open' not in hist_data.columns:
                 raise KeyError("BUSD requires 'Open' and 'Close' columns.")
            hist_data.loc[hist_data['Close'] > hist_data['Open'], 'signal'] = 1
            hist_data.loc[hist_data['Close'] < hist_data['Open'], 'signal'] = -1

        elif strategy == 'trend_following':
            ema_short = params['ema_short']
            ema_long = params['ema_long']
            adx_thresh = params['adx_thresh']
            hist_data[f'EMA_{ema_short}'] = hist_data[price_col].ewm(span=ema_short, adjust=False).mean()
            hist_data[f'EMA_{ema_long}'] = hist_data[price_col].ewm(span=ema_long, adjust=False).mean()
            hist_data['ADX'] = calculate_adx(hist_data)
            long_cond = (hist_data[f'EMA_{ema_short}'] > hist_data[f'EMA_{ema_long}']) & (hist_data['ADX'] > adx_thresh)
            short_cond = (hist_data[f'EMA_{ema_short}'] < hist_data[f'EMA_{ema_long}']) & (hist_data['ADX'] > adx_thresh)
            hist_data['position'] = np.select([long_cond, short_cond], [1, -1], default=0)
            hist_data['signal'] = hist_data['position'].diff().fillna(0)

        elif strategy == 'mean_reversion':
            bb_window = params['bb_window']
            bb_std = params['bb_std']
            rsi_period = params['rsi_period']
            rsi_buy = params['rsi_buy']
            rsi_sell = params['rsi_sell']
            hist_data['SMA'] = hist_data[price_col].rolling(window=bb_window).mean()
            hist_data['STD'] = hist_data[price_col].rolling(window=bb_window).std()
            hist_data['Upper_Band'] = hist_data['SMA'] + (hist_data['STD'] * bb_std)
            hist_data['Lower_Band'] = hist_data['SMA'] - (hist_data['STD'] * bb_std)
            hist_data['RSI'] = calculate_rsi(hist_data, period=rsi_period)
            buy_cond = (hist_data[price_col] <= hist_data['Lower_Band']) & (hist_data['RSI'] < rsi_buy)
            sell_cond = (hist_data[price_col] >= hist_data['Upper_Band']) & (hist_data['RSI'] > rsi_sell)
            hist_data.loc[buy_cond, 'signal'] = 1 
            hist_data.loc[sell_cond, 'signal'] = -1

        elif strategy == 'volatility_breakout':
            donchian_window = params['donchian_window']
            hist_data['Upper_Channel'] = hist_data['High'].rolling(window=donchian_window).max().shift(1)
            hist_data['Lower_Channel'] = hist_data['Low'].rolling(window=donchian_window).min().shift(1)
            buy_cond = hist_data['Close'] > hist_data['Upper_Channel']
            sell_cond = hist_data['Close'] < hist_data['Lower_Channel']
            hist_data.loc[buy_cond, 'signal'] = 1 
            hist_data.loc[sell_cond, 'signal'] = -1

    except KeyError as e:
        err_msg = f"Error applying strategy logic: Missing expected column - {e}."
        if is_cli_call: print(f"❌ {err_msg}")
        return {"status": "error", "message": err_msg}
    except Exception as e:
        err_msg = f"Unexpected error applying strategy logic: {e}"
        if is_cli_call: print(f"❌ {err_msg}")
        return {"status": "error", "message": err_msg}

    first_valid_index = hist_data.dropna(subset=['signal']).index.min()
    hist_data = hist_data.loc[first_valid_index:]
    if hist_data.empty:
        err_msg = "Error: No valid data remaining after calculating indicators/signals."
        if is_cli_call: print(f"❌ {err_msg}")
        return {"status": "error", "message": err_msg}

    if is_cli_call: print("   -> Simulating trades and calculating equity curves...")
    
    initial_capital = 10000.0
    cash = initial_capital
    shares = 0.0
    hist_data['strategy_equity'] = initial_capital
    
    # --- START OF MODIFICATION: Debug log removed, calculation remains ---
    try:
        hist_data['hold_equity'] = initial_capital * (hist_data[price_col] / hist_data[price_col].iloc[0])
        hold_return_pct = (hist_data['hold_equity'].iloc[-1] / initial_capital - 1) * 100
    except Exception as e:
        if not is_cli_call:
            prometheus_logger.error(f"  [Backtest] CRITICAL: Failed to calculate B&H return: {e}")
        hist_data['hold_equity'] = initial_capital
        hold_return_pct = 0.0
    # --- END OF MODIFICATION ---

    for i in range(len(hist_data)):
        current_price = hist_data[price_col].iloc[i]
        signal_value = hist_data['signal'].iloc[i]
        
        if pd.isna(current_price) or current_price <= 0:
            hist_data.iloc[i, hist_data.columns.get_loc('strategy_equity')] = hist_data.iloc[i-1, hist_data.columns.get_loc('strategy_equity')] if i > 0 else initial_capital
            continue 

        current_equity = cash + (shares * current_price)
        hist_data.iloc[i, hist_data.columns.get_loc('strategy_equity')] = current_equity

        if signal_value > 0: # Buy Signal
            if shares < 0:
                if is_cli_call: prometheus_logger.debug(f"[{hist_data.index[i].date()}] Closing short: {shares} shares @ ${current_price:.2f}")
                cash += shares * current_price
                shares = 0
            if shares == 0:
                shares_to_buy = cash / current_price
                shares += shares_to_buy
                cash -= shares_to_buy * current_price
                if is_cli_call: prometheus_logger.debug(f"[{hist_data.index[i].date()}] Opening long: {shares_to_buy:.2f} shares @ ${current_price:.2f}")

        elif signal_value < 0: # Sell Signal
            if shares > 0:
                if is_cli_call: prometheus_logger.debug(f"[{hist_data.index[i].date()}] Closing long: {shares} shares @ ${current_price:.2f}")
                cash += shares * current_price
                shares = 0
            
            if strategy in ['ma_crossover', 'trend_following', 'rsi', 'mean_reversion', 'volatility_breakout']:
                if shares == 0:
                    shares_to_short = cash / current_price
                    shares -= shares_to_short
                    cash += shares_to_short * current_price
                    if is_cli_call: prometheus_logger.debug(f"[{hist_data.index[i].date()}] Opening short: {shares_to_short:.2f} shares @ ${current_price:.2f}")

    strategy_return_pct = (hist_data['strategy_equity'].iloc[-1] / initial_capital - 1) * 100
    strategy_returns = hist_data['strategy_equity'].pct_change().dropna()
    sharpe_ratio = (strategy_returns.mean() / strategy_returns.std()) * np.sqrt(252) if strategy_returns.std() != 0 else 0.0
    trade_count = int(np.sum(hist_data['signal'] != 0))

    if is_cli_call:
        print("\n--- Backtest Results ---")
        results_table = [
            ["Strategy Return", f"{strategy_return_pct:+.2f}%"],
            ["Buy & Hold Return", f"{hold_return_pct:+.2f}%"],
            ["Sharpe Ratio (Annualized)", f"{sharpe_ratio:.3f}"],
            ["Total Trade Signals", f"{trade_count}"]
        ]
        print(tabulate(results_table, tablefmt="fancy_grid"))
        print("------------------------")

        print("   -> Generating results plot...")
        plot_backtest_results(hist_data, ticker, strategy)

    return {
        "status": "success",
        "ticker": ticker,
        "strategy": strategy,
        "period": period_display,
        "parameters": params,
        "total_return_pct": strategy_return_pct,
        "buy_hold_return_pct": hold_return_pct, 
        "sharpe_ratio": sharpe_ratio,
        "trade_count": trade_count
    }

# --- Plotting Function ---
def plot_backtest_results(data: pd.DataFrame, ticker: str, strategy: str):
    """
    Generates a two-panel chart visualizing the backtest results.
    """
    try:
        plt.style.use('dark_background')
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 10), sharex=True, gridspec_kw={'height_ratios': [2, 1]})

        # Use the same price column as the backtest ('Adj Close' or 'Close')
        price_col = 'Adj Close' if 'Adj Close' in data.columns else 'Close'

        ax1.plot(data.index, data[price_col], label=f'{ticker} Price', color='grey', linewidth=1.5)

        buy_signals = data[data['signal'] > 0]
        ax1.plot(buy_signals.index, buy_signals[price_col], '^', markersize=10, color='lime', label='Buy Signal', markeredgecolor='black')

        sell_signals = data[data['signal'] < 0]
        ax1.plot(sell_signals.index, sell_signals[price_col], 'v', markersize=10, color='red', label='Sell Signal', markeredgecolor='black')

        ax1.set_title(f"{ticker} Backtest: '{strategy.replace('_', ' ').title()}' Strategy", color='white', fontsize=16)
        ax1.set_ylabel(f"{price_col.replace('_',' ')} Price (USD)", color='white') # Dynamic label
        ax1.legend()
        ax1.grid(True, color='dimgray', linestyle='--', linewidth=0.5, alpha=0.5)
        # Set y-axis limits for price slightly padded
        min_price, max_price = data[price_col].min(), data[price_col].max()
        ax1.set_ylim(min_price * 0.95, max_price * 1.05)


        ax2.plot(data.index, data['strategy_equity'], label='Strategy Equity', color='cyan', linewidth=2)
        ax2.plot(data.index, data['hold_equity'], label='Buy & Hold Equity', color='orange', linestyle='--')

        ax2.set_xlabel("Date", color='white')
        ax2.set_ylabel("Portfolio Value ($)", color='white')
        ax2.legend()
        ax2.grid(True, color='dimgray', linestyle='--', linewidth=0.5, alpha=0.5)
        # Set y-axis limits for equity slightly padded
        min_equity = min(data['strategy_equity'].min(), data['hold_equity'].min())
        max_equity = max(data['strategy_equity'].max(), data['hold_equity'].max())
        ax2.set_ylim(min_equity * 0.95, max_equity * 1.05)

        fig.tight_layout()
        filename = f"backtest_{ticker}_{strategy}_{uuid.uuid4().hex[:6]}.png"
        plt.savefig(filename, facecolor='black', edgecolor='black', dpi=300)
        plt.close(fig)
        print(f"📂 Backtest results chart saved as: {filename}")

    except Exception as e:
        print(f"❌ Error plotting backtest results: {e}")
        # Ensure figure is closed even if plotting fails
        if 'fig' in locals() and plt.fignum_exists(fig.number):
            plt.close(fig)

# --- Main Command Handler ---
async def handle_backtest_command(args: List[str], ai_params: Optional[Dict] = None, is_called_by_ai: bool = False):
    """
    Handles the /backtest command. Returns results dict for logging/AI, prints for CLI.
    Now accepts parameters as a single JSON string for robustness.
    """
    prometheus_logger.debug(f"handle_backtest_command received: args={args}, ai_params={ai_params}, is_called_by_ai={is_called_by_ai}")

    if ai_params:
        err_msg = "AI natural language calls to /backtest are not supported."
        prometheus_logger.warning(f"Backtest rejected an ai_params call: {ai_params}")
        return {"status": "error", "message": err_msg}

    if not args or len(args) < 3:
        err_msg = "Usage: /backtest <TICKER> <strategy> <period_or_daterange> [params... |OR| param_json_string]"
        prometheus_logger.warning(f"Backtest called with insufficient args: {args}")
        
        is_cli_call_for_help = not is_called_by_ai
        if is_cli_call_for_help:
            print(err_msg)
            print("   <period_or_daterange>: '1y', '6mo', etc. OR '{\"start\":\"YYYY-MM-DD\",\"end\":\"YYYY-MM-DD\"}'")
            print("\n--- Available Strategies & Parameters ---")
            print("  ma_crossover [short_win (def:50)] [long_win (def:200)]")
            print("  rsi [period (def:14)] [buy_lvl (def:30)] [sell_lvl (def:70)]")
            # ... (rest of help text) ...
        
        return {"status": "error", "message": err_msg}

    is_cli_call = not is_called_by_ai
    
    if is_cli_call:
        print("\n--- Trading Strategy Backtest Engine ---")
    else:
        prometheus_logger.debug("Backtest call identified as internal (GA). Suppressing console prints.")

    ticker = args[0].upper()
    strategy = args[1].lower()
    
    period_or_dates_arg = args[2]
    period_to_run: Optional[str] = None
    start_date_to_run: Optional[str] = None
    end_date_to_run: Optional[str] = None
    
    try:
        if period_or_dates_arg.startswith('{'):
            date_dict = json.loads(period_or_dates_arg)
            start_date_to_run = date_dict.get('start')
            end_date_to_run = date_dict.get('end')
            if not start_date_to_run or not end_date_to_run:
                raise ValueError("JSON must contain 'start' and 'end' keys.")
        else:
            period_to_run = period_or_dates_arg.lower()
    except (json.JSONDecodeError, ValueError) as e:
        err_msg = f"❌ Error: Invalid period/date range argument '{period_or_dates_arg}'. {e}"
        if is_cli_call: print(err_msg)
        return {"status": "error", "message": err_msg}

    strategy_params = {}
    valid_strategies = [
        'ma_crossover', 'rsi', 'busd', 'trend_following',
        'mean_reversion', 'volatility_breakout'
    ]
    if strategy not in valid_strategies:
        err_msg = f"❌ Error: Invalid strategy '{strategy}'. Choose from: {', '.join(valid_strategies)}"
        if is_cli_call: print(err_msg)
        return {"status": "error", "message": err_msg}

    param_args = args[3:] # This is now either [p1, p2, p3] OR [json_string]
    
    try:
        # --- START OF KEY FIX ---
        # Check if parameters are passed as a single JSON string (from GA)
        if len(param_args) == 1 and param_args[0].startswith('{'):
            prometheus_logger.debug("Parsing params from JSON string (GA call)")
            strategy_params = json.loads(param_args[0])
            # Ensure types are correct
            if strategy == 'rsi':
                strategy_params['rsi_period'] = int(strategy_params['rsi_period'])
                strategy_params['rsi_buy'] = int(strategy_params['rsi_buy'])
                strategy_params['rsi_sell'] = int(strategy_params['rsi_sell'])
            elif strategy == 'ma_crossover':
                strategy_params['short_ma'] = int(strategy_params['short_ma'])
                strategy_params['long_ma'] = int(strategy_params['long_ma'])
            # (Add type conversions for other strategies as needed)
            
        # --- Fallback to old positional parsing (for CLI user) ---
        elif strategy == 'ma_crossover':
            strategy_params['short_ma'] = int(param_args[0]) if len(param_args) > 0 else 50
            strategy_params['long_ma'] = int(param_args[1]) if len(param_args) > 1 else 200
        elif strategy == 'rsi':
            strategy_params['rsi_period'] = int(param_args[0]) if len(param_args) > 0 else 14
            strategy_params['rsi_buy'] = int(param_args[1]) if len(param_args) > 1 else 30
            strategy_params['rsi_sell'] = int(param_args[2]) if len(param_args) > 2 else 70
        elif strategy == 'busd':
            pass # No parameters
        # (Add other strategies as needed)
        
        # --- END OF KEY FIX ---
            
        # (Parameter validation remains the same)
        if 'short_ma' in strategy_params and strategy_params['short_ma'] >= strategy_params.get('long_ma', 200):
            raise ValueError("Short MA window must be less than Long MA window.")
        if 'rsi_buy' in strategy_params and strategy_params['rsi_buy'] >= strategy_params.get('rsi_sell', 70):
            raise ValueError("RSI Buy Level must be less than Sell Level.")

    except (json.JSONDecodeError, ValueError, IndexError, TypeError) as e:
        err_msg = f"⚠️ Warning: Invalid or missing parameters: {e}. Using default values."
        if is_cli_call: print(err_msg)
        prometheus_logger.warning(f"Backtest param error: {e}. Using defaults for {strategy}.")
        # (Default parameter assignment logic remains identical)
        if strategy == 'ma_crossover': strategy_params = {'short_ma': 50, 'long_ma': 200}
        elif strategy == 'rsi': strategy_params = {'rsi_period': 14, 'rsi_buy': 30, 'rsi_sell': 70}
        # ... (other defaults) ...

    period_display_str = period_to_run if period_to_run else f"{start_date_to_run} to {end_date_to_run}"
    if is_cli_call:
        print(f"-> Starting backtest for {ticker} using '{strategy}' over {period_display_str}...")
        print(f"   -> Parameters: {strategy_params if strategy_params else 'Defaults'}")

    backtest_results = await run_strategy_backtest(
        ticker, strategy, strategy_params, 
        is_cli_call=is_cli_call,
        period=period_to_run,
        start=start_date_to_run,
        end=end_date_to_run
    )

    return backtest_results