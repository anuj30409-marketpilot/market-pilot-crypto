"""Canonical Data Contracts for Crypto Research Desk."""
from pydantic import BaseModel, Field
from typing import List, Optional


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
    microprice_edge_bps: float = 0.0
    bid_depth_5: float
    ask_depth_5: float
    imbalance_1: float = 0.0
    imbalance_3: float = 0.0
    imbalance_5: float = 0.0
    imbalance_10: float = 0.0
    bid_notional_5: float = 0.0
    ask_notional_5: float = 0.0
    notional_imbalance_5: float = 0.0
    bid_depth_20: float = 0.0
    ask_depth_20: float = 0.0
    imbalance_20: float = 0.0
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
    funding_interval_hours: int = 8
    annualized_funding: float = 0.0
    funding_zscore_7d: float = 0.0
    funding_percentile: float = 50.0
    distance_to_next_funding_mins: int = 0
    predicted_funding: float = 0.0
    cvd_1m: float = 0.0
    cvd_notional_usd_1m: float = 0.0
    cvd_notional_usd_zscore: float = 0.0
    taker_buy_ratio_1m: float = 0.5
    liquidation_notional_60s: float = 0.0
    liquidation_intensity: float = 1.0
    liquidation_oi_impact: float = 0.0
    open_interest_delta: float = 0.0
    oi_change_pct_1h: float = 0.0
    regime: str = "RANGE"
    quarantine_state: str = "NORMAL"
    state_time_ms: int
    created_at_iso: str


class CandidateRecord(BaseModel):
    candidate_id: str
    timestamp_ms: int
    symbol: str
    origin: str = "QUANT"  # "QUANT", "ML", "SUPERHUMAN"
    strategy_id: str = "STRAT_UNKNOWN"
    strategy_version: str = "v1.0"
    feature_version: str = "v1.0"
    parameter_version: str = "v1.0"
    engine_version: str = "v1.0"
    model_version: str = "v1.0"
    signal_version: str = "v1.0"
    decision: str  # "ACCEPT", "REJECT"
    decision_reason: str
    signal_score: float = 0.0
    expected_edge_bps: float = 0.0
    estimated_cost_bps: float = 0.0
    expected_net_edge_bps: float = 0.0
    regime: str = "RANGE"
    rejection_codes: List[str] = Field(default_factory=list)
    conversion_rate_applied: float = 89.50
    research_venue: str = "BINANCE"
    execution_venue: str = "DELTA_INDIA"
    hypothetical_entry: Optional[float] = None
    hypothetical_exit: Optional[float] = None
    hypothetical_pnl: Optional[float] = None
    actual_paper_entry: Optional[float] = None
    actual_paper_exit: Optional[float] = None
    actual_pnl: Optional[float] = None
    created_at_iso: str
