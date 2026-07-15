# --- Imports for mlforecast_command ---
import asyncio
import uuid
import traceback
from datetime import datetime, timedelta
from typing import List, Dict, Any

import yfinance as yf
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from tabulate import tabulate

# --- Helper Function 1: Technical Indicators (from Singularity) ---
def calculate_technical_indicators(data: pd.DataFrame) -> pd.DataFrame:
    """
    Calculates a set of technical indicators and adds them to the DataFrame.
    This version is from the main Singularity 19.09.25 file.
    """
    try:
        if 'Close' not in data.columns:
            raise KeyError("Required 'Close' column not found.")
        
        # 1. 14-day RSI (Relative Strength Index)
        delta = data['Close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        with np.errstate(divide='ignore', invalid='ignore'):
            rs = gain / loss
        rs.replace([np.inf], 999999, inplace=True)
        rs.fillna(0, inplace=True)
        data['RSI'] = 100 - (100 / (1 + rs))

        # 2. MACD value and MACD signal line
        exp1 = data['Close'].ewm(span=12, adjust=False).mean()
        exp2 = data['Close'].ewm(span=26, adjust=False).mean()
        data['MACD'] = exp1 - exp2
        data['MACD_Signal'] = data['MACD'].ewm(span=9, adjust=False).mean()

        # 3. The percentage difference between the 50-day and 200-day SMAs
        sma50 = data['Close'].rolling(window=50).mean()
        sma200 = data['Close'].rolling(window=200).mean()
        data['SMA_Diff'] = ((sma50 - sma200) / sma200) * 100

        # 4. 30-day historical volatility
        data['Volatility'] = data['Close'].pct_change().rolling(window=30).std() * np.sqrt(252)
        
        return data
    except Exception:
        # Return original dataframe if indicators fail, allowing downstream to handle missing columns
        return data

# --- Helper Function 2: Graph Plotting (from Singularity) ---
def plot_advanced_forecast_graph(ticker, historical_data, forecast_points, weekly_forecast_points=None):
    """
    Generates and saves a graph showing historical prices, a predictive weekly
    forecast path, and the key forecast points that anchor the path.
    This version is from the main Singularity 19.09.25 file.
    """
    try:
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(14, 8))

        past_year_data = historical_data.iloc[-252:]
        ax.plot(past_year_data.index, past_year_data['Close'], 
               label='Past Year Price', color='grey', linewidth=1.5)
        
        last_date = past_year_data.index[-1]
        last_price = past_year_data['Close'].iloc[-1]
        
        # Plot the adjusted weekly forecast path
        if weekly_forecast_points:
            weekly_dates = [p['date'] for p in weekly_forecast_points]
            weekly_prices = [p['price'] for p in weekly_forecast_points]
            complete_weekly_dates = [last_date] + weekly_dates
            complete_weekly_prices = [last_price] + weekly_prices
            ax.plot(complete_weekly_dates, complete_weekly_prices, 
                   label='Adjusted Weekly Forecast Path', linestyle=':', color='yellow', 
                   linewidth=1.5, alpha=0.9)

        # Plot the main, annotated forecast points
        if forecast_points:
            forecast_points.sort(key=lambda p: p['date'])
            forecast_dates = [fp['date'] for fp in forecast_points]
            forecast_prices = [fp['price'] for fp in forecast_points]
            
            ax.plot(forecast_dates, forecast_prices, 
                   label='Key Forecasts (Anchors)', linestyle='None', color='cyan', 
                   marker='o', markersize=8, markerfacecolor='cyan')

            for fp in forecast_points:
                ax.annotate(f"${fp['price']:.2f}",
                            xy=(fp['date'], fp['price']),
                            xytext=(5, -15), textcoords='offset points',
                            color='cyan', fontsize=10, fontweight='bold',
                            bbox=dict(boxstyle='round,pad=0.3', facecolor='black', 
                                    edgecolor='cyan', alpha=0.7))
            
            # Set X-Axis to a Fixed 2-Year Span
            axis_start_date = last_date - pd.Timedelta(days=365)
            axis_end_date = last_date + pd.Timedelta(days=365)
            ax.set_xlim(axis_start_date, axis_end_date)
        
        # Finalize Plot
        ax.set_title(f"{ticker} Price History and Multi-Period Forecast", 
                    color='white', fontsize=16, fontweight='bold')
        ax.set_xlabel("Date", color='white', fontsize=12)
        ax.set_ylabel("Price (USD)", color='white', fontsize=12)
        ax.legend(facecolor='black', edgecolor='white', labelcolor='white', 
                 framealpha=0.8, loc='upper left')
        ax.grid(True, color='dimgray', linestyle='-', linewidth=0.3, alpha=0.3)
        ax.tick_params(axis='x', colors='white', rotation=45, labelsize=10)
        ax.tick_params(axis='y', colors='white', labelsize=10)
        
        for spine in ax.spines.values():
            spine.set_color('white')
        
        fig.tight_layout()

        filename = f"ml_advanced_forecast_{ticker}_{uuid.uuid4().hex[:6]}.png"
        plt.savefig(filename, facecolor='black', edgecolor='black', dpi=300, 
                   bbox_inches='tight')
        plt.close(fig)
        print(f"📂 Advanced forecast graph saved: {filename}")
        return filename
        
    except Exception as e:
        print(f"❌ An error occurred during graph plotting: {e}")
        traceback.print_exc()
        return None

# --- Main Command Handler (from Singularity, with MultiIndex fix) ---
async def handle_mlforecast_command(args: List[str] = None, ai_params: dict = None, is_called_by_ai: bool = False):
    """
    Handles the /mlforecast command. It trains models for key time horizons, generates a separate
    raw forecast for every week, and then adjusts the weekly path to align with the key forecasts.
    """
    ticker = None
    if is_called_by_ai and ai_params:
        ticker = ai_params.get("ticker")
    elif args:
        ticker = args[0].upper()
    else:
        ticker_input = input("Enter the stock ticker for the ML forecast: ")
        ticker = ticker_input.strip().upper() if ticker_input.strip() else None

    if not ticker:
        message = "Usage: /mlforecast <TICKER>"
        if not is_called_by_ai: print(message)
        return {"error": message} if is_called_by_ai else None

    if not is_called_by_ai:
        print("\n--- Advanced Machine Learning Price Forecast ---")
        print(f"-> Running advanced forecast for {ticker}...")

    try:
        # 1. Data Fetching and Prep
        data_daily = pd.DataFrame()
        
        # --- START OF FIX ---
        # Changed keys from days to yfinance-compatible period strings
        fetch_periods_map = {"10-Year": "10y", "5-Year": "5y", "3-Year": "3y", "1-Year": "1y"}
        # --- END OF FIX ---
        
        successful_period_name = None
        
        # --- MODIFIED LOOP ---
        for period_name, period_str in fetch_periods_map.items():
            if not is_called_by_ai: print(f"-> Attempting to fetch {period_name} of historical data...")
            
            # Pass the period string directly to yfinance
            temp_data = await asyncio.to_thread(
                yf.download, ticker, period=period_str, progress=False, auto_adjust=True
            )
            
            if not temp_data.empty and len(temp_data) > 504: # Need > 2 years of data for 1-year forecast
                data_daily = temp_data
                successful_period_name = period_name
                if not is_called_by_ai: print(f"   -> Successfully fetched {successful_period_name} of data.")
                break
        # --- END MODIFIED LOOP ---
        
        if data_daily.empty:
            message = f"❌ Error: Not enough historical data found for {ticker} to perform a forecast."
            if not is_called_by_ai: print(message)
            return {"error": message} if is_called_by_ai else None

        # --- CRITICAL FIX: Flatten MultiIndex columns right after download ---
        if isinstance(data_daily.columns, pd.MultiIndex):
            data_daily.columns = data_daily.columns.get_level_values(0)
        # --- END OF FIX ---

        data_weekly = data_daily.resample('W-FRI').last()
        
        all_forecast_horizons = {
            "5-Day": {"days": 5, "data": data_daily, "min_hist_days": 90},
            "1-Month (21-Day)": {"days": 21, "data": data_daily, "min_hist_days": 180},
            "3-Month (63-Day)": {"days": 63, "data": data_daily, "min_hist_days": 365},
            "6-Month (26-Week)": {"days": 26, "data": data_weekly, "min_hist_days": 1095}, # ~2 years of weekly data
            "1-Year (52-Week)": {"days": 52, "data": data_weekly, "min_hist_days": 1825}, # ~3.5 years of weekly data
        }
        
        available_data_days = (data_daily.index[-1] - data_daily.index[0]).days
        forecast_horizons_to_run = {
            name: params for name, params in all_forecast_horizons.items()
            if available_data_days >= params["min_hist_days"]
        }

        if not forecast_horizons_to_run:
            message = f"❌ Error: The fetched data ({successful_period_name}) is not sufficient for any forecast horizons."
            if not is_called_by_ai: print(message)
            return {"error": message} if is_called_by_ai else None

        results, forecast_points, weekly_forecast_points_raw = [], [], []
        last_price = data_daily['Close'].iloc[-1]
        true_last_date = data_daily.index[-1]

        # 2. Generate Key Forecasts
        for period_name, params in forecast_horizons_to_run.items():
            if not is_called_by_ai: print(f"\n-> Processing {period_name} forecast...")
            horizon, data = params["days"], params["data"].copy()
            data = calculate_technical_indicators(data)
            features = ['RSI', 'MACD', 'MACD_Signal', 'SMA_Diff', 'Volatility']
            
            if not all(feature in data.columns and not data[feature].isnull().all() for feature in features):
                if not is_called_by_ai: print(f"   -> Skipping {period_name}: Missing one or more required technical indicators.")
                continue

            data['Future_Close'] = data['Close'].shift(-horizon)
            data['Pct_Change'] = (data['Future_Close'] - data['Close']) / data['Close']
            data['Direction'] = (data['Future_Close'] > data['Close']).astype(int)
            data.dropna(subset=features + ['Direction', 'Pct_Change'], inplace=True)

            if len(data) < 50:
                if not is_called_by_ai: print(f"   -> Skipping {period_name}: Not enough training data ({len(data)} rows).")
                continue

            X, y_direction, y_magnitude = data[features], data['Direction'], data['Pct_Change']
            clf = RandomForestClassifier(n_estimators=100, random_state=42, n_jobs=-1).fit(X, y_direction)
            reg = RandomForestRegressor(n_estimators=100, random_state=42, n_jobs=-1).fit(X, y_magnitude)
            
            last_features = X.iloc[-1:]
            direction_pred = clf.predict(last_features)[0]
            confidence = clf.predict_proba(last_features)[0][direction_pred] * 100
            magnitude_pred = reg.predict(last_features)[0] * 100
            
            results.append({"Period": period_name, "Prediction": "UP" if direction_pred == 1 else "DOWN", "Confidence": f"{confidence:.0f}%", "Est. % Change": f"{magnitude_pred:+.2f}%"})
            
            time_delta_unit = 'W' if params["data"] is data_weekly else 'D'
            forecast_date = true_last_date + pd.Timedelta(weeks=horizon) if time_delta_unit == 'W' else true_last_date + pd.Timedelta(days=horizon)
            forecast_price = last_price * (1 + (magnitude_pred / 100))
            forecast_points.append({'date': forecast_date, 'price': forecast_price})

        if is_called_by_ai:
            return results

        # 3. Generate the "Raw" Weekly Forecast Path (CLI Only)
        print("\n-> Generating raw 52-week forecast path (this may take a moment)...")
        weekly_data_base = data_weekly.copy()
        weekly_data_base = calculate_technical_indicators(weekly_data_base)
        features_w = ['RSI', 'MACD', 'MACD_Signal', 'SMA_Diff', 'Volatility']
        
        if all(f in weekly_data_base.columns and not weekly_data_base[f].isnull().all() for f in features_w):
            last_features_w = weekly_data_base[features_w].iloc[-1:]
            for week_horizon in range(1, 53):
                data_temp = weekly_data_base.copy()
                data_temp['Future_Close'] = data_temp['Close'].shift(-week_horizon)
                data_temp['Pct_Change'] = (data_temp['Future_Close'] - data_temp['Close']) / data_temp['Close']
                data_temp.dropna(subset=features_w + ['Pct_Change'], inplace=True)
                
                if len(data_temp) < 50:
                    print(f"\n   -> Stopping weekly forecast at week {week_horizon-1} due to insufficient data.")
                    break
                
                X_w, y_magnitude_w = data_temp[features_w], data_temp['Pct_Change']
                weekly_reg = RandomForestRegressor(n_estimators=50, random_state=42, n_jobs=-1, max_depth=10).fit(X_w, y_magnitude_w)
                predicted_pct_change = weekly_reg.predict(last_features_w)[0]
                forecast_price = last_price * (1 + predicted_pct_change)
                forecast_date = true_last_date + pd.Timedelta(weeks=week_horizon)
                weekly_forecast_points_raw.append({'date': forecast_date, 'price': forecast_price})
                if week_horizon % 5 == 0: print(f"   ...week {week_horizon}/52 calculated.")
        
        # 4. Adjust the Raw Weekly Path
        adjusted_weekly_path = []
        if weekly_forecast_points_raw and forecast_points:
            print("\n-> Adjusting weekly path to align with key forecasts...")
            anchor_points = [{'date': true_last_date, 'price': last_price}] + sorted(forecast_points, key=lambda x: x['date'])
            
            for i in range(len(anchor_points) - 1):
                start_anchor, end_anchor = anchor_points[i], anchor_points[i+1]
                raw_segment = [p for p in weekly_forecast_points_raw if start_anchor['date'] < p['date'] <= end_anchor['date']]
                
                if not raw_segment: continue
                
                raw_segment_start_price = weekly_forecast_points_raw[weekly_forecast_points_raw.index(raw_segment[0]) -1]['price'] if raw_segment[0] != weekly_forecast_points_raw[0] else last_price
                raw_segment_end_price = raw_segment[-1]['price']
                raw_delta = raw_segment_end_price - raw_segment_start_price
                target_delta = end_anchor['price'] - start_anchor['price']
                
                for point in raw_segment:
                    scaling_factor = (point['price'] - raw_segment_start_price) / raw_delta if raw_delta != 0 else 0
                    adjusted_price = start_anchor['price'] + (scaling_factor * target_delta)
                    adjusted_weekly_path.append({'date': point['date'], 'price': adjusted_price})
            print("   -> Path adjustment complete.")

        # 5. Output Final Results
        print("\n" + "="*80)
        print(f"--- Advanced Forecast Results for {ticker} (based on {successful_period_name} of data) ---")
        if results:
            print(tabulate(results, headers="keys", tablefmt="pretty"))
            print("\n-> Generating forecast graph...")
            plot_advanced_forecast_graph(ticker, data_daily, forecast_points, adjusted_weekly_path)
        else:
            print("Could not generate any forecasts due to insufficient data across all time horizons.")
        print("="*80)

    except Exception as e:
        message = f"❌ An unexpected error occurred during the forecast: {e}"
        print(message)
        traceback.print_exc()
        return {"error": message} if is_called_by_ai else None