# --- Imports for counter_command ---
import os
import csv
import asyncio
import traceback
from typing import List
from tabulate import tabulate

# --- Constants (moved for self-containment) ---
COMMAND_COUNTS_FILE = 'command_usage_counts.csv'
COMMAND_STATUS_FILE = 'command_counting_status.csv'

# Master list of all trackable commands
TRACKABLE_COMMANDS = [
    "/ai", "/voice", "/monitor", "/options", "/reportgeneration", "/briefing", 
    "/spear", "/invest", "/custom", "/breakout", "/market", "/cultivate", 
    "/assess A", "/assess B", "/assess C", "/assess D", "/assess E",
    "/quickscore", "/risk", "/history", "/simulation", "/macdforecast", "/heatmap", 
    "/fundamentals", "/optimize", "/sentiment", "/mlforecast", "/powerscore", 
    "/compare", "/sector", "/favorites", "/backtest", "/web", "/help", "/counter",
    "/dev", "/futures", "/strategies", "/tracking", "/fairvalue", "/derivative", 
    "/prometheus", "/kronos", "/nexus" # <<< ADDED /nexus
]

# --- Counter System Logic (moved for self-containment) ---

def _load_command_data_sync(filepath: str, key_col: str, val_col: str) -> dict:
    """Synchronously loads command data from a CSV into a dictionary."""
    if not os.path.exists(filepath):
        return {}
    data_dict = {}
    try:
        with open(filepath, mode='r', newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                if row and key_col in row and val_col in row:
                    data_dict[row[key_col]] = row[val_col]
        return data_dict
    except Exception as e:
        print(f"Error loading data from {filepath}: {e}")
        return {}

def _save_command_data_sync(filepath: str, data_dict: dict, key_col: str, val_col: str):
    """Atomically saves command data from a dictionary to a CSV by writing to a temp file first."""
    temp_filepath = filepath + ".tmp"
    try:
        sorted_items = sorted(data_dict.items())
        with open(temp_filepath, mode='w', newline='', encoding='utf-8') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=[key_col, val_col])
            writer.writeheader()
            for key, val in sorted_items:
                writer.writerow({key_col: key, val_col: val})
        os.replace(temp_filepath, filepath)
    except Exception as e:
        print(f"Error saving data to {filepath}: {e}")
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)

def _initialize_counter_files_sync():
    """Synchronous part of the initialization for easier logic."""
    counts = _load_command_data_sync(COMMAND_COUNTS_FILE, 'command', 'count')
    statuses = _load_command_data_sync(COMMAND_STATUS_FILE, 'command', 'enabled')
    counts_updated, statuses_updated = False, False

    for cmd in TRACKABLE_COMMANDS:
        if cmd not in counts:
            counts[cmd] = 0
            counts_updated = True
        if cmd not in statuses:
            statuses[cmd] = 'True'
            statuses_updated = True
            
    if counts_updated:
        _save_command_data_sync(COMMAND_COUNTS_FILE, counts, 'command', 'count')
    if statuses_updated:
        _save_command_data_sync(COMMAND_STATUS_FILE, statuses, 'command', 'enabled')
    print("✔ Command counter is ready.")

async def initialize_counter_files():
    """Ensures the counter CSV files exist and are up-to-date with all trackable commands."""
    print("⚙️  Verifying command counter files...")
    await asyncio.to_thread(_initialize_counter_files_sync)

def _increment_command_count_sync(command_name: str):
    """Synchronous core logic for incrementing the command count."""
    statuses = _load_command_data_sync(COMMAND_STATUS_FILE, 'command', 'enabled')
    if statuses.get(command_name, 'False').lower() == 'true':
        counts = _load_command_data_sync(COMMAND_COUNTS_FILE, 'command', 'count')
        current_count = int(counts.get(command_name, 0))
        counts[command_name] = current_count + 1
        _save_command_data_sync(COMMAND_COUNTS_FILE, counts, 'command', 'count')

async def increment_command_count(command_name: str):
    """Increments the usage count for a command if its tracking is enabled."""
    try:
        await asyncio.to_thread(_increment_command_count_sync, command_name)
    except Exception as e:
        print(f"CRITICAL ASYNC WRAPPER ERROR in increment_command_count for '{command_name}': {e}")
        traceback.print_exc()

# --- Main Command Handler ---

async def handle_counter_command(args: List[str]):
    """Handles the /counter command to display usage tallies or manage tracking status."""
    if not args or args[0].lower() not in ['tally', 'enabled']:
        print("Usage: /counter <tally | enabled>")
        return

    action = args[0].lower()

    if action == 'tally':
        print("\n--- Command Usage Tally ---")
        counts = _load_command_data_sync(COMMAND_COUNTS_FILE, 'command', 'count')
        if not counts:
            print("No command usage has been recorded yet.")
            return
        table_data = [[cmd, count] for cmd, count in sorted(counts.items())]
        print(tabulate(table_data, headers=["Command", "Times Used"], tablefmt="pretty"))

    elif action == 'enabled':
        statuses = _load_command_data_sync(COMMAND_STATUS_FILE, 'command', 'enabled')
        while True:
            print("\n--- Command Counting Status ---")
            command_map = {i + 1: cmd for i, cmd in enumerate(TRACKABLE_COMMANDS)}
            table_data = [[num, cmd, "✅ Enabled" if statuses.get(cmd, 'False').lower() == 'true' else "❌ Disabled"] for num, cmd in command_map.items()]
            print(tabulate(table_data, headers=["#", "Command", "Status"], tablefmt="pretty"))
            user_input = input("\nEnter numbers to toggle, -1 to ENABLE ALL, -2 to DISABLE ALL, or 0 to exit: ").strip()
            
            should_save = False
            if user_input == '0': break
            elif user_input == '-1':
                for cmd in TRACKABLE_COMMANDS: statuses[cmd] = 'True'
                should_save = True
            elif user_input == '-2':
                for cmd in TRACKABLE_COMMANDS: statuses[cmd] = 'False'
                should_save = True
            else:
                try:
                    numbers_to_toggle = [int(n.strip()) for n in user_input.split(',')]
                    for num in numbers_to_toggle:
                        if num in command_map:
                            cmd_to_toggle = command_map[num]
                            statuses[cmd_to_toggle] = 'False' if statuses.get(cmd_to_toggle, 'False').lower() == 'true' else 'True'
                            should_save = True
                except ValueError: print("❌ Invalid input.")
            if should_save:
                _save_command_data_sync(COMMAND_STATUS_FILE, statuses, 'command', 'enabled')
