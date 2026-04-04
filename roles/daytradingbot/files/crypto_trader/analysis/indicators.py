"""
analysis/indicators.py
-----------------------
Technical indicator calculations used in the confluence checklist:

  1. RSI (Relative Strength Index) + bullish/bearish divergence
  2. MACD (Moving Average Convergence Divergence)
  3. EMA (Exponential Moving Average) — 50 EMA is AJ's key level
  4. Fibonacci retracement levels
  5. Money flow / volume trend (simplified Market Cipher B proxy)

All functions take a pandas OHLCV DataFrame and return numeric values
or signal strings so the confluence engine can score them easily.
"""

import numpy as np
import pandas as pd
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# RSI
# ─────────────────────────────────────────────

def calc_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Standard RSI calculation."""
    delta  = df["close"].diff()
    gain   = delta.clip(lower=0)
    loss   = (-delta).clip(lower=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi.rename("rsi")


def detect_rsi_divergence(df: pd.DataFrame, rsi: pd.Series,
                          lookback: int = 20) -> str:
    """
    Detect bullish or bearish RSI divergence over the last `lookback` candles.

    Bullish divergence (buy signal):
        Price makes a lower low, but RSI makes a higher low.
        This is the "price coming down, oscillator going up" AJ describes.

    Bearish divergence (sell/short signal):
        Price makes a higher high, but RSI makes a lower high.

    Returns: "bullish", "bearish", or "none"
    """
    window_close = df["close"].iloc[-lookback:]
    window_rsi   = rsi.iloc[-lookback:]

    # Find two most recent lows in price
    price_lows_idx = window_close.nsmallest(2).index.tolist()
    if len(price_lows_idx) >= 2:
        idx1, idx2 = sorted(price_lows_idx)
        price_lower_low = window_close[idx2] < window_close[idx1]
        rsi_higher_low  = window_rsi[idx2]   > window_rsi[idx1]
        if price_lower_low and rsi_higher_low:
            logger.debug("Bullish RSI divergence detected")
            return "bullish"

    # Find two most recent highs in price
    price_highs_idx = window_close.nlargest(2).index.tolist()
    if len(price_highs_idx) >= 2:
        idx1, idx2 = sorted(price_highs_idx)
        price_higher_high = window_close[idx2] > window_close[idx1]
        rsi_lower_high    = window_rsi[idx2]    < window_rsi[idx1]
        if price_higher_high and rsi_lower_high:
            logger.debug("Bearish RSI divergence detected")
            return "bearish"

    return "none"


# ─────────────────────────────────────────────
# MACD
# ─────────────────────────────────────────────

def calc_macd(df: pd.DataFrame,
              fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """
    Standard MACD.

    Returns DataFrame with columns:
        macd, signal_line, histogram
    """
    ema_fast   = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow   = df["close"].ewm(span=slow, adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - signal_line

    return pd.DataFrame({
        "macd":        macd_line,
        "signal_line": signal_line,
        "histogram":   histogram,
    })


def macd_crossover(macd_df: pd.DataFrame) -> str:
    """
    Detect the most recent MACD crossover signal.

    Returns: "bullish_cross", "bearish_cross", or "none"
    """
    hist = macd_df["histogram"]
    if len(hist) < 2:
        return "none"
    if hist.iloc[-2] < 0 and hist.iloc[-1] >= 0:
        return "bullish_cross"
    if hist.iloc[-2] > 0 and hist.iloc[-1] <= 0:
        return "bearish_cross"
    return "none"


# ─────────────────────────────────────────────
# EMA
# ─────────────────────────────────────────────

def calc_ema(df: pd.DataFrame, period: int = 50) -> pd.Series:
    """Calculate EMA for a given period."""
    return df["close"].ewm(span=period, adjust=False).mean().rename(f"ema_{period}")


def price_vs_ema(df: pd.DataFrame, ema: pd.Series) -> str:
    """
    Compare current close to EMA.

    Returns:
        "above_ema"  — bullish context, price cleared the EMA
        "below_ema"  — bearish context
        "crossing"   — price crossed EMA in last 3 candles (momentum event)
    """
    current = df["close"].iloc[-1]
    ema_now  = ema.iloc[-1]

    # Check for recent cross (last 3 candles)
    crossed = False
    if len(df) >= 3:
        prev_above = df["close"].iloc[-3] > ema.iloc[-3]
        now_above  = current > ema_now
        crossed = prev_above != now_above

    if crossed:
        return "crossing"
    return "above_ema" if current > ema_now else "below_ema"


# ─────────────────────────────────────────────
# Fibonacci Retracement
# ─────────────────────────────────────────────

FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
FIB_LABELS = ["0%", "23.6%", "38.2%", "50%", "61.8%", "78.6%", "100%"]


@dataclass
class FibLevels:
    swing_low: float
    swing_high: float
    levels: dict  # { "61.8%": price_value, ... }

    def in_accumulation_zone(self, price: float) -> bool:
        """
        AJ's rule: accumulate under the 61.8% or ideally under the 78.6%
        retracement from swing high.
        """
        fib_618 = self.levels.get("61.8%", 0)
        return price <= fib_618

    def in_deep_accumulation_zone(self, price: float) -> bool:
        fib_786 = self.levels.get("78.6%", 0)
        return price <= fib_786

    def nearest_level(self, price: float) -> tuple[str, float]:
        """Return the (label, price) of the nearest fib level."""
        nearest = min(self.levels.items(), key=lambda kv: abs(kv[1] - price))
        return nearest


def calc_fibonacci(df: pd.DataFrame, lookback: int = 100) -> FibLevels:
    """
    Auto-detect swing high and swing low over `lookback` candles
    and compute standard Fibonacci retracement levels.

    These retracements go FROM swing high DOWN to swing low
    (measuring how much has been given back).
    """
    window    = df.tail(lookback)
    swing_low  = float(window["low"].min())
    swing_high = float(window["high"].max())
    price_range = swing_high - swing_low

    levels = {}
    for ratio, label in zip(FIB_LEVELS, FIB_LABELS):
        # Retracement from high: high - (ratio * range)
        levels[label] = round(swing_high - ratio * price_range, 8)

    return FibLevels(swing_low=swing_low, swing_high=swing_high, levels=levels)


# ─────────────────────────────────────────────
# Volume / Money Flow (Market Cipher B proxy)
# ─────────────────────────────────────────────

def calc_money_flow_index(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Money Flow Index (MFI) — volume-weighted RSI.
    A simplified proxy for the money flow component of Market Cipher B.

    Values < 20: oversold (bullish for longs)
    Values > 80: overbought (bearish / good for shorts)
    """
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    raw_money_flow = typical_price * df["volume"]

    positive_flow = raw_money_flow.where(typical_price > typical_price.shift(1), 0)
    negative_flow = raw_money_flow.where(typical_price < typical_price.shift(1), 0)

    pos_sum = positive_flow.rolling(period).sum()
    neg_sum = negative_flow.rolling(period).sum()

    mfr = pos_sum / neg_sum.replace(0, np.nan)
    mfi = 100 - (100 / (1 + mfr))
    return mfi.rename("mfi")


def money_flow_signal(mfi: pd.Series) -> str:
    """
    Translate MFI into a signal.

    Returns: "oversold" (buy bias), "overbought" (sell bias), "neutral"
    """
    current = mfi.iloc[-1]
    prev    = mfi.iloc[-2] if len(mfi) > 1 else current

    if current < 25:
        return "oversold"
    if current > 75:
        return "overbought"
    # Turning green — rising from oversold territory
    if prev < 40 and current > prev:
        return "turning_green"
    return "neutral"
