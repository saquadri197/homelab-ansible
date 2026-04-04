"""
main.py
--------
Entry point for the automated crypto trading bot.

Run with:
    python main.py                   # Live trading (uses config.yaml)
    python main.py --dry-run         # Paper mode: signals only, no real orders
    python main.py --scan-once       # Run one full scan and exit (for testing)

The bot runs on a loop defined by config.yaml scan_interval_minutes.
Every cycle it:
  1. Fetches fresh OHLCV data for each watched symbol
  2. Computes S/R levels, indicators, and confluence scores
  3. If score >= min_score AND no existing position: opens a trade
  4. If a position is open: checks trailing stop logic
  5. Sends Telegram alerts for all significant events
"""

import argparse
import logging
import time
import yaml
import sys
from pathlib import Path
from datetime import datetime, timezone

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from data.fetcher import get_exchange, fetch_all_symbols, get_current_price, get_account_balance
from analysis.support_resistance import find_sr_levels
from analysis.confluence import run_confluence_check
from trading.executor import open_trade, update_trailing_stop, close_position, OpenPosition
from alerts.telegram import build_alerter

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("trader.log"),
    ],
)
logger = logging.getLogger("main")


# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────

def load_config(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


# ─────────────────────────────────────────────
# Main scan loop
# ─────────────────────────────────────────────

def run_scan(config: dict,
             exchange,
             alerter,
             open_positions: dict[str, OpenPosition],
             dry_run: bool = False) -> int:
    """
    Run a full scan of all watched symbols.
    Returns the number of new trades opened.
    """
    symbols        = config["symbols"]
    timeframes     = ["1h", "4h"]
    min_score      = config.get("min_confluence_score", 55)
    max_positions  = config.get("max_open_positions", 3)
    trail_pct      = config.get("trailing_stop_pct", 0.03)

    trades_opened = 0
    setups_found  = 0

    # ── Fetch all data in one batch ───────────────────────────────────────────
    logger.info(f"Scanning {len(symbols)} symbols...")
    all_data = fetch_all_symbols(exchange, symbols, timeframes)

    # ── Process each symbol ───────────────────────────────────────────────────
    for symbol in symbols:
        try:
            df_1h = all_data.get(symbol, {}).get("1h")
            df_4h = all_data.get(symbol, {}).get("4h")

            if df_1h is None or df_4h is None or len(df_1h) < 100:
                logger.warning(f"Insufficient data for {symbol}, skipping")
                continue

            current_price = float(df_1h["close"].iloc[-1])

            # ── Update trailing stop for open positions ───────────────────────
            if symbol in open_positions:
                pos = open_positions[symbol]
                old_sl = pos.stop_loss
                pos    = update_trailing_stop(exchange, pos, current_price, trail_pct)

                if pos.stop_loss != old_sl:
                    alerter.alert_trailing_stop_updated(
                        symbol, pos.direction, old_sl, pos.stop_loss, current_price
                    )
                    open_positions[symbol] = pos
                continue  # Don't look for new setups if already in a trade

            # ── Skip if at max positions ──────────────────────────────────────
            if len(open_positions) >= max_positions:
                logger.debug(f"Max positions ({max_positions}) reached, skipping {symbol}")
                continue

            # ── Run confluence check ──────────────────────────────────────────
            sr_levels  = find_sr_levels(df_4h, min_touches=2)
            result     = run_confluence_check(df_1h, df_4h, sr_levels, config)

            logger.info(
                f"{symbol}: score={result.score}/100 | "
                f"direction={result.direction} | tradeable={result.is_tradeable(min_score)}"
            )

            if result.is_tradeable(min_score):
                setups_found += 1
                logger.info(f"\n{result.summary()}\n")
                alerter.alert_confluence_found(
                    symbol, result.score, result.direction, result.signals
                )

                if result.suggested_stop_loss is None:
                    logger.warning(f"No stop loss calculated for {symbol} — skipping trade")
                    continue

                if dry_run:
                    logger.info(f"[DRY RUN] Would open {result.direction.upper()} on {symbol}")
                    continue

                # ── Execute the trade ─────────────────────────────────────────
                balance  = get_account_balance(exchange)
                position = open_trade(
                    exchange=exchange,
                    symbol=symbol,
                    direction=result.direction,
                    entry_price=current_price,
                    stop_loss_price=result.suggested_stop_loss,
                    account_balance=balance,
                    confluence_score=result.score,
                    config=config,
                )

                if position:
                    open_positions[symbol] = position
                    trades_opened += 1
                    alerter.alert_trade_opened(
                        symbol=symbol,
                        direction=position.direction,
                        entry=position.entry_price,
                        stop_loss=position.stop_loss,
                        quantity=position.quantity,
                        leverage=position.leverage,
                        score=result.score,
                    )

        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}", exc_info=True)
            alerter.alert_error(symbol, str(e))

    alerter.alert_scan_summary(len(symbols), setups_found, trades_opened)
    return trades_opened


# ─────────────────────────────────────────────
# Entrypoint
# ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AJ-style Crypto Trading Bot")
    parser.add_argument("--dry-run",    action="store_true", help="Signals only, no real orders")
    parser.add_argument("--scan-once",  action="store_true", help="Run one scan and exit")
    parser.add_argument("--config",     default="config.yaml", help="Path to config file")
    args = parser.parse_args()

    config   = load_config(args.config)
    alerter  = build_alerter(config)
    exchange = get_exchange(config)

    mode = "DRY RUN (paper)" if args.dry_run else (
        "TESTNET" if config["exchange"].get("testnet") else "LIVE"
    )

    logger.info(f"Bot starting in {mode} mode")
    alerter.alert_startup(config["symbols"], mode)

    # Track open positions in memory (survives restarts via exchange API)
    open_positions: dict[str, OpenPosition] = {}

    scan_interval = config.get("scan_interval_minutes", 15) * 60

    if args.scan_once:
        run_scan(config, exchange, alerter, open_positions, dry_run=args.dry_run)
        return

    # ── Continuous loop ───────────────────────────────────────────────────────
    while True:
        try:
            run_scan(config, exchange, alerter, open_positions, dry_run=args.dry_run)
        except KeyboardInterrupt:
            logger.info("Shutting down — closing all open positions...")
            for symbol, pos in open_positions.items():
                try:
                    close_position(exchange, pos)
                    logger.info(f"Closed {symbol}")
                except Exception as e:
                    logger.error(f"Failed to close {symbol}: {e}")
            break
        except Exception as e:
            logger.error(f"Scan loop error: {e}", exc_info=True)
            alerter.alert_error("main loop", str(e))

        logger.info(f"Next scan in {scan_interval // 60} minutes...")
        time.sleep(scan_interval)


if __name__ == "__main__":
    main()
