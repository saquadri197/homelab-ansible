"""
data/fetcher.py
---------------
Fetches OHLCV candlestick data from the exchange using CCXT.
Supports Bybit (recommended) and Blofin.
"""

import ccxt
import pandas as pd
import time
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def get_exchange(config: dict) -> ccxt.Exchange:
    """Initialize the exchange connection."""
    exchange_id = config["exchange"]["name"]  # e.g. "bybit" or "blofin"
    exchange_class = getattr(ccxt, exchange_id)

    exchange = exchange_class({
        "apiKey": config["exchange"]["api_key"],
        "secret": config["exchange"]["api_secret"],
        "enableRateLimit": True,
        "options": {
            "defaultType": "future",      # Use futures/perpetuals market
            "adjustForTimeDifference": True,
        },
    })

    # Bybit testnet support — set sandbox=True in config to paper trade
    if config["exchange"].get("testnet", False):
        exchange.set_sandbox_mode(True)
        logger.info("Running in TESTNET (paper trading) mode")

    return exchange


def fetch_ohlcv(exchange: ccxt.Exchange, symbol: str, timeframe: str = "1h", limit: int = 300) -> pd.DataFrame:
    """
    Fetch OHLCV candles for a symbol.

    Args:
        exchange:  Initialized CCXT exchange object
        symbol:    Trading pair, e.g. "ADA/USDT:USDT"
        timeframe: Candle size — "15m", "1h", "4h", "1d"
        limit:     Number of candles to fetch (max ~1000)

    Returns:
        DataFrame with columns: timestamp, open, high, low, close, volume
    """
    try:
        raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.sort_values("timestamp").reset_index(drop=True)
        logger.debug(f"Fetched {len(df)} candles for {symbol} on {timeframe}")
        return df
    except ccxt.NetworkError as e:
        logger.error(f"Network error fetching {symbol}: {e}")
        raise
    except ccxt.ExchangeError as e:
        logger.error(f"Exchange error fetching {symbol}: {e}")
        raise


def fetch_all_symbols(exchange: ccxt.Exchange, symbols: list[str], timeframes: list[str]) -> dict:
    """
    Fetch OHLCV data for all watched symbols across all required timeframes.

    Returns:
        Nested dict: { symbol: { timeframe: DataFrame } }
    """
    data = {}
    for symbol in symbols:
        data[symbol] = {}
        for tf in timeframes:
            try:
                data[symbol][tf] = fetch_ohlcv(exchange, symbol, tf)
                time.sleep(exchange.rateLimit / 1000)  # Respect rate limits
            except Exception as e:
                logger.warning(f"Skipping {symbol} {tf}: {e}")
    return data


def get_current_price(exchange: ccxt.Exchange, symbol: str) -> float:
    """Get the latest bid/ask midpoint for a symbol."""
    ticker = exchange.fetch_ticker(symbol)
    return (ticker["bid"] + ticker["ask"]) / 2


def get_order_book_depth(exchange: ccxt.Exchange, symbol: str) -> float:
    """
    Return total order book depth (USD) as a rough liquidity measure.
    AJ's rule: prefer coins with >$50M on the order book for large positions.
    """
    ob = exchange.fetch_order_book(symbol, limit=50)
    bids_usd = sum(price * qty for price, qty in ob["bids"])
    asks_usd = sum(price * qty for price, qty in ob["asks"])
    return bids_usd + asks_usd
