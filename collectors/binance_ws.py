"""High-Performance Binance WebSocket Ingestion Engine.

Connects to Binance Spot and USD-M Futures combined multiplex streams.
Consumes:
- 1m Kline (@kline_1m)
- Aggregated Trades (@aggTrade)
- Top 20 Depth (@depth20@100ms)
- Mark Price & Funding (@markPrice@1s)
- Liquidations (!forceOrder@arr)

Designed for ultra-low memory usage (<150 MB RAM) and CPU efficiency on 1 GB VMs.
"""
import asyncio
import logging
from typing import Dict, List, Optional
import websockets
try:
    import orjson
except ImportError:
    import json as orjson
from config.settings import settings
from core.clock import now_utc_ms
from core.contracts import Candle1m, AggTrade
from core.state_machine import FeedStatus, FeedState
from collectors.orderbook import BoundedOrderbook
from collectors.derivatives import derivatives_engine
from storage.sqlite_store import sqlite_store
from storage.parquet_store import parquet_store

logger = logging.getLogger("binance_ws")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

class BinanceStreamCollector:
    def __init__(self):
        self.running = False
        self.orderbooks: Dict[str, BoundedOrderbook] = {}
        self.feed_statuses: Dict[str, FeedStatus] = {}
        self.trade_batch: List[AggTrade] = []
        self.trade_batch_lock = asyncio.Lock()
        
        # Initialize orderbooks and statuses
        for sym in settings.SYMBOLS_FUTURES:
            upper_sym = sym.upper()
            self.orderbooks[upper_sym] = BoundedOrderbook(upper_sym, market_type="FUTURES", max_levels=settings.MAX_DEPTH_LEVELS)
            self.feed_statuses[upper_sym] = FeedStatus(
                feed_name="binance_futures",
                symbol=upper_sym,
                state=FeedState.NORMAL
            )

    def _build_futures_stream_url(self) -> str:
        """Constructs multiplex stream URL for all configured symbols."""
        streams = []
        for sym in settings.SYMBOLS_FUTURES:
            s = sym.lower()
            streams.append(f"{s}@kline_1m")
            streams.append(f"{s}@aggTrade")
            streams.append(f"{s}@depth20@100ms")
            streams.append(f"{s}@markPrice@1s")
        # Global liquidation stream
        streams.append("!forceOrder@arr")
        stream_path = "/".join(streams)
        return f"{settings.BINANCE_FUTURES_WS}/stream?streams={stream_path}"

    async def _handle_message(self, raw_bytes: bytes):
        try:
            received_ms = now_utc_ms()
            data = orjson.loads(raw_bytes)
            stream_name = data.get("stream", "")
            payload = data.get("data", {})

            if not payload:
                return

            # 1. Kline stream
            if "@kline_1m" in stream_name:
                k = payload.get("k", {})
                symbol = (payload.get("s") or k.get("s") or "").upper()
                event_time_ms = payload.get("E", received_ms)
                
                candle = Candle1m(
                    symbol=symbol,
                    market_type="FUTURES",
                    open_time_ms=k.get("t"),
                    close_time_ms=k.get("T"),
                    open=float(k.get("o")),
                    high=float(k.get("h")),
                    low=float(k.get("l")),
                    close=float(k.get("c")),
                    volume=float(k.get("v")),
                    quote_volume=float(k.get("q")),
                    trades_count=int(k.get("n")),
                    taker_buy_base_volume=float(k.get("V")),
                    taker_buy_quote_volume=float(k.get("Q")),
                    event_time_ms=event_time_ms,
                    received_at_ms=received_ms,
                    is_closed=bool(k.get("x"))
                )
                # Store completed candles or update latest bar in SQLite
                sqlite_store.upsert_candle(candle)
                
                # Update derivatives flow (CVD, taker buy ratio, snapshot on close)
                feed_state_val = self.feed_statuses[symbol].state.value if symbol in self.feed_statuses else "NORMAL"
                derivatives_engine.handle_candle_update(candle, feed_state_val)

                if symbol in self.feed_statuses:
                    self.feed_statuses[symbol].mark_event(event_time_ms, received_ms)
                    sqlite_store.update_feed_status(self.feed_statuses[symbol])

            # 2. Aggregated Trade stream
            elif "@aggTrade" in stream_name:
                trade = AggTrade(
                    symbol=(payload.get("s") or "").upper(),
                    market_type="FUTURES",
                    trade_id=payload.get("a"),
                    price=float(payload.get("p")),
                    quantity=float(payload.get("q")),
                    is_buyer_maker=bool(payload.get("m")),
                    event_time_ms=payload.get("E", received_ms),
                    received_at_ms=received_ms
                )
                async with self.trade_batch_lock:
                    self.trade_batch.append(trade)
                    if len(self.trade_batch) >= 200:
                        batch_to_write = list(self.trade_batch)
                        self.trade_batch.clear()
                        # Non-blocking parquet write offloaded to executor
                        asyncio.get_running_loop().run_in_executor(None, parquet_store.write_agg_trades_batch, batch_to_write)

            # 3. Depth snapshot / diff
            elif "@depth" in stream_name:
                symbol = (payload.get("s") or (stream_name.split("@")[0] if "@" in stream_name else "")).upper()
                if symbol in self.orderbooks:
                    bids = payload.get("b", [])
                    asks = payload.get("a", [])
                    update_id = payload.get("u", 0)
                    event_time_ms = payload.get("E", received_ms)
                    self.orderbooks[symbol].apply_snapshot(bids, asks, update_id)
                    if symbol in self.feed_statuses:
                        self.feed_statuses[symbol].mark_event(event_time_ms, received_ms)
                        sqlite_store.update_feed_status(self.feed_statuses[symbol])

            # 4. Mark Price & Funding stream
            elif "@markPrice" in stream_name:
                symbol = (payload.get("s") or (stream_name.split("@")[0] if "@" in stream_name else "")).upper()
                derivatives_engine.handle_mark_price_message(symbol, payload)
                if symbol in self.feed_statuses:
                    event_time_ms = payload.get("E", received_ms)
                    self.feed_statuses[symbol].mark_event(event_time_ms, received_ms)
                    sqlite_store.update_feed_status(self.feed_statuses[symbol])
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def _flush_periodically(self):
        """Flushes buffered trades to Parquet every 30 seconds."""
        while self.running:
            await asyncio.sleep(30)
            async with self.trade_batch_lock:
                if self.trade_batch:
                    batch_to_write = list(self.trade_batch)
                    self.trade_batch.clear()
                    await asyncio.get_running_loop().run_in_executor(None, parquet_store.write_agg_trades_batch, batch_to_write)

    async def run(self):
        self.running = True
        flush_task = asyncio.create_task(self._flush_periodically())
        url = self._build_futures_stream_url()
        logger.info(f"Connecting to Binance stream: {url}")

        while self.running:
            try:
                # Proactive rotation: close connection after 23 hours to prevent exchange force kill
                async with websockets.connect(
                    url,
                    ping_interval=settings.WS_HEARTBEAT_SECONDS,
                    ping_timeout=10,
                    max_size=10 * 1024 * 1024
                ) as ws:
                    logger.info("Connected to Binance WebSocket stream.")
                    while self.running:
                        msg = await ws.recv()
                        await self._handle_message(msg)
            except Exception as e:
                logger.error(f"WebSocket error: {e}. Reconnecting in 5 seconds...")
                await asyncio.sleep(5)

        flush_task.cancel()

collector = BinanceStreamCollector()

if __name__ == "__main__":
    asyncio.run(collector.run())
