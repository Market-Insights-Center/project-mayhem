# tracking_command.py

# --- Imports ---
import asyncio
import os
import csv
from typing import List, Dict, Any, Optional
from collections import defaultdict
import pandas as pd
from tabulate import tabulate
import matplotlib.pyplot as plt
import numpy as np
import uuid
import traceback
import re
import smtplib
import configparser
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
import math

# --- Local Imports from other command modules ---
from custom_command import (
    _get_custom_portfolio_run_csv_filepath, 
    _save_custom_portfolio_run_to_csv, 
    TRACKING_ORIGIN_FILE,
    load_portfolio_config, 
    PORTFOLIO_DB_FILE
)
from invest_command import process_custom_portfolio, calculate_ema_invest
from custom_command import PORTFOLIO_DB_FILE

try:
    from execution_command import execute_portfolio_rebalance, get_robinhood_equity, get_robinhood_holdings
except ImportError:
    # Fallback if execution module is missing
    def execute_portfolio_rebalance(trades, known_holdings=None): 
        print("Execution module not found.")
        return []
    def get_robinhood_equity(): return 0.0
    def get_robinhood_holdings(): return {}

# --- Constants ---
SUBPORTFOLIO_NAMES_FILE = 'portfolio_subportfolio_names.csv'
config = configparser.ConfigParser()
config.read('config.ini')

# --- Helper Functions ---
async def _send_tracking_email(subject: str, html_body: str, recipient_email: str):
    """Sends an HTML email notification."""
    try:
        config = configparser.ConfigParser()
        config.read('config.ini')
        
        smtp_server = config.get('EMAIL_CONFIG', 'SMTP_SERVER')
        smtp_port = config.getint('EMAIL_CONFIG', 'SMTP_PORT')
        sender_email = config.get('EMAIL_CONFIG', 'SENDER_EMAIL')
        sender_password = config.get('EMAIL_CONFIG', 'SENDER_PASSWORD')

        if not all([smtp_server, smtp_port, sender_email, sender_password, recipient_email]):
            print("⚠️ Email config incomplete. Cannot send notification.")
            return

        msg = MIMEMultipart()
        msg['From'], msg['To'], msg['Subject'] = sender_email, recipient_email, subject
        msg.attach(MIMEText(html_body, 'html'))

        def _send_email_sync():
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(sender_email, sender_password)
                server.send_message(msg)
        
        await asyncio.to_thread(_send_email_sync)
        
    except Exception as e:
        print(f"❌ Failed to send tracking email: {e}")

async def send_notification(subject: str, body: str, recipient_email_override: Optional[str] = None):
    """Sends a plain text email notification."""
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

        def _send_email_sync():
            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(sender_email, sender_password)
                server.send_message(msg)
        
        await asyncio.to_thread(_send_email_sync)
        print(f"✔ Email notification sent successfully to {recipient}.")
    except Exception as e:
        print(f"❌ Failed to send email notification: {e}")
   
async def _ask_and_send_trade_email(
    portfolio_code: str, 
    trade_recs: List[Dict[str, Any]], 
    new_run_data: List[Dict[str, Any]], 
    new_cash: float, 
    new_total_value: float
):
    """Asks user to send trade email and sends HTML version."""
    print("\n--- 📧 Trade Recommendation Email ---")
    try:
        send_email = input("Would you like to receive an email with these trade recommendations? (yes/no): ").lower().strip()
        if send_email != 'yes':
            print("-> Email not sent.")
            return

        email_regex = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
        recipient_email = ""
        while True:
            recipient_email = input("Enter your email address: ").strip()
            if re.match(email_regex, recipient_email):
                break
            print("Invalid email format. Please try again.")

        print(f"-> Preparing HTML email for {recipient_email}...")
        
        info_blocks_html = ""
        if trade_recs:
            info_blocks_html += "<h2>Trade Recommendations (Changes)</h2>"
            info_blocks_html += "<pre style='font-family: monospace; background-color: #2c2f33; color: #DAA520; padding: 10px; border-radius: 5px;'>"
            
            for rec in trade_recs:
                ticker = rec['ticker']
                share_change = rec['share_change']
                live_price = rec['live_price']
                
                action = "BUY" if share_change > 0 else "SELL"
                shares = abs(share_change)
                estimated_value = abs(share_change * live_price)
                power_direction = "Used" if action == "BUY" else "Returned"
                robinhood_link = f"https://robinhood.com/stocks/{ticker}"

                block = (
                    "---------------------------------------------\n"
                    "--- TRADE RECOMMENDATION (Info Block) ---\n"
                    f"Action:     {action}\n"
                    f"Ticker:     {ticker}\n"
                    f"Order Type: Market\n"
                    f"Amount:     {shares:.2f} Shares\n"
                    f"Est. Power {power_direction}: ${estimated_value:,.2f}\n\n"
                    f"Trade Link: {robinhood_link}\n"
                    "---------------------------------------------\n"
                )
                info_blocks_html += block
            info_blocks_html += "</pre>"
        else:
            info_blocks_html = "<h2>No Trade Changes Recommended</h2><p>Your portfolio allocation is already aligned with the new recommendation.</p>"

        full_table_html = "<h2>Full Recommended Portfolio</h2>"
        full_table_html += """
        <style>
            table.mic-table {
                border-collapse: collapse;
                width: 100%;
                font-family: Arial, sans-serif;
                color: #f0f0f0 !important; 
                border: 1px solid #555;
            }
            table.mic-table th, table.mic-table td {
                border: 1px solid #555;
                padding: 8px 12px;
                text-align: left;
                color: #f0f0f0 !important;
            }
            table.mic-table th {
                background-color: #9400D3; /* Violet Purple */
                color: #ffffff !important;
                text-align: center;
            }
            table.mic-table tr:nth-child(even) {
                background-color: #3e4147;
            }
            table.mic-table td:nth-child(n+3) {
                text-align: right;
            }
            table.mic-table a {
                color: #00A36C; /* Green link */
                font-weight: bold;
                text-decoration: none;
            }
            table.mic-table a:hover {
                text-decoration: underline;
            }
        </style>
        <table class='mic-table'>
            <thead>
                <tr>
                    <th>Ticker</th>
                    <th>Sub-Portfolio Path</th>
                    <th>Shares</th>
                    <th>$ Value</th>
                    <th>% of Total</th>
                    <th>Trade Link</th>
                </tr>
            </thead>
            <tbody>
        """
        
        sorted_new_run = sorted(new_run_data, key=lambda x: x.get('ticker', ''))

        for item in sorted_new_run:
            ticker = item.get('ticker', 'N/A')
            shares = float(item.get('shares', 0))
            value = float(item.get('actual_money_allocation', 0))
            percent = (value / new_total_value) * 100 if new_total_value > 0 else 0
            path = item.get('SubPortfolioPath', 'N/A')
            
            robinhood_link = f"https://robinhood.com/stocks/{ticker}"
            link_html = f"<a href='{robinhood_link}'>Trade</a>"
            
            full_table_html += (
                f"<tr>"
                f"<td>{ticker}</td>"
                f"<td>{path}</td>"
                f"<td>{shares:.2f}</td>"
                f"<td>${value:,.2f}</td>"
                f"<td>{percent:.2f}%</td>"
                f"<td>{link_html}</td>"
                f"</tr>"
            )

        cash_percent = (new_cash / new_total_value) * 100 if new_total_value > 0 else 0
        full_table_html += (
            f"<tr style='font-weight: bold; background-color: #1e1f22;'>"
            f"<td>Cash</td>"
            f"<td>Cash</td>"
            f"<td>${new_cash:,.2f}</td>"
            f"<td>${new_cash:,.2f}</td>"
            f"<td>{cash_percent:.2f}%</td>"
            f"<td>-</td>"
            f"</tr>"
        )
        full_table_html += "</tbody></table>"

        email_body = f"""
        <html>
            <head>
                <style>
                    body {{
                        font-family: Arial, sans-serif;
                        line-height: 1.6;
                        background-color: #1e1f22;
                        color: #f0f0f0 !important; 
                        margin: 0;
                        padding: 20px;
                    }}
                    .container {{
                        width: 90%;
                        max-width: 900px;
                        margin: auto;
                        background: #2c2f33;
                        padding: 30px;
                        border-radius: 8px;
                        color: #f0f0f0 !important;
                    }}
                    h1, h2 {{ color: #9400D3; }}
                    p {{ font-size: 16px; color: #f0f0f0 !important; }}
                    .disclaimer {{
                        font-size: 12px;
                        color: #B8860B;
                        margin-top: 30px;
                        border-top: 1px solid #555;
                        padding-top: 15px;
                    }}
                </style>
            </head>
            <body>
                <div class='container'>
                    <h1>M.I.C. Singularity - Trade Recommendations</h1>
                    <p>Hello,</p>
                    <p>Here are the trade recommendations and the full new portfolio allocation for <strong>'{portfolio_code}'</strong> based on your recent /tracking run for a total value of <strong>${new_total_value:,.2f}</strong>.</p>
                    {info_blocks_html}
                    <br><br>
                    {full_table_html}
                    <p class='disclaimer'>
                        Disclaimer: These are algorithmically generated suggestions, not financial advice. 
                        Always verify information before executing any trade.
                    </p>
                </div>
            </body>
        </html>
        """
        
        subject = f"M.I.C. Singularity - Trade Recommendations for {portfolio_code}"
        await _send_tracking_email(subject, email_body, recipient_email)
        print(f"✅ Successfully sent HTML trade recommendations to {recipient_email}.")
    
    except Exception as e:
        print(f"❌ Error sending trade email: {e}")
        traceback.print_exc()
          
async def _load_portfolio_run(portfolio_code: str) -> Optional[List[Dict[str, Any]]]:
    """Loads and parses the last saved run data for a portfolio."""
    filepath = _get_custom_portfolio_run_csv_filepath(portfolio_code)
    if not os.path.exists(filepath):
        print(f"Info: No saved run data found for portfolio '{portfolio_code}'.")
        return None
    try:
        run_data = []
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip() == "# ---BEGIN_DATA---":
                    break
            reader = csv.DictReader(f)
            for row in reader:
                run_data.append(row)
        return run_data
    except Exception as e:
        print(f"❌ Error loading saved run data for '{portfolio_code}': {e}")
        return None

def _get_subportfolio_map_from_config(portfolio_config: Dict[str, Any]) -> Dict[str, str]:
    """Reads the portfolio config and returns a map of {ticker: sub_portfolio_id}."""
    ticker_map = {}
    num_portfolios = int(portfolio_config.get('num_portfolios', 0))
    for i in range(1, num_portfolios + 1):
        sub_portfolio_id = f'Sub-Portfolio {i}'
        tickers_str = portfolio_config.get(f'tickers_{i}', '')
        for ticker in tickers_str.split(','):
            if ticker.strip():
                ticker_map[ticker.strip().upper()] = sub_portfolio_id
    return ticker_map

def _load_all_subportfolio_names() -> Dict[str, str]:
    """Loads all custom names from the dedicated CSV."""
    if not os.path.exists(SUBPORTFOLIO_NAMES_FILE):
        return {}
    names = {}
    try:
        with open(SUBPORTFOLIO_NAMES_FILE, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                composite_key = f"{row['PortfolioCode'].lower().strip()}|{row['SubPortfolioID'].strip()}"
                names[composite_key] = row['SubPortfolioName']
    except Exception:
        pass 
    return names

def _save_all_subportfolio_names(all_names_map: Dict[str, str]):
    """Saves the complete map of all portfolio names back to the CSV."""
    rows_to_write = []
    for composite_key, name in all_names_map.items():
        try:
            portfolio_code, sub_id = composite_key.split('|', 1)
            rows_to_write.append({
                'PortfolioCode': portfolio_code,
                'SubPortfolioID': sub_id,
                'SubPortfolioName': name
            })
        except ValueError:
            continue

    sorted_rows = sorted(rows_to_write, key=lambda x: (x['PortfolioCode'], x['SubPortfolioID']))
    try:
        with open(SUBPORTFOLIO_NAMES_FILE, 'w', newline='', encoding='utf-8') as f:
            fieldnames = ['PortfolioCode', 'SubPortfolioID', 'SubPortfolioName']
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(sorted_rows)
    except IOError as e:
        print(f"❌ Error saving sub-portfolio names: {e}")

async def manage_subportfolio_names(
    portfolio_code: str,
    portfolio_config: Dict[str, Any],
    all_names_map: Dict[str, str],
    force_rename: bool = False
) -> bool:
    """Identifies sub-portfolios from config and prompts user to name them."""
    num_portfolios = 0
    try:
        num_portfolios = int(portfolio_config.get('num_portfolios', 0))
    except (ValueError, TypeError):
        print("Warning: 'num_portfolios' in config is not a valid number.")
        return False

    if num_portfolios == 0:
        return False

    sub_ids = [f'Sub-Portfolio {i}' for i in range(1, num_portfolios + 1)]

    print("\n--- Sub-Portfolio Naming ---")
    updated = False

    for sub_id in sub_ids:
        composite_key = f"{portfolio_code.lower()}|{sub_id}"
        current_name = all_names_map.get(composite_key, sub_id)

        if force_rename or (composite_key not in all_names_map):
            prompt = f"Enter name for '{sub_id}' of portfolio '{portfolio_code}' (current: '{current_name}', press Enter to keep): "
            custom_name = input(prompt).strip()

            if custom_name and custom_name != current_name:
                all_names_map[composite_key] = custom_name
                updated = True
            elif composite_key not in all_names_map:
                all_names_map[composite_key] = sub_id
        elif composite_key not in all_names_map:
            all_names_map[composite_key] = sub_id

    if updated:
        _save_all_subportfolio_names(all_names_map)
        print("✔ Sub-portfolio names saved.")

    return updated

# --- NEW HIERARCHICAL PERFORMANCE FUNCTIONS ---

def _build_nested_performance_dict(run_data: List[Dict[str, Any]], live_prices: Dict[str, float]) -> Dict:
    """Builds a nested dictionary from the flat run data."""
    root = {'children': {}, 'positions': [], 'initial_value': 0.0, 'current_value': 0.0}

    for row in run_data:
        ticker = row.get('Ticker')
        if not ticker or ticker == 'Cash':
            continue

        try:
            path_str = row.get('SubPortfolioPath', '')
            path_parts = [part.strip() for part in path_str.split('>') if part.strip()]
            
            saved_shares = float(row['Shares'])
            initial_value = float(row['ActualMoneyAllocation'])
            
            live_price = live_prices.get(ticker)
            current_value = (saved_shares * live_price) if live_price is not None else initial_value

            pnl = current_value - initial_value
            pnl_percent = (pnl / initial_value) * 100 if initial_value > 0 else 0

            position_data = {
                'ticker': ticker, 'initial_value': initial_value, 'current_value': current_value,
                'pnl': pnl, 'pnl_percent': pnl_percent
            }

            current_node = root
            for part in path_parts:
                if part not in current_node['children']:
                    current_node['children'][part] = {'children': {}, 'positions': [], 'initial_value': 0.0, 'current_value': 0.0}
                current_node = current_node['children'][part]
                current_node['initial_value'] += initial_value
                current_node['current_value'] += current_value
            
            current_node['positions'].append(position_data)
            root['initial_value'] += initial_value
            root['current_value'] += current_value

        except (ValueError, TypeError, KeyError) as e:
            print(f"  -> ⚠️ Warning: Could not process saved row for '{ticker}'. Error: {e}. Skipping.")
    
    return root['children']

def _display_performance_recursively(performance_nodes: Dict, indent: int = 0):
    """Recursively displays the performance data."""
    indent_str = "  " * indent
    for name, data in sorted(performance_nodes.items()):
        total_pnl = data['current_value'] - data['initial_value']
        total_pnl_pct = (total_pnl / data['initial_value']) * 100 if data['initial_value'] > 0 else 0

        header = f"**{name}**"
        stats = f"Initial: ${data['initial_value']:,.2f} | Current: ${data['current_value']:,.2f} | P&L: ${total_pnl:,.2f} ({total_pnl_pct:.2f}%)"
        print(f"\n{indent_str}{header} | {stats}")
        
        if data['positions']:
            position_table = [
                [p['ticker'], f"${p['initial_value']:,.2f}", f"${p['current_value']:,.2f}", f"${p['pnl']:,.2f}", f"{p['pnl_percent']:.2f}%"]
                for p in sorted(data['positions'], key=lambda x: x['initial_value'], reverse=True)
            ]
            table_str = tabulate(position_table, headers=["Ticker", "Initial Value", "Current Value", "P&L ($)", "P&L (%)"], tablefmt="pretty")
            indented_table_str = "\n".join([f"{indent_str}  {line}" for line in table_str.splitlines()])
            print(indented_table_str)
        
        if data['children']:
            _display_performance_recursively(data['children'], indent + 1)

async def _load_portfolio_origin_data(portfolio_code: str) -> Dict[str, Dict[str, float]]:
    """Loads the permanent origin data for a specific portfolio."""
    origin_data = {}
    if not os.path.exists(TRACKING_ORIGIN_FILE):
        return origin_data
    try:
        with open(TRACKING_ORIGIN_FILE, 'r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row['PortfolioCode'] == portfolio_code:
                    try:
                        origin_data[row['Ticker']] = {
                            'shares': float(row['Shares']),
                            'price': float(row['Price'])
                        }
                    except (ValueError, TypeError):
                        continue
    except Exception as e:
        print(f"⚠️ Warning: Could not process origin data file: {e}")
    return origin_data

async def display_all_time_performance(
    portfolio_code: str, 
    new_run_data: List[Dict[str, Any]], 
    old_run_data: Optional[List[Dict[str, Any]]], 
    portfolio_config: Dict[str, Any], 
    names_map: Dict[str, str],
    live_rh_holdings: Optional[Dict[str, float]] = None
) -> Dict[str, float]:
    """Calculates and displays all-time performance, preferring live RH shares if available."""
    print("\n--- All-Time Performance & Holdings Analysis ---")
    origin_data = await _load_portfolio_origin_data(portfolio_code)
    
    ticker_to_sub_id_map = _get_subportfolio_map_from_config(portfolio_config)
    ticker_to_sub_name_map = {ticker: names_map.get(sub_id, sub_id) for ticker, sub_id in ticker_to_sub_id_map.items()}

    # --- DETERMINE CURRENT HOLDINGS ---
    # Prioritize Live Robinhood Data for "Current Shares"
    current_holdings = {}
    if live_rh_holdings:
        current_holdings = live_rh_holdings
    else:
        # Fallback to new_run_data (calculated tailored holdings) or old data
        current_holdings = {h['ticker']: float(h.get('shares', 0)) for h in new_run_data if h.get('ticker') != 'Cash'}
    
    previous_holdings = {h['Ticker']: float(h.get('Shares', 0)) for h in old_run_data if h.get('Ticker') != 'Cash'} if old_run_data else {}
    
    all_held_tickers = set(current_holdings.keys()) | set(previous_holdings.keys())
    if origin_data:
        all_held_tickers = all_held_tickers | set(origin_data.keys())

    tasks = [calculate_ema_invest(ticker, ema_interval=2, is_called_by_ai=True) for ticker in all_held_tickers]
    live_price_results = await asyncio.gather(*tasks)
    live_prices = {ticker: res[0] for ticker, res in zip(all_held_tickers, live_price_results) if res and res[0] is not None}

    if not origin_data:
        print("No origin data found. This table will be populated after the first run is saved.")
        return live_prices

    all_time_data_by_sub = defaultdict(list)
    sub_portfolio_pnl_totals = defaultdict(float)
    grand_total_pnl = 0.0

    for ticker in sorted(list(all_held_tickers)):
        origin = origin_data.get(ticker)
        if not origin: continue

        live_price = live_prices.get(ticker)
        if live_price is None: continue

        origin_shares = origin['shares']
        origin_price = origin['price']
        origin_value = origin_shares * origin_price
        
        # Use the decided current holdings source
        current_shares = current_holdings.get(ticker, 0.0)
            
        all_time_pnl = (live_price - origin_price) * origin_shares
        
        all_time_pnl_pct = (all_time_pnl / origin_value) * 100 if origin_value > 0 else 0
        share_change = current_shares - origin_shares
        
        table_row = [
            ticker, f"{origin_price:.2f}", f"{live_price:.2f}", f"{origin_shares:.2f}",
            f"{current_shares:.2f}", f"{share_change:+.2f}", f"${all_time_pnl:,.2f}", f"{all_time_pnl_pct:.2f}%"
        ]
        
        sub_name = ticker_to_sub_name_map.get(ticker, "Unassigned")
        all_time_data_by_sub[sub_name].append(table_row)
        sub_portfolio_pnl_totals[sub_name] += all_time_pnl
        grand_total_pnl += all_time_pnl

    if not all_time_data_by_sub:
        print("Could not calculate all-time performance data.")
        return live_prices

    for sub_name, table_data in sorted(all_time_data_by_sub.items()):
        print(f"\n**--- {sub_name} ---**")
        print(tabulate(table_data, headers=["Ticker", "Origin Price", "Live Price", "Origin Shares", "Current Shares", "Share +/-", "All-Time P&L ($)", "All-Time P&L (%)"], tablefmt="pretty"))
        total_pnl_for_sub = sub_portfolio_pnl_totals[sub_name]
        print(f"Sub-Portfolio Total P&L: ${total_pnl_for_sub:,.2f}")

    print("\n" + "="*50)
    print(f"**Entire Portfolio All-Time Total P&L: ${grand_total_pnl:,.2f}**")
    print("="*50)
    
    return live_prices

def generate_allocation_comparison_chart(old_run: List[Dict], new_run: List[Dict], portfolio_code: str) -> Optional[str]:
    """Generates a grouped bar chart comparing old and new dollar allocations."""
    print("📊 Generating allocation comparison chart...")
    old_alloc = {row['Ticker']: float(row.get('ActualMoneyAllocation', 0)) for row in old_run if row['Ticker'] != 'Cash'}
    new_alloc = {row['ticker']: float(row.get('actual_money_allocation', 0)) for row in new_run}

    all_tickers = sorted(list(set(old_alloc.keys()) | set(new_alloc.keys())))
    if not all_tickers:
        print("No allocation data to plot.")
        return None

    old_values = [old_alloc.get(t, 0) for t in all_tickers]
    new_values = [new_alloc.get(t, 0) for t in all_tickers]
    
    x = np.arange(len(all_tickers))
    width = 0.35

    plt.style.use('dark_background')
    fig, ax = plt.subplots(figsize=(16, 8))
    
    rects1 = ax.bar(x - width/2, old_values, width, label='Old Allocation', color='#4E79A7')
    rects2 = ax.bar(x + width/2, new_values, width, label='New Allocation', color='#F28E2B')

    ax.set_ylabel('USD ($) Allocation', color='white')
    ax.set_title(f'Allocation Comparison for Portfolio: {portfolio_code}', color='white', fontsize=16)
    ax.set_xticks(x)
    ax.set_xticklabels(all_tickers, rotation=45, ha="right", color='white')
    ax.legend(facecolor='black', edgecolor='white', labelcolor='white')
    ax.grid(True, axis='y', color='dimgray', linestyle='--', linewidth=0.5, alpha=0.7)
    ax.tick_params(axis='y', colors='white')

    def autolabel(rects):
        for rect in rects:
            height = rect.get_height()
            if height > 0:
                ax.annotate(f'${height:,.0f}', xy=(rect.get_x() + rect.get_width() / 2, height),
                            xytext=(0, 3), textcoords="offset points", ha='center', va='bottom', color='lightgrey', fontsize=8)

    autolabel(rects1)
    autolabel(rects2)

    fig.tight_layout()
    filename = f"tracking_comparison_{portfolio_code}_{uuid.uuid4().hex[:6]}.png"
    plt.savefig(filename, facecolor='black', edgecolor='black')
    plt.close(fig)
    print(f"📂 Chart saved: {filename}")
    return filename

async def handle_comparison_subcommand():
    """Handles the logic for the '/tracking comparison' subcommand."""
    print("\n--- Portfolio Comparison ---")
    code1 = input("Enter the first portfolio code to compare: ").strip()
    code2 = input("Enter the second portfolio code to compare: ").strip()

    if not code1 or not code2:
        print("❌ Error: Both portfolio codes are required.")
        return

    filepath1 = _get_custom_portfolio_run_csv_filepath(code1)
    filepath2 = _get_custom_portfolio_run_csv_filepath(code2)

    errors = []
    if not os.path.exists(filepath1):
        errors.append(f"  - No saved run found for '{code1}'.")
    if not os.path.exists(filepath2):
        errors.append(f"  - No saved run found for '{code2}'.")

    if errors:
        print("\n❌ Comparison cannot proceed due to missing data:")
        for error in errors:
            print(error)
        return

    run_data1 = await _load_portfolio_run(code1)
    run_data2 = await _load_portfolio_run(code2)
    
    try:
        holdings1 = {row['Ticker']: float(row['Shares']) for row in run_data1 if row.get('Ticker') != 'Cash' and row.get('Shares') != '-'}
        holdings2 = {row['Ticker']: float(row['Shares']) for row in run_data2 if row.get('Ticker') != 'Cash' and row.get('Shares') != '-'}
    except (ValueError, TypeError) as e:
        print(f"❌ Error processing share data: {e}")
        return

    all_tickers = sorted(list(set(holdings1.keys()) | set(holdings2.keys())))
    comparison_table = []

    for ticker in all_tickers:
        shares1 = holdings1.get(ticker, 0.0)
        shares2 = holdings2.get(ticker, 0.0)
        change = shares1 - shares2
        status = ""

        if shares1 > 0 and shares2 == 0: status = f"Only in {code1}"
        elif shares2 > 0 and shares1 == 0: status = f"Only in {code2}"
        elif np.isclose(shares1, shares2): status = "Equal Holdings"
        elif shares1 > shares2: status = f"More in {code1}"
        else: status = f"Less in {code1}"
            
        comparison_table.append([ticker, f"{shares1:.2f}", f"{shares2:.2f}", f"{change:+.2f}", status])
        
    print(f"\n--- Comparison of Holdings: '{code1}' vs. '{code2}' ---")
    if not comparison_table:
        print("No stock holdings found in either portfolio to compare.")
    else:
        headers = ["Ticker", f"Shares in {code1}", f"Shares in {code2}", f"Difference ({code1} - {code2})", "Status"]
        print(tabulate(comparison_table, headers=headers, tablefmt="pretty"))

# --- Main Handler for /tracking ---
async def handle_tracking_command(args: List[str]):
    """Handles the /tracking command."""
    print("\n--- /tracking Command ---")
    if not args:
        print("Usage: /tracking <portfolio_code | comparison> [name]")
        return

    if args[0].lower() == 'comparison':
        await handle_comparison_subcommand()
        return

    portfolio_code = args[0]
    subcommand = args[1].lower() if len(args) > 1 else None
    all_names_map = _load_all_subportfolio_names()
    
    portfolio_config = await load_portfolio_config(portfolio_code)
    if not portfolio_config:
        return

    if subcommand == 'name':
        await manage_subportfolio_names(portfolio_code, portfolio_config, all_names_map, force_rename=True)
        return

    old_run_data = await _load_portfolio_run(portfolio_code)

    print("⏳ Connecting to Robinhood to fetch current portfolio value & positions...")
    
    # --- FETCH LIVE ROBINHOOD DATA ---
    rh_equity = await asyncio.to_thread(get_robinhood_equity)
    live_rh_holdings = await asyncio.to_thread(get_robinhood_holdings)
    
    if live_rh_holdings:
        print(f"✔ Successfully loaded {len(live_rh_holdings)} active positions from Robinhood.")
    else:
        print("⚠️ Could not load live holdings (or portfolio is empty).")

    suggested_value = None
    if rh_equity > 0:
        suggested_value = math.floor(rh_equity * 0.98)
        print(f"✔ Robinhood Portfolio Value Fetched: ${rh_equity:,.2f}")
        print(f"  -> Suggested Tailoring Value (98%): ${suggested_value:,.2f}")
    else:
        print("⚠️ Could not fetch Robinhood value (or login failed). Proceeding with manual input.")

    if old_run_data and any(not row.get('SubPortfolio') or row.get('SubPortfolio') == 'Unassigned' for row in old_run_data):
        ticker_to_sub_map = _get_subportfolio_map_from_config(portfolio_config)
        for row in old_run_data:
            if not row.get('SubPortfolio') or row.get('SubPortfolio') == 'Unassigned':
                row['SubPortfolio'] = ticker_to_sub_map.get(row['Ticker'], 'Unassigned')

    await manage_subportfolio_names(portfolio_code, portfolio_config, all_names_map, force_rename=False)

    if old_run_data:
        print("\n--- Performance Since Last Save ---")
        tickers_to_fetch = [row['Ticker'] for row in old_run_data if row.get('Ticker') != 'Cash']
        tasks = [calculate_ema_invest(ticker, ema_interval=2, is_called_by_ai=True) for ticker in tickers_to_fetch]
        live_price_results = await asyncio.gather(*tasks, return_exceptions=True)
        live_prices_temp = {
            ticker: res[0]
            for ticker, res in zip(tickers_to_fetch, live_price_results)
            if not isinstance(res, Exception) and res and res[0] is not None
        }
        nested_performance_data = _build_nested_performance_dict(old_run_data, live_prices_temp)
        if nested_performance_data:
            _display_performance_recursively(nested_performance_data)

    print("\n--- Generating New Portfolio Recommendation ---")
    
    frac_shares_config = portfolio_config.get('frac_shares', 'false').lower() == 'true'
    frac_prompt = f"Use fractional shares? (yes/no, config default: {frac_shares_config}): "
    frac_input = input(frac_prompt).lower().strip()
    
    use_frac_shares_new = frac_shares_config
    if frac_input == 'yes': use_frac_shares_new = True
    elif frac_input == 'no': use_frac_shares_new = False

    val_prompt = "Enter total portfolio value"
    if suggested_value:
        val_prompt += f" (default: {suggested_value})"
    val_prompt += ": "
    
    val_input = input(val_prompt).strip()
    
    new_total_value = 0.0
    if not val_input and suggested_value:
        new_total_value = float(suggested_value)
    else:
        try:
            new_total_value = float(val_input)
            if new_total_value <= 0: raise ValueError
        except ValueError:
            print("Invalid value. Aborting.")
            return

    _, _, new_cash, new_run_data = await process_custom_portfolio(
        portfolio_data_config=portfolio_config,
        tailor_portfolio_requested=True,
        frac_shares_singularity=use_frac_shares_new,
        total_value_singularity=new_total_value,
        is_custom_command_simplified_output=False, 
        is_called_by_ai=False,
        names_map=all_names_map
    )

    # Pass live_rh_holdings to prefer live data for the summary table
    live_prices_for_adjustments = await display_all_time_performance(
        portfolio_code, new_run_data, old_run_data, portfolio_config, all_names_map, live_rh_holdings
    )

    print("⚙️ Verifying Order Minimums & Constraints...")
    
    # --- CRITICAL: DETERMINE SOURCE OF TRUTH FOR "CURRENT HOLDINGS" ---
    old_holdings_map = {}
    
    if live_rh_holdings:
        print("ℹ️ Using LIVE Robinhood holdings for trade calculation.")
        old_holdings_map = live_rh_holdings
    elif old_run_data:
        print("ℹ️ Using SAVED CSV run data for trade calculation (Live RH unavailable).")
        old_holdings_map = {h['Ticker']: float(h.get('Shares', 0)) for h in old_run_data if h['Ticker'] != 'Cash'}
    else:
        print("ℹ️ No previous holdings data found. Assuming all positions are new.")

    adjusted_count = 0
    for row in new_run_data:
        ticker = row.get('ticker')
        if not ticker or ticker == 'Cash': continue
        
        price = live_prices_for_adjustments.get(ticker)
        if not price: continue

        raw_target_shares = float(row.get('shares', 0))
        
        # --- Strict BYDDY Rounding Check ---
        if ticker.upper() == 'BYDDY':
            raw_target_shares = round(raw_target_shares)
            row['shares'] = str(raw_target_shares)
            row['actual_money_allocation'] = str(raw_target_shares * price)
        
        current_shares = old_holdings_map.get(ticker, 0.0)
        diff = raw_target_shares - current_shares
        
        if diff > 0:
            trade_value = diff * price
            if trade_value < 1.00:
                is_fractional = use_frac_shares_new and (ticker.upper() != 'BYDDY')
                step = 0.01 if is_fractional else 1.0
                while (diff * price) < 1.00:
                    diff += step
                
                new_target_shares = current_shares + diff
                row['shares'] = str(new_target_shares)
                row['actual_money_allocation'] = str(new_target_shares * price)
                adjusted_count += 1
            # BYDDY check is redundant here but safe to keep for logic flow
            elif ticker.upper() == 'BYDDY' and raw_target_shares != float(row['shares']):
                 row['shares'] = str(raw_target_shares)
                 row['actual_money_allocation'] = str(raw_target_shares * price)
    
    if adjusted_count > 0:
        print(f"-> Adjusted {adjusted_count} orders to meet minimum $1.00 execution size.")

    trades_to_execute = [] 
    comparison_table_str = ""

    # Always generate comparison if we have target data
    if new_run_data:
        print("\n--- Comparison of Holdings (Current vs. New Target) ---")
        
        # Determine source label
        source_label = "Live Shares" if live_rh_holdings else "Old Saved Shares"
        
        old_holdings = old_holdings_map
        new_holdings = {h['ticker']: float(h.get('shares', 0)) for h in new_run_data}
        all_tickers = sorted(list(set(old_holdings.keys()) | set(new_holdings.keys())))
        
        comparison_table = []
        for ticker in all_tickers:
            old_s = old_holdings.get(ticker, 0)
            new_s = new_holdings.get(ticker, 0)
            change = new_s - old_s
            
            status = ""
            if old_s == 0 and new_s > 0: status = "New"
            elif new_s == 0 and old_s > 0: status = "Removed"
            elif not np.isclose(change, 0): status = "Modified"
                
            if status:
                comparison_table.append([ticker, f"{old_s:.2f}", f"{new_s:.2f}", f"{change:+.2f}", status])
                if change > 0:
                    trades_to_execute.append({'ticker': ticker, 'side': 'buy', 'quantity': abs(change)})
                elif change < 0:
                    trades_to_execute.append({'ticker': ticker, 'side': 'sell', 'quantity': abs(change)})
        
        if comparison_table:
            comparison_table_str = tabulate(comparison_table, headers=["Ticker", source_label, "New Target", "Change", "Status"], tablefmt="pretty")
            print(comparison_table_str)
        else:
            print("No changes in holdings between the current state and the new recommendation.")
            
        if old_run_data:
            generate_allocation_comparison_chart(old_run_data, new_run_data, portfolio_code)

    print("\n--- 📧 Trade Recommendation Email ---")
    email_choice = input("Would you like to receive an email with these trade recommendations? (yes/no): ").lower().strip()
    if email_choice == 'yes':
        email_subject = f"Tracking Update: {portfolio_code} Trade Recommendations"
        email_body = (f"Tracking Analysis for Portfolio: {portfolio_code}\n"
                      f"Total Value Used: ${new_total_value:,.2f}\n\n"
                      f"--- Recommended Trades ---\n"
                      f"{comparison_table_str if comparison_table_str else 'No trades recommended.'}\n\n"
                      f"End of Report.")
        await send_notification(email_subject, email_body)

    executed = False
    if trades_to_execute:
        print(f"\n🚀 Detected {len(trades_to_execute)} potential rebalancing trades.")
        exec_input = input(">>> Execute these trades on Robinhood? (yes/no): ").lower().strip()
        
        if exec_input == 'yes':
            # Run execution (Now captures returned modified trades)
            # We pass live_rh_holdings so execution command doesn't have to fetch them again blindly
            executed_trades = await asyncio.to_thread(execute_portfolio_rebalance, trades_to_execute, live_rh_holdings)
            
            if executed_trades:
                print("\n💾 Trades executed. Updating run data with actual execution quantities...")
                
                # Update new_run_data with the actual executed quantities
                executed_map = {t['ticker']: float(t['quantity']) for t in executed_trades}
                
                for row in new_run_data:
                    ticker = row.get('ticker')
                    if ticker and ticker in executed_map:
                        trade = next((t for t in executed_trades if t['ticker'] == ticker), None)
                        if trade:
                            executed_qty = float(trade['quantity'])
                            old_qty = old_holdings_map.get(ticker, 0.0)
                            
                            final_qty = 0.0
                            if trade['side'] == 'buy':
                                final_qty = old_qty + executed_qty
                            else: # sell
                                final_qty = old_qty - executed_qty
                                if final_qty < 0: final_qty = 0 # Safety
                            
                            row['shares'] = str(final_qty)
                            # Update money alloc estimation based on live price
                            live_p = live_prices_for_adjustments.get(ticker, 0.0)
                            if live_p > 0:
                                row['actual_money_allocation'] = str(final_qty * live_p)

                await _save_custom_portfolio_run_to_csv(
                    portfolio_code=portfolio_code,
                    tailored_stock_holdings=new_run_data,
                    final_cash=new_cash,
                    total_portfolio_value_for_percent_calc=new_total_value
                )
                print(f"✔ Run saved successfully.")
                executed = True
            else:
                 print("Execution cancelled or failed.")
        else:
            print("Trade execution skipped.")

    if not executed:
        overwrite_input = input("\nOverwrite last saved run with these new results? (yes/no): ").lower().strip()
        if overwrite_input == 'yes':
            await _save_custom_portfolio_run_to_csv(
                portfolio_code=portfolio_code,
                tailored_stock_holdings=new_run_data,
                final_cash=new_cash,
                total_portfolio_value_for_percent_calc=new_total_value
            )
            print(f"✔ New run for portfolio '{portfolio_code}' has been saved.")
        else:
            print("Last saved run was not changed.")
        
    print("\n/tracking analysis complete.")