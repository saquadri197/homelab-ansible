"""
trading/executor.py
--------------------
Handles all order placement and position management.

Implements AJ's exact execution rules:
  - Isolated margin only (never cross)
  - Configurable leverage (3x–10x depending on confidence score)
  - Stop loss placed at nearest S/R level + buffer
  - Trailing stop: move SL into profit as trade progresses
  - Position sized by risk % of account, not arbitrary dollar amount
"""

import ccxt
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Position tracking
# ─────────────────────────────────────────────

@dataclass
class OpenPosition:
    symbol: str
    direction: str              # "long" or "short"
    entry_price: float
    quantity: float             # Contract size / coin quantity
    leverage: int
    stop_loss: float
    initial_stop_loss: float    # Original SL (for reference)
    order_id: str
    sl_order_id: Optional[str] = None
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    highest_profit_pct: float = 0.0   # For trailing SL tracking
    is_open: bool = True


# ─────────────────────────────────────────────
# Risk & Position Sizing
# ─────────────────────────────────────────────

def calculate_position_size(account_balance: float,
                             entry_price: float,
                             stop_loss_price: float,
                             leverage: int,
                             risk_pct: float = 1.0) -> float:
    """
    Calculate position size using the risk-per-trade method.

    Rule: Never risk more than `risk_pct`% of your account on a single trade.

    Example:
        Account: $10,000
        Risk per trade: 1% → max loss = $100
        Entry: $0.75, Stop loss: $0.70 → risk per coin = $0.05
        Position size = $100 / $0.05 = 2000 coins

    With leverage applied, the margin required = position_value / leverage.
    """
    if entry_price <= 0 or stop_loss_price <= 0:
        raise ValueError("Entry and stop loss prices must be positive")

    risk_per_coin = abs(entry_price - stop_loss_price)
    if risk_per_coin == 0:
        raise ValueError("Entry price cannot equal stop loss price")

    max_loss_usd   = account_balance * (risk_pct / 100)
    coins_to_buy   = max_loss_usd / risk_per_coin

    logger.info(
        f"Position size calc: balance={account_balance:.2f}, "
        f"risk={risk_pct}% (${max_loss_usd:.2f}), "
        f"entry={entry_price}, SL={stop_loss_price}, "
        f"coins={coins_to_buy:.4f}"
    )
    return coins_to_buy


def select_leverage(confluence_score: int) -> int:
    """
    Map confluence score to leverage level.
    AJ's approach: higher confidence = higher leverage, but never excessive.

    Score 55–64:  3x  (borderline setup — be cautious)
    Score 65–74:  5x  (solid setup)
    Score 75–84:  7x  (strong setup)
    Score 85+:    9x  (high-conviction setup)
    """
    if confluence_score >= 85:
        return 9
    if confluence_score >= 75:
        return 7
    if confluence_score >= 65:
        return 5
    return 3


# ─────────────────────────────────────────────
# Order Execution
# ─────────────────────────────────────────────

def set_leverage(exchange: ccxt.Exchange, symbol: str, leverage: int) -> None:
    """Set leverage for a symbol on the exchange."""
    try:
        exchange.set_leverage(leverage, symbol)
        logger.info(f"Leverage set to {leverage}x for {symbol}")
    except ccxt.ExchangeError as e:
        logger.warning(f"Could not set leverage ({e}) — proceeding with exchange default")


def set_margin_mode_isolated(exchange: ccxt.Exchange, symbol: str) -> None:
    """
    Switch to ISOLATED margin mode.
    AJ's rule: always use isolated, never cross.
    Isolated = only the margin in this trade is at risk.
    """
    try:
        exchange.set_margin_mode("isolated", symbol)
        logger.info(f"Margin mode set to ISOLATED for {symbol}")
    except ccxt.ExchangeError as e:
        # Some exchanges don't support the API call but default to isolated
        logger.warning(f"Could not set margin mode ({e})")


def place_market_order(exchange: ccxt.Exchange,
                        symbol: str,
                        direction: str,
                        quantity: float,
                        params: dict = None) -> dict:
    """
    Place a market order.

    Args:
        direction: "long" (buy) or "short" (sell)
        quantity:  Amount of the base asset to buy/sell
        params:    Extra exchange-specific params

    Returns:
        CCXT order dict
    """
    side = "buy" if direction == "long" else "sell"
    params = params or {}

    order = exchange.create_market_order(
        symbol=symbol,
        side=side,
        amount=quantity,
        params=params,
    )
    logger.info(
        f"Market order placed: {direction.upper()} {quantity} {symbol} "
        f"@ market | order_id={order['id']}"
    )
    return order


def place_stop_loss_order(exchange: ccxt.Exchange,
                           symbol: str,
                           direction: str,
                           quantity: float,
                           stop_price: float) -> dict:
    """
    Place a stop-loss order to protect the position.

    For a LONG position, stop loss is a SELL stop.
    For a SHORT position, stop loss is a BUY stop.
    """
    sl_side = "sell" if direction == "long" else "buy"

    try:
        # Try exchange-native stop market order
        order = exchange.create_order(
            symbol=symbol,
            type="stop_market",
            side=sl_side,
            amount=quantity,
            params={"stopPrice": stop_price, "reduceOnly": True},
        )
    except ccxt.ExchangeError:
        # Fallback: stop-limit with 0.5% slippage buffer
        limit_price = stop_price * (0.995 if direction == "long" else 1.005)
        order = exchange.create_order(
            symbol=symbol,
            type="stop",
            side=sl_side,
            amount=quantity,
            price=limit_price,
            params={"stopPrice": stop_price, "reduceOnly": True},
        )

    logger.info(
        f"Stop loss placed at {stop_price} for {direction.upper()} "
        f"{quantity} {symbol} | order_id={order['id']}"
    )
    return order


def open_trade(exchange: ccxt.Exchange,
               symbol: str,
               direction: str,
               entry_price: float,
               stop_loss_price: float,
               account_balance: float,
               confluence_score: int,
               config: dict) -> Optional[OpenPosition]:
    """
    Full trade entry pipeline:
      1. Set isolated margin mode
      2. Set leverage based on confidence
      3. Size position by risk %
      4. Place market entry order
      5. Place stop loss order

    Returns OpenPosition object, or None if something fails.
    """
    leverage   = select_leverage(confluence_score)
    risk_pct   = config.get("risk_per_trade_pct", 1.0)

    try:
        set_margin_mode_isolated(exchange, symbol)
        set_leverage(exchange, symbol, leverage)

        quantity = calculate_position_size(
            account_balance=account_balance,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            leverage=leverage,
            risk_pct=risk_pct,
        )

        # Apply exchange minimum quantity constraints
        markets = exchange.load_markets()
        if symbol in markets:
            min_qty = markets[symbol].get("limits", {}).get("amount", {}).get("min", 0)
            if quantity < min_qty:
                logger.warning(
                    f"Calculated quantity {quantity:.6f} is below exchange "
                    f"minimum {min_qty} for {symbol}. Skipping trade."
                )
                return None

        # Place entry
        entry_order = place_market_order(exchange, symbol, direction, quantity)
        time.sleep(0.5)  # Brief pause before placing SL

        # Place stop loss
        sl_order = place_stop_loss_order(exchange, symbol, direction, quantity, stop_loss_price)

        position = OpenPosition(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            quantity=quantity,
            leverage=leverage,
            stop_loss=stop_loss_price,
            initial_stop_loss=stop_loss_price,
            order_id=entry_order["id"],
            sl_order_id=sl_order.get("id"),
        )

        logger.info(
            f"Trade opened: {direction.upper()} {symbol} | "
            f"Entry={entry_price} | SL={stop_loss_price} | "
            f"Qty={quantity:.4f} | Leverage={leverage}x"
        )
        return position

    except Exception as e:
        logger.error(f"Failed to open trade on {symbol}: {e}")
        return None


# ─────────────────────────────────────────────
# Trailing Stop Loss
# ─────────────────────────────────────────────

def update_trailing_stop(exchange: ccxt.Exchange,
                          position: OpenPosition,
                          current_price: float,
                          trail_pct: float = 0.03) -> OpenPosition:
    """
    AJ's trailing stop logic:
      - Instead of a fixed take profit, move the stop loss into profit
        as the trade moves in your favour.
      - The worst outcome becomes a smaller gain, not a loss.
      - trail_pct: how far below the peak to trail (default 3%)

    For a LONG:
        New SL = current_price * (1 - trail_pct)
        Only move SL UP, never down.

    For a SHORT:
        New SL = current_price * (1 + trail_pct)
        Only move SL DOWN, never up.
    """
    if position.direction == "long":
        new_sl = current_price * (1 - trail_pct)
        if new_sl > position.stop_loss:
            logger.info(
                f"Trailing SL UP: {position.stop_loss:.6f} → {new_sl:.6f} "
                f"(price={current_price:.6f})"
            )
            _update_sl_on_exchange(exchange, position, new_sl)
            position.stop_loss = new_sl

    elif position.direction == "short":
        new_sl = current_price * (1 + trail_pct)
        if new_sl < position.stop_loss:
            logger.info(
                f"Trailing SL DOWN: {position.stop_loss:.6f} → {new_sl:.6f} "
                f"(price={current_price:.6f})"
            )
            _update_sl_on_exchange(exchange, position, new_sl)
            position.stop_loss = new_sl

    return position


def _update_sl_on_exchange(exchange: ccxt.Exchange,
                            position: OpenPosition,
                            new_sl_price: float) -> None:
    """Cancel the existing SL order and place a new one at the updated price."""
    if position.sl_order_id:
        try:
            exchange.cancel_order(position.sl_order_id, position.symbol)
        except ccxt.ExchangeError as e:
            logger.warning(f"Could not cancel old SL order: {e}")

    new_sl_order = place_stop_loss_order(
        exchange, position.symbol,
        position.direction, position.quantity, new_sl_price
    )
    position.sl_order_id = new_sl_order.get("id")


def close_position(exchange: ccxt.Exchange, position: OpenPosition) -> dict:
    """Market close an open position."""
    close_side = "sell" if position.direction == "long" else "buy"
    order = exchange.create_market_order(
        symbol=position.symbol,
        side=close_side,
        amount=position.quantity,
        params={"reduceOnly": True},
    )
    position.is_open = False
    logger.info(
        f"Position closed: {position.direction.upper()} {position.symbol} "
        f"| order_id={order['id']}"
    )
    return order


def get_account_balance(exchange: ccxt.Exchange, currency: str = "USDT") -> float:
    """Fetch available USDT balance."""
    balance = exchange.fetch_balance()
    return float(balance.get(currency, {}).get("free", 0))
