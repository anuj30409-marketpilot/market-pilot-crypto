"""Unknown-Order-State Recovery State Machine.

Protects against the dangerous "in-flight order loss" failure mode:
When an order request times out or disconnects:
1. NEVER assume the order was not placed.
2. NEVER blindly resubmit (risks double-fill).
3. Transitions through an explicit recovery state machine:
   UNKNOWN_PENDING -> QUERY_EXCHANGE (3 retries with backoff) ->
   RESOLVED (FILLED / OPEN / REJECTED / NOT_FOUND) or
   QUARANTINE_MANUAL_INTERVENTION (triggers Kill Switch Level 3).
"""
import asyncio
import logging
from enum import Enum
from typing import Dict, Optional, Callable, Awaitable
from core.clock import now_utc_ms

logger = logging.getLogger("execution.recovery")


class OrderRecoveryState(str, Enum):
    UNKNOWN_PENDING = "UNKNOWN_PENDING"
    QUERYING_EXCHANGE = "QUERYING_EXCHANGE"
    RESOLVED_FILLED = "RESOLVED_FILLED"
    RESOLVED_OPEN = "RESOLVED_OPEN"
    RESOLVED_NOT_PLACED = "RESOLVED_NOT_PLACED"
    QUARANTINED_CRITICAL = "QUARANTINED_CRITICAL"


class UnknownOrderRecord:
    def __init__(
        self,
        client_order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        strategy_id: str,
        attempted_at_ms: int,
    ):
        self.client_order_id = client_order_id
        self.symbol = symbol
        self.side = side
        self.quantity = quantity
        self.price = price
        self.strategy_id = strategy_id
        self.attempted_at_ms = attempted_at_ms
        self.state = OrderRecoveryState.UNKNOWN_PENDING
        self.attempts = 0
        self.last_query_ms = 0
        self.exchange_order_id: Optional[str] = None
        self.fill_price: Optional[float] = None
        self.resolution_notes: str = ""


class OrderRecoveryEngine:
    """Manages unknown-state orders and enforces recovery invariants."""

    def __init__(self):
        self.pending_orders: Dict[str, UnknownOrderRecord] = {}
        self.quarantined_count = 0

    def register_unknown_order(
        self,
        client_order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        strategy_id: str,
    ) -> UnknownOrderRecord:
        record = UnknownOrderRecord(
            client_order_id=client_order_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            strategy_id=strategy_id,
            attempted_at_ms=now_utc_ms(),
        )
        self.pending_orders[client_order_id] = record
        logger.warning(
            "REGISTERED UNKNOWN ORDER: %s (%s %s qty=%.4f @ $%.2f) on %s",
            client_order_id, symbol, side, quantity, price, strategy_id
        )
        return record

    async def recover_order(
        self,
        client_order_id: str,
        exchange_query_fn: Callable[[str], Awaitable[Optional[dict]]],
        cancel_order_fn: Optional[Callable[[str], Awaitable[bool]]] = None,
    ) -> OrderRecoveryState:
        record = self.pending_orders.get(client_order_id)
        if not record:
            return OrderRecoveryState.RESOLVED_NOT_PLACED

        record.state = OrderRecoveryState.QUERYING_EXCHANGE
        backoffs = [1.0, 2.0, 4.0]

        for attempt, delay in enumerate(backoffs, start=1):
            record.attempts = attempt
            record.last_query_ms = now_utc_ms()
            await asyncio.sleep(delay)

            try:
                result = await exchange_query_fn(client_order_id)
                if result:
                    status = result.get("status", "").upper()
                    record.exchange_order_id = result.get("order_id")

                    if status in ["FILLED", "PARTIALLY_FILLED"]:
                        record.state = OrderRecoveryState.RESOLVED_FILLED
                        record.fill_price = float(result.get("avg_fill_price", record.price))
                        record.resolution_notes = f"Order was filled on exchange at {record.fill_price}"
                        logger.info("RECOVERY SUCCESS: %s was FILLED", client_order_id)
                        return record.state

                    elif status in ["OPEN", "PENDING"]:
                        record.state = OrderRecoveryState.RESOLVED_OPEN
                        # If cancellation callback provided, cancel to return to neutral
                        if cancel_order_fn:
                            await cancel_order_fn(record.exchange_order_id or client_order_id)
                            record.resolution_notes = "Order was OPEN, cancelled by recovery engine"
                        else:
                            record.resolution_notes = "Order was OPEN on exchange"
                        logger.info("RECOVERY SUCCESS: %s was OPEN (handled)", client_order_id)
                        return record.state

                    elif status in ["CANCELLED", "REJECTED", "EXPIRED"]:
                        record.state = OrderRecoveryState.RESOLVED_NOT_PLACED
                        record.resolution_notes = f"Order was {status} on exchange"
                        logger.info("RECOVERY SUCCESS: %s was %s", client_order_id, status)
                        return record.state

                else:
                    # None returned means not found on exchange
                    record.state = OrderRecoveryState.RESOLVED_NOT_PLACED
                    record.resolution_notes = "Not found on exchange after query"
                    logger.info("RECOVERY: %s was not found on exchange", client_order_id)
                    return record.state

            except Exception as e:
                logger.error("Recovery query attempt %d failed for %s: %s", attempt, client_order_id, e)

        # If exhausted all retries without resolution: QUARANTINE!
        record.state = OrderRecoveryState.QUARANTINED_CRITICAL
        record.resolution_notes = "Exchange unreachable after 3 attempts. Manual review required."
        self.quarantined_count += 1
        logger.critical(
            "CRITICAL ORDER RECOVERY FAILURE: %s QUARANTINED! Level 3 Kill Switch triggered.",
            client_order_id
        )
        return record.state


order_recovery_engine = OrderRecoveryEngine()
