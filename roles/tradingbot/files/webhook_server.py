"""
webhook_server.py — TradingView → Blofin Automated Trading Bridge
=================================================================
Receives JSON alerts from TradingView, validates them, executes trades
on Blofin Futures, and sends Telegram notifications.

Run:
    python webhook_server.py

Endpoints:
    POST /webhook       — receives TradingView alerts
    GET  /health        — health-check (returns server status + open positions)
    GET  /trades        — returns last 20 trades from the log CSV

Requirements: see requirements.txt
Environment:  copy .env.example → .env and fill in your credentials
"""

import os
import sys
import csv
import json
import hmac
import base64
import hashlib
import logging
import datetime
import time
import traceback
from pathlib import Path

import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv

# ── Load environment variables from .env file ─────────────────────────────────
load_dotenv()

# ── Import our config ─────────────────────────────────────────────────────────
from config import (
    BLOFIN_API_KEY, BLOFIN_API_SECRET, BLOFIN_PASSPHRASE,
    BLOFIN_BASE_URL, BLOFIN_DEMO_MODE,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    LEVERAGE, RISK_PERCENT, MAX_POSITIONS,
    SYMBOL_MAP, TRADE_LOG_FILE, WEBHOOK_SECRET,
    SERVER_HOST, SERVER_PORT
)

# =============================================================================
# ── LOGGING SETUP ─────────────────────────────────────────────────────────────
# =============================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# =============================================================================
# ── FLASK APP ─────────────────────────────────────────────────────────────────
# =============================================================================

app = Flask(__name__)

# =============================================================================
# ── BLOFIN API CLIENT ─────────────────────────────────────────────────────────
# =============================================================================

class BlofinClient:
    """
    Minimal Blofin Futures REST API client.

    Authentication uses HMAC-SHA256. Every request needs:
      ACCESS-KEY        — your API key
      ACCESS-SIGN       — HMAC-SHA256(timestamp + method + path + body)
      ACCESS-TIMESTAMP  — Unix timestamp in milliseconds (string)
      ACCESS-PASSPHRASE — your API passphrase (set when creating the key)
    """

    def __init__(self):
        self.base_url   = BLOFIN_BASE_URL
        self.api_key    = BLOFIN_API_KEY
        self.api_secret = BLOFIN_API_SECRET
        self.passphrase = BLOFIN_PASSPHRASE
        self.demo       = BLOFIN_DEMO_MODE
        self.session    = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    # ── Signature generation ──────────────────────────────────────────────────
    def _sign(self, timestamp: str, method: str, path: str, body: str = "") -> str:
        """
        Create HMAC-SHA256 signature.
        Prehash string: timestamp + method.upper() + path + body
        """
        prehash = f"{timestamp}{method.upper()}{path}{body}"
        mac     = hmac.new(
            self.api_secret.encode("utf-8"),
            prehash.encode("utf-8"),
            hashlib.sha256,
        )
        return base64.b64encode(mac.digest()).decode("utf-8")

    def _headers(self, method: str, path: str, body: str = "") -> dict:
        """Build authenticated request headers."""
        ts = str(int(time.time() * 1000))
        return {
            "ACCESS-KEY":        self.api_key,
            "ACCESS-SIGN":       self._sign(ts, method, path, body),
            "ACCESS-TIMESTAMP":  ts,
            "ACCESS-PASSPHRASE": self.passphrase,
            "Content-Type":      "application/json",
        }

    # ── HTTP helpers ──────────────────────────────────────────────────────────
    def _get(self, path: str, params: dict = None) -> dict:
        """Authenticated GET request."""
        query = ""
        if params:
            query = "?" + "&".join(f"{k}={v}" for k, v in params.items())
        full_path = path + query
        url  = self.base_url + full_path
        resp = self.session.get(url, headers=self._headers("GET", full_path), timeout=10)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        """Authenticated POST request."""
        body_str = json.dumps(body)
        url      = self.base_url + path
        resp     = self.session.post(
            url,
            headers=self._headers("POST", path, body_str),
            data=body_str,
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    # ── Account ───────────────────────────────────────────────────────────────
    def get_balance(self) -> float:
        """
        Returns the total USDT equity in your futures account.
        We use this to size positions correctly (risk % of equity).
        """
        try:
            resp = self._get("/api/v1/account/balance")
            # Blofin returns a list of asset balances; find USDT
            if resp.get("code") == "0":
                for asset in resp.get("data", []):
                    if asset.get("currency", "").upper() == "USDT":
                        return float(asset.get("equity", 0))
            log.error("Balance fetch failed: %s", resp)
            return 0.0
        except Exception as e:
            log.error("get_balance error: %s", e)
            return 0.0

    # ── Positions ─────────────────────────────────────────────────────────────
    def get_open_positions(self, inst_id: str = None) -> list:
        """Returns list of open positions, optionally filtered by instrument."""
        try:
            params = {}
            if inst_id:
                params["instId"] = inst_id
            resp = self._get("/api/v1/account/positions", params)
            if resp.get("code") == "0":
                return resp.get("data", [])
            return []
        except Exception as e:
            log.error("get_open_positions error: %s", e)
            return []

    # ── Orders ────────────────────────────────────────────────────────────────
    def place_order(
        self,
        inst_id:   str,
        side:      str,   # "buy" or "sell"
        order_type: str,  # "market" or "limit"
        size:      str,   # quantity in contracts
        sl_price:  str = None,
        tp_price:  str = None,
        pos_side:  str = "net",  # "net" for one-way mode
    ) -> dict:
        """
        Place a futures order on Blofin.

        Parameters
        ----------
        inst_id    : instrument ID, e.g. "ETH-USDT"
        side       : "buy" (open long / close short) or "sell"
        order_type : "market" recommended for webhook bots
        size       : number of contracts (1 contract = 1 ETH on Blofin)
        sl_price   : stop-loss trigger price (string)
        tp_price   : take-profit trigger price (string)
        pos_side   : "net" for one-way margin mode (recommended)
        """
        body = {
            "instId":      inst_id,
            "marginMode":  "cross",       # Cross margin
            "positionSide": pos_side,
            "side":        side,
            "orderType":   order_type,
            "size":        size,
            "leverage":    str(LEVERAGE),
        }

        # Attach SL/TP if provided
        if sl_price:
            body["slTriggerPrice"] = sl_price
            body["slOrderPrice"]   = "-1"   # -1 = market order when triggered
        if tp_price:
            body["tpTriggerPrice"] = tp_price
            body["tpOrderPrice"]   = "-1"   # -1 = market order when triggered

        log.info("Placing order: %s", json.dumps(body, indent=2))

        if self.demo:
            log.warning("⚠️  DEMO MODE — order NOT sent to exchange")
            return {"code": "0", "data": [{"orderId": "DEMO_ORDER", "clOrdId": ""}]}

        try:
            resp = self._post("/api/v1/trade/order", body)
            return resp
        except Exception as e:
            log.error("place_order error: %s", e)
            raise

    def cancel_order(self, inst_id: str, order_id: str) -> dict:
        """Cancel an open order by ID."""
        body = {"instId": inst_id, "orderId": order_id}
        try:
            return self._post("/api/v1/trade/cancel-order", body)
        except Exception as e:
            log.error("cancel_order error: %s", e)
            raise

    def get_pending_orders(self, inst_id: str) -> list:
        """Returns all pending (open) orders for an instrument."""
        try:
            resp = self._get("/api/v1/trade/orders-pending", {"instId": inst_id})
            if resp.get("code") == "0":
                return resp.get("data", [])
            return []
        except Exception as e:
            log.error("get_pending_orders error: %s", e)
            return []


# =============================================================================
# ── RISK MANAGEMENT ───────────────────────────────────────────────────────────
# =============================================================================

def calculate_position_size(
    balance:   float,
    entry_price: float,
    sl_price:  float,
    leverage:  int = LEVERAGE,
    risk_pct:  float = RISK_PERCENT,
) -> float:
    """
    Calculate position size in contracts so we risk exactly risk_pct% of
    account balance on this trade (before leverage).

    Formula:
        risk_amount = balance * (risk_pct / 100)
        sl_distance = |entry_price - sl_price|
        contracts   = risk_amount / sl_distance

    With 2x leverage the position notional = contracts * entry_price * leverage,
    but our loss is still limited to the SL distance per contract.
    """
    if entry_price <= 0 or sl_price <= 0:
        return 0.0

    risk_amount = balance * (risk_pct / 100)
    sl_distance = abs(entry_price - sl_price)

    if sl_distance == 0:
        log.error("SL distance is zero — cannot calculate position size")
        return 0.0

    contracts = risk_amount / sl_distance

    # Round down to 1 decimal place (Blofin min lot: 0.1 ETH)
    contracts = max(0.1, round(contracts, 1))

    log.info(
        "Position sizing: balance=%.2f, risk=%.1f%%, risk_amount=%.2f, "
        "sl_dist=%.4f, contracts=%.1f",
        balance, risk_pct, risk_amount, sl_distance, contracts,
    )
    return contracts


# =============================================================================
# ── TELEGRAM NOTIFICATIONS ────────────────────────────────────────────────────
# =============================================================================

def send_telegram(message: str) -> None:
    """
    Send a message to your Telegram bot.
    Create a bot via @BotFather, get the token, then get your chat ID.
    """
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log.debug("Telegram not configured — skipping notification")
        return

    url     = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id":    TELEGRAM_CHAT_ID,
        "text":       message,
        "parse_mode": "HTML",
    }

    try:
        resp = requests.post(url, json=payload, timeout=5)
        if resp.status_code != 200:
            log.warning("Telegram send failed: %s", resp.text)
    except Exception as e:
        log.warning("Telegram error (non-fatal): %s", e)


# =============================================================================
# ── TRADE LOGGING ─────────────────────────────────────────────────────────────
# =============================================================================

def log_trade(record: dict) -> None:
    """
    Append a trade record to the CSV log file.
    Creates the file with headers if it doesn't exist.
    """
    fieldnames = [
        "timestamp", "action", "ticker", "inst_id",
        "price", "sl", "tp", "size_contracts",
        "balance", "order_id", "status", "notes",
    ]

    file_path   = Path(TRADE_LOG_FILE)
    write_header = not file_path.exists()

    with open(file_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        record.setdefault("timestamp", datetime.datetime.utcnow().isoformat() + "Z")
        writer.writerow(record)


# =============================================================================
# ── SIGNAL VALIDATION ─────────────────────────────────────────────────────────
# =============================================================================

def validate_signal(data: dict) -> tuple[bool, str]:
    """
    Validate an incoming TradingView webhook payload.

    Expected fields:
        action  : "buy" | "sell" | "close_long" | "close_short"
        ticker  : instrument symbol from TradingView (e.g. "ETHUSDTPERP")
        price   : current close price (float string)
        sl      : stop-loss price (float string)  — required for open orders
        tp      : take-profit price (float string) — required for open orders

    Returns (is_valid: bool, error_message: str)
    """
    required = ["action", "ticker", "price"]
    for field in required:
        if field not in data:
            return False, f"Missing required field: {field}"

    action = data["action"].lower()
    valid_actions = ["buy", "sell", "close_long", "close_short"]
    if action not in valid_actions:
        return False, f"Unknown action '{action}'. Must be one of: {valid_actions}"

    # Open orders need SL/TP
    if action in ["buy", "sell"]:
        for field in ["sl", "tp"]:
            if field not in data:
                return False, f"Open order action '{action}' requires field: {field}"

    # Validate numeric fields
    for field in ["price", "sl", "tp"]:
        if field in data:
            try:
                val = float(data[field])
                if val <= 0:
                    return False, f"Field '{field}' must be positive, got: {val}"
            except ValueError:
                return False, f"Field '{field}' is not a valid number: {data[field]}"

    return True, ""


# =============================================================================
# ── TRADE EXECUTION ───────────────────────────────────────────────────────────
# =============================================================================

blofin = BlofinClient()


def execute_trade(data: dict) -> dict:
    """
    Core trade execution logic.

    1. Validate signal
    2. Check existing positions / max position limit
    3. Calculate position size based on account balance
    4. Submit order to Blofin
    5. Log trade + send Telegram notification
    """
    result = {"success": False, "message": "", "order_id": None}

    # ── 1. Validate ───────────────────────────────────────────────────────────
    valid, error = validate_signal(data)
    if not valid:
        result["message"] = f"Validation failed: {error}"
        log.warning("Invalid signal: %s | data: %s", error, data)
        return result

    action     = data["action"].lower()
    ticker     = data["ticker"]
    price      = float(data.get("price", 0))
    sl_price   = float(data.get("sl", 0))
    tp_price   = float(data.get("tp", 0))

    # Map TradingView ticker to Blofin instrument ID
    inst_id = SYMBOL_MAP.get(ticker, ticker)
    log.info("Signal received: action=%s, ticker=%s → inst_id=%s, price=%.4f",
             action, ticker, inst_id, price)

    # ── 2. Close/Exit actions ─────────────────────────────────────────────────
    if action in ["close_long", "close_short"]:
        positions = blofin.get_open_positions(inst_id)
        if not positions:
            result["message"] = "No open position to close"
            log.info("Close signal ignored — no open position for %s", inst_id)
            return result

        side = "sell" if action == "close_long" else "buy"
        pos  = positions[0]
        size = str(abs(float(pos.get("positions", pos.get("availPos", "0.1")))))

        resp = blofin.place_order(
            inst_id=inst_id, side=side,
            order_type="market", size=size,
        )

        if resp.get("code") == "0":
            order_id = resp["data"][0].get("orderId", "")
            result.update({"success": True, "message": "Position closed", "order_id": order_id})
            log.info("✅ Closed position: %s orderId=%s", action, order_id)
            send_telegram(
                f"🔴 <b>Position Closed</b>\n"
                f"Action: {action.upper()}\n"
                f"Instrument: {inst_id}\n"
                f"Exit Price: {price}\n"
                f"Order ID: {order_id}"
            )
            log_trade({
                "action": action, "ticker": ticker, "inst_id": inst_id,
                "price": price, "size_contracts": size,
                "order_id": order_id, "status": "closed",
            })
        else:
            err_msg = resp.get("msg", "Unknown error")
            result["message"] = f"Close order failed: {err_msg}"
            log.error("Close order failed: %s", resp)
        return result

    # ── 3. Open order — check position limit ──────────────────────────────────
    open_positions = blofin.get_open_positions(inst_id)
    if len(open_positions) >= MAX_POSITIONS:
        result["message"] = f"Max positions ({MAX_POSITIONS}) reached — skipping signal"
        log.info("Signal skipped: already at max positions (%d)", MAX_POSITIONS)
        return result

    # ── 4. Get account balance ────────────────────────────────────────────────
    balance = blofin.get_balance()
    if balance <= 0:
        result["message"] = "Could not fetch account balance"
        log.error("Balance fetch returned 0 — aborting trade")
        return result

    # ── 5. Calculate position size ────────────────────────────────────────────
    contracts = calculate_position_size(
        balance=balance,
        entry_price=price,
        sl_price=sl_price,
    )
    if contracts <= 0:
        result["message"] = "Position size calculation returned 0 — trade skipped"
        return result

    # ── 6. Place order on Blofin ──────────────────────────────────────────────
    side = "buy" if action == "buy" else "sell"
    resp = blofin.place_order(
        inst_id=inst_id,
        side=side,
        order_type="market",
        size=str(contracts),
        sl_price=str(round(sl_price, 2)),
        tp_price=str(round(tp_price, 2)),
    )

    # ── 7. Handle response ────────────────────────────────────────────────────
    if resp.get("code") == "0":
        order_id = resp["data"][0].get("orderId", "")
        direction = "LONG 🟢" if side == "buy" else "SHORT 🔴"

        result.update({"success": True, "message": "Order placed", "order_id": order_id})
        log.info("✅ Order placed: %s %s contracts=%s orderId=%s",
                 direction, inst_id, contracts, order_id)

        send_telegram(
            f"⚡ <b>New Trade Opened</b>\n"
            f"Direction: <b>{direction}</b>\n"
            f"Instrument: {inst_id}\n"
            f"Entry: {price}\n"
            f"Stop Loss: {sl_price}\n"
            f"Take Profit: {tp_price}\n"
            f"Size: {contracts} contracts\n"
            f"Balance: ${balance:.2f}\n"
            f"Risk: {RISK_PERCENT}% = ${balance * RISK_PERCENT / 100:.2f}\n"
            f"Order ID: {order_id}"
        )
        log_trade({
            "action": action, "ticker": ticker, "inst_id": inst_id,
            "price": price, "sl": sl_price, "tp": tp_price,
            "size_contracts": contracts, "balance": round(balance, 2),
            "order_id": order_id, "status": "opened",
        })
    else:
        err_code = resp.get("code", "")
        err_msg  = resp.get("msg", "Unknown Blofin error")
        result["message"] = f"Blofin error {err_code}: {err_msg}"
        log.error("Order placement failed: %s", resp)

        send_telegram(
            f"❌ <b>Order FAILED</b>\n"
            f"Action: {action}\n"
            f"Instrument: {inst_id}\n"
            f"Error: {err_msg}\n"
            f"Code: {err_code}"
        )
        log_trade({
            "action": action, "ticker": ticker, "inst_id": inst_id,
            "price": price, "sl": sl_price, "tp": tp_price,
            "status": "failed", "notes": err_msg,
        })

    return result


# =============================================================================
# ── FLASK ROUTES ──────────────────────────────────────────────────────────────
# =============================================================================

@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Main TradingView webhook receiver.

    TradingView sends a POST with a JSON body containing your alert message.
    We validate an optional shared secret header, parse the payload, and
    call execute_trade().
    """
    # ── Optional: validate shared secret from header ──────────────────────────
    if WEBHOOK_SECRET:
        provided_secret = request.headers.get("X-Webhook-Secret", "")
        if provided_secret != WEBHOOK_SECRET:
            log.warning("Invalid webhook secret from IP: %s", request.remote_addr)
            return jsonify({"error": "Unauthorized"}), 401

    # ── Parse JSON body ───────────────────────────────────────────────────────
    try:
        data = request.get_json(force=True)
        if not data:
            # TradingView sometimes sends plain text; try to parse it
            raw = request.data.decode("utf-8")
            data = json.loads(raw)
    except Exception as e:
        log.error("Failed to parse webhook payload: %s | raw: %s", e, request.data)
        return jsonify({"error": "Invalid JSON payload"}), 400

    log.info("📥 Webhook received: %s", json.dumps(data))

    # ── Execute trade ─────────────────────────────────────────────────────────
    try:
        result = execute_trade(data)
    except Exception as e:
        log.error("execute_trade raised exception: %s\n%s", e, traceback.format_exc())
        send_telegram(f"🚨 <b>Bot Exception</b>\n{str(e)[:300]}")
        return jsonify({"error": "Internal server error", "detail": str(e)}), 500

    status_code = 200 if result["success"] else 400
    return jsonify(result), status_code


@app.route("/health", methods=["GET"])
def health():
    """
    Health check endpoint.
    Returns server status, current open positions, and account balance.
    Useful for monitoring that the bot is alive and connected to Blofin.
    """
    try:
        balance   = blofin.get_balance()
        positions = blofin.get_open_positions()
        return jsonify({
            "status":         "ok",
            "timestamp":      datetime.datetime.utcnow().isoformat() + "Z",
            "demo_mode":      BLOFIN_DEMO_MODE,
            "balance_usdt":   round(balance, 2),
            "open_positions": len(positions),
            "positions":      positions,
        })
    except Exception as e:
        return jsonify({"status": "error", "detail": str(e)}), 500


@app.route("/trades", methods=["GET"])
def trades():
    """
    Returns the last 20 trades from the CSV log.
    Useful for quick review without opening the file.
    """
    file_path = Path(TRADE_LOG_FILE)
    if not file_path.exists():
        return jsonify({"trades": [], "message": "No trades logged yet"})

    rows = []
    with open(file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    return jsonify({"trades": rows[-20:], "total": len(rows)})


# =============================================================================
# ── STARTUP ───────────────────────────────────────────────────────────────────
# =============================================================================

def startup_checks():
    """
    Run sanity checks on startup:
    - Confirm API keys are set
    - Test Blofin connection by fetching balance
    - Send Telegram startup notification
    """
    log.info("=" * 60)
    log.info("  Black Cat Trading Bot — Starting up")
    log.info("  Demo mode : %s", "✅ YES (paper trading)" if BLOFIN_DEMO_MODE else "❌ NO (LIVE TRADING)")
    log.info("  Leverage  : %dx", LEVERAGE)
    log.info("  Risk/trade: %.1f%%", RISK_PERCENT)
    log.info("=" * 60)

    # Check API keys
    if not BLOFIN_API_KEY or BLOFIN_API_KEY == "your_api_key_here":
        log.error("❌ BLOFIN_API_KEY not set! Edit your .env file.")
        sys.exit(1)

    # Test connection
    balance = blofin.get_balance()
    if balance > 0:
        log.info("✅ Blofin connection OK — Balance: $%.2f USDT", balance)
    else:
        log.warning("⚠️  Could not fetch balance. Check API keys and IP whitelist.")

    # Send startup Telegram message
    mode = "DEMO (paper)" if BLOFIN_DEMO_MODE else "LIVE 🔴"
    send_telegram(
        f"🤖 <b>Black Cat Bot Started</b>\n"
        f"Mode: {mode}\n"
        f"Balance: ${balance:.2f} USDT\n"
        f"Leverage: {LEVERAGE}x | Risk: {RISK_PERCENT}%/trade\n"
        f"Server: {SERVER_HOST}:{SERVER_PORT}"
    )


if __name__ == "__main__":
    startup_checks()
    app.run(
        host=SERVER_HOST,
        port=SERVER_PORT,
        debug=False,      # Never use debug=True in production
        use_reloader=False,
    )
