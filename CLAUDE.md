# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

**M.I.C. Singularity** (Market Insights Center Singularity) is an interactive CLI application for stock market analysis, portfolio management, and AI-assisted investment insights. It runs as a persistent async event loop, dispatching slash commands to modular handler functions.

## Running the Application

```bash
python main_singularity.py
```

The app requires a `config.ini` file in the working directory (git-ignored). It will fail to start if missing.

There are no automated tests, no linter config, and no build step — the app is run directly.

## Architecture

### Entry Point & Main Loop

`main_singularity.py` contains the full application orchestration:

- Reads `config.ini` at module load time for all API keys and file paths
- Initializes global async primitives: `GEMINI_API_LOCK`, `YFINANCE_API_SEMAPHORE` (limit 8), `API_TASK_SEMAPHORE` (limit 8)
- `main_singularity()` runs an infinite `while True` input loop dispatching `/commands` to handler modules
- Routing priority: hardcoded special cases (`/ai`, `/voice`, `/dev`, `/report`, `/compare`, `/assess`, `/favorites`, `/help`, `/exit`, `/prometheus`) → `prometheus.toolbox` → unknown command fallback to AI chat

### Command Module Pattern

Every command lives in its own `*_command.py` file. Each exposes a `handle_<name>_command(args, ai_params, is_called_by_ai)` function:

- **`args`**: list of CLI tokens when called directly
- **`ai_params`**: dict when invoked by the Gemini AI tool system
- **`is_called_by_ai`**: bool that gates print statements and changes return type — functions return a string summary for AI, `None` for CLI

All blocking I/O (yfinance, file reads, matplotlib) is offloaded with `asyncio.to_thread()`.

### Key Helper Functions in `main_singularity.py`

Functions defined here are shared across command modules via import from the main file or re-implemented locally:

- `get_yf_download_robustly()` — yfinance wrapper with 3-attempt retry + exponential backoff
- `get_yf_data_singularity()` — returns a wide DataFrame of closing prices, one column per ticker
- `get_yfinance_info_robustly()` — fetches `.info` dict with semaphore and retry
- `find_and_screen_stocks()` — runs `screentest.py` as a subprocess, parses stdout JSON
- `build_gics_database_file()` — one-time scrape of Wikipedia S&P 500 list to build `gics_database.txt`
- `check_usage_limit()` — enforces per-command rate limits from `command_states.json`

### AI / Gemini Integration

The AI system (`ai_command.py`) wraps Google Gemini (`gemini-1.5-flash`) as a function-calling agent named "Nexus":

- `initialize_ai_components()` configures the model and builds `AVAILABLE_PYTHON_FUNCTIONS` — a dict mapping tool names to actual Python callables
- Tool declarations (`FunctionDeclaration`) are defined at the bottom of `main_singularity.py` and passed to Gemini
- When the user types free-form text (no `/` prefix), it enters an AI chat session; `end chat` clears the session history
- The AI can autonomously chain tool calls (e.g., call `get_user_preferences_tool` then `create_dynamic_investment_plan`)
- System prompt is loaded from `system_prompt.txt` at startup; a default is written if the file is missing

### Prometheus Sub-Shell

`prometheus_core.py` (not in this repo — external dependency) implements a `Prometheus` meta-AI class with its own `.toolbox` dict and interactive session. Commands not hardcoded in the main loop are routed through `prometheus.execute_and_log()`.

### Portfolio Code System

`portfolio_codes_database.csv` stores portfolio configurations. Each row is keyed by `portfolio_code`. `process_custom_portfolio()` in `invest_command.py` recursively resolves nested portfolio codes — a portfolio's ticker list can reference another portfolio code, which gets recursively expanded and weighted.

### Command State Management

`command_states.json` (runtime-generated, git-ignored) controls:
- Per-command enable/disable flags
- Per-command rate limits (with periods: `minute`, `hour`, `day`, `week`, `month`)
- Startup animation toggle
- Custom disabled/limit-reached messages
- Named presets (snapshots of the above config)

Managed interactively via `/help` → dev options, or `/dev`.

## Required Configuration Files

`config.ini` must contain these sections (git-ignored, never committed):

```ini
[API_KEYS]
GEMINI_API_KEY = ...

[FILE_PATHS]
PORTFOLIO_DB_FILE = portfolio_codes_database.csv
PORTFOLIO_OUTPUT_DIR = portfolio_outputs
BREAKOUT_TICKERS_FILE = breakout_tickers.csv
BREAKOUT_HISTORICAL_DB_FILE = breakout_historical_database.csv
CULTIVATE_INITIAL_METRICS_FILE = cultivate_initial_metrics.csv
CULTIVATE_T1_FILE = cultivate_ticker_list_one.csv
CULTIVATE_T_MINUS_1_FILE = cultivate_ticker_list_negative_one.csv
CULTIVATE_TF_FINAL_FILE = cultivate_ticker_list_final.csv
RISK_CSV_FILE = market_data.csv
RISK_EOD_CSV_FILE = risk_eod_data.csv
RISK_LOG_FILE = risk_calculations.log

[APP_SETTINGS]
TIMEZONE = US/Eastern
MARKET_HEDGING_TICKERS = SPY,DIA,QQQ
RESOURCE_HEDGING_TICKERS = GLD,SLV

[EMAIL_CONFIG]
SMTP_SERVER = ...
SMTP_PORT = 587
SENDER_EMAIL = ...
SENDER_PASSWORD = ...
RECIPIENT_EMAIL = ...
```

Static data files that **are** committed: `gics_map.txt`, `system_prompt.txt`, `gics_database.txt`.

## Runtime-Generated Files (git-ignored)

- `command_states.json` — command enable/disable/limit config
- `command_usage_counts.csv` — usage counters
- `alerts.csv` — active price alerts for `/monitor`
- `user_preferences.json` — saved AI user preferences
- `users_favorites.txt` — watchlist
- `*.csv`, `*.log`, `portfolio_outputs/` — analysis outputs

## Concurrency Conventions

- All command handlers are `async def`; blocking operations use `await asyncio.to_thread(...)`
- `YFINANCE_API_SEMAPHORE = asyncio.Semaphore(8)` throttles concurrent yfinance calls
- `GEMINI_API_LOCK = asyncio.Lock()` serializes Gemini API calls
- `monitor_command.py` runs `alert_worker()` as a background `asyncio.Task` (created at startup, cancelled on exit)

## Adding a New Command

1. Create `<name>_command.py` with `async def handle_<name>_command(args, ai_params=None, is_called_by_ai=False)`
2. Import it in `main_singularity.py` alongside the other command imports
3. Add routing in the `main_singularity()` loop (either hardcoded `elif command == "/<name>"` or via Prometheus toolbox)
4. Add the command name string to `MASTER_COMMAND_LIST` in `help_command.py` and `TRACKABLE_COMMANDS` in `counter_command.py`
5. If the AI should be able to call it, add a `FunctionDeclaration` entry and register it in `AVAILABLE_PYTHON_FUNCTIONS`
