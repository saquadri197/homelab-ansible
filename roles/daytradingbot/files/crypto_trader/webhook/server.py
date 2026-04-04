"""
webhook/server.py
------------------
FastAPI webhook receiver for TradingView alerts.

TradingView fires a POST request to this server whenever a Pine Script
alert condition is met. The server validates the request, runs any final
checks, then executes the trade on Kraken Futures.

Security:
  - Every request must include a secret token in the JSON body.
    Set WEBHOOK_SECRET in config.yaml — TradingView includes it in the
    alert message template. Anyone without the secret is rejected (401).

  - Optionally whitelist TradingView's IP ranges (see TRADINGVIEW_IPS below).

Run with:
    uvicorn webhook.server:app --host 0.0.0.0 --port 8000
    (or via systemd — see setup/crypto-trader.service)
"""

import re
import logging
import sys
import yaml
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, validator

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from data.fetcher import get_exchange, fetch_ohlcv
from analysis.support_resistance import find_sr_levels
from analysis.confluence import run_confluence_check
from trading.executor import open_trade, update_trailing_stop, close_position, OpenPosition, get_account_balance
from alerts.telegram import build_alerter

logger = logging.getLogger(__name__)

# ── TradingView's outbound IP ranges (optional extra security layer) ──────────
# https://www.tradingview.com/support/solutions/43000529348
TRADINGVIEW_IPS = {
    "52.89.214.238", "34.212.75.30", "54.218.53.128", "52.32.178.7",
    "54.237.33.149", "52.0.77.194", "34.224.195.186", "34.237.251.190",
}


# ── Config & singletons (loaded once at startup) ─────────────────────────────
def load_config() -> dict:
    path = Path(__file__).parent.parent / "config.yaml"
    with open(path) as f:
        return yaml.safe_load(f)


CONFIG   = load_config()
EXCHANGE = get_exchange(CONFIG)
ALERTER  = build_alerter(CONFIG)

# In-memory position tracker (survives restarts if you persist to a file —
# see the optional persistence section at the bottom)
open_positions: dict[str, OpenPosition] = {}


# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(title="Crypto Trader Webhook", version="1.0")


# ── Request model ─────────────────────────────────────────────────────────────
class TradingViewSignal(BaseModel):
    """
    JSON body sent by TradingView when an alert fires.

    In TradingView, set the alert message to exactly this JSON template:
    {
      "secret":    "{{YOUR_SECRET_HERE}}",
      "symbol":    "{{ticker}}",
      "direction": "{{strategy.order.action}}",
      "price":     {{close}},
      "signal":    "confluence_alert",
      "score":     {{plot_0}},
      "timeframe": "{{interval}}"
    }
    """
    secret:    str
    symbol:    str            # e.g. "ADAUSDT" — normalized to Kraken format below
    direction: str            # "long" / "short" / "buy" / "sell"
    price:     float
    signal:    str = "confluence_alert"
    score:     Optional[int] = None      # Confluence score from Pine Script (0–100)
    timeframe: Optional[str] = "1h"

    @validator("direction")
    def normalize_direction(cls, v):
        v = v.lower().strip()
        if v in ("buy",  "long"):  return "long"
        if v in ("sell", "short"): return "short"
        raise ValueError(f"Unknown direction: {v}")

    @validator("symbol")
    def normalize_symbol(cls, v):
        """Convert TradingView symbol format to Kraken CCXT format.
        e.g. 'ADAUSDT' → 'ADA/USDT:USDT'
        Handles TradingView quirks: embedded slashes, XBT alias, trailing colons.
        """
        v = v.upper().replace("PERP", "").replace(".P", "").replace("_", "")

        # If already in proper CCXT format, return as-is (handle XBT alias)
        if re.match(r"^[A-Z]+/USDT:USDT$", v):
            if v.startswith("XBT"):
                return "BTC" + v[3:]
            return v

        # Strip all punctuation to get raw base+quote letters
        raw = v.replace(":", "").replace("/", "").replace(".", "")

        # Extract base currency from raw string
        m = re.match(r"^([A-Z]+?)USDT", raw)
        if not m:
            return v  # fallback: leave unchanged

        base = m.group(1)
        # Kraken uses XBT for Bitcoin
        if base == "XBT":
            base = "BTC"
        return f"{base}/USDT:USDT"


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    """Quick liveness check — call this to verify the server is running."""
    return {
        "status": "ok",
        "time":   datetime.now(timezone.utc).isoformat(),
        "open_positions": list(open_positions.keys()),
    }


# ── Main webhook endpoint ─────────────────────────────────────────────────────
@app.post("/webhook")
async def receive_signal(signal: TradingViewSignal, request: Request):
    """
    Receives a TradingView alert and executes a trade if valid.
    """
    client_ip = request.client.host

    # ── 1. Validate secret ────────────────────────────────────────────────────
    expected_secret = CONFIG.get("webhook", {}).get("secret", "")
    if not expected_secret:
        logger.error("No webhook.secret set in config.yaml — rejecting all requests")
        raise HTTPException(status_code=503, detail="Webhook not configured")

    if signal.secret != expected_secret:
        logger.warning(f"Invalid secret from {client_ip} for {signal.symbol}")
        raise HTTPException(status_code=401, detail="Invalid secret")

    # ── 2. Optional IP whitelist ──────────────────────────────────────────────
    if CONFIG.get("webhook", {}).get("restrict_to_tradingview_ips", False):
        if client_ip not in TRADINGVIEW_IPS:
            logger.warning(f"Request from non-TradingView IP: {client_ip}")
            raise HTTPException(status_code=403, detail="IP not whitelisted")

    logger.info(
        f"Signal received: {signal.symbol} {signal.direction.upper()} "
        f"@ {signal.price} | score={signal.score} | from={client_ip}"
    )

    # ── 3. Check if symbol is in our watchlist ────────────────────────────────
    if signal.symbol not in CONFIG.get("symbols", []):
        logger.warning(f"Symbol {signal.symbol} not in watchlist — ignoring")
        return JSONResponse({"status": "ignored", "reason": "symbol not in watchlist"})

    # ── 4. Check position limits ──────────────────────────────────────────────
    max_positions = CONFIG.get("max_open_positions", 3)
    if signal.symbol in open_positions:
        logger.info(f"Already in a position for {signal.symbol} — ignoring signal")
        return JSONResponse({"status": "ignored", "reason": "position already open"})

    if len(open_positions) >= max_positions:
        logger.info(f"Max positions ({max_positions}) reached — ignoring signal")
        return JSONResponse({"status": "ignored", "reason": "max positions reached"})

    # ── 5. Run confluence check (using live data to confirm the signal) ───────
    # Even though TradingView sent the signal, we run our own data fetch and
    # confluence check to independently verify before committing capital.
    try:
        df_1h = fetch_ohlcv(EXCHANGE, signal.symbol, "1h", limit=200)
        df_4h = fetch_ohlcv(EXCHANGE, signal.symbol, "4h", limit=200)
        sr_levels = find_sr_levels(df_4h, min_touches=2)
        result    = run_confluence_check(df_1h, df_4h, sr_levels, CONFIG)
    except Exception as e:
        logger.error(f"Data fetch/analysis failed for {signal.symbol}: {e}")
        ALERTER.alert_error(f"webhook analysis {signal.symbol}", str(e))
        raise HTTPException(status_code=500, detail="Analysis failed")

    min_score = CONFIG.get("min_confluence_score", 60)

    # Use TradingView's score if our analysis agrees on direction,
    # otherwise use our own score
    effective_score = signal.score if (
        signal.score and result.direction == signal.direction
    ) else result.score

    logger.info(
        f"{signal.symbol}: TV_score={signal.score} | "
        f"local_score={result.score} | effective={effective_score} | "
        f"direction={result.direction}"
    )

    if effective_score < min_score or result.direction == "none":
        logger.info(
            f"Score {effective_score} below threshold {min_score} "
            f"or no clear direction — no trade"
        )
        ALERTER.alert_confluence_found(
            signal.symbol, effective_score, signal.direction, result.signals
        )
        return JSONResponse({
            "status":     "no_trade",
            "score":      effective_score,
            "threshold":  min_score,
            "signals":    result.signals,
        })

    # ── 6. Validate stop loss ─────────────────────────────────────────────────
    if result.suggested_stop_loss is None:
        logger.warning(f"No stop loss for {signal.symbol} — aborting trade")
        return JSONResponse({"status": "aborted", "reason": "no stop loss calculated"})

    # ── 7. Execute trade ──────────────────────────────────────────────────────
    dry_run = CONFIG.get("dry_run", False)
    if dry_run:
        logger.info(f"[DRY RUN] Would open {signal.direction.upper()} on {signal.symbol}")
        return JSONResponse({
            "status":    "dry_run",
            "symbol":    signal.symbol,
            "direction": signal.direction,
            "score":     effective_score,
            "entry":     signal.price,
            "stop_loss": result.suggested_stop_loss,
        })

    balance  = get_account_balance(EXCHANGE)
    position = open_trade(
        exchange=EXCHANGE,
        symbol=signal.symbol,
        direction=signal.direction,
        entry_price=signal.price,
        stop_loss_price=result.suggested_stop_loss,
        account_balance=balance,
        confluence_score=effective_score,
        config=CONFIG,
    )

    if not position:
        raise HTTPException(status_code=500, detail="Order placement failed")

    open_positions[signal.symbol] = position

    ALERTER.alert_trade_opened(
        symbol=signal.symbol,
        direction=position.direction,
        entry=position.entry_price,
        stop_loss=position.stop_loss,
        quantity=position.quantity,
        leverage=position.leverage,
        score=effective_score,
    )

    logger.info(f"Trade opened successfully for {signal.symbol}")
    return JSONResponse({
        "status":    "trade_opened",
        "symbol":    signal.symbol,
        "direction": position.direction,
        "entry":     position.entry_price,
        "stop_loss": position.stop_loss,
        "leverage":  position.leverage,
        "quantity":  position.quantity,
        "score":     effective_score,
    })


# ── Trailing stop update endpoint ─────────────────────────────────────────────
@app.post("/webhook/trail")
async def trail_stop(signal: TradingViewSignal, request: Request):
    """
    Called by a separate TradingView alert on each candle close to
    update trailing stops on open positions.
    """
    if signal.secret != CONFIG.get("webhook", {}).get("secret", ""):
        raise HTTPException(status_code=401, detail="Invalid secret")

    if signal.symbol not in open_positions:
        return JSONResponse({"status": "no_position"})

    pos     = open_positions[signal.symbol]
    old_sl  = pos.stop_loss
    trail   = CONFIG.get("trailing_stop_pct", 0.03)
    pos     = update_trailing_stop(EXCHANGE, pos, signal.price, trail)

    if pos.stop_loss != old_sl:
        ALERTER.alert_trailing_stop_updated(
            signal.symbol, pos.direction, old_sl, pos.stop_loss, signal.price
        )
        open_positions[signal.symbol] = pos
        return JSONResponse({"status": "updated", "new_sl": pos.stop_loss})

    return JSONResponse({"status": "no_change", "sl": pos.stop_loss})


# ── Close position endpoint ───────────────────────────────────────────────────
@app.post("/webhook/close")
async def close_signal(signal: TradingViewSignal, request: Request):
    """Called when TradingView signals an exit (strategy.close event)."""
    from trading.executor import close_position

    if signal.secret != CONFIG.get("webhook", {}).get("secret", ""):
        raise HTTPException(status_code=401, detail="Invalid secret")

    if signal.symbol not in open_positions:
        return JSONResponse({"status": "no_position"})

    pos   = open_positions[signal.symbol]
    order = close_position(EXCHANGE, pos)
    del open_positions[signal.symbol]

    ALERTER.alert_trade_closed(
        symbol=signal.symbol,
        direction=pos.direction,
        entry=pos.entry_price,
        exit_price=signal.price,
        quantity=pos.quantity,
        leverage=pos.leverage,
    )
    return JSONResponse({"status": "closed", "order_id": order.get("id")})
