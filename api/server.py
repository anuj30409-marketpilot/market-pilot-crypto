"""FastAPI Telemetry and State Server for Crypto Research Desk.

Listens on port 8800.
Exposes endpoints for Market Pilot React UI and external monitoring.
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from config.settings import settings
from collectors.binance_ws import collector
from storage.sqlite_store import sqlite_store
from core.clock import now_utc_iso

app = FastAPI(
    title="Market Pilot Crypto Research API",
    version="1.0.0",
    docs_url="/docs"
)

# Allow CORS for Market Pilot frontend domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
def get_health():
    """Returns system health, feed statuses, and canonical clock time."""
    statuses = sqlite_store.get_all_feed_statuses()
    return {
        "status": "HEALTHY",
        "service": "market-pilot-crypto",
        "server_time_utc": now_utc_iso(),
        "active_feeds": statuses
    }

@app.get("/candles")
def get_candles(
    symbol: str = Query("BTCUSDT", description="Symbol name e.g. BTCUSDT"),
    market_type: str = Query("FUTURES", description="SPOT or FUTURES"),
    limit: int = Query(100, ge=1, le=500)
):
    """Returns latest 1-minute candles for UI charts and quant indicators."""
    candles = sqlite_store.get_latest_candles(symbol, market_type, limit)
    return {
        "symbol": symbol.upper(),
        "market_type": market_type.upper(),
        "count": len(candles),
        "candles": candles
    }

@app.get("/orderbook")
def get_orderbook(symbol: str = Query("BTCUSDT")):
    """Returns latest L2 depth snapshot with microprice and depth imbalance."""
    upper_sym = symbol.upper()
    if upper_sym not in collector.orderbooks:
        raise HTTPException(status_code=404, detail=f"Symbol {upper_sym} not tracked")
    
    try:
        snapshot = collector.orderbooks[upper_sym].get_snapshot()
        return snapshot.model_dump()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Orderbook not ready: {str(e)}")

@app.get("/feeds")
def get_feeds():
    """Returns feed health and quarantine state machine records."""
    return sqlite_store.get_all_feed_statuses()
