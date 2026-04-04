"""
config.py — Central Configuration for the Black Cat Trading Bot
===============================================================
All settings are loaded from environment variables (your .env file).
NEVER hardcode API keys or secrets in this file.

To get started:
  1. Copy .env.example to .env
  2. Fill in all the values in .env
  3. Run: python webhook_server.py
"""

import os
from dotenv import load_dotenv

# Load .env file into environment
load_dotenv()

# =============================================================================
# ── BLOFIN API CREDENTIALS ────────────────────────────────────────────────────
# =============================================================================
# Get these from Blofin → Account → API Management
# IMPORTANT: When creating the API key, enable Futures Trading only.
#            Add your server's IP to the whitelist for extra security.

BLOFIN_API_KEY    = os.getenv("BLOFIN_API_KEY", "")
BLOFIN_API_SECRET = os.getenv("BLOFIN_API_SECRET", "")
BLOFIN_PASSPHRASE = os.getenv("BLOFIN_PASSPHRASE", "")

# Blofin REST API base URL
BLOFIN_BASE_URL   = os.getenv("BLOFIN_BASE_URL", "https://openapi.blofin.com")

# Demo/Paper mode: set to "true" in .env to test without real orders
BLOFIN_DEMO_MODE  = os.getenv("BLOFIN_DEMO_MODE", "true").lower() == "true"

# =============================================================================
# ── TELEGRAM NOTIFICATIONS ────────────────────────────────────────────────────
# =============================================================================
# 1. Message @BotFather on Telegram, create a new bot → get BOT_TOKEN
# 2. Message your new bot, then visit:
#    https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
#    to find your CHAT_ID (it looks like a large number)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID",   "")

# =============================================================================
# ── TRADING PARAMETERS ────────────────────────────────────────────────────────
# =============================================================================

# Maximum leverage to use (conservative: 2x)
# Blofin allows up to 100x — NEVER go above 5x without experience
LEVERAGE = int(os.getenv("LEVERAGE", "2"))

# Percentage of account balance to risk on each trade
# 1.0 = 1% (conservative), 2.0 = 2% (moderate)
RISK_PERCENT = float(os.getenv("RISK_PERCENT", "1.0"))

# Maximum number of simultaneously open positions
# Recommended: 1 — this is the safest setting for beginners
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "1"))

# ATR multiplier for stop-loss distance (must match your Pine Script setting)
ATR_MULT_SL = float(os.getenv("ATR_MULT_SL", "3.0"))

# Minimum risk:reward ratio (must match your Pine Script setting)
RR_RATIO = float(os.getenv("RR_RATIO", "1.5"))

# =============================================================================
# ── SYMBOL / INSTRUMENT MAPPING ───────────────────────────────────────────────
# =============================================================================
# TradingView uses different ticker names than Blofin.
# Add entries here for each market you want to trade.
#
# TradingView ticker  →  Blofin instId
#
# How to find Blofin instId:
#   GET https://openapi.blofin.com/api/v1/market/instruments
#   Look for "instId" field, e.g. "ETH-USDT"

SYMBOL_MAP = {
    # ETH perpetual futures
    "ETHUSDTPERP":  "ETH-USDT",     # TradingView BYBIT/Blofin format
    "ETHUSDT.P":    "ETH-USDT",     # TradingView Phemex format
    "ETHUSD":       "ETH-USDT",     # Generic
    "ETH/USDT":     "ETH-USDT",
    "ETH-USDT":     "ETH-USDT",     # Already in Blofin format

    # Add more pairs here if you expand the bot later:
    # "BTCUSDTPERP": "BTC-USDT",
    # "SOLUSDT":     "SOL-USDT",
}

# Default instrument if ticker not found in map
DEFAULT_INST_ID = os.getenv("DEFAULT_INST_ID", "ETH-USDT")

# =============================================================================
# ── WEBHOOK SERVER ────────────────────────────────────────────────────────────
# =============================================================================

# Host to listen on. Use "0.0.0.0" to accept from anywhere (needed for cloud),
# or "127.0.0.1" if running behind a reverse proxy (nginx/caddy).
SERVER_HOST = os.getenv("SERVER_HOST", "0.0.0.0")

# Port for the Flask webhook server
SERVER_PORT = int(os.getenv("SERVER_PORT", "5000"))

# Optional shared secret: TradingView adds this to the webhook URL as a header.
# Set WEBHOOK_SECRET in your .env, then in TradingView use:
#   Header name:  X-Webhook-Secret
#   Header value: your secret
# Leave empty to disable this security check (not recommended for live trading)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# =============================================================================
# ── LOGGING ───────────────────────────────────────────────────────────────────
# =============================================================================

# Path to the CSV file where all trades are logged
TRADE_LOG_FILE = os.getenv("TRADE_LOG_FILE", "trades.csv")

# =============================================================================
# ── VALIDATION ────────────────────────────────────────────────────────────────
# =============================================================================
# These checks print warnings on startup if critical settings are missing.

def print_config_summary():
    """Print a safe summary of current config (no secrets)."""
    print("\n── Configuration Summary ─────────────────────────────")
    print(f"  Demo mode    : {'YES (paper trading)' if BLOFIN_DEMO_MODE else 'NO — LIVE TRADING'}")
    print(f"  Blofin URL   : {BLOFIN_BASE_URL}")
    print(f"  API Key set  : {'YES' if BLOFIN_API_KEY else 'NO ⚠️'}")
    print(f"  Secret set   : {'YES' if BLOFIN_API_SECRET else 'NO ⚠️'}")
    print(f"  Passphrase   : {'SET' if BLOFIN_PASSPHRASE else 'NOT SET ⚠️'}")
    print(f"  Telegram     : {'Configured' if TELEGRAM_BOT_TOKEN else 'Not configured'}")
    print(f"  Leverage     : {LEVERAGE}x")
    print(f"  Risk/trade   : {RISK_PERCENT}%")
    print(f"  Max positions: {MAX_POSITIONS}")
    print(f"  Server       : {SERVER_HOST}:{SERVER_PORT}")
    print(f"  Trade log    : {TRADE_LOG_FILE}")
    print("──────────────────────────────────────────────────────\n")


if __name__ == "__main__":
    # Run this file directly to check your config: python config.py
    print_config_summary()
