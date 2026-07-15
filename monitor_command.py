# --- Imports for monitor_command ---
import asyncio
import os
import csv
import smtplib
import configparser
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Dict, Optional
from collections import defaultdict
import re
import yfinance as yf
import pandas as pd
from tabulate import tabulate
import time
import pytz
from datetime import datetime, time as dt_time

# --- Imports from other command modules ---
from invest_command import calculate_ema_invest
from tracking_command import _load_portfolio_run
from risk_command import perform_risk_calculations_singularity

# --- Module-level Globals & Constants ---
active_alerts: List[Dict] = []
alert_lock = asyncio.Lock()
ALERTS_FILE = 'alerts.csv'

# --- Configuration for Notifications ---
config = configparser.ConfigParser()
config.read('config.ini')

# --- Helper Functions (moved or copied for self-containment) ---
def _is_market_open() -> bool:
    """Checks if the US stock market is open (Mon-Fri, 9:30-16:00 EST/EDT)."""
    try:
        # Use US/Eastern directly as it is the market standard
        tz = pytz.timezone('US/Eastern') 
        now_et = datetime.now(tz)
        
        # Market open 9:30 AM, close 4:00 PM (16:00) Eastern Time
        # FIX: Use dt_time (aliased datetime.time) to construct comparison times
        market_open = now_et.time() >= dt_time(9, 30)
        market_close = now_et.time() < dt_time(16, 0)
        is_weekday = now_et.weekday() < 5 # 0=Monday, 4=Friday
        
        is_open = is_weekday and market_open and market_close
        
        if not is_open:
             # Debug info to help diagnose why it thinks market is closed
             print(f"   [DEBUG] Market Closed Check: Detected ET Time is {now_et.strftime('%a %H:%M')}")
             
        return is_open
    except Exception as e:
        print(f"   [WARNING] Failed to check market hours ({e}). Defaulting to OPEN.")
        # Fail safe: Return True so alerts are NOT blocked if the time check errors out
        return True
        
def _parse_interval_to_seconds(interval_str: str) -> Optional[int]:
    """Converts interval string (e.g., '30m', '1h', '2d') to seconds."""
    match = re.match(r"(\d+)([mhd])", interval_str.lower())
    if not match:
        return None
    try:
        value = int(match.group(1))
        unit = match.group(2)
        if unit == 'm':
            return value * 60
        elif unit == 'h':
            return value * 3600
        elif unit == 'd':
            return value * 86400
    except (ValueError, TypeError):
        return None
    return None

async def send_notification(subject: str, body: str, recipient_email_override: Optional[str] = None):
    """Sends an email notification."""
    try:
        smtp_server = config.get('EMAIL_CONFIG', 'SMTP_SERVER')
        smtp_port = config.getint('EMAIL_CONFIG', 'SMTP_PORT')
        sender_email = config.get('EMAIL_CONFIG', 'SENDER_EMAIL')
        sender_password = config.get('EMAIL_CONFIG', 'SENDER_PASSWORD')
        recipient = recipient_email_override or config.get('EMAIL_CONFIG', 'RECIPIENT_EMAIL', fallback=None)

        if not all([smtp_server, smtp_port, sender_email, sender_password, recipient]):
            print("⚠️ Email config incomplete. Cannot send notification.")
            return

        msg = MIMEMultipart()
        msg['From'], msg['To'], msg['Subject'] = sender_email, recipient, subject
        msg.attach(MIMEText(body, 'plain'))

        # Define the synchronous function to be run in a thread
        def _send_email_sync():
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()  # Secure the connection
                server.login(sender_email, sender_password) # Login
                server.send_message(msg) # Send the email
        
        # Run the blocking email code in a separate thread
        await asyncio.to_thread(_send_email_sync)
        print(f"✔ Email notification sent successfully to {recipient}.")
    except Exception as e:
        print(f"❌ Failed to send email notification: {e}")
        
def save_alerts_to_csv():
    """Saves the current state of active_alerts to the CSV file."""
    try:
        with open(ALERTS_FILE, mode='w', newline='', encoding='utf-8') as f:
            # --- MODIFIED: Add 'market_hours_only' ---
            fieldnames = ['ticker', 'metric', 'operator', 'value', 'sensitivity', 'recipient_email', 'portfolio_code', 'check_interval', 'market_hours_only']
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(active_alerts)
    except Exception as e:
        print(f"Error saving alerts to {ALERTS_FILE}: {e}")

async def load_alerts_from_csv():
    """Loads alerts from the CSV file into memory at startup."""
    if not os.path.exists(ALERTS_FILE): return
    try:
        with open(ALERTS_FILE, mode='r', newline='', encoding='utf-8') as f:
            temp_alerts = []
            for row in csv.DictReader(f):
                try:
                    row['value'] = float(row['value'])
                    row['sensitivity'] = int(row['sensitivity']) if row.get('sensitivity') else None
                    row['check_interval'] = int(row['check_interval']) if row.get('check_interval') else None
                    # --- ADD THIS LINE ---
                    row['market_hours_only'] = str(row.get('market_hours_only', 'False')).lower() == 'true'
                    temp_alerts.append(row)
                except (ValueError, KeyError):
                    continue
            async with alert_lock:
                global active_alerts
                active_alerts = temp_alerts
            if active_alerts: print(f"✅ Loaded {len(active_alerts)} alert(s) from {ALERTS_FILE}.")
    except Exception as e:
        print(f"Error loading alerts from {ALERTS_FILE}: {e}")
        
async def alert_worker():
    """
    A continuous background task that checks 'fast' alerts (price, invest) every 10 seconds.
    """
    print("🚀 Real-time (Fast) Alert Worker has been started.")
    while True:
        await asyncio.sleep(10)  # Check every 10 seconds
        
        alerts_to_check = []
        triggered_indices = []

        async with alert_lock:
            # --- MODIFIED: Filter for only price and invest alerts ---
            alerts_to_check = [(i, a) for i, a in enumerate(active_alerts) if a['metric'] in ('price', 'invest')]
            if not alerts_to_check:
                continue

        # --- Efficiently check all PRICE alerts first ---
        price_alerts = [(i, a) for i, a in alerts_to_check if a['metric'] == 'price']
        if price_alerts:
            tickers_to_fetch_price = list(set([alert['ticker'] for _, alert in price_alerts]))
            try:
                data = await asyncio.to_thread(yf.download, tickers=tickers_to_fetch_price, period="1d", progress=False, auto_adjust=False)
                close_prices = data.get('Close')
                if close_prices is not None:
                    for index, alert in price_alerts:
                        current_price = None
                        if isinstance(close_prices, pd.DataFrame):
                            current_price = close_prices[alert['ticker']].iloc[-1] if alert['ticker'] in close_prices.columns else None
                        elif isinstance(close_prices, pd.Series):
                            current_price = close_prices.iloc[-1]

                        if current_price is None or pd.isna(current_price): continue
                        
                        op, val = alert['operator'], alert['value']
                        if (op == '>' and current_price > val) or (op == '<' and current_price < val) or \
                           (op == '>=' and current_price >= val) or (op == '<=' and current_price <= val):
                            print("\n" + "!"*80 + f"\n🔔 PRICE ALERT TRIGGERED! 🔔\n   Ticker:    {alert['ticker']}\n   Condition: Price {op} {val}\n   Live Price:  ${current_price:,.2f}\n" + "!"*80)
                            print(f"\nEnter command: ", end="", flush=True)
                            
                            recipient = alert.get('recipient_email')
                            if recipient:
                                subject = f"M.I.C. Singularity Price Alert: {alert['ticker']}"
                                body = (f"A price alert for {alert['ticker']} has been triggered.\n\n"
                                        f"Details:\n - Ticker: {alert['ticker']}\n"
                                        f" - Condition: Price {op} {val}\n"
                                        f" - Current Price: ${current_price:,.2f}\n\n"
                                        f"This alert has now been removed from the active list.")
                                await send_notification(subject, body, recipient_email_override=recipient)

                            triggered_indices.append(index)
            except Exception: pass

        # --- Check all INVEST score alerts individually ---
        invest_alerts = [(i, a) for i, a in alerts_to_check if a['metric'] == 'invest']
        if invest_alerts:
            for index, alert in invest_alerts:
                try:
                    _, current_score = await calculate_ema_invest(alert['ticker'], alert['sensitivity'], is_called_by_ai=True)
                    if current_score is None: continue

                    op, val = alert['operator'], alert['value']
                    if (op == '>' and current_score > val) or (op == '<' and current_score < val) or \
                       (op == '>=' and current_score >= val) or (op == '<=' and current_score <= val):
                        print("\n" + "!"*80 + f"\n🔔 INVEST SCORE ALERT TRIGGERED! 🔔\n   Ticker:      {alert['ticker']}\n   Condition:   INVEST Score (Sens: {alert['sensitivity']}) {op} {val}\n   Live Score:  {current_score:,.2f}%\n" + "!"*80)
                        print(f"\nEnter command: ", end="", flush=True)

                        recipient = alert.get('recipient_email')
                        if recipient:
                            subject = f"M.I.C. Singularity Invest Score Alert: {alert['ticker']}"
                            body = (f"An INVEST score alert for {alert['ticker']} has been triggered.\n\n"
                                    f"Details:\n - Ticker: {alert['ticker']}\n"
                                    f" - Sensitivity: {alert['sensitivity']}\n"
                                    f" - Condition: INVEST Score {op} {val}\n"
                                    f" - Current Score: {current_score:,.2f}%\n\n"
                                    f"This alert has now been removed from the active list.")
                            await send_notification(subject, body, recipient_email_override=recipient)

                        triggered_indices.append(index)
                except Exception: pass

        # --- P&L CHECK HAS BEEN MOVED TO persistent_alert_worker ---

        # Safely remove all triggered alerts from the main list
        if triggered_indices:
            async with alert_lock:
                # Use set to ensure unique indices before popping
                for index in sorted(list(set(triggered_indices)), reverse=True):
                    if index < len(active_alerts):
                        active_alerts.pop(index)
                # After removing, save the updated list
                save_alerts_to_csv()

async def persistent_alert_worker():
    """
    A continuous background task that checks 'slow' alerts (P&L, Risk)
    at their own specified intervals. These alerts are NOT removed on trigger.
    """
    print("🚀 Persistent (Slow) Alert Worker has been started.")
    while True:
        await asyncio.sleep(60) # Check every 60 seconds
        
        now = time.time()
        alerts_to_check = []

        async with alert_lock:
            # Get all persistent alerts
            alerts_to_check = [a for a in active_alerts if a['metric'] not in ('price', 'invest')]
        
        if not alerts_to_check:
            continue
            
        alerts_to_run_now = []
        for alert in alerts_to_check:
            # Default interval: 1 hour for PNL, 6 hours for Risk
            default_interval = 3600 if alert['metric'] == 'pnl' else 21600 
            interval_sec = alert.get('check_interval', default_interval)
            last_checked = alert.get('last_checked', 0)
            
            if now >= (last_checked + interval_sec):
                alerts_to_run_now.append(alert)
                alert['last_checked'] = now # Update timestamp *before* running
        
        if not alerts_to_run_now:
            continue

        print(f"-> Running {len(alerts_to_run_now)} persistent alert check(s)...")
        
        # --- NEW: Check for market hours *before* running any jobs that require it ---
        is_market_currently_open = _is_market_open()
        
        # --- Check all P&L alerts ---
        pnl_alerts = [a for a in alerts_to_run_now if a['metric'] == 'pnl']
        if pnl_alerts:
            # --- NEW: Check market hours for P&L ---
            if not is_market_currently_open:
                print("   -> Skipping P&L alerts: Market is closed.")
            else:
                alerts_by_portfolio = defaultdict(list)
                for alert in pnl_alerts:
                    alerts_by_portfolio[alert['portfolio_code']].append(alert)

                for portfolio_code, alerts in alerts_by_portfolio.items():
                    try:
                        old_run_data = await _load_portfolio_run(portfolio_code) # From tracking_command.py
                        if not old_run_data: continue

                        tickers_to_fetch = [row['Ticker'] for row in old_run_data if row.get('Ticker') != 'Cash']
                        data = await asyncio.to_thread(yf.download, tickers=tickers_to_fetch, period="1d", interval="1m", progress=False, auto_adjust=False)
                        if data is None or data.empty: continue
                        
                        open_prices = data['Open'].iloc[0] if isinstance(data['Open'], pd.DataFrame) else data['Open']
                        live_prices = data['Close'].iloc[-1] if isinstance(data['Close'], pd.DataFrame) else data['Close']

                        opening_value = sum(float(row.get('Shares', 0)) * open_prices.get(row['Ticker'], 0) for row in old_run_data if row.get('Ticker') != 'Cash')
                        current_value = sum(float(row.get('Shares', 0)) * live_prices.get(row['Ticker'], 0) for row in old_run_data if row.get('Ticker') != 'Cash')
                        daily_pnl = current_value - opening_value
                        
                        for alert in alerts:
                            op, val = alert['operator'], alert['value']
                            if (op == '>' and daily_pnl > val) or (op == '<' and daily_pnl < val) or \
                               (op == '>=' and daily_pnl >= val) or (op == '<=' and daily_pnl <= val):
                                print("\n" + "!"*80 + f"\n📈 DAILY P&L ALERT TRIGGERED! 📈\n   Portfolio: {portfolio_code}\n   Condition: Daily P&L {op} {val:,.2f}\n   Live P&L:  ${daily_pnl:,.2f}\n" + "!"*80)
                                print(f"\nEnter command: ", end="", flush=True)

                                recipient = alert.get('recipient_email')
                                if recipient:
                                    subject = f"M.I.C. Singularity P&L Alert: {portfolio_code}"
                                    body = (f"A daily P&L alert for portfolio '{portfolio_code}' has been triggered.\n\n"
                                            f"Details:\n - Portfolio: {portfolio_code}\n"
                                            f" - Current Portfolio Value: ${current_value:,.2f}\n"
                                            f" - Current Daily P&L: ${daily_pnl:,.2f}\n"
                                            f" - Triggered Condition: Daily P&L {op} {val:,.2f}\n\n"
                                            f"This alert is persistent and will NOT be removed from the active list.")
                                    await send_notification(subject, body, recipient_email_override=recipient)
                                
                    except Exception as e:
                        print(f"❌ Error during P&L check for portfolio {portfolio_code}: {e}")
                        pass
        
        # --- Check all RISK alerts ---
        risk_alerts = [a for a in alerts_to_run_now if a['metric'] in ('combined_score', 'market_invest_score')]
        if risk_alerts:
            # --- NEW: Check market hours for any risk alert that requires it ---
            alerts_to_run_risk = []
            for alert in risk_alerts:
                if alert.get('market_hours_only', False) and not is_market_currently_open:
                    print(f"   -> Skipping risk alert '{alert['metric']} {alert['operator']} {alert['value']}': Market is closed.")
                else:
                    alerts_to_run_risk.append(alert)
            
            if alerts_to_run_risk:
                try:
                    print(f"   -> Running /risk command for {len(alerts_to_run_risk)} risk alert(s)...")
                    risk_results, _ = await perform_risk_calculations_singularity(is_called_by_ai=True)
                    
                    combined_s_raw = risk_results.get('combined_score', 'N/A')
                    invest_s_raw = risk_results.get('market_invest_score', 'N/A')
                    
                    combined_s = float(combined_s_raw.replace('%', '')) if combined_s_raw != 'N/A' else None
                    invest_s = float(invest_s_raw.replace('%', '')) if invest_s_raw != 'N/A' else None
                    
                    for alert in alerts_to_run_risk:
                        current_val = combined_s if alert['metric'] == 'combined_score' else invest_s
                        if current_val is None or pd.isna(current_val):
                            continue
                            
                        op, val = alert['operator'], alert['value']
                        if (op == '>' and current_val > val) or (op == '<' and current_val < val) or \
                           (op == '>=' and current_val >= val) or (op == '<=' and current_val <= val):
                            
                            metric_name = "Combined Market Score" if alert['metric'] == 'combined_score' else "Market Invest Score"
                            print("\n" + "!"*80 + f"\n🚨 MARKET RISK ALERT TRIGGERED! 🚨\n   Metric:    {metric_name}\n   Condition: {alert['metric']} {op} {val}\n   Live Score:  {current_val:,.2f}\n" + "!"*80)
                            print(f"\nEnter command: ", end="", flush=True)

                            recipient = alert.get('recipient_email')
                            if recipient:
                                subject = f"M.I.C. Singularity Risk Alert: {metric_name}"
                                body = (f"A market risk alert for '{metric_name}' has been triggered.\n\n"
                                        f"Details:\n - Metric: {metric_name}\n"
                                        f" - Condition: {alert['metric']} {op} {val}\n"
                                        f" - Current Score: {current_val:,.2f}\n\n"
                                        f"This alert is persistent and will NOT be removed from the active list.")
                                await send_notification(subject, body, recipient_email_override=recipient)
                                
                except Exception as e:
                    print(f"❌ Error during risk alert check: {e}")

# --- Main Command Handler ---
async def handle_monitor_command(args: list, is_called_by_ai: bool = False):
    """
    Manages real-time monitoring alerts.
    - price/invest alerts are checked every 10s and removed on trigger.
    - pnl/risk alerts are checked at a user-defined interval and are persistent.
    """
    if is_called_by_ai:
        return "This command is interactive and designed for direct user use in the CLI."

    if not args:
        print("Usage: /monitor <add|list|remove> [options]")
        return

    action = args[0].lower()
    async with alert_lock:
        if action == "add":
            # --- New Robust Parser ---
            recipient_email = None
            check_interval = None
            market_hours_only = False
            
            # Use a copy to safely pop items
            command_args = list(args[1:]) 
            
            try:
                # Iteratively parse flags from the end
                while command_args:
                    flag = command_args[-1].lower()
                    if flag == '--market-hours':
                        market_hours_only = True
                        command_args.pop()
                    elif flag.startswith('@') or '@' in flag: # Simple email check
                        if len(command_args) > 1 and command_args[-2].lower() == 'to':
                            recipient_email = command_args.pop() # Pop the email
                            command_args.pop() # Pop 'to'
                        else:
                            break # Not a flag, must be part of the main args
                    elif re.match(r"(\d+)([mhd])", flag):
                        if len(command_args) > 1 and command_args[-2].lower() == 'every':
                            interval_str = command_args.pop() # Pop the interval
                            check_interval = _parse_interval_to_seconds(interval_str)
                            if check_interval is None:
                                print(f"Error: Invalid interval format '{interval_str}'. Use '30m', '1h', '6h', '1d', etc.")
                                return
                            command_args.pop() # Pop 'every'
                        else:
                            break # Not a flag, must be part of the main args
                    else:
                        break # No more flags found at the end
            except IndexError:
                pass # Will be caught by length checks below

            # 3. Parse remaining args
            if len(command_args) < 2: # Must have at least <identifier> <op> <value> or <identifier> <metric> ...
                print("Usage: /monitor add <TICKER|METRIC|PORTFOLIO> [metric|sensitivity] <op> <value> [flags...]")
                print("Flags: [--market-hours] [every <interval>] [to <email>]")
                return

            alert_to_add = None
            
            # --- START OF FIX: Identify metric based on content, not just position ---
            
            # Check for risk metrics first, as they don't have a <TICKER>
            metric_candidate_risk = command_args[0].lower() # <-- FIX: Check args[0]
            if metric_candidate_risk in ('combined_score', 'market_invest_score'):
                metric = metric_candidate_risk
                identifier = None # Not used
                metric_args_list = command_args[1:] # <-- FIX: Start from args[1]
                expected_arg_count = 2 # <op> <value>
                usage_msg = f"Usage: /monitor add {metric} <op> <value> [flags...]"

            # Check for other metrics that require an identifier
            elif len(command_args) > 2:
                identifier = command_args[0].upper() # <-- FIX: This is args[0]
                metric = command_args[1].lower() # <-- FIX: This is args[1]
                metric_args_list = command_args[2:] # <-- FIX: This is args[2:]
                expected_arg_count = -1 # Handled inside metric block
                usage_msg = "Usage: See specific metric usage." # Generic
            
            else:
                print(f"Error: Invalid command structure. Not enough arguments after parsing flags.")
                return
            
            # --- END OF FIX ---

            # --- Validate and Build Alert ---
            if metric == 'price':
                expected_arg_count = 2 # <op> <value>
                usage_msg = "Usage: /monitor add <TICKER> price <op> <value> [flags...]"
                if check_interval is not None:
                    print("Error: 'every <interval>' is not used for 'price' alerts. They are checked every 10s."); return
                if market_hours_only:
                    print("Error: '--market-hours' is not used for 'price' alerts. They are checked every 10s."); return
                if len(metric_args_list) != expected_arg_count: print(usage_msg); return
                try:
                    operator = metric_args_list[0]
                    value = float(metric_args_list[1])
                    if operator not in ['>', '<', '>=', '<=']: print(f"Error: Invalid operator '{operator}'."); return
                    alert_to_add = {"ticker": identifier, "metric": "price", "operator": operator, "value": value, "recipient_email": recipient_email}
                except ValueError: print("Error: The value for the alert must be a valid number.")

            elif metric == 'invest':
                expected_arg_count = 3 # <sens> <op> <value>
                usage_msg = "Usage: /monitor add <TICKER> invest <sens> <op> <value> [flags...]"
                if check_interval is not None or market_hours_only:
                    print("Error: 'every <interval>' and '--market-hours' are not used for 'invest' alerts. They are checked every 10s."); return
                if len(metric_args_list) != expected_arg_count: print(usage_msg); return
                try:
                    sensitivity = int(metric_args_list[0])
                    operator = metric_args_list[1]
                    value = float(metric_args_list[2])
                    if sensitivity not in [1, 2, 3]: print("Error: Invalid sensitivity. Use 1, 2, or 3."); return
                    if operator not in ['>', '<', '>=', '<=']: print(f"Error: Invalid operator '{operator}'."); return
                    alert_to_add = {"ticker": identifier, "metric": "invest", "sensitivity": sensitivity, "operator": operator, "value": value, "recipient_email": recipient_email}
                except ValueError: print("Error: Sensitivity and value must be valid numbers.")
            
            elif metric == 'pnl':
                expected_arg_count = 2 # <op> <value>
                usage_msg = "Usage: /monitor add <PORTFOLIO_CODE> pnl <op> <value> [flags...]"
                if check_interval is None:
                    print(f"Error: 'every <interval>' is REQUIRED for 'pnl' alerts.\n{usage_msg}"); return
                if market_hours_only:
                    print("Error: '--market-hours' is implied for 'pnl' alerts (runs during market hours) and not needed."); return
                if len(metric_args_list) != expected_arg_count: print(usage_msg); return
                try:
                    operator = metric_args_list[0]
                    value = float(metric_args_list[1])
                    if operator not in ['>', '<', '>=', '<=']: print(f"Error: Invalid operator '{operator}'."); return
                    alert_to_add = {"portfolio_code": identifier, "metric": "pnl", "operator": operator, "value": value, "recipient_email": recipient_email, "check_interval": check_interval, "market_hours_only": True} # Force market hours for P&L
                except ValueError: print("Error: The value for the P&L interval must be a valid number.")

            elif metric in ('combined_score', 'market_invest_score'):
                expected_arg_count = 2 # <op> <value>
                usage_msg = f"Usage: /monitor add {metric} <op> <value> [flags...]"
                if check_interval is None:
                    print(f"Error: 'every <interval>' is REQUIRED for '{metric}' alerts.\n{usage_msg}"); return
                if len(metric_args_list) != expected_arg_count: print(usage_msg); return
                try:
                    operator = metric_args_list[0]
                    value = float(metric_args_list[1])
                    if operator not in ['>', '<', '>=', '<=']: print(f"Error: Invalid operator '{operator}'."); return
                    alert_to_add = {"metric": metric, "operator": operator, "value": value, "recipient_email": recipient_email, "check_interval": check_interval, "market_hours_only": market_hours_only, "ticker": None, "portfolio_code": None} # Explicitly nullify identifiers
                except ValueError: print("Error: The value for the score must be a valid number.")
            
            else:
                print(f"Error: Invalid metric '{metric}'. Use 'price', 'invest', 'pnl', 'combined_score', or 'market_invest_score'.")

            if alert_to_add:
                active_alerts.append(alert_to_add)
                save_alerts_to_csv()
                print(f"✅ Alert added and saved.")
                if alert_to_add.get('check_interval'):
                    print(f"   -> This is a persistent alert and will be checked every {alert_to_add['check_interval']} seconds.")
                    if alert_to_add.get('market_hours_only'):
                        print("   -> This alert will ONLY run during US market hours.")
                else:
                    print("   -> This is a one-time alert and will be checked every 10 seconds.")

        elif action == "list":
            if not active_alerts:
                print("No active alerts to display.")
                return
            print("\n--- Active Alerts ---")
            table_data = []
            for i, alert in enumerate(active_alerts):
                condition_str, identifier, details = "", "", ""
                
                if alert['metric'] == 'price':
                    identifier = alert.get('ticker')
                    condition_str = f"price {alert['operator']} {alert['value']}"
                    details = "Check: 10s (One-Time)"
                elif alert['metric'] == 'invest':
                    identifier = alert.get('ticker')
                    condition_str = f"INVEST (sens: {alert.get('sensitivity', 'N/A')}) {alert['operator']} {alert['value']}"
                    details = "Check: 10s (One-Time)"
                elif alert['metric'] == 'pnl':
                    identifier = alert.get('portfolio_code')
                    condition_str = f"P&L {alert['operator']} {alert['value']}"
                    details = f"Check: {alert.get('check_interval')}s (Persistent, Market Hours)"
                elif alert['metric'] in ('combined_score', 'market_invest_score'):
                    identifier = "Market Risk"
                    condition_str = f"{alert['metric']} {alert['operator']} {alert['value']}"
                    market_only_str = " (Market Hours)" if alert.get('market_hours_only') else ""
                    details = f"Check: {alert.get('check_interval')}s (Persistent{market_only_str})"
                
                recipient_display = alert.get('recipient_email') or "Terminal Only"
                table_data.append([i + 1, identifier, condition_str, details, recipient_display])
            print(tabulate(table_data, headers=["Index", "Identifier", "Condition", "Check Logic", "Recipient"], tablefmt="pretty"))

        elif action == "remove":
            if len(args) < 2:
                print("Usage: /monitor remove <index>"); return
            try:
                index_to_remove = int(args[1]) - 1
                if 0 <= index_to_remove < len(active_alerts):
                    active_alerts.pop(index_to_remove)
                    save_alerts_to_csv()
                    print(f"✅ Alert at index {args[1]} removed and file updated.")
                else:
                    print("Error: Invalid index. Use '/monitor list' to see active alerts.")
            except ValueError:
                print("Error: Please provide a valid number for the index.")
        else:
            print(f"Unknown monitor command: {action}. Use 'add', 'list', or 'remove'.")