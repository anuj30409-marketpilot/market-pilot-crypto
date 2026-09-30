"""REST 1m-kline fallback poller for the crypto desk.

WHY THIS EXISTS (2026-09-30 incident):
The Binance USD-M Futures combined WebSocket on Oracle VM2 began delivering
ONLY @depth20 updates while @kline_1m, @aggTrade and @markPrice produced zero
frames (verified: depth ~110 msgs/12s; kline/aggTrade/markPrice = 0). That
froze candle ingest at 2026-09-29 03:53 UTC and, with it, the derived CVD
z-score / ADX / Donchian inputs, so every strategy rejected (0 trades).
The REST endpoint /fapi/v1/klines kept returning fresh bars, so we poll REST
to keep the candle + derivatives pipeline alive regardless of WS health.

This is additive plumbing only. It does NOT change any strategy threshold or
gate logic (see CRYPTO_LOGBOOK.md FM-1).
"""
import asyncio
import logging

import httpx

from config.settings import settings
from core.clock import now_utc_ms
from core.contracts import Candle1m
from storage.sqlite_store import sqlite_store
from collectors.derivatives import derivatives_engine

logger = logging.getLogger("crypto_kline_rest")

POLL_INTERVAL_SECONDS = 20
KLINE_LIMIT = 3


class KlineRestFallback:
    def __init__(self):
        self.symbols = [s.upper() for s in settings.SYMBOLS_FUTURES]

    async def _fetch(self, symbol: str):
        url = f"{settings.BINANCE_FUTURES_REST}/fapi/v1/klines"
        params = {"symbol": symbol, "interval": "1m", "limit": KLINE_LIMIT}
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    return resp.json()
                logger.warning(f"REST klines {symbol} HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"REST klines {symbol} failed: {e}")
        return []

    async def _poll_once(self):
        now_ms = now_utc_ms()
        for symbol in self.symbols:
            bars = await self._fetch(symbol)
            if not bars:
                continue
            tracker = derivatives_engine.trackers.get(symbol)
            last_open = tracker.last_candle_time_ms if tracker else 0
            for b in bars:
                try:
                    open_time = int(b[0])
                    close_time = int(b[6])
                    if open_time < last_open:
                        continue
                    candle = Candle1m(
                        symbol=symbol,
                        market_type="FUTURES",
                        open_time_ms=open_time,
                        close_time_ms=close_time,
                        open=float(b[1]),
                        high=float(b[2]),
                        low=float(b[3]),
                        close=float(b[4]),
                        volume=float(b[5]),
                        quote_volume=float(b[7]),
                        trades_count=int(b[8]),
                        taker_buy_base_volume=float(b[9]),
                        taker_buy_quote_volume=float(b[10]),
                        event_time_ms=close_time,
                        received_at_ms=now_ms,
                        is_closed=(close_time < now_ms),
                    )
                except Exception as e:
                    logger.warning(f"Bad kline row {symbol}: {e}")
                    continue
                sqlite_store.upsert_candle(candle)
                derivatives_engine.handle_candle_update(candle, "NORMAL")

    async def run(self):
        logger.info(
            "REST kline fallback poller started (interval=%ss, symbols=%s)",
            POLL_INTERVAL_SECONDS, self.symbols,
        )
        while True:
            try:
                await self._poll_once()
            except Exception as e:
                logger.error(f"REST kline poller cycle error: {e}")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)


kline_rest_fallback = KlineRestFallback()
