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
        self.funding_interval_hours: int = 8
        self.next_funding_time_ms: int = 0
        self.open_interest: float = 0.0
        self.last_oi_fetch_ms: int = 0
        self.recent_funding_rates: List[float] = []  # For rolling z-score
        
        # 1m flow metrics
        self.cvd_1m: float = 0.0
        self.cvd_notional_usd_1m: float = 0.0
        self.recent_cvd_notionals: List[float] = []
        self.taker_buy_ratio_1m: float = 0.5
        self.last_candle_time_ms: int = 0
        
        # Liquidation and price history for regime classification
        self.liquidation_notional_60s: float = 0.0
        self.recent_liquidation_notionals: List[float] = []
        self.recent_closes: List[float] = []
        self.recent_highs: List[float] = []
        self.recent_lows: List[float] = []
        self.recent_volumes: List[float] = []

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
        if self.mark_price == 0.0:
            self.mark_price = c.close
        if self.index_price == 0.0:
            self.index_price = c.close
        mark = self.mark_price
        self.cvd_notional_usd_1m = self.cvd_1m * mark
        self.recent_cvd_notionals.append(self.cvd_notional_usd_1m)
        if len(self.recent_cvd_notionals) > 60:
            self.recent_cvd_notionals.pop(0)

        self.taker_buy_ratio_1m = (taker_buy / total_vol) if total_vol > 0 else 0.5
        self.last_candle_time_ms = c.open_time_ms

        self.recent_closes.append(c.close)
        self.recent_highs.append(c.high)
        self.recent_lows.append(c.low)
        self.recent_volumes.append(c.volume)
        if len(self.recent_closes) > 200:
            self.recent_closes.pop(0)
            self.recent_highs.pop(0)
            self.recent_lows.pop(0)
            self.recent_volumes.pop(0)

    def record_liquidation(self, notional_usd: float):
        self.liquidation_notional_60s += notional_usd
        self.recent_liquidation_notionals.append(notional_usd)
        if len(self.recent_liquidation_notionals) > 30:
            self.recent_liquidation_notionals.pop(0)

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

    def compute_cvd_zscore(self) -> float:
        if len(self.recent_cvd_notionals) < 10:
            return 0.0
        mean = sum(self.recent_cvd_notionals) / len(self.recent_cvd_notionals)
        variance = sum((x - mean) ** 2 for x in self.recent_cvd_notionals) / len(self.recent_cvd_notionals)
        std = variance ** 0.5
        if std > 1e-9:
            return (self.cvd_notional_usd_1m - mean) / std
        return 0.0

    def compute_liquidation_intensity(self) -> float:
        if not self.recent_liquidation_notionals:
            return 1.0
        sorted_liq = sorted(self.recent_liquidation_notionals)
        median = sorted_liq[len(sorted_liq) // 2]
        if median > 1000.0:
            return self.liquidation_notional_60s / median
        return 1.0

    def get_state(self, quarantine_state: str = "NORMAL") -> Optional[DerivativesState]:
        if self.mark_price == 0.0:
            return None
        now_ms = now_utc_ms()
        from core.regime import classify_regime

        funding_z = self.compute_funding_zscore()
        cvd_z = self.compute_cvd_zscore()
        liq_intensity = self.compute_liquidation_intensity()
        
        oi_usd = self.open_interest * self.mark_price
        liq_oi_impact = (self.liquidation_notional_60s / oi_usd) if oi_usd > 0 else 0.0

        dist_funding_mins = max(0, (self.next_funding_time_ms - now_ms) // 60000) if self.next_funding_time_ms > now_ms else 0
        annualized = self.funding_rate * (24.0 / max(1, self.funding_interval_hours)) * 365.0 * 100.0

        # Percentile
        if self.recent_funding_rates:
            less_count = sum(1 for r in self.recent_funding_rates if r < self.funding_rate)
            pct = (less_count / len(self.recent_funding_rates)) * 100.0
        else:
            pct = 50.0

        regime_state = classify_regime(
            symbol=self.symbol,
            recent_closes=self.recent_closes,
            recent_highs=self.recent_highs,
            recent_lows=self.recent_lows,
            recent_volumes=self.recent_volumes,
            funding_zscore=funding_z,
            liquidation_intensity=liq_intensity,
            cvd_notional_zscore=cvd_z,
            feed_quarantine=(quarantine_state != "NORMAL"),
            feed_latency_ms=0,
            timestamp_ms=now_ms,
        )

        return DerivativesState(
            symbol=self.symbol,
            mark_price=self.mark_price,
            index_price=self.index_price,
            basis_bps=self.compute_basis_bps(),
            open_interest=self.open_interest,
            funding_rate=self.funding_rate,
            funding_interval_hours=self.funding_interval_hours,
            annualized_funding=annualized,
            funding_zscore_7d=funding_z,
            funding_percentile=pct,
            distance_to_next_funding_mins=dist_funding_mins,
            predicted_funding=self.funding_rate,
            cvd_1m=self.cvd_1m,
            cvd_notional_usd_1m=self.cvd_notional_usd_1m,
            cvd_notional_usd_zscore=cvd_z,
            taker_buy_ratio_1m=self.taker_buy_ratio_1m,
            liquidation_notional_60s=self.liquidation_notional_60s,
            liquidation_intensity=liq_intensity,
            liquidation_oi_impact=liq_oi_impact,
            regime=regime_state.primary_regime.value,
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

    async def fetch_premium_index(self, symbol: str) -> dict:
        """Polls Binance Futures REST for Mark Price, Index Price, and Funding Rate."""
        try:
            url = f"{settings.BINANCE_FUTURES_REST}/fapi/v1/premiumIndex?symbol={symbol}"
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    mark = float(data.get("markPrice", 0.0))
                    index = float(data.get("indexPrice", 0.0))
                    funding = float(data.get("lastFundingRate", 0.0))
                    next_funding = int(data.get("nextFundingTime", 0))
                    upper = symbol.upper()
                    if upper in self.trackers and mark > 0:
                        self.trackers[upper].update_mark_price(mark, index, funding, next_funding)
                    return data
        except Exception as e:
            logger.warning(f"Failed to fetch premiumIndex for {symbol}: {e}")
        return {}

    async def run_oi_poller(self):
        """Polls Open Interest and Premium Index every 30 seconds for configured futures symbols."""
        # Initial immediate fetch on startup
        for symbol, tracker in self.trackers.items():
            await self.fetch_premium_index(symbol)
            oi = await self.fetch_open_interest(symbol)
            if oi > 0:
                tracker.open_interest = oi
                tracker.last_oi_fetch_ms = now_utc_ms()

        while True:
            await asyncio.sleep(30)
            for symbol, tracker in self.trackers.items():
                await self.fetch_premium_index(symbol)
                oi = await self.fetch_open_interest(symbol)
                if oi > 0:
                    tracker.open_interest = oi
                    tracker.last_oi_fetch_ms = now_utc_ms()

    def handle_mark_price_message(self, symbol: str, payload: dict):
        upper = symbol.upper()
        if upper in self.trackers:
            mark = float(payload.get("p", payload.get("markPrice", 0.0)))
            index = float(payload.get("P", payload.get("i", payload.get("indexPrice", 0.0))))
            funding = float(payload.get("r", payload.get("lastFundingRate", 0.0)))
            next_funding = int(payload.get("T", payload.get("nextFundingTime", 0)))
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
