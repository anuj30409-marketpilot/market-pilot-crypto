"""Realistic slippage simulator using live L2 orderbook depth.

Walks the actual bid/ask levels to compute a weighted average fill price
for a given notional size. Returns slippage in bps vs the mid price.

No external dependencies — uses the in-memory BoundedOrderbook already
maintained by the WebSocket collector.
"""
from dataclasses import dataclass
from typing import List, Tuple, Optional
import logging

logger = logging.getLogger("paper.slippage")

# Binance USD-M Futures taker fee (bps)
TAKER_FEE_BPS = 4.0  # 0.04%

# Binance USD-M Futures maker fee (bps) — cuts fee friction by 50%
MAKER_FEE_BPS = 2.0  # 0.02%

# Adverse selection drift penalty for passive maker fills (AFML institutional standard)
ADVERSE_SELECTION_HAIRCUT_BPS = 1.0

# Extra gap-risk multiplier applied to SL/forced exits
# (fast market assumption: actual fill is worse than model)
GAP_RISK_MULTIPLIER = 1.5


@dataclass
class FillResult:
    avg_price: float          # Weighted average fill price
    slippage_bps: float       # (avg_price - mid) / mid * 10000 (positive = worse for buyer)
    filled_notional: float    # USDT actually filled
    requested_notional: float
    partial: bool             # True if book was too thin to fill in full
    levels_consumed: int      # How many orderbook levels were walked
    fee_usdt: float           # Taker/maker fee in USDT


def simulate_maker_fill(
    side: str,                          # "BUY" or "SELL"
    notional_usdt: float,               # How much USDT to buy/sell
    best_price: float,                  # Best bid (for BUY) or best ask (for SELL)
    mid_price: float,
) -> FillResult:
    """Simulates a non-optimistic passive Maker fill at top-of-book.

    Captures the passive limit price with zero book-walk slippage, earns maker fee tier (2.0 bps),
    and applies an institutional 1.0 bps adverse selection penalty to ensure paper rigor.
    """
    if best_price <= 0.0:
        best_price = mid_price

    # Adverse selection penalty: buyer fills slightly higher than un-shocked limit, seller slightly lower
    if side == "BUY":
        fill_price = best_price * (1.0 + (ADVERSE_SELECTION_HAIRCUT_BPS / 10_000))
    else:
        fill_price = best_price * (1.0 - (ADVERSE_SELECTION_HAIRCUT_BPS / 10_000))

    slippage_bps = abs((fill_price - mid_price) / mid_price * 10_000)
    fee_usdt = notional_usdt * (MAKER_FEE_BPS / 10_000)

    return FillResult(
        avg_price=fill_price,
        slippage_bps=slippage_bps,
        filled_notional=notional_usdt,
        requested_notional=notional_usdt,
        partial=False,
        levels_consumed=1,
        fee_usdt=fee_usdt,
    )



def simulate_fill(
    side: str,                          # "BUY" or "SELL"
    notional_usdt: float,               # How much USDT to buy/sell
    levels: List[Tuple[float, float]],  # [(price, qty_base), ...] — already sorted best-first
    mid_price: float,
    gap_risk: bool = False,             # True for SL/forced exits
) -> FillResult:
    """Walk the orderbook levels and compute realistic fill price."""
    remaining = notional_usdt
    total_cost = 0.0
    total_base = 0.0
    levels_used = 0

    for price, qty_base in levels:
        if remaining <= 0:
            break
        available_notional = price * qty_base
        fill_notional = min(remaining, available_notional)
        fill_base = fill_notional / price

        total_cost += fill_notional
        total_base += fill_base
        remaining -= fill_notional
        levels_used += 1

    if total_base == 0:
        # Empty book — return mid with large slippage
        logger.warning(f"Empty orderbook on {side} side, returning mid price")
        return FillResult(
            avg_price=mid_price,
            slippage_bps=50.0,
            filled_notional=0.0,
            requested_notional=notional_usdt,
            partial=True,
            levels_consumed=0,
            fee_usdt=0.0,
        )

    avg_price = total_cost / total_base
    filled_notional = total_cost
    partial = remaining > 0.01  # More than $0.01 unfilled

    # Slippage: positive = paid more (worse for buyer), negative = received less (worse for seller)
    raw_slippage_bps = (avg_price - mid_price) / mid_price * 10_000
    if side == "SELL":
        raw_slippage_bps = -raw_slippage_bps  # Seller gets less than mid

    if gap_risk:
        raw_slippage_bps *= GAP_RISK_MULTIPLIER

    fee_usdt = filled_notional * (TAKER_FEE_BPS / 10_000)

    return FillResult(
        avg_price=avg_price,
        slippage_bps=abs(raw_slippage_bps),
        filled_notional=filled_notional,
        requested_notional=notional_usdt,
        partial=partial,
        levels_consumed=levels_used,
        fee_usdt=fee_usdt,
    )


def get_ask_levels(orderbook) -> List[Tuple[float, float]]:
    """Extract canonically sorted ask levels from BoundedOrderbook (best/lowest ask first)."""
    try:
        snap = orderbook.asks
        return sorted(
            ((float(p), float(q)) for p, q in snap.items() if float(q) > 0.0),
            key=lambda x: x[0],
        )
    except Exception:
        return []


def get_bid_levels(orderbook) -> List[Tuple[float, float]]:
    """Extract canonically sorted bid levels from BoundedOrderbook (best/highest bid first)."""
    try:
        snap = orderbook.bids
        return sorted(
            ((float(p), float(q)) for p, q in snap.items() if float(q) > 0.0),
            key=lambda x: x[0],
            reverse=True,
        )
    except Exception:
        return []
