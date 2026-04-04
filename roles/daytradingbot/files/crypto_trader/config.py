"""
config.py — Central configuration for the Kraken Day Trading Bot
================================================================
Settings load from environment variables (populated by the .env file
that Ansible deploys from daytradingbot.env.j2).

For local development: create a .env file in this directory.
For production: Ansible writes the .env from vault-encrypted vars.

NEVER hardcode API keys or secrets in this file.
"""

import os
from dotenv import load_dotenv

# Load .env file into environment (no-op if already set by systemd EnvironmentFile)
load_dotenv()

# ── Kraken Futures API ────────────────────────────────────────────────────────
KRAKEN_API_KEY    = os.getenv("KRAKEN_API_KEY", "")
KRAKEN_API_SECRET = os.getenv("KRAKEN_API_SECRET", "")
KRAKEN_TESTNET    = os.getenv("KRAKEN_TESTNET", "true").lower() == "true"

# ── Telegram Notifications ────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── Trading Parameters ────────────────────────────────────────────────────────
MIN_CONFLUENCE_SCORE     = int(os.getenv("MIN_CONFLUENCE_SCORE",     "60"))
RISK_PER_TRADE_PCT       = float(os.getenv("RISK_PER_TRADE_PCT",     "1.0"))
MAX_OPEN_POSITIONS       = int(os.getenv("MAX_OPEN_POSITIONS",       "3"))
STOP_LOSS_BUFFER_PCT     = float(os.getenv("STOP_LOSS_BUFFER_PCT",   "0.015"))
TRAILING_STOP_PCT        = float(os.getenv("TRAILING_STOP_PCT",      "0.03"))
SCAN_INTERVAL_MINUTES    = int(os.getenv("SCAN_INTERVAL_MINUTES",    "15"))
MIN_CONSOLIDATION_CANDLES = int(os.getenv("MIN_CONSOLIDATION_CANDLES", "8"))

# ── Symbols to watch ─────────────────────────────────────────────────────────
_symbols_env = os.getenv(
    "SYMBOLS",
    "ADA/USDT:USDT,ALGO/USDT:USDT,VET/USDT:USDT,XRP/USDT:USDT,BTC/USDT:USDT"
)
SYMBOLS = [s.strip() for s in _symbols_env.split(",") if s.strip()]

# ── Webhook Server ────────────────────────────────────────────────────────────
SERVER_HOST    = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT    = int(os.getenv("SERVER_PORT", "5000"))
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")
DRY_RUN        = os.getenv("DRY_RUN", "true").lower() == "true"

# ── Build config dict (compatible with existing module interfaces) ─────────────
def get_config() -> dict:
    """Return a config dict matching the format expected by analysis/trading modules."""
    return {
        "exchange": {
            "name":       "krakenfutures",
            "api_key":    KRAKEN_API_KEY,
            "api_secret": KRAKEN_API_SECRET,
            "testnet":    KRAKEN_TESTNET,
        },
        "symbols":                   SYMBOLS,
        "min_confluence_score":      MIN_CONFLUENCE_SCORE,
        "risk_per_trade_pct":        RISK_PER_TRADE_PCT,
        "max_open_positions":        MAX_OPEN_POSITIONS,
        "stop_loss_buffer_pct":      STOP_LOSS_BUFFER_PCT,
        "trailing_stop_pct":         TRAILING_STOP_PCT,
        "scan_interval_minutes":     SCAN_INTERVAL_MINUTES,
        "min_consolidation_candles": MIN_CONSOLIDATION_CANDLES,
        "dry_run":                   DRY_RUN,
        "webhook": {
            "secret": WEBHOOK_SECRET,
        },
        "alerts": {
            "telegram": {
                "enabled":   bool(TELEGRAM_BOT_TOKEN),
                "bot_token": TELEGRAM_BOT_TOKEN,
                "chat_id":   TELEGRAM_CHAT_ID,
            }
        },
    }
