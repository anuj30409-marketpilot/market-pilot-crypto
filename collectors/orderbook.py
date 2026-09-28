"""Orderbook Tracker and Microstructure Calculator.

Maintains bounded top-20 bid/ask levels in-memory to keep RAM bounded (<30 MB).
Computes Microprice, spread bps, and multi-tier depth imbalances.
"""
from typing import Dict, List, Tuple
from core.contracts import OrderbookSnapshot
from core.clock import now_utc_ms

class BoundedOrderbook:
    def __init__(self, symbol: str, market_type: str = "FUTURES", max_levels: int = 20):
        self.symbol = symbol.upper()
        self.market_type = market_type.upper()
        self.max_levels = max_levels
        
        # Internal dictionaries: price -> quantity
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.last_update_id: int = 0
        self.last_event_time_ms: int = 0

    def apply_snapshot(self, bids: List[List[str]], asks: List[List[str]], last_update_id: int):
        """Initializes or resets orderbook from REST/WS full snapshot."""
        self.bids = {float(p): float(q) for p, q in bids if float(q) > 0}
        self.asks = {float(p): float(q) for p, q in asks if float(q) > 0}
        self.last_update_id = last_update_id
        self._prune()

    def apply_diff(self, bids: List[List[str]], asks: List[List[str]], final_update_id: int, event_time_ms: int):
        """Applies incremental depth update."""
        for p_str, q_str in bids:
            price = float(p_str)
            qty = float(q_str)
            if qty == 0.0:
                self.bids.pop(price, None)
            else:
                self.bids[price] = qty

        for p_str, q_str in asks:
            price = float(p_str)
            qty = float(q_str)
            if qty == 0.0:
                self.asks.pop(price, None)
            else:
                self.asks[price] = qty

        self.last_update_id = final_update_id
        self.last_event_time_ms = event_time_ms
        self._prune()

    def _prune(self):
        """Keeps only top max_levels bids and asks to bound memory footprint."""
        if len(self.bids) > self.max_levels * 2:
            sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)[:self.max_levels]
            self.bids = dict(sorted_bids)

        if len(self.asks) > self.max_levels * 2:
            sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])[:self.max_levels]
            self.asks = dict(sorted_asks)

    def get_snapshot(self, received_ms: int = 0) -> OrderbookSnapshot:
        """Computes current orderbook metrics including microprice and depth imbalance."""
        if not self.bids or not self.asks:
            raise ValueError(f"Orderbook {self.symbol} is uninitialized")

        sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)[:self.max_levels]
        sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])[:self.max_levels]

        best_bid, best_bid_qty = sorted_bids[0]
        best_ask, best_ask_qty = sorted_asks[0]
        mid_price = (best_bid + best_ask) / 2.0
        spread = best_ask - best_bid
        spread_bps = (spread / mid_price) * 10000.0 if mid_price > 0 else 0.0

        # Microprice calculation: weighted by opposite top-level size
        denom = best_bid_qty + best_ask_qty
        microprice = ((best_bid_qty * best_ask) + (best_ask_qty * best_bid)) / denom if denom > 0 else mid_price

        # Depth tiers
        bid_depth_5 = sum(q for _, q in sorted_bids[:5])
        ask_depth_5 = sum(q for _, q in sorted_asks[:5])
        d5_total = bid_depth_5 + ask_depth_5
        imbalance_5 = (bid_depth_5 - ask_depth_5) / d5_total if d5_total > 0 else 0.0

        bid_depth_20 = sum(q for _, q in sorted_bids[:20])
        ask_depth_20 = sum(q for _, q in sorted_asks[:20])
        d20_total = bid_depth_20 + ask_depth_20
        imbalance_20 = (bid_depth_20 - ask_depth_20) / d20_total if d20_total > 0 else 0.0

        return OrderbookSnapshot(
            symbol=self.symbol,
            market_type=self.market_type,
            best_bid=best_bid,
            best_ask=best_ask,
            mid_price=mid_price,
            microprice=microprice,
            spread=spread,
            spread_bps=spread_bps,
            bid_depth_5=bid_depth_5,
            ask_depth_5=ask_depth_5,
            imbalance_5=imbalance_5,
            bid_depth_20=bid_depth_20,
            ask_depth_20=ask_depth_20,
            imbalance_20=imbalance_20,
            update_id=self.last_update_id,
            event_time_ms=self.last_event_time_ms,
            received_at_ms=received_ms or now_utc_ms()
        )
