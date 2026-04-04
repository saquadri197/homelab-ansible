"""
alerts/telegram.py
-------------------
Sends trade alerts and status updates via Telegram.

Setup (one-time):
  1. Message @BotFather on Telegram, create a bot, copy the token.
  2. Start a chat with your bot, then get your chat_id by visiting:
     https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
  3. Add both to config.yaml under alerts.telegram

Messages sent:
  - 🔔 Setup alert: confluence score hit threshold, about to trade
  - ✅ Trade opened: entry price, SL, leverage
  - 📈 Trailing stop updated: new SL level
  - 🛑 Trade closed: P&L summary
  - ⚠️  Error / risk warnings
"""

import requests
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramAlerter:
    def __init__(self, token: str, chat_id: str, enabled: bool = True):
        self.token   = token
        self.chat_id = chat_id
        self.enabled = enabled

    def send(self, message: str, silent: bool = False) -> bool:
        """Send a message. Returns True if successful."""
        if not self.enabled:
            logger.debug(f"Telegram disabled. Message: {message}")
            return True

        url     = TELEGRAM_API.format(token=self.token)
        payload = {
            "chat_id":              self.chat_id,
            "text":                 message,
            "parse_mode":           "HTML",
            "disable_notification": silent,
        }
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except requests.RequestException as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    # ── Formatted message helpers ──────────────────────────────────────────────

    def alert_confluence_found(self, symbol: str, score: int,
                                direction: str, signals: dict) -> None:
        """Alert when confluence score triggers a potential trade."""
        signal_lines = "\n".join(
            f"  • {k}: {v}" for k, v in signals.items()
        )
        msg = (
            f"🔔 <b>Setup Found: {symbol}</b>\n"
            f"Score: <b>{score}/100</b> | Direction: <b>{direction.upper()}</b>\n"
            f"\n<b>Signals:</b>\n{signal_lines}"
        )
        self.send(msg)

    def alert_trade_opened(self, symbol: str, direction: str,
                            entry: float, stop_loss: float,
                            quantity: float, leverage: int,
                            score: int) -> None:
        """Alert when a trade is executed."""
        sl_pct = abs(entry - stop_loss) / entry * 100
        msg = (
            f"✅ <b>Trade Opened: {symbol}</b>\n"
            f"Direction: <b>{direction.upper()}</b> | Leverage: <b>{leverage}x</b>\n"
            f"Entry: <code>{entry:.6f}</code>\n"
            f"Stop Loss: <code>{stop_loss:.6f}</code> ({sl_pct:.2f}% risk)\n"
            f"Quantity: <code>{quantity:.4f}</code>\n"
            f"Confluence Score: <b>{score}/100</b>\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        self.send(msg)

    def alert_trailing_stop_updated(self, symbol: str, direction: str,
                                     old_sl: float, new_sl: float,
                                     current_price: float) -> None:
        """Alert when the trailing stop is moved into profit."""
        pnl_pct = ((current_price - old_sl) / old_sl * 100
                   if direction == "long"
                   else (old_sl - current_price) / old_sl * 100)
        msg = (
            f"📈 <b>Trailing Stop Updated: {symbol}</b>\n"
            f"SL moved: <code>{old_sl:.6f}</code> → <code>{new_sl:.6f}</code>\n"
            f"Current price: <code>{current_price:.6f}</code>\n"
            f"Locked-in floor P&L: <b>+{pnl_pct:.2f}%</b>"
        )
        self.send(msg)

    def alert_trade_closed(self, symbol: str, direction: str,
                            entry: float, exit_price: float,
                            quantity: float, leverage: int) -> None:
        """Alert when a position is closed."""
        if direction == "long":
            raw_pct = (exit_price - entry) / entry * 100
        else:
            raw_pct = (entry - exit_price) / entry * 100

        leveraged_pct = raw_pct * leverage
        pnl_usd       = (exit_price - entry) * quantity * (1 if direction == "long" else -1)
        emoji = "🟢" if pnl_usd >= 0 else "🔴"

        msg = (
            f"{emoji} <b>Trade Closed: {symbol}</b>\n"
            f"Direction: {direction.upper()} | Leverage: {leverage}x\n"
            f"Entry: <code>{entry:.6f}</code> → Exit: <code>{exit_price:.6f}</code>\n"
            f"P&L: <b>{'+'if pnl_usd>=0 else ''}{pnl_usd:.2f} USDT "
            f"({leveraged_pct:+.2f}% leveraged)</b>"
        )
        self.send(msg)

    def alert_error(self, context: str, error: str) -> None:
        """Alert on errors."""
        msg = f"⚠️ <b>Error in {context}</b>\n<code>{error}</code>"
        self.send(msg)

    def alert_scan_summary(self, symbols_checked: int,
                            setups_found: int,
                            trades_opened: int) -> None:
        """Quiet summary after each scan cycle (sent silently)."""
        msg = (
            f"🔄 Scan complete: {symbols_checked} symbols checked | "
            f"{setups_found} setups found | {trades_opened} trades opened"
        )
        self.send(msg, silent=True)

    def alert_startup(self, symbols: list[str], mode: str) -> None:
        """Alert when the bot starts."""
        msg = (
            f"🚀 <b>Crypto Trader Bot Started</b>\n"
            f"Mode: <b>{mode}</b>\n"
            f"Watching: {', '.join(symbols)}\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        self.send(msg)


def build_alerter(config: dict) -> TelegramAlerter:
    """Build alerter from config."""
    tg = config.get("alerts", {}).get("telegram", {})
    return TelegramAlerter(
        token=tg.get("bot_token", ""),
        chat_id=tg.get("chat_id", ""),
        enabled=tg.get("enabled", False),
    )
