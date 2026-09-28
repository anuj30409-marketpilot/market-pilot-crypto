"""Canonical Data Contracts for Crypto Research Desk."""
from pydantic import BaseModel, Field
from typing import Optional

class Candle1m(BaseModel):
    symbol: str
    market_type: str = "SPOT"  # "SPOT" or "FUTURES"
    open_time_ms: int
    close_time_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float
    trades_count: int
    taker_buy_base_volume: float
    taker_buy_quote_volume: float
    event_time_ms: int
    received_at_ms: int
    is_closed: bool = True

class AggTrade(BaseModel):
    symbol: str
    market_type: str = "SPOT"
    trade_id: int
    price: float
    quantity: float
    is_buyer_maker: bool  # True = seller is aggressor (taker sell), False = buyer is aggressor (taker buy)
    event_time_ms: int
    received_at_ms: int

class OrderbookSnapshot(BaseModel):
    symbol: str
    market_type: str
    best_bid: float
    best_ask: float
    mid_price: float
    microprice: float
    spread: float
    spread_bps: float
    bid_depth_5: float
    ask_depth_5: float
    imbalance_5: float
    bid_depth_20: float
    ask_depth_20: float
    imbalance_20: float
    update_id: int
    event_time_ms: int
    received_at_ms: int

class DerivativesState(BaseModel):
    symbol: str
    mark_price: float
    index_price: float
    basis_bps: float
    open_interest: float
    funding_rate: float
    funding_zscore_7d: float = 0.0
    cvd_1m: float = 0.0
    taker_buy_ratio_1m: float = 0.5
    quarantine_state: str = "NORMAL"
    state_time_ms: int
    created_at_iso: str
