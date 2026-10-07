"""Paths, universes and the local trader.json config (single home of every state path)."""

import os
import json
import datetime
from typing import Dict, Any


try:
    import zoneinfo
    ET_ZONE = zoneinfo.ZoneInfo("America/New_York")
except Exception:
    # Fallback to fixed Eastern Time (UTC-5 / UTC-4 approx)
    ET_ZONE = datetime.timezone(datetime.timedelta(hours=-5), "EST")

CONFIG_DIR = os.path.expanduser("~/.config/ai")
CACHE_DIR = os.path.expanduser("~/.cache/ai/trading")
os.makedirs(CACHE_DIR, exist_ok=True)
TRADING_STATE_FILE = os.path.join(CONFIG_DIR, "robinhood_trading_state.json")
VAULT_DIR = os.path.join(CONFIG_DIR, "trading_vault")
# Symbols listed here (one per line) are never sold by the automated strategy.
TRADING_HOLD_FILE = os.path.join(CONFIG_DIR, "trading_hold.txt")

# Multi-Sector High-Growth Universe (Curated by Sector)
SECTOR_UNIVERSES = {
    "AI_CHIPS_INFRA": ["NVDA", "AVGO", "ARM", "SMCI", "PLTR", "ANET", "AMD", "TSM", "ASML", "AMAT"],
    "CYBER_CLOUD_SAAS": ["PANW", "CRWD", "NET", "MDB", "DDOG", "SNOW", "MSFT", "GOOGL", "AMZN"],
    "BIOTECH_HEALTHCARE": ["LLY", "NVO", "VRTX", "REGN", "ISRG"],
    "FINTECH_CRYPTO_ENERGY": ["COIN", "HOOD", "RDW", "CEG", "VST", "TSLA"],
    "CORE_INDEX_ETFS": ["QQQ", "SPY", "SMH", "VTI"]
}

DEFAULT_WATCHLIST = [
    t for sector_tickers in SECTOR_UNIVERSES.values() for t in sector_tickers
]

# Balanced cross-sector watchlist representing Core ballast, AI/Chips, Cyber/Cloud, Healthcare, and Fintech
BALANCED_LIFECYCLE_WATCHLIST = [
    # Core Ballast ETFs
    "VTI", "QQQ",
    # AI & Semi Leaders
    "NVDA", "AVGO", "PLTR", "TSM",
    # Cyber & Cloud SaaS
    "CRWD", "PANW", "MSFT", "GOOGL",
    # Healthcare & Defensive Biotech
    "LLY", "NVO", "VRTX",
    # Fintech & Momentum
    "COIN", "TSLA"
]

def trader_config_path() -> str:
    """Local, untracked trader config (account mapping). Override with RH_TRADER_CONFIG."""
    return os.environ.get("RH_TRADER_CONFIG") or os.path.join(CONFIG_DIR, "trader.json")


def load_trader_config() -> Dict[str, Any]:
    """{"accounts": {"agentic": "...", "primary": "...", "roth": "..."}}; {} if missing or unreadable."""
    try:
        with open(trader_config_path(), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}

# Risk / compliance state files (kept here so every module and test patch sees one value)

PDT_TRACKER_FILE = os.path.join(os.path.expanduser("~/.config/ai"), "pdt_tracker.json")
CIRCUIT_BREAKER_FILE = os.path.join(os.path.expanduser("~/.config/ai"), "daily_circuit_breaker.json")
# Written when circuit breaker Tier 3 is confirmed; read by _momentum_rebalance_pulse to block orders for the session
SESSION_HALT_FLAG = os.path.join(os.path.expanduser("~/.config/ai"), ".monitor_flags", "tier3_halt_{date}.flag")
# Written when a PDT-deferred exit is pending; reviewed at pre-market briefing
DEFERRED_EXIT_FLAG_DIR = os.path.join(os.path.expanduser("~/.config/ai"), ".monitor_flags")

RISK_MONITOR_LOG = os.path.join(os.path.expanduser("~/.cache/ai"), "risk_monitor.log")
RISK_MONITOR_FLAGS = os.path.join(os.path.expanduser("~/.config/ai"), ".monitor_flags")
