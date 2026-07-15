# --- quickscore_command.py ---
# Standalone module for the /quickscore command.

import yfinance as yf
import pandas as pd
import asyncio
import uuid
import matplotlib
matplotlib.use('Agg') # Set backend for non-GUI environments
import matplotlib.pyplot as plt
import numpy as np
from tabulate import tabulate
from typing import Optional, List, Dict

# --- Dependencies for this command ---
YFINANCE_API_SEMAPHORE = asyncio.Semaphore(8)

async def calculate_ema_invest(ticker: str, ema_interval: int, is_called_by_ai: bool = False) -> tuple[Optional[float], Optional[float]]:
    """Calculates EMA-based investment score for a ticker."""
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

def plot_ticker_graph(ticker: str, ema_interval: int, is_called_by_ai: bool = False) -> Optional[str]:
    """Generates and saves a price/EMA graph for a ticker."""
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
        # --- MODIFICATION: Removed emoji ---
        if not is_called_by_ai: print(f"Graph saved: {filename}")
        return filename
    except Exception as e:
        # --- MODIFICATION: Removed emoji ---
        if not is_called_by_ai: print(f"[Error] plotting graph for {ticker}: {e}")
        if 'fig' in locals() and plt.fignum_exists(fig.number): plt.close(fig)
        return None

# --- Main Command Handler ---
async def handle_quickscore_command(args: List[str], ai_params: Optional[Dict]=None, is_called_by_ai: bool = False):
    """Handles the /quickscore command for CLI and AI."""
    if not is_called_by_ai: print("\n--- /quickscore Command ---")
    ticker_qs = None
    if ai_params: ticker_qs = ai_params.get("ticker")
    elif args: ticker_qs = args[0].upper()

    if not ticker_qs:
        msg = "Usage: /quickscore <ticker> or AI must provide ticker."
        if not is_called_by_ai: print(msg)
        return f"Error: {msg}" if is_called_by_ai else None

    if not is_called_by_ai: print(f"Processing /quickscore for {ticker_qs}...")
    scores_qs, graphs_qs_files, live_price_qs_display = {}, [], "N/A"
    sensitivity_map = {1: 'Weekly (5Y)', 2: 'Daily (1Y)', 3: 'Hourly (6M)'}

    for sens_key, sens_name in sensitivity_map.items():
        live_p, ema_inv = await calculate_ema_invest(ticker_qs, sens_key, is_called_by_ai=is_called_by_ai)
        scores_qs[sens_key] = f"{ema_inv:.2f}%" if ema_inv is not None else "N/A"
        if live_p is not None and sens_key == 2: live_price_qs_display = f"${live_p:.2f}"
        
        graph_file = await asyncio.to_thread(plot_ticker_graph, ticker_qs, sens_key, is_called_by_ai=is_called_by_ai)
        graphs_qs_files.append(f"{sens_name}: {graph_file if graph_file else 'Failed'}")

    if not is_called_by_ai: # Print results for CLI
        print("\n--- /quickscore Results ---")
        print(f"Ticker: {ticker_qs}\nLive Price (Daily): {live_price_qs_display}\nInvest Scores:")
        for sk, sn in sensitivity_map.items(): print(f"  {sn}: {scores_qs.get(sk, 'N/A')}")
        print("\nGenerated Graphs:"); [print(f"  {g}") for g in graphs_qs_files]
        print("\n/quickscore analysis complete.")

    summary = f"Quickscore for {ticker_qs}: Price {live_price_qs_display}. Scores: "
    summary += ", ".join([f"{sensitivity_map[sk]} {scores_qs.get(sk,'N/A')}" for sk in sensitivity_map]) + ". "
    summary += "Graphs: " + ", ".join([g for g in graphs_qs_files if "Failed" not in g]) + "."
    
    # --- MODIFICATION: Return dict for AI, str for logging summary ---
    # The workflow executor in prometheus_core.py expects a dictionary
    # for /quickscore, but this function was only returning a string.
    # The executor's summarization logic will handle this dict.
    if is_called_by_ai:
        return {
            "status": "success",
            "ticker": ticker_qs,
            "live_price": live_price_qs_display,
            "scores": scores_qs,
            "graphs": graphs_qs_files,
            "summary": summary
        }
    
    # Return the string summary for logging if called by user
    return summary
