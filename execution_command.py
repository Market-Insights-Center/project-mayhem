# execution_command.py

import robin_stocks.robinhood as r
import configparser
import time
import pyotp
from typing import List, Dict, Any, Optional
import math

# Load Configuration
config = configparser.ConfigParser()
config.read('config.ini')

def login_to_robinhood():
    """Logs into Robinhood using credentials and auto-generates 2FA token."""
    try:
        username = config.get('ROBINHOOD', 'RH_USERNAME', fallback=None)
        password = config.get('ROBINHOOD', 'RH_PASSWORD', fallback=None)
        mfa_secret = config.get('ROBINHOOD', 'RH_MFA_CODE', fallback=None)

        if not username or not password:
            print("❌ Error: Robinhood credentials missing.")
            return False

        totp_code = None
        if mfa_secret:
            totp = pyotp.TOTP(mfa_secret)
            totp_code = totp.now()

        r.login(username, password, mfa_code=totp_code)
        return True
    except Exception as e:
        print(f"❌ Login failed: {e}")
        return False

def get_robinhood_equity() -> float:
    """Logs in and fetches the total equity of the account."""
    if not login_to_robinhood():
        return 0.0
    
    try:
        profile = r.profiles.load_portfolio_profile()
        if profile and 'equity' in profile:
            return float(profile['equity'])
    except Exception as e:
        print(f"❌ Error fetching Robinhood equity: {e}")
    
    return 0.0

def get_robinhood_holdings() -> Dict[str, float]:
    """
    Logs in and fetches current share holdings as {ticker: quantity}.
    Useful for ensuring rebalance calculations use LIVE data.
    """
    if not login_to_robinhood():
        return {}
    
    try:
        print("⏳ Fetching live positions from Robinhood API...")
        holdings_data = r.build_holdings()
        
        holdings_map = {}
        for ticker, data in holdings_data.items():
            if data and 'quantity' in data:
                try:
                    qty = float(data['quantity'])
                    if qty > 0:
                        holdings_map[ticker] = qty
                except ValueError:
                    continue
        
        return holdings_map

    except Exception as e:
        print(f"❌ Error fetching Robinhood holdings: {e}")
        return {}

def _get_single_holding(ticker: str) -> float:
    """Helper to fetch the exact shares held for a single ticker."""
    try:
        # 'build_holdings' is heavy, let's use account positions or build_holdings if needed
        # simpler: just use build_holdings again or filter. 
        # robin_stocks doesn't have a cheap 'get_shares(ticker)' so we reuse build_holdings
        # or we can try get_open_stock_positions
        data = r.build_holdings()
        if ticker in data:
            return float(data[ticker].get('quantity', 0.0))
    except Exception:
        pass
    return 0.0

def execute_portfolio_rebalance(trades: List[Dict[str, Any]], known_holdings: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
    """
    Executes trades. 
    - Sells: If fail due to count, retries with max available.
    - Buys: If fail due to funds, retries after all other trades.
    Returns the list of trades with UPDATED quantities if changes were made.
    """
    if not trades:
        print("No trades to execute.")
        return []

    print(f"\n--- 🏹 Robinhood Trade Execution ({len(trades)} orders) ---")
    
    confirm = input(f"⚠️  Are you sure you want to execute these {len(trades)} trades on Robinhood REAL MONEY account? (yes/no): ").lower().strip()
    if confirm != 'yes':
        print("🚫 Execution cancelled.")
        return []

    if not login_to_robinhood():
        return []

    # --- BATCH PRICE FETCH ---
    print("\n⏳ Fetching latest prices for all tickers to ensure accuracy...")
    price_map = {}
    try:
        all_tickers = list(set([t['ticker'] for t in trades]))
        quotes = r.stocks.get_latest_price(all_tickers, includeExtendedHours=False)
        
        if len(quotes) == len(all_tickers):
            for i, ticker in enumerate(all_tickers):
                try:
                    price_map[ticker] = float(quotes[i])
                except (ValueError, TypeError):
                    price_map[ticker] = 0.0
        else:
            print("⚠️ Warning: Quote count mismatch. Some prices may be zero.")
            
    except Exception as e:
        print(f"⚠️ Batch price fetch failed ({e}). Proceeding with individual lookups (slower).")

    print("\n🚀 Executing orders...")
    successful_trades = 0
    failed_trades = 0

    # Sort Sells first to free up cash
    trades.sort(key=lambda x: x['side'] == 'buy') 

    # Queue for buys that fail due to funding
    deferred_buys: List[int] = [] 

    # We iterate by index to modify in place
    # Using a while loop structure or just standard for loop. 
    # Standard for loop is fine, we will handle deferreds after.
    
    for i, trade in enumerate(trades):
        ticker = trade['ticker']
        raw_qty = float(trade['quantity'])
        side = trade['side']
        
        # --- 1. Enforce Integer constraints (BYDDY) ---
        is_integer_only = False
        if ticker.upper() == 'BYDDY':
            qty = int(round(raw_qty))
            is_integer_only = True
        else:
            qty = round(raw_qty, 6) 

        if qty <= 0: continue

        # --- 2. Get Price ---
        current_price = price_map.get(ticker, 0.0)
        if current_price == 0.0:
            try:
                price_info = r.stocks.get_latest_price(ticker, includeExtendedHours=False)
                current_price = float(price_info[0]) if price_info and price_info[0] else 0.0
            except Exception:
                current_price = 0.0

        print(f"   Processing: {side.upper()} {qty} {ticker}...", end=" ")

        # --- 3. Execution Loop ---
        adjustment_attempts = 0
        max_adjustments = 5
        trade_complete = False
        
        # Check constraints before loop
        if current_price > 0 and (qty * current_price) < 1.00:
             # Bump immediately
             step = 1.0 if is_integer_only else 0.01
             while (qty * current_price) < 1.00:
                 qty += step
             if not is_integer_only: qty = round(qty, 6)
             print(f"\n      ⚠️  Value < $1. Auto-adjusted to {qty} shares.", end=" ")

        while not trade_complete and adjustment_attempts < max_adjustments:
            
            max_network_retries = 3
            
            for attempt in range(max_network_retries):
                try:
                    order = None
                    if side == 'buy':
                        order = r.orders.order_buy_fractional_by_quantity(ticker, qty)
                    elif side == 'sell':
                        order = r.orders.order_sell_fractional_by_quantity(ticker, qty)
                    
                    if order is None:
                        raise ValueError("API returned None (Rate Limit?)")
                    
                    # Check throttled
                    if 'detail' in order and 'throttled' in str(order['detail']).lower():
                        raise ValueError(f"Rate Limited: {order['detail']}")

                    # --- SUCCESS ---
                    if 'id' in order:
                        exec_price = float(order.get('price') or current_price)
                        total_val = exec_price * qty
                        
                        print(f"\n   ✅ EXECUTED: {side.upper()} {qty} {ticker} @ ${exec_price:.2f}")
                        print(f"      Total Value: ${total_val:.2f} | Order ID: {order['id']}")
                        successful_trades += 1
                        trades[i]['quantity'] = qty # Update actual executed qty
                        trade_complete = True
                        break 
                    
                    # --- FAILURE HANDLING ---
                    error_str = str(order).lower()
                    
                    # A. MINIMUM $1 ERROR
                    if "at least $1" in error_str:
                        step = 1.0 if is_integer_only else 0.01
                        qty += step
                        if not is_integer_only: qty = round(qty, 6)
                        print(f"\n      ❌ Too small (<$1). Retrying with {qty}...", end=" ")
                        adjustment_attempts += 1
                        break # Break network loop, retry in adjustment loop

                    # B. SELL ERROR: NOT ENOUGH SHARES
                    # Common errors: "not enough shares", "holding", "sellable"
                    elif side == 'sell' and ("enough shares" in error_str or "shares" in error_str):
                        print(f"\n      ⚠️  Sell failed (Not enough shares). Checking live max...", end=" ")
                        
                        # Fetch authoritative quantity
                        actual_held = _get_single_holding(ticker)
                        if actual_held < qty and actual_held > 0:
                            qty = actual_held
                            print(f"Adjusted to {qty} (Max Available). Retrying...", end=" ")
                            adjustment_attempts += 1 # Count as adjustment
                            break # Retry execution
                        elif actual_held <= 0:
                            print(f"\n      ❌ You do not own {ticker}. Skipping.")
                            failed_trades += 1
                            trade_complete = True
                            break
                        else:
                            # We hold enough, but API rejected? 
                            print(f"\n      ❌ API rejected sell despite holdings. {order}")
                            failed_trades += 1
                            trade_complete = True
                            break

                    # C. BUY ERROR: INSUFFICIENT FUNDS
                    elif side == 'buy' and ("buying power" in error_str or "funds" in error_str):
                         print(f"\n      ⚠️  Insufficient Funds. Deferring trade to end of queue.", end=" ")
                         deferred_buys.append(i) # Store index to retry later
                         trade_complete = True # Mark "complete" for the main loop, so we move on
                         break

                    # D. OTHER ERRORS
                    elif 'detail' in order:
                        print(f"\n   ❌ Failed: {order['detail']}")
                        failed_trades += 1
                        trade_complete = True
                        break
                    elif 'non_field_errors' in order:
                        print(f"\n   ❌ Failed: {order['non_field_errors']}")
                        failed_trades += 1
                        trade_complete = True
                        break
                    else:
                        print(f"\n   ⚠️  Unknown response: {order}")
                        failed_trades += 1
                        trade_complete = True
                        break

                except Exception as e:
                    if attempt == max_network_retries - 1:
                        print(f"\n   ❌ Network Error {ticker}: {e}")
                        failed_trades += 1
                        trade_complete = True
                    else:
                        time.sleep(30)

            if trade_complete:
                break
            time.sleep(1)

        if trade_complete and successful_trades > 0:
            time.sleep(2) 

    # --- PROCESS DEFERRED BUYS ---
    if deferred_buys:
        print(f"\n\n🔄 Retrying {len(deferred_buys)} deferred buy orders (after Sells have cleared)...")
        
        for idx in deferred_buys:
            trade = trades[idx]
            ticker = trade['ticker']
            qty = float(trade['quantity']) # Use the value from the list (which might have been bumped)
            
            # Re-check price
            current_price = price_map.get(ticker, 0.0)
            
            print(f"   Retrying: BUY {qty} {ticker}...", end=" ")
            
            try:
                order = r.orders.order_buy_fractional_by_quantity(ticker, qty)
                if order and 'id' in order:
                    exec_price = float(order.get('price') or current_price)
                    print(f"\n   ✅ EXECUTED: BUY {qty} {ticker} @ ${exec_price:.2f}")
                    successful_trades += 1
                    trades[idx]['quantity'] = qty # Update
                else:
                    detail = order.get('detail') or order.get('non_field_errors') or "Unknown Error"
                    print(f"\n   ❌ Failed Final Attempt: {detail}")
                    failed_trades += 1
            except Exception as e:
                print(f"\n   ❌ Exception: {e}")
                failed_trades += 1
                
            time.sleep(2)

    print("-" * 50)
    print(f"Execution Complete. Success: {successful_trades} | Failed: {failed_trades}")
    print("-" * 50)
    
    r.logout()
    return trades