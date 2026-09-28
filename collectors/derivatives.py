"""Derivatives State Engine for Crypto Research Desk.

Calculates:
- Mark Price, Index Price, Basis (bps)
- Open Interest (periodically refreshed via REST)
- 7-Day Funding Rate Z-score
- Real-time 1m Cumulative Volume Delta (CVD) and Taker Buy Ratio
- Canonical CryptoDerivativesState record upserted into SQLite every 1m
"""
import asyncio
import logging
from typing import Dict, List, Optional
import httpx
from config.settings import settings
from core.clock import now_utc_ms, ms_to_iso
from core.contracts import DerivativesState, Candle1m
from storage.sqlite_store import sqlite_store

logger = logging.getLogger("crypto_derivatives")

class SymbolDerivativesTracker:
    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        self.mark_price: float = 0.0
        self.index_price: float = 0.0
        self.funding_rate: float = 0.0
        self.next_funding_time_ms: int = 0
        self.open_interest: float = 0.0
        self.last_oi_fetch_ms: int = 0
        self.recent_funding_rates: List[float] = []  # For rolling z-score
        
        # 1m flow metrics
        self.cvd_1m: float = 0.0
        self.taker_buy_ratio_1m: float = 0.5
        self.last_candle_time_ms: int = 0

    def update_mark_price(self, mark: float, index: float, funding_rate: float, next_funding_time: int):
        self.mark_price = mark
        self.index_price = index
        self.funding_rate = funding_rate
        self.next_funding_time_ms = next_funding_time
        
        if funding_rate != 0.0:
            self.recent_funding_rates.append(funding_rate)
            if len(self.recent_funding_rates) > 200:
                self.recent_funding_rates.pop(0)

    def update_from_candle(self, c: Candle1m):
        """Updates flow metrics from completed or current 1m bar."""
        total_vol = c.volume
        taker_buy = c.taker_buy_base_volume
        taker_sell = max(0.0, total_vol - taker_buy)
        self.cvd_1m = taker_buy - taker_sell
        self.taker_buy_ratio_1m = (taker_buy / total_vol) if total_vol > 0 else 0.5
        self.last_candle_time_ms = c.open_time_ms

    def compute_basis_bps(self) -> float:
        if self.index_price > 0:
            return ((self.mark_price - self.index_price) / self.index_price) * 10000.0
        return 0.0

    def compute_funding_zscore(self) -> float:
        if len(self.recent_funding_rates) < 10:
            return 0.0
        mean = sum(self.recent_funding_rates) / len(self.recent_funding_rates)
        variance = sum((x - mean) ** 2 for x in self.recent_funding_rates) / len(self.recent_funding_rates)
        std = variance ** 0.5
        if std > 1e-9:
            return (self.funding_rate - mean) / std
        return 0.0

    def get_state(self, quarantine_state: str = "NORMAL") -> Optional[DerivativesState]:
        if self.mark_price == 0.0:
            return None
        now_ms = now_utc_ms()
        return DerivativesState(
            symbol=self.symbol,
            mark_price=self.mark_price,
            index_price=self.index_price,
            basis_bps=self.compute_basis_bps(),
            open_interest=self.open_interest,
            funding_rate=self.funding_rate,
            funding_zscore_7d=self.compute_funding_zscore(),
            cvd_1m=self.cvd_1m,
            taker_buy_ratio_1m=self.taker_buy_ratio_1m,
            quarantine_state=quarantine_state,
            state_time_ms=self.last_candle_time_ms or now_ms,
            created_at_iso=ms_to_iso(now_ms)
        )

class DerivativesEngine:
    def __init__(self):
        self.trackers: Dict[str, SymbolDerivativesTracker] = {}
        for sym in settings.SYMBOLS_FUTURES:
            upper = sym.upper()
            self.trackers[upper] = SymbolDerivativesTracker(upper)

    async def fetch_open_interest(self, symbol: str) -> float:
        """Polls Binance Futures REST for current Open Interest."""
        try:
            url = f"{settings.BINANCE_FUTURES_REST}/fapi/v1/openInterest?symbol={symbol}"
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    return float(data.get("openInterest", 0.0))
        except Exception as e:
            logger.warning(f"Failed to fetch OI for {symbol}: {e}")
        return 0.0

    async def run_oi_poller(self):
        """Polls Open Interest every 30 seconds for configured futures symbols."""
        while True:
            for symbol, tracker in self.trackers.items():
                oi = await self.fetch_open_interest(symbol)
                if oi > 0:
                    tracker.open_interest = oi
                    tracker.last_oi_fetch_ms = now_utc_ms()
            await asyncio.sleep(30)

    def handle_mark_price_message(self, symbol: str, payload: dict):
        upper = symbol.upper()
        if upper in self.trackers:
            mark = float(payload.get("p", 0.0))
            index = float(payload.get("i", 0.0))
            funding = float(payload.get("r", 0.0))
            next_funding = int(payload.get("T", 0))
            self.trackers[upper].update_mark_price(mark, index, funding, next_funding)

    def handle_candle_update(self, candle: Candle1m, quarantine_state: str = "NORMAL"):
        upper = candle.symbol.upper()
        if upper in self.trackers:
            self.trackers[upper].update_from_candle(candle)
            # Write snapshot to SQLite on closed candle
            if candle.is_closed:
                state = self.trackers[upper].get_state(quarantine_state)
                if state:
                    sqlite_store.upsert_derivatives_state(state)

derivatives_engine = DerivativesEngine()
