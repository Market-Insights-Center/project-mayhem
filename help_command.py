# --- Imports for help_command ---
import time as py_time
from typing import List
import json
import os

# --- Default & Constant Definitions ---
COMMAND_STATES_FILE = 'command_states.json'

DEFAULT_DISABLED_MSG = "Command '/{command}' is currently disabled by the developer."
DEFAULT_LIMIT_MSG = "Command '/{command}' has reached its usage limit of {limit_count} per {period}. Please try again later."

MASTER_COMMAND_LIST = [
    "favorites", "counter", "briefing", "web", "strategies", "futures", "heatmap",
    "powerscore", "compare", "optimize", "sentiment", "fundamentals", "sector",
    "options", "monitor", "reportgeneration", "spear", "risk", "history", "invest",
    "custom", "quickscore", "breakout", "market", "cultivate", 
    "assess A", "assess B", "assess C", "assess D", "assess E",
    "simulation", "backtest", "macdforecast", "mlforecast", "ai", "voice", "dev",
    "tracking", "fairvalue", "derivative", "prometheus", "kronos", "nexus"
]

def load_command_states():
    """Loads command states, usage limits, presets, and custom messages from a JSON file."""
    default_states = {
        "startup_animation_enabled": True,
        "commands": {cmd: True for cmd in MASTER_COMMAND_LIST},
        "usage_limits": {},
        "disabled_command_message": DEFAULT_DISABLED_MSG,
        "limit_reached_message": DEFAULT_LIMIT_MSG,
        "presets": {}
    }

    if not os.path.exists(COMMAND_STATES_FILE):
        save_command_states(default_states)
        return default_states
    
    try:
        with open(COMMAND_STATES_FILE, 'r') as f:
            loaded_states = json.load(f)
            for key, value in default_states.items():
                if key not in loaded_states:
                    loaded_states[key] = value
            if 'commands' not in loaded_states:
                 loaded_states['commands'] = {cmd: True for cmd in MASTER_COMMAND_LIST}
            for cmd in MASTER_COMMAND_LIST:
                if cmd not in loaded_states['commands']:
                    loaded_states['commands'][cmd] = True
            return loaded_states
    except (json.JSONDecodeError, IOError):
        return default_states

def save_command_states(states):
    """Saves the current command states to the JSON file."""
    try:
        with open(COMMAND_STATES_FILE, 'w') as f:
            json.dump(states, f, indent=2)
    except IOError as e:
        print(f"Error saving command states: {e}")

def _delete_preset(states, preset_name):
    """Deletes a specified preset after confirmation."""
    print(f"\n--- Deleting Preset: {preset_name} ---")
    if preset_name not in states.get('presets', {}):
        print(f"Error: Preset '{preset_name}' does not exist.")
        return
    
    confirm = input(f"Are you sure you want to permanently delete preset '{preset_name}'? (yes/no): ").lower().strip()
    if confirm == 'yes':
        del states['presets'][preset_name]
        save_command_states(states)
        print(f"Preset '{preset_name}' has been deleted.")
    else:
        print("Deletion cancelled.")

def _edit_existing_preset(states, preset_name):
    """Interactive CLI to edit an existing command preset."""
    if preset_name not in states.get('presets', {}):
        print(f"Error: Preset '{preset_name}' does not exist.")
        return

    print(f"\n--- Editing Preset: {preset_name} ---")
    preset_data = states['presets'][preset_name]

    edited_data = {
        "description": preset_data.get('description', ''),
        "commands": preset_data.get('commands', {}).copy(),
        "usage_limits": preset_data.get('usage_limits', {}).copy(),
        "disabled_command_message": preset_data.get('disabled_command_message'),
        "limit_reached_message": preset_data.get('limit_reached_message')
    }

    for cmd in MASTER_COMMAND_LIST:
        if cmd not in edited_data["commands"]:
            edited_data["commands"][cmd] = True

    # Step 1: Edit description
    new_desc = input(f"Enter new description (current: '{edited_data['description']}'): ").strip()
    if new_desc:
        edited_data['description'] = new_desc

    # Step 2: Edit command states
    print("\n--- Edit Command States ---")
    while True:
        for i, cmd in enumerate(MASTER_COMMAND_LIST, 1):
            status = "Enabled" if edited_data["commands"].get(cmd, True) else "Disabled"
            print(f"[{i:2d}] /{cmd:<20} - {status}")
        toggle_input = input("Enter numbers to toggle, or 'done' to continue: ").strip().lower()
        if toggle_input == 'done': break
        try:
            indices_to_toggle = [int(n.strip()) - 1 for n in toggle_input.split(',') if n.strip()]
            for index in indices_to_toggle:
                if 0 <= index < len(MASTER_COMMAND_LIST):
                    cmd_to_toggle = MASTER_COMMAND_LIST[index]
                    edited_data["commands"][cmd_to_toggle] = not edited_data["commands"].get(cmd_to_toggle, True)
        except ValueError:
            print("Invalid input. Please enter numbers or 'done'.")

    # Step 3: Edit usage limits
    print("\n--- Edit Usage Limits ---")
    enabled_cmds = [cmd for cmd, is_enabled in edited_data["commands"].items() if is_enabled]
    if not enabled_cmds:
        print("No commands are enabled, clearing all usage limits.")
        edited_data["usage_limits"] = {}
    else:
        while True:
            print("\nEditing limits for enabled commands:")
            for i, cmd in enumerate(enabled_cmds, 1):
                limit_info = edited_data["usage_limits"].get(cmd)
                limit_str = f"Current: {limit_info['limit']}/{limit_info['period']}" if limit_info else "No limit set"
                print(f"[{i:2d}] /{cmd:<20} - {limit_str}")
            
            cmd_choice = input("\nEnter number to edit/add limit, or 'done': ").strip().lower()
            if cmd_choice == 'done': break
            try:
                index = int(cmd_choice) - 1
                if not (0 <= index < len(enabled_cmds)):
                    print("Invalid number."); continue
                cmd_to_limit = enabled_cmds[index]
                
                action = input(f"For /{cmd_to_limit}, enter new limit (e.g., '10'), 'remove', or press Enter to cancel: ").strip().lower()
                if not action: continue
                if action == 'remove':
                    if cmd_to_limit in edited_data["usage_limits"]:
                        del edited_data["usage_limits"][cmd_to_limit]
                        print(f"Limit for /{cmd_to_limit} removed.")
                    else: print("No limit to remove.")
                    continue
                
                if action.isdigit() and int(action) > 0:
                    limit_count = int(action)
                    valid_periods = ['minute', 'hour', 'day', 'week', 'month']
                    while True:
                        limit_period = input(f"Enter period ({', '.join(valid_periods)}): ").strip().lower()
                        if limit_period in valid_periods: break
                        else: print("Invalid period.")
                    edited_data["usage_limits"][cmd_to_limit] = {"limit": limit_count, "period": limit_period}
                    print(f"Limit for /{cmd_to_limit} set to {limit_count} per {limit_period}.")
                else:
                    print("Invalid input. Please enter a positive number or 'remove'.")
            except ValueError:
                print("Invalid input. Please enter a number or 'done'.")

    # Step 4: Edit custom messages
    print("\n--- Edit Custom Messages ---")
    current_disabled_msg = edited_data['disabled_command_message'] or 'Default'
    print(f"Current disabled message: {current_disabled_msg}")
    action_disabled = input("Enter new message, 'default' to reset, or press Enter to skip: ").strip()
    if action_disabled.lower() == 'default':
        edited_data['disabled_command_message'] = None
    elif action_disabled:
        edited_data['disabled_command_message'] = action_disabled

    current_limit_msg = edited_data['limit_reached_message'] or 'Default'
    print(f"Current limit message: {current_limit_msg}")
    action_limit = input("Enter new message, 'default' to reset, or press Enter to skip: ").strip()
    if action_limit.lower() == 'default':
        edited_data['limit_reached_message'] = None
    elif action_limit:
        edited_data['limit_reached_message'] = action_limit
    
    # Final Step: Save
    states['presets'][preset_name] = edited_data
    save_command_states(states)
    print(f"\n✅ Preset '{preset_name}' updated and saved successfully!")

def _create_new_preset(states, preset_name):
    """Interactive CLI to build a new command preset."""
    print(f"\n--- Creating New Preset: {preset_name} ---")
    description = input("Enter a short description for this preset: ").strip()
    if not description: description = f"Custom preset {preset_name}"

    # Step 2: Set command states
    print("\n--- Set Command States ---")
    new_commands = {cmd: True for cmd in MASTER_COMMAND_LIST}
    while True:
        for i, cmd in enumerate(MASTER_COMMAND_LIST, 1):
            status = "Enabled" if new_commands.get(cmd, True) else "Disabled"
            print(f"[{i:2d}] /{cmd:<20} - {status}")
        toggle_input = input("Enter numbers to toggle, or 'done' to continue: ").strip().lower()
        if toggle_input == 'done': break
        try:
            indices = [int(n.strip()) - 1 for n in toggle_input.split(',') if n.strip()]
            for index in indices:
                if 0 <= index < len(MASTER_COMMAND_LIST):
                    cmd_to_toggle = MASTER_COMMAND_LIST[index]
                    new_commands[cmd_to_toggle] = not new_commands.get(cmd_to_toggle, True)
        except ValueError:
            print("Invalid input. Please enter numbers or 'done'.")

    # Step 3: Set usage limits
    print("\n--- Set Usage Limits ---")
    new_limits = {}
    enabled_cmds = [cmd for cmd, is_enabled in new_commands.items() if is_enabled]
    if not enabled_cmds:
        print("No commands are enabled, skipping usage limit setup.")
    elif input("Set usage limits for this preset? (yes/no): ").lower() == 'yes':
        while True:
            print("\nEnabled commands available to limit:")
            for i, cmd in enumerate(enabled_cmds, 1): print(f"[{i:2d}] /{cmd}")
            indices_input = input("\nEnter comma-separated numbers for commands to limit (or 'done'): ").lower()
            if indices_input == 'done': break
            try:
                indices = [int(n.strip()) - 1 for n in indices_input.split(',') if n.strip()]
                valid_periods = ['minute', 'hour', 'day', 'week', 'month']
                for index in indices:
                    if not (0 <= index < len(enabled_cmds)):
                        print(f"Invalid number: {index + 1}. Skipping."); continue
                    cmd_to_limit = enabled_cmds[index]
                    while True:
                        limit_count_str = input(f"Enter usage limit for /{cmd_to_limit} (e.g., 5, 100): ").strip()
                        if limit_count_str.isdigit() and int(limit_count_str) > 0:
                            limit_count = int(limit_count_str); break
                        else: print("Invalid input. Please enter a positive whole number.")
                    while True:
                        limit_period = input(f"Enter period ({', '.join(valid_periods)}): ").strip().lower()
                        if limit_period in valid_periods: break
                        else: print(f"Invalid period. Must be one of: {', '.join(valid_periods)}.")
                    new_limits[cmd_to_limit] = {"limit": limit_count, "period": limit_period}
                    print(f"Limit for /{cmd_to_limit} set to {limit_count} per {limit_period}.")
            except ValueError:
                print("Invalid input. Please enter command numbers.")
    
    # Step 4: Set custom messages
    print("\n--- Set Custom Messages ---")
    new_disabled_msg = None
    if input("Set custom message for DISABLED commands? (yes/no): ").lower() == 'yes':
        new_disabled_msg = input("Enter message (use {command} as a placeholder): ").strip()

    new_limit_msg = None
    if input("Set custom message for LIMIT REACHED? (yes/no): ").lower() == 'yes':
        new_limit_msg = input("Enter message (use {command}, {limit_count}, {period}): ").strip()

    # Final Step: Assemble and save
    new_preset_data = {
        "description": description, "commands": new_commands, "usage_limits": new_limits,
        "disabled_command_message": new_disabled_msg, "limit_reached_message": new_limit_msg
    }
    if 'presets' not in states: states['presets'] = {}
    states['presets'][preset_name] = new_preset_data
    save_command_states(states)
    print(f"\n✅ Preset '{preset_name}' created and saved successfully!")

def handle_dev_menu():
    """Displays and handles the developer menu for toggling commands and managing presets."""
    while True:
        states = load_command_states()
        enabled_commands = states.get('commands', {})
        active_limits = states.get('usage_limits', {})
        
        print("\n--- Developer Command Menu ---")
        for i, cmd in enumerate(MASTER_COMMAND_LIST, 1):
            status = "Enabled" if enabled_commands.get(cmd, True) else "Disabled"
            limit_str = ""
            if cmd in active_limits:
                limit_info = active_limits[cmd]
                limit_str = f" (Limit: {limit_info.get('limit', 'N/A')}/{limit_info.get('period', 'N/A')})"
            print(f"[{i:2d}] /{cmd:<20} - {status}{limit_str}")

        animation_status = "Enabled" if states.get("startup_animation_enabled", True) else "Disabled"
        print("\n--- Other Settings ---")
        print(f"[-1] Startup Animation      - {animation_status}")
        
        print("\n--- Presets ---")
        presets_data = states.get('presets', {})
        if not presets_data:
            print("     No presets created yet.")
        else:
            for p_name, p_data in presets_data.items():
                print(f"     '{p_name}' - {p_data.get('description', 'No description')}")
        
        print("\n[ 0] Exit Developer Menu")
        print("----------------------------")
        
        prompt = (
            "Enter nums to toggle, preset action (e.g., p1, p2 edit, p3 delete),\n"
            "-1 for animation, or 0 to exit: "
        )
        user_input = input(prompt).strip().lower()
        user_input_parts = user_input.split()
        primary_action = user_input_parts[0] if user_input_parts else ""

        if primary_action == '0':
            print("Exiting developer menu."); break
        
        elif primary_action.startswith('p') and primary_action[1:].isdigit():
            preset_name = primary_action
            if len(user_input_parts) > 1:
                secondary_action = user_input_parts[1].lower()
                if secondary_action == 'edit':
                    _edit_existing_preset(states, preset_name)
                elif secondary_action == 'delete':
                    _delete_preset(states, preset_name)
                else:
                    print(f"Invalid action '{secondary_action}'. Use 'edit' or 'delete'.")
            else:
                if preset_name in presets_data:
                    print(f"Applying preset '{preset_name}'...")
                    settings = presets_data[preset_name]
                    states['commands'] = settings.get('commands', {cmd: True for cmd in MASTER_COMMAND_LIST})
                    states['usage_limits'] = settings.get('usage_limits', {})
                    states['disabled_command_message'] = settings.get('disabled_command_message') or DEFAULT_DISABLED_MSG
                    states['limit_reached_message'] = settings.get('limit_reached_message') or DEFAULT_LIMIT_MSG
                    save_command_states(states)
                    print(f"Preset '{preset_name}' applied successfully.")
                else:
                    print(f"Preset '{preset_name}' not found.")
                    if input(f"Create a new preset named '{preset_name}'? (yes/no): ").lower() == 'yes':
                        _create_new_preset(states, preset_name)
                    else: print("Preset creation cancelled.")
                    
        elif primary_action == '-1':
            states["startup_animation_enabled"] = not states.get("startup_animation_enabled", True)
            print(f"Startup animation is now {'Enabled' if states['startup_animation_enabled'] else 'Disabled'}.")
            save_command_states(states)
        else:
            try:
                indices_to_toggle = [int(n.strip()) - 1 for n in user_input.split(',') if n.strip()]
                toggled_any = False
                for index in indices_to_toggle:
                    if 0 <= index < len(MASTER_COMMAND_LIST):
                        cmd_to_toggle = MASTER_COMMAND_LIST[index]
                        enabled_commands[cmd_to_toggle] = not enabled_commands.get(cmd_to_toggle, True)
                        toggled_any = True
                if toggled_any:
                    states['commands'] = enabled_commands
                    save_command_states(states)
                    print("Command states updated.")
                elif user_input:
                    print("Invalid input. Please enter numbers, a preset, -1, or 0.")
            except ValueError:
                print("Invalid input. Please enter numbers separated by commas.")

def display_commands():
    """Displays the list of available commands that are currently enabled with a typing animation."""
    command_states = load_command_states()
    enabled_commands = command_states.get('commands', {})
    
    def is_enabled(cmd_name):
        return enabled_commands.get(cmd_name, True)

    command_lines = []
    command_lines.append("\nAvailable Commands:")
    command_lines.append("-------------------")
    command_lines.append("\nGENERAL Commands")
    command_lines.append("-------------------")
    
    if is_enabled("favorites"):
        command_lines.append("/favorites - View and overwrite your saved list of favorite tickers.")
        command_lines.append("  Description: Shows your current saved watchlist and prompts you to enter a new list, which will overwrite the old one. This list is used by the /briefing command.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /favorites   (Starts an interactive session to view and overwrite your list)")

    if is_enabled("counter"):
        command_lines.append("\n/counter - View command usage statistics and manage counting.")
        command_lines.append("  Description: Shows how many times each command has been used and allows you to enable or disable counting for specific commands.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /counter tally      (Displays a table of command usage counts)")
        command_lines.append("    /counter enabled    (Starts an interactive session to toggle command counting on/off)")

    if is_enabled("briefing"):
        command_lines.append("\n/briefing - Generate a comprehensive daily market summary.")
        command_lines.append("  Description: Provides a snapshot of market prices, risk scores, top/bottom movers, breakout activity, and watchlist performance.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /briefing   (The script will run all necessary analyses and display the report)")

    if is_enabled("web"):
        command_lines.append("\n/web - Interactive visualization of the M.I.C. company network.")
        command_lines.append("  Description: Replicates the MICWEB project. Prompts for a company, display mode, and stage filter, then generates a text report and a network graph image.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /web   (Starts the interactive session)")

    if is_enabled("strategies"):
        command_lines.append("\n/strategies - Run a specific trading strategy on any asset.")
        command_lines.append("  Description: Select a predefined strategy by number to get a Buy/Sell/Hold signal.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /strategies                  (Lists available strategies)")
        command_lines.append("    /strategies 1 NVDA           (Runs Trend Following on NVIDIA)")
        command_lines.append("    /strategies 2 GOLD           (Runs Mean Reversion on GOLD)")
        command_lines.append("    /strategies 3 /CL            (Runs Volatility Breakout on Crude Oil futures)")

    if is_enabled("futures"):
        command_lines.append("\n/futures - Get information and analysis on futures contracts.")
        command_lines.append("  Description: Provides specs, signals, or term structure analysis for common futures.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /futures info ES             (Get contract info for E-mini S&P 500 futures)")
        command_lines.append("    /futures analyze NQ          (Get a Buy/Sell/Hold signal for NASDAQ 100 futures)")
        command_lines.append("    /futures termstructure CL    (Analyze the forward curve for Crude Oil futures)")

    if is_enabled("derivative"):
        command_lines.append("\n/derivative - Find the best-fit polynomial and its derivative for a stock.")
        command_lines.append("  Description: Analyzes a stock's price history over multiple periods (Day, Week, Month, etc.), finds the polynomial that best fits the data, and calculates the rate of change (derivative) at the most recent point. Saves a plot for each period.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /derivative TSLA              (Analyzes Tesla's price history and calculates derivatives)")

    if is_enabled("heatmap"):
        command_lines.append("\n/heatmap - Generate a correlation heatmap for a set of stocks.")
        command_lines.append("  Description: Fetches 1-year daily returns and creates a heatmap of the Pearson correlation matrix.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /heatmap AAPL MSFT GOOG       (For a specific list of tickers)")
        command_lines.append("    /heatmap I-SPY                (For an entire index: SPY, QQQ, DIA, RUT)")
        command_lines.append("    /heatmap P-4                  (For a saved custom portfolio code)")

    if is_enabled("powerscore"):
        command_lines.append("\n/powerscore - Generate a comprehensive 'PowerScore' for a single stock.")
        command_lines.append("  Description: Calculates a weighted score from 7 different modules (/risk, /assess, /fundamentals, /quickscore, /sentiment, and /mlforecast) to provide a holistic rating from 0-100.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /powerscore NVDA 2              (Runs PowerScore for NVIDIA with sensitivity 2)")

    if is_enabled("compare"):
        command_lines.append("\n/compare - Perform a head-to-head analysis of multiple stocks.")
        command_lines.append("  Description: Gathers PowerScore (sensitivity 2) and fundamental data for a list of tickers, displays them in a comparison table, and generates a 1-year normalized performance chart against SPY.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /compare NVDA AMD TSM       (Compares NVIDIA, AMD, and TSM)")

    if is_enabled("optimize"):
        command_lines.append("\n/optimize - Find optimal portfolio weights for a set of stocks.")
        command_lines.append("  Description: Uses the PyPortfolioOpt library to calculate the portfolio with the maximum Sharpe ratio (best risk-adjusted return).")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /optimize AAPL GOOG JPM XOM     (For a specific list of tickers)")
        command_lines.append("    /optimize P-4                   (For a saved custom portfolio code)")

    if is_enabled("sentiment"):
        command_lines.append("\n/sentiment - Perform AI-powered sentiment analysis on a stock.")
        command_lines.append("  Description: Scrapes recent news headlines and social media posts, then uses the Gemini AI")
        command_lines.append("               to generate a sentiment score (-1.0 to 1.0), a summary, and key positive/negative keywords.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /sentiment NVDA               (Runs sentiment analysis for NVIDIA)")

    if is_enabled("fundamentals"):
        command_lines.append("\n/fundamentals - Get a fundamental score for a single ticker.")
        command_lines.append("  Description: Fetches P/E, Revenue Growth, Debt-to-Equity, and Profit Margin to calculate a score out of 100.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /fundamentals GOOG              (Get fundamental score for Google)")

    if is_enabled("sector"):
        command_lines.append("\n/sector - Perform a deep-dive analysis on an industry sector or the whole market.")
        command_lines.append("  Description: Analyzes a group of stocks based on a GICS code, sector name, or the keyword 'Market'. It identifies top companies by market cap, their performance, calculates aggregate health scores, finds top/bottom performers by Invest Score, and performs sentiment analysis.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /sector 4530                                     (Analyzes the sector for GICS code 4530)")
        command_lines.append("    /sector \"Semiconductors & Semiconductor Equipment\" (Analyzes by full name)")
        command_lines.append("    /sector Market                                   (Analyzes all stocks in the GICS database)")

    if is_enabled("options"):
        command_lines.append("\n/options - Advanced options analysis and strategy modeling.")
        command_lines.append("  Description: An interactive, menu-driven tool to analyze options. Features include single contract analysis with 3D visualizations of price and delta, P/L diagrams for common strategies, and IV-based short strangle recommendations.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /options   (Starts the interactive options analysis menu)")

    if is_enabled("monitor"):
        command_lines.append("\n/monitor - Manage real-time price & score alerts.")
        command_lines.append("  Description: Add, list, or remove real-time alerts for when a stock's price or its INVEST score crosses a certain point.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /monitor add NVDA price > 1000            (Adds a new price alert)")
        command_lines.append("    /monitor add AAPL invest 2 < 40         (Adds an INVEST score alert for daily sensitivity)")
        command_lines.append("    /monitor list                             (Shows all active alerts)")
        command_lines.append("    /monitor remove 1                         (Removes the alert with index 1)")

    if is_enabled("reportgeneration"):
        command_lines.append("\n/reportgeneration - Generate a detailed, custom investment report.")
        command_lines.append("  Description: Initiates an interactive session to build a tailored investment plan based on your goals, risk, and portfolio value. The final output is a formatted .txt file.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /reportgeneration   (Starts the interactive report generation process)")

    if is_enabled("spear"):
        command_lines.append("\nSPEAR Commands")
        command_lines.append("-------------------")
        command_lines.append("/spear - Predict a stock's performance around its upcoming earnings report.")
        command_lines.append("  Description: Uses the SPEAR model to generate an earnings prediction based on financial and market sentiment inputs.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /spear   (The script will then prompt you for all necessary inputs step-by-step)")

    if is_enabled("risk") or is_enabled("history"):
        command_lines.append("\nRISK Commands")
        command_lines.append("-------------------")
    if is_enabled("risk"):
        command_lines.append("/risk - Perform R.I.S.K. module calculations, display results, and save data.")
        command_lines.append("  Description: Calculates a suite of market risk indicators and determines a market signal.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /risk                           (Performs standard R.I.S.K. calculation and saves data)")
        command_lines.append("    /risk eod                       (Performs End-of-Day R.I.S.K. calculation and saves EOD specific data)")
    if is_enabled("history"):
        command_lines.append("\n/history - Generate and save historical R.I.S.K. module graphs.")
        command_lines.append("  Description: Creates visual charts of historical R.I.S.K. indicators.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /history                        (Generates and saves all R.I.S.K. history graphs)")

    invest_cmds = ["invest", "custom", "tracking", "quickscore", "breakout", "market", "cultivate", "fairvalue", "assess A", "assess B", "assess C", "assess D", "assess E", "nexus"]
    if any(is_enabled(c) for c in invest_cmds):
        command_lines.append("\nINVEST Commands")
        command_lines.append("-------------------")
    if is_enabled("invest"):
        command_lines.append("/invest - Analyze multiple stocks based on EMA sensitivity and amplification.")
        command_lines.append("  Description: Prompts for EMA sensitivity, amplification, number of sub-portfolios,")
        command_lines.append("               tickers for each, and their weights. Can optionally tailor to a portfolio value.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /invest   (The script will then prompt you for all necessary inputs step-by-step)")
    if is_enabled("fairvalue"):
        command_lines.append("\n/fairvalue - Estimate a stock's fair value based on price vs. INVEST score changes.")
        command_lines.append("  Description: Calculates a 'Valuation Factor' by comparing the percentage change of a stock's price to the percentage change of its INVEST score over a period. This factor is then used to estimate a 'Fair Price'.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /fairvalue NVDA 1y              (Calculates fair value for NVIDIA over 1 year)")
        command_lines.append("    /fairvalue MSFT 3mo             (Calculates fair value for Microsoft over 3 months)")
    if is_enabled("custom"):
        command_lines.append("\n/custom - Run portfolio analysis using a saved code, create/save a new one, or save legacy data.")
        command_lines.append("  Description: Manages custom portfolio configurations. Running a portfolio automatically saves/overwrites")
        command_lines.append("               its detailed tailored output. The '3725' option is for a legacy combined percentage save.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /custom MYPORTFOLIO             (Runs portfolio 'MYPORTFOLIO', creates if new, saves detailed run output)")
        command_lines.append("    /custom #                       (Creates a new portfolio with the next available numeric code, saves detailed run output)")
        command_lines.append("    /custom MYPORTFOLIO 3725        (Saves legacy combined percentage data for 'MYPORTFOLIO' after prompting for date)")
    if is_enabled("tracking"):
        command_lines.append("\n/tracking - Track the performance and evolution of a custom portfolio.")
        command_lines.append("  Description: Loads the last saved run of a portfolio, calculates its P&L against live prices,")
        command_lines.append("               generates a new recommendation, and provides a detailed comparison of changes.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /tracking MYPORTFOLIO           (Starts the tracking and comparison process for 'MYPORTFOLIO')")
    if is_enabled("nexus"):
        command_lines.append("\n/nexus - Manage meta-portfolios combining standard portfolios and dynamic commands.")
        command_lines.append("  Description: Creates and tracks 'Nexus' portfolios which are composed of other Portfolio Codes")
        command_lines.append("               or dynamic output from commands like Market, Breakout, and Cultivate.")
        command_lines.append("               Features full tracking, email notifications, and Robinhood execution.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /nexus NEXUS_CODE               (Runs or creates the Nexus portfolio)")
    if is_enabled("quickscore"):
        command_lines.append("\n/quickscore - Get quick scores and graphs for a single ticker.")
        command_lines.append("  Description: Provides EMA-based investment scores (Weekly, Daily, Hourly) and generates price/EMA graphs for one stock.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /quickscore AAPL                (Get scores and graphs for Apple Inc.)")
        command_lines.append("    /quickscore MSFT                (Get scores and graphs for Microsoft Corp.)")
    if is_enabled("breakout"):
        command_lines.append("\n/breakout - Run breakout analysis or save current breakout data.")
        command_lines.append("  Description: Identifies stocks with strong breakout potential or saves the current list of breakout stocks historically.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /breakout                       (Runs a new breakout analysis and saves the current findings)")
        command_lines.append("    /breakout 3725                  (Saves the current breakout_tickers.csv data to the historical database, prompts for date)")
    if is_enabled("market"):
        command_lines.append("\n/market - Display S&P 500 market scores or save full S&P 500 market data.")
        command_lines.append("  Description: Provides an overview of S&P 500 stock scores or saves detailed data for a chosen EMA sensitivity.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /market                         (Prompts for EMA sensitivity to display S&P 500 scores)")
        command_lines.append("    /market 2                       (Directly displays S&P 500 scores using Daily EMA sensitivity)")
        command_lines.append("    /market 3725                    (Prompts for sensitivity and date to save full S&P 500 market data)")
    if is_enabled("cultivate"):
        command_lines.append("\n/cultivate - Craft a Cultivate portfolio or save its data.")
        command_lines.append("  Description: Generates a diversified portfolio based on 'Cultivate' strategy codes A or B, portfolio value, and share preference.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /cultivate A 10000 yes          (Run Cultivate Code A for $10,000 value with fractional shares)")
        command_lines.append("    /cultivate B 50000 no           (Run Cultivate Code B for $50,000 value without fractional shares)")
        command_lines.append("    /cultivate A 25000 yes 3725     (Generate data for Cultivate Code A, $25k, frac. shares, then prompts for date to save)")
    
    assess_any_enabled = any(is_enabled(c) for c in ["assess A", "assess B", "assess C", "assess D", "assess E"])
    if assess_any_enabled:
        command_lines.append("\n/assess - Assess stock volatility, portfolio risk, etc., based on different codes.")
        command_lines.append("  Description: Performs various financial assessments.")
        command_lines.append("    A (Stock Volatility): Analyzes individual stock volatility against user's risk tolerance.")
        command_lines.append("      CLI Usage: /assess A AAPL,GOOG 1Y 3  (Assess Apple and Google over 1 year with risk tolerance 3)")
        command_lines.append("                 /assess A TSLA 3M 5       (Assess Tesla over 3 months with risk tolerance 5)")
        command_lines.append("    B (Manual Portfolio Risk): Calculates Beta/Correlation for a manually entered portfolio.")
        command_lines.append("      CLI Usage: /assess B 1y              (Script will prompt for tickers/shares/cash for a 1-year backtest)")
        command_lines.append("                 /assess B 5y              (Prompt for holdings for a 5-year backtest)")
        command_lines.append("    C (Custom Portfolio Risk): Calculates Beta/Correlation for a saved custom portfolio configuration.")
        command_lines.append("      CLI Usage: /assess C MYPORTFOLIO 25000 3y (Assess 'MYPORTFOLIO' tailored to $25,000 for a 3-year backtest)")
        command_lines.append("                 /assess C AlphaGrowth 100000 5y (Assess 'AlphaGrowth' at $100,000 for 5-year backtest)")
        command_lines.append("    D (Cultivate Portfolio Risk): Calculates Beta/Correlation for a generated Cultivate portfolio.")
        command_lines.append("      CLI Usage: /assess D A 50000 yes 5y    (Assess Cultivate Code A, $50k, frac. shares, 5-year backtest)")
        command_lines.append("                 /assess D B 10000 no 1y     (Assess Cultivate Code B, $10k, no frac. shares, 1-year backtest)")
        command_lines.append("    E (Portfolio Backtesting): Runs a historical simulation for a saved portfolio between two dates.")
        command_lines.append("      CLI Usage: /assess E MYPORTFOLIO 2020-01-01 2023-01-01 (Backtest 'MYPORTFOLIO' from 2020 to 2023)")
        command_lines.append("                 /assess E AlphaGrowth 2022-06-01 2024-06-01 (Backtest 'AlphaGrowth' for 2 years)")

    if any(is_enabled(c) for c in ["simulation", "backtest", "macdforecast", "mlforecast"]):
        command_lines.append("\nTIME Commands")
        command_lines.append("-------------------")
    if is_enabled("simulation"):
        command_lines.append("/simulation - Run an interactive historical stock market simulation.")
        command_lines.append("  Description: Selects random stocks over a random 5-year period, allowing you to buy/sell based on historical weekly data.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /simulation   (Starts the interactive simulation setup)")
        command_lines.append("    Simulation Commands: Buy/Sell [shares|dollars|max] [name], Set speed [X]x, End simulation")
    if is_enabled("backtest"):
        command_lines.append("\n/backtest - Backtest a specific trading strategy on a stock with customizable parameters.")
        command_lines.append("  Description: Runs a historical backtest for a ticker using a defined strategy and optional parameters.")
        command_lines.append("               Calculates the strategy's return vs. buy-and-hold and generates a detailed performance chart.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /backtest <TICKER> <strategy> <period> [params...]")
        command_lines.append("  Strategies & Parameters:")
        command_lines.append("    MA_crossover [short_window (def:50)] [long_window (def:200)]")
        command_lines.append("      └ ex: /backtest AAPL MA_crossover 5y 20 100")
        command_lines.append("    RSI [period (def:14)] [buy_level (def:30)] [sell_level (def:70)]")
        command_lines.append("      └ ex: /backtest TSLA RSI 2y 21 25 75")
        command_lines.append("    BUSD [buy_pct (def:10)] [sell_pct (def:10)]")
        command_lines.append("      └ ex: /backtest NVDA BUSD 1y 5 10")
    if is_enabled("macdforecast"):
        command_lines.append("\n/macdforecast - Forecasts stock price based on MACD CTC analysis.")
        command_lines.append("  Description: Uses MACD convergence/divergence changes to forecast a future price and date.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /macdforecast AAPL MSFT           (Runs forecast for Apple and Microsoft)")
        command_lines.append("    /macdforecast                     (Prompts for tickers if none are provided)")
    if is_enabled("mlforecast"):
        command_lines.append("\n/mlforecast - Predicts short-term price movement using a machine learning model.")
        command_lines.append("  Description: Uses a RandomForest model trained on technical indicators to forecast if a stock's price")
        command_lines.append("               is likely to be higher or lower in 5 trading days.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /mlforecast AAPL                (Runs the ML forecast for Apple Inc.)")

    if any(is_enabled(c) for c in ["ai", "voice", "dev", "prometheus", "kronos"]):
        command_lines.append("\nAI & Voice Commands")
        command_lines.append("-------------------")
    if is_enabled("ai"):
        command_lines.append("/ai - Interact with Cognis, the AI, using natural language.")
        command_lines.append("  CLI Usage Examples:")
        command_lines.append("    /ai show me the breakout stocks then quickscore the top one")
        command_lines.append("    /ai run my custom portfolio 'AlphaPicks' and tailor it to $75000 with fractional shares")
        command_lines.append("    /ai what is the market signal today?")
        command_lines.append("    /ai find all stocks in the Energy sector with a fundamental score above 85")
    if is_enabled("voice"):
        command_lines.append("\n/voice - Activate the voice assistant for hands-free commands.")
        command_lines.append("  Description: Starts a continuous listening session. Say the wake word 'Cognis' followed by your command.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /voice   (Activates the listener)")
        command_lines.append("  Voice Usage Example:")
        command_lines.append("    (you say) \"Cognis, give me the daily briefing.\"")
        command_lines.append("  To Exit: Say \"stop listening\", \"stop chat\" or \"end conversation.\"")
    if is_enabled("dev"):
        command_lines.append("\n/dev - Create, modify, and backtest trading strategies using natural language.")
        command_lines.append("  Description: An interactive suite for quantitative analysis. Describe a strategy to have the AI")
        command_lines.append("               generate the Python code, then run historical backtests on a single stock or on a")
        command_lines.append("               dynamic list of stocks from the AI screener.")
        command_lines.append("  CLI Usage:")
        command_lines.append("    /dev new \"<your strategy description>\"")
        command_lines.append("    /dev modify <file.py> \"<change request>\"")
        command_lines.append("    /dev backtest <file.py> on <TICKER> over <period>")
        command_lines.append("    /dev backtest <file.py> on SCREENER over <period>")
    if is_enabled("prometheus"):
        command_lines.append("\n/prometheus - Open the Prometheus Meta-AI shell.")
        command_lines.append("  Description: Access the AI's internal analysis shell. Used to manually trigger")
        command_lines.append("               workflow analysis, generate strategy recipes, or propose code improvements.")
        command_lines.append("  CLI Usage: /prometheus   (Enters the interactive Prometheus shell)")
    if is_enabled("kronos"):
        command_lines.append("\n/kronos - Open the Kronos Meta-Control shell.")
        command_lines.append("  Description: Access the supervisor shell to manage Prometheus's autonomous features,")
        command_lines.append("               schedule tasks, and run automated optimization and testing loops.")
        command_lines.append("  CLI Usage: /kronos   (Enters the interactive Kronos shell)")
    # --- END OF NEW SECTION ---

    command_lines.append("\nUtility Commands")
    command_lines.append("-------------------")
    command_lines.append("/help - Display this list of commands.")
    command_lines.append("/exit - Close the Market Insights Center Singularity.")
    command_lines.append("-------------------\n")

    full_command_text = "\n".join(command_lines)
    typing_speed = 0.0005
    for char_cmd in full_command_text:
        print(char_cmd, end="", flush=True)
        py_time.sleep(typing_speed)
    print()
    
async def handle_help_command(args: List[str], is_called_by_ai: bool = False):
    """
    Handles the /help command. Shows the dev menu if the special code is provided.
    """
    if args and args[0] == "013725":
        handle_dev_menu()
    else:
        display_commands()