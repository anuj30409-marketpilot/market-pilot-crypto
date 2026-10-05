"""Cross-Venue Basis & Execution Parity Engine (INV-CRYPTO-021).

Enforces strict cross-venue execution constraints:
1. No Binance research signal may be routed to Delta Exchange India for live execution
   unless real-time basis parity and liquidity parity are demonstrably verified.
2. Monitors Binance vs Delta price dislocation in basis points.
3. Quantifies spread inflation and execution drag before order dispatch.
"""
import logging
from typing import Dict, Optional, Tuple
from core.clock import now_utc_ms

logger = logging.getLogger("execution.parity")

MAX_TOLERATED_DISLOCATION_BPS = 15.0  # Max acceptable price dislocation
MAX_TOLERATED_DELTA_SPREAD_BPS = 15.0  # Max acceptable Delta bid-ask spread
MAX_STALENESS_MS = 15000  # 15 seconds staleness threshold
DELTA_INDIA_TICKERS_URL = "https://api.india.delta.exchange/v2/tickers"
DELTA_SYMBOL_MAP = {
    "BTCUSD": "BTCUSDT",
    "ETHUSD": "ETHUSDT",
    "BTCUSDT": "BTCUSDT",
    "ETHUSDT": "ETHUSDT",
}


class VenuePriceState:
    def __init__(self, venue: str, symbol: str):
        self.venue = venue
        self.symbol = symbol
        self.mark_price: float = 0.0
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.spread_bps: float = 0.0
        self.last_update_ms: int = 0

    def update(self, mark: float, bid: float, ask: float, timestamp_ms: int):
        self.mark_price = mark
        self.best_bid = bid
        self.best_ask = ask
        mid = (bid + ask) / 2.0 if (bid + ask) > 0 else mark
        self.spread_bps = ((ask - bid) / mid * 10000.0) if mid > 0 else 0.0
        self.last_update_ms = timestamp_ms

    def is_fresh(self, now_ms: int) -> bool:
        return self.mark_price > 0 and (now_ms - self.last_update_ms) <= MAX_STALENESS_MS


class CrossVenueParityEngine:
    """Validates whether research signals on Binance translate faithfully to Delta India."""

    def __init__(self):
        # symbol -> VenuePriceState
        self.binance_prices: Dict[str, VenuePriceState] = {}
        self.delta_prices: Dict[str, VenuePriceState] = {}

    def update_binance_price(self, symbol: str, mark: float, bid: float, ask: float, timestamp_ms: int):
        upper = symbol.upper()
        if upper not in self.binance_prices:
            self.binance_prices[upper] = VenuePriceState("BINANCE", upper)
        self.binance_prices[upper].update(mark, bid, ask, timestamp_ms)

    def update_delta_price(self, symbol: str, mark: float, bid: float, ask: float, timestamp_ms: int):
        upper = symbol.upper()
        if upper not in self.delta_prices:
            self.delta_prices[upper] = VenuePriceState("DELTA_INDIA", upper)
        self.delta_prices[upper].update(mark, bid, ask, timestamp_ms)

    async def poll_delta_prices(self):
        """Fetches live mark price and top-of-book quotes from Delta Exchange India."""
        try:
            import httpx
            async with httpx.AsyncClient(timeout=4.0) as client:
                resp = await client.get(DELTA_INDIA_TICKERS_URL)
                if resp.status_code == 200:
                    now_ms = now_utc_ms()
                    data = resp.json().get("result", [])
                    for item in data:
                        sym = item.get("symbol", "")
                        target_sym = DELTA_SYMBOL_MAP.get(sym)
                        if target_sym:
                            mark = float(item.get("mark_price", 0.0) or 0.0)
                            quotes = item.get("quotes", {}) or {}
                            bid = float(quotes.get("best_bid", 0.0) or mark)
                            ask = float(quotes.get("best_ask", 0.0) or mark)
                            if mark > 0:
                                self.update_delta_price(target_sym, mark, bid, ask, now_ms)
        except Exception as e:
            logger.debug("Failed to poll Delta India tickers: %s", e)

    async def run_delta_poller(self, interval_seconds: float = 3.0):
        """Continuously polls Delta India market tickers in background."""
        logger.info("Starting Delta India Parity Feed Poller (interval=%.1fs)", interval_seconds)
        while True:
            await self.poll_delta_prices()
            await asyncio.sleep(interval_seconds)

    def evaluate_parity(self, symbol: str, now_ms: Optional[int] = None) -> Tuple[bool, str, dict]:
        """Evaluates whether symbol satisfies INV-CRYPTO-021 parity invariants.

        Returns:
            (is_parity_ok, status_code, telemetry_dict)
        """
        now_ms = now_ms or now_utc_ms()
        upper = symbol.upper()

        b_state = self.binance_prices.get(upper)
        d_state = self.delta_prices.get(upper)

        if not b_state or not b_state.is_fresh(now_ms):
            return False, "PARITY_BINANCE_UNAVAILABLE", {"reason": "Binance price feed unavailable or stale"}

        if not d_state or not d_state.is_fresh(now_ms):
            return False, "PARITY_DELTA_FEED_DISCONNECTED", {
                "binance_mark": b_state.mark_price,
                "delta_mark": 0.0,
                "dislocation_bps": 0.0,
                "reason": "Delta India live feed not yet connected or stale"
            }

        # Calculate basis dislocation
        dislocation_bps = ((d_state.mark_price - b_state.mark_price) / b_state.mark_price) * 10000.0
        abs_dislocation = abs(dislocation_bps)

        telemetry = {
            "symbol": upper,
            "binance_mark": b_state.mark_price,
            "delta_mark": d_state.mark_price,
            "binance_spread_bps": b_state.spread_bps,
            "delta_spread_bps": d_state.spread_bps,
            "dislocation_bps": round(dislocation_bps, 2),
            "timestamp_ms": now_ms,
        }

        # Check spread illiquidity on Delta
        if d_state.spread_bps > MAX_TOLERATED_DELTA_SPREAD_BPS:
            telemetry["reason"] = f"Delta spread too wide ({d_state.spread_bps:.1f} bps > {MAX_TOLERATED_DELTA_SPREAD_BPS} bps)"
            return False, "PARITY_DELTA_ILLIQUID", telemetry

        # Check price dislocation
        if abs_dislocation > MAX_TOLERATED_DISLOCATION_BPS:
            telemetry["reason"] = f"Price dislocation too high ({abs_dislocation:.1f} bps > {MAX_TOLERATED_DISLOCATION_BPS} bps)"
            return False, "PARITY_DISLOCATED", telemetry

        telemetry["reason"] = "Parity within verified institutional tolerances"
        return True, "PARITY_VERIFIED", telemetry


parity_engine = CrossVenueParityEngine()

