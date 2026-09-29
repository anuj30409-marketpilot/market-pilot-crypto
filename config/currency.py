"""Tri-Rate Currency Service & Audit Engine for Crypto Research Desk.

Maintains three decoupled currency concepts:
1. market_usdt_inr: Market floating reference rate (default: 89.50).
2. exchange_settlement_usd_inr: Contractual platform settlement rate (e.g. Delta India platform conversion: 85.00).
3. display_usd_inr: User-facing display conversion.

Every P&L computation and live execution record audits the exact conversion rate used.
"""
import logging
from typing import Dict, Optional
from pydantic import BaseModel
from core.clock import now_utc_ms
from storage.sqlite_store import sqlite_store

logger = logging.getLogger("crypto_currency")


class CurrencyRateRecord(BaseModel):
    timestamp_ms: int
    source: str
    base_currency: str = "USDT"
    quote_currency: str = "INR"
    rate: float
    rate_type: str  # 'MARKET', 'SETTLEMENT', 'DISPLAY'
    confidence: float = 1.0
    is_stale: bool = False


class TriRateCurrencyService:
    def __init__(self):
        self.market_usdt_inr: float = 89.50
        self.exchange_settlement_usd_inr: float = 85.00  # Delta Exchange India documented platform reference
        self.display_usd_inr: float = 89.50
        self.last_update_ms: int = now_utc_ms()
        self._ensure_table()

    def _ensure_table(self):
        with sqlite_store._get_connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS crypto_currency_rates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_ms INTEGER NOT NULL,
                source TEXT NOT NULL,
                base_currency TEXT NOT NULL,
                quote_currency TEXT NOT NULL,
                rate REAL NOT NULL,
                rate_type TEXT NOT NULL,
                confidence REAL NOT NULL,
                is_stale INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_curr_rate_ts 
            ON crypto_currency_rates(timestamp_ms DESC);
            """)
            conn.commit()

    def record_rate(
        self,
        rate: float,
        rate_type: str,
        source: str = "STATIC_BENCHMARK",
        base_currency: str = "USDT",
        confidence: float = 1.0,
    ) -> CurrencyRateRecord:
        """Records a new currency observation to the persistent audit table."""
        now_ms = now_utc_ms()
        rec = CurrencyRateRecord(
            timestamp_ms=now_ms,
            source=source,
            base_currency=base_currency,
            quote_currency="INR",
            rate=rate,
            rate_type=rate_type.upper(),
            confidence=confidence,
            is_stale=False,
        )

        if rec.rate_type == "MARKET":
            self.market_usdt_inr = rate
        elif rec.rate_type == "SETTLEMENT":
            self.exchange_settlement_usd_inr = rate
        elif rec.rate_type == "DISPLAY":
            self.display_usd_inr = rate

        self.last_update_ms = now_ms

        with sqlite_store._get_connection() as conn:
            conn.execute("""
            INSERT INTO crypto_currency_rates (
                timestamp_ms, source, base_currency, quote_currency,
                rate, rate_type, confidence, is_stale
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                rec.timestamp_ms, rec.source, rec.base_currency,
                rec.quote_currency, rec.rate, rec.rate_type,
                rec.confidence, 1 if rec.is_stale else 0
            ))
            conn.commit()

        return rec

    def get_rate(self, rate_type: str = "DISPLAY") -> float:
        rate_type = rate_type.upper()
        if rate_type == "SETTLEMENT":
            return self.exchange_settlement_usd_inr
        elif rate_type == "MARKET":
            return self.market_usdt_inr
        return self.display_usd_inr

    def convert_usdt_to_inr(self, usdt: float, rate_type: str = "DISPLAY") -> float:
        return usdt * self.get_rate(rate_type)

    def convert_inr_to_usdt(self, inr: float, rate_type: str = "SETTLEMENT") -> float:
        rate = self.get_rate(rate_type)
        return (inr / rate) if rate > 0 else 0.0


currency_service = TriRateCurrencyService()
