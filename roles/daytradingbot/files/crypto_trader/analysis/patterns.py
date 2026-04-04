"""
analysis/patterns.py
---------------------
Detects the two core price patterns AJ uses:

  1. Consolidation Range — the coin is "coiling" in a tight price band.
     When range compresses enough, a breakout is imminent.

  2. Breakout + Confirmation — price exits the range AND holds above/below
     the level on the next candle close (avoids fakeouts).

  3. Trend detection — local uptrend / downtrend / point of intersection
     (where a local uptrend collides with a broader downtrend, forcing
     a resolution — AJ's favourite setup trigger).
"""

import numpy as np
import pandas as pd
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Consolidation Range
# ─────────────────────────────────────────────

@dataclass
class ConsolidationRange:
    high: float                   # Top of the range
    low: float                    # Bottom of the range
    range_pct: float              # (high - low) / low * 100
    candles_in_range: int         # How many candles consolidated
    inner_high: float             # Tighter inner range high (range-within-range)
    inner_low: float              # Tighter inner range low
    inner_range_pct: float        # Inner range size %


def find_consolidation(df: pd.DataFrame,
                        min_candles: int = 10,
                        max_range_pct: float = 20.0) -> Optional[ConsolidationRange]:
    """
    Scan the most recent candles to find a consolidation range.

    A consolidation is identified when price stays within `max_range_pct`
    for at least `min_candles` consecutive candles.

    AJ's Cardano example: 97 days inside a 19% range — that's your alarm zone.

    Returns None if no valid consolidation is found.
    """
    # Work backwards from the latest candle
    highs  = df["high"].values
    lows   = df["low"].values
    n      = len(df)

    best_range = None
    best_count = 0

    for start in range(n - min_candles, -1, -1):
        window_highs = highs[start:]
        window_lows  = lows[start:]

        range_high = float(np.max(window_highs))
        range_low  = float(np.min(window_lows))
        range_pct  = (range_high - range_low) / range_low * 100

        if range_pct <= max_range_pct:
            count = n - start
            if count > best_count:
                best_count = count
                # Find tighter inner range (middle 80% of candles by price)
                p10 = float(np.percentile(window_lows, 10))
                p90 = float(np.percentile(window_highs, 90))
                inner_pct = (p90 - p10) / p10 * 100

                best_range = ConsolidationRange(
                    high=round(range_high, 8),
                    low=round(range_low, 8),
                    range_pct=round(range_pct, 2),
                    candles_in_range=count,
                    inner_high=round(p90, 8),
                    inner_low=round(p10, 8),
                    inner_range_pct=round(inner_pct, 2),
                )
        else:
            # Broke the range — stop looking further back
            break

    if best_range:
        logger.debug(
            f"Consolidation found: {best_range.candles_in_range} candles, "
            f"{best_range.range_pct:.1f}% range ({best_range.low} – {best_range.high})"
        )
    return best_range


# ─────────────────────────────────────────────
# Breakout Detection
# ─────────────────────────────────────────────

@dataclass
class BreakoutSignal:
    direction: str         # "long" or "short"
    breakout_price: float  # The level that was broken
    confirmed: bool        # Did the next candle confirm (close beyond level)?
    candle_index: int      # Index in the DataFrame where breakout happened
    range_pct: float       # Size of the range that was broken (bigger = more significant)


def detect_breakout(df: pd.DataFrame,
                    consolidation: ConsolidationRange,
                    confirmation_candles: int = 1) -> Optional[BreakoutSignal]:
    """
    Check whether price has broken out of the consolidation range.

    AJ's rule: Don't trade the candle that breaks the line — wait for the
    NEXT candle to close outside the range to confirm it's real.

    Args:
        df:                   OHLCV DataFrame
        consolidation:        The detected range
        confirmation_candles: How many candles must close outside range

    Returns BreakoutSignal or None.
    """
    if len(df) < confirmation_candles + 2:
        return None

    latest_close = df["close"].iloc[-1]
    prev_close   = df["close"].iloc[-2]

    # Check for upward breakout (long signal)
    if (prev_close <= consolidation.high and
            latest_close > consolidation.high):
        confirmed = all(
            df["close"].iloc[-(i + 1)] > consolidation.high
            for i in range(confirmation_candles)
        )
        logger.info(
            f"Upward breakout at {latest_close:.6f} "
            f"(above {consolidation.high:.6f}), confirmed={confirmed}"
        )
        return BreakoutSignal(
            direction="long",
            breakout_price=consolidation.high,
            confirmed=confirmed,
            candle_index=len(df) - 1,
            range_pct=consolidation.range_pct,
        )

    # Check for downward breakout (short signal)
    if (prev_close >= consolidation.low and
            latest_close < consolidation.low):
        confirmed = all(
            df["close"].iloc[-(i + 1)] < consolidation.low
            for i in range(confirmation_candles)
        )
        logger.info(
            f"Downward breakout at {latest_close:.6f} "
            f"(below {consolidation.low:.6f}), confirmed={confirmed}"
        )
        return BreakoutSignal(
            direction="short",
            breakout_price=consolidation.low,
            confirmed=confirmed,
            candle_index=len(df) - 1,
            range_pct=consolidation.range_pct,
        )

    return None


# ─────────────────────────────────────────────
# Trend Detection
# ─────────────────────────────────────────────

@dataclass
class TrendInfo:
    short_trend: str    # "up", "down", "sideways" — local (50 candles)
    long_trend: str     # "up", "down", "sideways" — macro (200 candles)
    converging: bool    # Are short and long trends about to intersect?
    candles_to_intersect: Optional[int]  # Estimated candles until forced resolution


def detect_trend(df: pd.DataFrame,
                 short_period: int = 50,
                 long_period: int = 200) -> TrendInfo:
    """
    Determine short-term and long-term trend direction using linear regression
    on the closing prices.

    AJ's "point of intersection" concept: when a local uptrend is about to
    collide with a broader downtrend, the chart is FORCED to resolve —
    either it breaks the downtrend or it breaks the uptrend. These
    intersections are high-probability setup moments.
    """
    def slope(series: pd.Series) -> float:
        x = np.arange(len(series))
        return float(np.polyfit(x, series.values, 1)[0])

    def classify(s: float, threshold: float = 0.001) -> str:
        norm = s / (series.mean() if (series := series) else 1)  # noqa: F841
        if s > threshold:
            return "up"
        if s < -threshold:
            return "down"
        return "sideways"

    short_series = df["close"].tail(short_period)
    long_series  = df["close"].tail(long_period)

    short_slope = slope(short_series)
    long_slope  = slope(long_series)

    # Normalise slopes relative to current price for comparability
    current_price = df["close"].iloc[-1]
    short_norm = short_slope / current_price
    long_norm  = long_slope  / current_price

    threshold = 0.0002  # 0.02% per candle

    short_trend = "up" if short_norm > threshold else ("down" if short_norm < -threshold else "sideways")
    long_trend  = "up" if long_norm  > threshold else ("down" if long_norm  < -threshold else "sideways")

    # Convergence: opposite trends are on a collision course
    converging = (short_trend == "up" and long_trend == "down") or \
                 (short_trend == "down" and long_trend == "up")

    # Rough estimate of candles until intersection (linear extrapolation)
    candles_to_intersect = None
    if converging and short_slope != long_slope:
        # Using last values of each regression line, find where they meet
        short_last = short_series.iloc[-1]
        long_last  = long_series.iloc[-1]
        diff = abs(short_last - long_last)
        rate = abs(short_slope - long_slope)
        if rate > 0:
            candles_to_intersect = int(diff / rate)

    return TrendInfo(
        short_trend=short_trend,
        long_trend=long_trend,
        converging=converging,
        candles_to_intersect=candles_to_intersect,
    )
