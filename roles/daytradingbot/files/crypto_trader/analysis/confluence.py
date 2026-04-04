"""
analysis/confluence.py
-----------------------
The Confluence Scoring Engine — the heart of AJ's checklist.

When an alarm fires (price hits a key S/R level or breaks a range),
this module runs through each indicator and scores the overall setup.
Only trade if the score is high enough — otherwise, do nothing.

Scoring system:
  Each positive signal adds points. Signals are weighted by importance.
  Minimum score to trigger a trade is configurable in config.yaml.

  Max possible score: ~100 points

  Weights used here are based on AJ's stated priorities:
    - S/R level touch:          25 pts  (most important)
    - RSI/MACD divergence:      20 pts
    - Money flow (MFI):         15 pts
    - EMA position/cross:       15 pts
    - Fibonacci zone:           15 pts
    - Breakout confirmed:       10 pts  (bonus on top of S/R)
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from analysis.support_resistance import SRLevel, price_near_level
from analysis.indicators import (
    calc_rsi, detect_rsi_divergence,
    calc_macd, macd_crossover,
    calc_ema, price_vs_ema,
    calc_money_flow_index, money_flow_signal,
    calc_fibonacci,
)
from analysis.patterns import (
    ConsolidationRange, BreakoutSignal, TrendInfo,
    find_consolidation, detect_breakout, detect_trend,
)

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class ConfluenceResult:
    score: int                          # Total score (0–100)
    direction: str                      # "long", "short", or "none"
    signals: dict[str, str]             # Human-readable signal breakdown
    near_sr_level: Optional[SRLevel]    # The S/R level that triggered
    consolidation: Optional[ConsolidationRange]
    breakout: Optional[BreakoutSignal]
    trend: Optional[TrendInfo]
    fib_zone: str                       # Description of fib position
    suggested_stop_loss: Optional[float]
    suggested_entry: Optional[float]

    def is_tradeable(self, min_score: int = 55) -> bool:
        return self.score >= min_score and self.direction != "none"

    def summary(self) -> str:
        lines = [
            f"Score: {self.score}/100  |  Direction: {self.direction.upper()}",
            f"Tradeable: {'YES ✅' if self.is_tradeable() else 'NO ❌'}",
            "",
            "Signal Breakdown:",
        ]
        for key, val in self.signals.items():
            lines.append(f"  {key}: {val}")
        if self.near_sr_level:
            lines.append(f"  Near S/R: {self.near_sr_level.price} ({self.near_sr_level.strength} touches)")
        if self.suggested_entry:
            lines.append(f"  Entry: {self.suggested_entry}")
        if self.suggested_stop_loss:
            lines.append(f"  Stop Loss: {self.suggested_stop_loss}")
        return "\n".join(lines)


def run_confluence_check(df_1h: pd.DataFrame,
                          df_4h: pd.DataFrame,
                          sr_levels: list[SRLevel],
                          config: dict) -> ConfluenceResult:
    """
    Run the full confluence checklist on a symbol.

    Uses 1h data for fine-grained entry signals and 4h data for
    broader trend context — multi-timeframe analysis.

    Args:
        df_1h:     1-hour OHLCV DataFrame
        df_4h:     4-hour OHLCV DataFrame
        sr_levels: Pre-computed S/R levels for this symbol
        config:    The user's config dict

    Returns:
        ConfluenceResult with score and trade parameters.
    """
    score   = 0
    signals = {}
    direction_votes = {"long": 0, "short": 0}

    current_price = float(df_1h["close"].iloc[-1])

    # ── 1. S/R LEVEL PROXIMITY (25 pts) ──────────────────────────────────────
    near_sr = None
    for lvl in sr_levels[:10]:  # Check top 10 strongest levels
        if price_near_level(current_price, lvl, tolerance_pct=0.025):
            near_sr = lvl
            score  += 25
            signals["S/R Level"] = (
                f"At {lvl.level_type} level {lvl.price} "
                f"({lvl.strength} touches) +25"
            )
            if lvl.level_type == "support":
                direction_votes["long"] += 3
            else:
                direction_votes["short"] += 3
            break

    if not near_sr:
        signals["S/R Level"] = "Not near any key level +0"

    # ── 2. RSI DIVERGENCE (20 pts) ────────────────────────────────────────────
    rsi_1h = calc_rsi(df_1h)
    div    = detect_rsi_divergence(df_1h, rsi_1h)

    if div == "bullish":
        score  += 20
        signals["RSI Divergence"] = "Bullish divergence (price down, RSI up) +20"
        direction_votes["long"] += 2
    elif div == "bearish":
        score  += 20
        signals["RSI Divergence"] = "Bearish divergence (price up, RSI down) +20"
        direction_votes["short"] += 2
    else:
        rsi_val = round(float(rsi_1h.iloc[-1]), 1)
        signals["RSI Divergence"] = f"No divergence (RSI={rsi_val}) +0"

    # ── 3. MACD CROSSOVER (part of divergence score) ─────────────────────────
    macd_df  = calc_macd(df_1h)
    crossover = macd_crossover(macd_df)

    if crossover == "bullish_cross":
        score  += 5
        signals["MACD"] = "Bullish crossover +5"
        direction_votes["long"] += 1
    elif crossover == "bearish_cross":
        score  += 5
        signals["MACD"] = "Bearish crossover +5"
        direction_votes["short"] += 1
    else:
        hist_val = round(float(macd_df["histogram"].iloc[-1]), 6)
        signals["MACD"] = f"No crossover (hist={hist_val:+.6f}) +0"

    # ── 4. MONEY FLOW (MFI) (15 pts) ─────────────────────────────────────────
    mfi    = calc_money_flow_index(df_1h)
    mf_sig = money_flow_signal(mfi)

    if mf_sig in ("oversold", "turning_green"):
        score  += 15
        signals["Money Flow"] = f"{mf_sig.replace('_', ' ').title()} — long bias +15"
        direction_votes["long"] += 2
    elif mf_sig == "overbought":
        score  += 15
        signals["Money Flow"] = "Overbought — short bias +15"
        direction_votes["short"] += 2
    else:
        mfi_val = round(float(mfi.iloc[-1]), 1)
        signals["Money Flow"] = f"Neutral (MFI={mfi_val}) +0"

    # ── 5. EMA (50) POSITION (15 pts) ─────────────────────────────────────────
    ema50    = calc_ema(df_1h, period=50)
    ema_sig  = price_vs_ema(df_1h, ema50)

    if ema_sig == "crossing":
        score += 15
        # Determine cross direction
        cross_dir = "long" if df_1h["close"].iloc[-1] > ema50.iloc[-1] else "short"
        signals["50 EMA"] = f"Crossing 50 EMA ({'bullish' if cross_dir == 'long' else 'bearish'}) +15"
        direction_votes[cross_dir] += 2
    elif ema_sig == "above_ema":
        score += 8
        signals["50 EMA"] = "Price above 50 EMA (bullish context) +8"
        direction_votes["long"] += 1
    else:
        score += 8
        signals["50 EMA"] = "Price below 50 EMA (bearish context) +8"
        direction_votes["short"] += 1

    # ── 6. FIBONACCI ZONE (15 pts) ────────────────────────────────────────────
    fib      = calc_fibonacci(df_4h, lookback=100)
    fib_zone = "none"

    if fib.in_deep_accumulation_zone(current_price):
        score   += 15
        fib_zone = "under_786"
        signals["Fibonacci"] = "Under 78.6% retracement (deep accumulation zone) +15"
        direction_votes["long"] += 2
    elif fib.in_accumulation_zone(current_price):
        score   += 10
        fib_zone = "under_618"
        signals["Fibonacci"] = "Under 61.8% retracement (accumulation zone) +10"
        direction_votes["long"] += 1
    else:
        nearest_label, nearest_price = fib.nearest_level(current_price)
        signals["Fibonacci"] = f"Near {nearest_label} fib ({nearest_price:.6f}) +0"

    # ── 7. BREAKOUT CONFIRMATION BONUS (10 pts) ───────────────────────────────
    consolidation = find_consolidation(df_1h, min_candles=config.get("min_consolidation_candles", 8))
    breakout      = None

    if consolidation:
        breakout = detect_breakout(df_1h, consolidation)
        if breakout and breakout.confirmed:
            score += 10
            signals["Breakout"] = (
                f"Confirmed {breakout.direction} breakout from "
                f"{breakout.range_pct:.1f}% range +10"
            )
            direction_votes[breakout.direction] += 3
        elif breakout:
            score += 5
            signals["Breakout"] = (
                f"Unconfirmed {breakout.direction} breakout "
                f"(wait for close) +5"
            )
            direction_votes[breakout.direction] += 1
        else:
            signals["Breakout"] = (
                f"Consolidating ({consolidation.range_pct:.1f}% range, "
                f"{consolidation.candles_in_range} candles) — no break yet +0"
            )

    # ── 8. TREND CONTEXT (from 4h) ────────────────────────────────────────────
    trend = detect_trend(df_4h)
    if trend.converging:
        score += 5
        eta = f"~{trend.candles_to_intersect} candles" if trend.candles_to_intersect else "soon"
        signals["Trend"] = (
            f"⚠️  Convergence point {eta}: {trend.short_trend} short-term "
            f"vs {trend.long_trend} long-term — forced break imminent +5"
        )
    else:
        signals["Trend"] = (
            f"Short-term: {trend.short_trend}, "
            f"Long-term: {trend.long_trend} +0"
        )

    # ── DETERMINE OVERALL DIRECTION ───────────────────────────────────────────
    if direction_votes["long"] > direction_votes["short"]:
        direction = "long"
    elif direction_votes["short"] > direction_votes["long"]:
        direction = "short"
    else:
        direction = "none"

    # ── CALCULATE ENTRY AND STOP LOSS ─────────────────────────────────────────
    suggested_entry     = current_price
    suggested_stop_loss = None
    sl_buffer_pct       = config.get("stop_loss_buffer_pct", 0.015)  # 1.5% default

    if direction == "long":
        # Stop loss: just below the nearest support level
        for lvl in sorted(sr_levels, key=lambda x: x.price, reverse=True):
            if lvl.price < current_price:
                suggested_stop_loss = round(lvl.price * (1 - sl_buffer_pct), 8)
                break
        if not suggested_stop_loss and consolidation:
            suggested_stop_loss = round(consolidation.low * (1 - sl_buffer_pct), 8)

    elif direction == "short":
        # Stop loss: just above the nearest resistance level
        for lvl in sorted(sr_levels, key=lambda x: x.price):
            if lvl.price > current_price:
                suggested_stop_loss = round(lvl.price * (1 + sl_buffer_pct), 8)
                break
        if not suggested_stop_loss and consolidation:
            suggested_stop_loss = round(consolidation.high * (1 + sl_buffer_pct), 8)

    score = min(score, 100)  # Cap at 100

    return ConfluenceResult(
        score=score,
        direction=direction,
        signals=signals,
        near_sr_level=near_sr,
        consolidation=consolidation,
        breakout=breakout,
        trend=trend,
        fib_zone=fib_zone,
        suggested_stop_loss=suggested_stop_loss,
        suggested_entry=suggested_entry,
    )
