"""
analysis/support_resistance.py
-------------------------------
Detects key support and resistance levels from historical price data.

Strategy from AJ:
  - Find price zones that have been tested multiple times
  - The more touches, the stronger the level
  - These zones are your alarm triggers and stop-loss anchors
"""

import numpy as np
import pandas as pd
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SRLevel:
    price: float          # The price of the level
    strength: int         # Number of times this level was touched/respected
    level_type: str       # "support" or "resistance"
    last_touch: pd.Timestamp


def find_swing_points(df: pd.DataFrame, lookback: int = 5) -> pd.DataFrame:
    """
    Identify swing highs and swing lows in price data.

    A swing high is a candle whose high is higher than the `lookback`
    candles on each side. Swing low is the inverse.
    """
    highs = []
    lows = []

    for i in range(lookback, len(df) - lookback):
        window_high = df["high"].iloc[i - lookback: i + lookback + 1]
        window_low  = df["low"].iloc[i - lookback: i + lookback + 1]

        if df["high"].iloc[i] == window_high.max():
            highs.append(i)
        if df["low"].iloc[i] == window_low.min():
            lows.append(i)

    df = df.copy()
    df["swing_high"] = False
    df["swing_low"]  = False
    df.loc[highs, "swing_high"] = True
    df.loc[lows,  "swing_low"]  = True
    return df


def cluster_levels(prices: list[float], tolerance_pct: float = 0.015) -> list[float]:
    """
    Cluster nearby price levels into single zones.

    Two prices are in the same cluster if they're within `tolerance_pct`
    of each other (default 1.5%). Returns the average price per cluster.
    """
    if not prices:
        return []

    prices = sorted(prices)
    clusters = [[prices[0]]]

    for price in prices[1:]:
        if (price - clusters[-1][-1]) / clusters[-1][-1] <= tolerance_pct:
            clusters[-1].append(price)
        else:
            clusters.append([price])

    return [float(np.mean(c)) for c in clusters]


def find_sr_levels(df: pd.DataFrame, lookback: int = 5,
                   tolerance_pct: float = 0.015,
                   min_touches: int = 2) -> list[SRLevel]:
    """
    Full pipeline: swing points → cluster → score by touch count.

    Args:
        df:            OHLCV DataFrame
        lookback:      Candles each side to confirm a swing point
        tolerance_pct: Price proximity to merge into one zone
        min_touches:   Minimum touches required to count as a valid level

    Returns:
        List of SRLevel objects, sorted by strength (strongest first)
    """
    df_swings = find_swing_points(df, lookback)

    swing_high_prices = df_swings.loc[df_swings["swing_high"], "high"].tolist()
    swing_low_prices  = df_swings.loc[df_swings["swing_low"], "low"].tolist()

    all_prices = swing_high_prices + swing_low_prices
    clustered  = cluster_levels(all_prices, tolerance_pct)

    current_price = df["close"].iloc[-1]
    levels = []

    for level_price in clustered:
        # Count how many candles touched (came within tolerance of) this level
        touches = 0
        last_touch_idx = None

        for i, row in df.iterrows():
            high_near = abs(row["high"] - level_price) / level_price <= tolerance_pct
            low_near  = abs(row["low"]  - level_price) / level_price <= tolerance_pct
            if high_near or low_near:
                touches += 1
                last_touch_idx = i

        if touches >= min_touches:
            level_type = "support" if level_price < current_price else "resistance"
            levels.append(SRLevel(
                price=round(level_price, 8),
                strength=touches,
                level_type=level_type,
                last_touch=df.loc[last_touch_idx, "timestamp"] if last_touch_idx is not None else df["timestamp"].iloc[-1],
            ))

    levels.sort(key=lambda x: x.strength, reverse=True)
    logger.debug(f"Found {len(levels)} S/R levels (min {min_touches} touches)")
    return levels


def nearest_sr_level(levels: list[SRLevel], current_price: float,
                     direction: str = "both") -> SRLevel | None:
    """
    Find the nearest S/R level above (resistance), below (support), or either.

    Args:
        direction: "support", "resistance", or "both"
    """
    candidates = []
    for lvl in levels:
        if direction == "both":
            candidates.append((abs(lvl.price - current_price), lvl))
        elif direction == "support" and lvl.price < current_price:
            candidates.append((current_price - lvl.price, lvl))
        elif direction == "resistance" and lvl.price > current_price:
            candidates.append((lvl.price - current_price, lvl))

    if not candidates:
        return None
    return min(candidates, key=lambda x: x[0])[1]


def price_near_level(price: float, level: SRLevel, tolerance_pct: float = 0.02) -> bool:
    """Return True if price is within tolerance% of a known S/R level."""
    return abs(price - level.price) / level.price <= tolerance_pct
