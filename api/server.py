"""FastAPI Telemetry and State Server for Crypto Research Desk.

Listens on port 8800.
Exposes endpoints for Market Pilot React UI and external monitoring.
"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from pydantic import BaseModel
from config.settings import settings
from collectors.binance_ws import collector
from collectors.derivatives import derivatives_engine
from core.contracts import CandidateRecord
from storage.sqlite_store import sqlite_store
from core.clock import now_utc_iso
from paper.paper_engine import paper_engine
from config.currency import currency_service
from storage.registry import StrategyRegistry, StrategyDefinition, StrategyStatus
from core.regime import MarketRegime

strategy_registry = StrategyRegistry(sqlite_store)

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

@app.get("/derivatives")
def get_derivatives(
    symbol: str = Query("BTCUSDT", description="Symbol name e.g. BTCUSDT"),
    limit: int = Query(50, ge=1, le=200)
):
    """Returns historical 1m derivatives state records and current live snapshot."""
    upper = symbol.upper()
    historical = sqlite_store.get_latest_derivatives_states(upper, limit)
    
    # Also fetch current live in-memory snapshot if available
    live_snapshot = None
    if upper in derivatives_engine.trackers:
        state = derivatives_engine.trackers[upper].get_state()
        if state:
            live_snapshot = state.model_dump()
            
    return {
        "symbol": upper,
        "live": live_snapshot,
        "count": len(historical),
        "history": historical
    }

@app.get("/candidates")
def get_candidates(
    symbol: Optional[str] = Query(None, description="Optional symbol filter"),
    limit: int = Query(50, ge=1, le=200)
):
    """Returns candidate ledger records including both accepted and rejected signals."""
    candidates = sqlite_store.get_candidates(symbol, limit)
    return {
        "symbol": symbol.upper() if symbol else "ALL",
        "count": len(candidates),
        "candidates": candidates
    }

@app.post("/candidates")
def record_candidate(record: CandidateRecord):
    """Records a candidate evaluation (accepted or rejected) into the ledger."""
    sqlite_store.insert_candidate(record)
    return {"status": "RECORDED", "candidate_id": record.candidate_id}


# ── Paper Trading Endpoints ────────────────────────────────────────────────

class OpenPositionRequest(BaseModel):
    symbol: str = "BTCUSDT"
    direction: str              # "LONG" or "SHORT"
    notional_usdt: float        # Size in USDT (e.g. 500)
    leverage: int = 5
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None


@app.post("/paper/positions")
async def open_paper_position(req: OpenPositionRequest):
    """Open a new realistic paper position with orderbook-walk slippage."""
    result = await paper_engine.open_position(
        symbol=req.symbol,
        direction=req.direction,
        notional_usdt=req.notional_usdt,
        leverage=req.leverage,
        stop_loss_price=req.stop_loss_price,
        take_profit_price=req.take_profit_price,
        orderbooks=collector.orderbooks,
    )
    if "error" in result:
        raise HTTPException(status_code=503, detail=result["error"])
    return result


@app.get("/paper/positions")
def get_paper_positions(
    symbol: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="OPEN | CLOSED | LIQUIDATED"),
    limit: int = Query(50, ge=1, le=200),
):
    """Returns paper positions from database (persisted across restarts)."""
    return {
        "positions": sqlite_store.get_paper_positions(symbol, status, limit),
        "summary": paper_engine.get_summary(),
    }


@app.get("/paper/positions/live")
def get_live_paper_positions(symbol: Optional[str] = Query(None)):
    """Returns live in-memory open positions with real-time unrealised PnL."""
    return {
        "positions": paper_engine.get_positions(symbol, status="OPEN"),
        "summary": paper_engine.get_summary(),
    }


@app.post("/paper/positions/{position_id}/close")
async def close_paper_position(position_id: str, reason: str = Query("MANUAL")):
    """Manually close an open paper position with realistic exit slippage."""
    result = await paper_engine.close_position(
        position_id=position_id,
        reason=reason,
        orderbooks=collector.orderbooks,
    )
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.get("/paper/summary")
def get_paper_summary():
    """Returns aggregate P&L, fees, funding drag and capital utilisation."""
    return paper_engine.get_summary()


# ── Strategy Registry & Governance Endpoints ───────────────────────────────

@app.get("/strategies")
def get_strategies(status: Optional[str] = Query(None)):
    """Returns strategies from the immutable registry."""
    st = StrategyStatus(status) if status else None
    return strategy_registry.get_active_strategies(st)


@app.get("/strategies/audit")
def get_strategy_audit(strategy_id: Optional[str] = Query(None)):
    """Runs 16-gate statistical maturity audit across registered strategies."""
    from scripts.validate_gates import StatisticalGateValidator
    strat_ids = [strategy_id] if strategy_id else [
        "STRAT_FUNDING_REVERSION_V1",
        "STRAT_ORDERBOOK_MOMENTUM_V1",
        "STRAT_LIQUIDATION_FADER_V1",
    ]
    reports = [StatisticalGateValidator(sid).run_validation() for sid in strat_ids]
    return {"reports": reports}


# ── Tri-Rate Currency Endpoints ────────────────────────────────────────────

@app.get("/currency")
def get_currency_rates():
    """Returns current market, settlement, and display rates with audit status."""
    return {
        "market_usdt_inr": currency_service.get_rate("MARKET"),
        "exchange_settlement_usd_inr": currency_service.get_rate("SETTLEMENT"),
        "display_usd_inr": currency_service.get_rate("DISPLAY"),
        "last_update_ms": currency_service.last_update_ms,
    }


# ── Market Regime Endpoints ────────────────────────────────────────────────

@app.get("/regime")
def get_regime(symbol: str = Query("BTCUSDT")):
    """Returns current 7-state market regime classification for symbol."""
    upper = symbol.upper()
    if upper in derivatives_engine.trackers:
        state = derivatives_engine.trackers[upper].get_state()
        if state:
            return {
                "symbol": upper,
                "regime": state.regime,
                "funding_zscore": state.funding_zscore_7d,
                "cvd_zscore": state.cvd_notional_usd_zscore,
                "liquidation_intensity": state.liquidation_intensity,
                "timestamp_ms": state.state_time_ms,
            }
    return {"symbol": upper, "regime": "RANGE", "status": "WARMING_UP"}

