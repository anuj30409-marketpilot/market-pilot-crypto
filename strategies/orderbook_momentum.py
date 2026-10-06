"""Strategy 2: Orderbook Imbalance & Portable CVD Momentum Scalper.

Hypothesis:
Deep orderbook asymmetry across 5 levels aligned with 1-minute aggressor trade flow
(measured by portable CVD notional z-score) predicts directional price drift over 5–15 min horizons.

Regime constraints:
Allowed in TRENDING_UP, TRENDING_DOWN, RANGE, and HIGH_VOLATILITY.
Explicitly rejected in LIQUIDATION_EVENT or DATA_DEGRADED.
"""
from typing import Optional, List
from core.contracts import DerivativesState, OrderbookSnapshot, CandidateRecord
from core.regime import MarketRegime
from core.clock import now_utc_ms, ms_to_iso


STRATEGY_ID = "STRAT_ORDERBOOK_MOMENTUM_V1"
VERSION = "1.0.0"
FEATURE_VERSION = "1.0"
PARAMETER_VERSION = "1.0"

ALLOWED_REGIMES = {
    MarketRegime.TRENDING_UP.value,
    MarketRegime.TRENDING_DOWN.value,
    MarketRegime.RANGE.value,
    MarketRegime.HIGH_VOLATILITY.value,
}


def evaluate_orderbook_momentum(
    deriv: DerivativesState,
    ob: OrderbookSnapshot,
    taker_fee_bps: float = 5.0,
    slippage_reserve_bps: float = 1.5,
    min_net_edge_bps: float = 2.5,
) -> CandidateRecord:
    """Evaluates the Orderbook Depth & CVD Momentum strategy against current state."""
    now_ms = now_utc_ms()
    candidate_id = f"CAND-CVD-{deriv.symbol}-{now_ms}"
    rejection_codes: List[str] = []

    # 1. Regime Check
    if deriv.regime not in ALLOWED_REGIMES:
        rejection_codes.append("REJECT_REGIME_UNFAVORABLE")

    # 2. Feed Health Check
    if deriv.quarantine_state != "NORMAL":
        rejection_codes.append("REJECT_DATA_STALE")

    # 3. Tight Spread Requirement for Scalping
    if ob.spread_bps > 2.0:
        rejection_codes.append("REJECT_HIGH_SPREAD")

    # 4. Liquidity Depth Check
    if ob.bid_depth_5 <= 0.0 or ob.ask_depth_5 <= 0.0:
        rejection_codes.append("REJECT_LOW_LIQUIDITY")

    # Directional Momentum Signals — Dual-Pathway Logic
    # Pathway A (Flow-Driven): Strong CVD impulse + taker aggressor dominance.
    #   Imbalance allowed to be temporarily neutral (aggressors thin bid depth during buying).
    flow_long  = (deriv.cvd_notional_usd_zscore >= 1.30 and deriv.taker_buy_ratio_1m >= 0.58 and ob.imbalance_5 >= -0.10)
    flow_short = (deriv.cvd_notional_usd_zscore <= -1.30 and deriv.taker_buy_ratio_1m <= 0.42 and ob.imbalance_5 <= 0.10)

    # Pathway B (Queue-Driven): Deep book skew + microprice edge with mild CVD confirmation.
    queue_long  = (ob.imbalance_5 >= 0.30 and ob.microprice_edge_bps >= 0.8 and deriv.cvd_notional_usd_zscore >= 0.50)
    queue_short = (ob.imbalance_5 <= -0.30 and ob.microprice_edge_bps <= -0.8 and deriv.cvd_notional_usd_zscore <= -0.50)

    # 5. Counter-Trend Macro Regime Guard
    if flow_long or queue_long:
        if deriv.regime == "TRENDING_DOWN":
            rejection_codes.append("REJECT_COUNTER_TREND_REGIME")
    if flow_short or queue_short:
        if deriv.regime == "TRENDING_UP":
            rejection_codes.append("REJECT_COUNTER_TREND_REGIME")

    is_long  = (flow_long  or queue_long) and deriv.regime != "TRENDING_DOWN"
    is_short = (flow_short or queue_short) and deriv.regime != "TRENDING_UP"

    direction = "LONG" if is_long else ("SHORT" if is_short else None)

    if not direction and "REJECT_COUNTER_TREND_REGIME" not in rejection_codes:
        rejection_codes.append("REJECT_SIGNAL_THRESHOLD_NOT_MET")

    # Edge vs Execution Friction — incorporates both pathways
    abs_imb = abs(ob.imbalance_5)
    abs_cvd_z = abs(deriv.cvd_notional_usd_zscore)
    abs_micro = abs(ob.microprice_edge_bps) if ob.microprice_edge_bps else 0.0
    expected_edge_bps = (abs_imb * 14.0) + (abs_cvd_z * 3.5) + (abs_micro * 2.0)

    estimated_cost_bps = ob.spread_bps + (taker_fee_bps * 2.0) + slippage_reserve_bps
    expected_net_edge_bps = expected_edge_bps - estimated_cost_bps

    if expected_net_edge_bps < min_net_edge_bps:
        rejection_codes.append("REJECT_EDGE_TOO_SMALL")

    # Final Decision
    is_accepted = (len(rejection_codes) == 0 and direction is not None)
    decision = "ACCEPT" if is_accepted else "REJECT"

    if is_accepted:
        decision_reason = (
            f"Valid {direction} momentum flow: Imbalance_5={ob.imbalance_5:+.2f}, "
            f"CVD Z-score={deriv.cvd_notional_usd_zscore:+.2f}, "
            f"Taker Buy Ratio={deriv.taker_buy_ratio_1m:.2f}, "
            f"Net edge={expected_net_edge_bps:.1f} bps"
        )
        hypothetical_entry = ob.best_ask if direction == "LONG" else ob.best_bid
    else:
        decision_reason = f"Rejected: {', '.join(rejection_codes)}"
        hypothetical_entry = None

    signal_score = max(0.0, min(1.0, (expected_net_edge_bps / 15.0))) if is_accepted else 0.0

    return CandidateRecord(
        candidate_id=candidate_id,
        timestamp_ms=now_ms,
        symbol=deriv.symbol,
        origin="QUANT",
        strategy_id=STRATEGY_ID,
        strategy_version=VERSION,
        feature_version=FEATURE_VERSION,
        parameter_version=PARAMETER_VERSION,
        engine_version="v2.0",
        model_version="v2.0",
        signal_version="v2.0",
        decision=decision,
        decision_reason=decision_reason,
        signal_score=signal_score,
        expected_edge_bps=expected_edge_bps,
        estimated_cost_bps=estimated_cost_bps,
        expected_net_edge_bps=expected_net_edge_bps,
        regime=deriv.regime,
        rejection_codes=rejection_codes,
        conversion_rate_applied=89.50,
        research_venue="BINANCE",
        execution_venue="DELTA_INDIA",
        hypothetical_entry=hypothetical_entry,
        created_at_iso=ms_to_iso(now_ms),
    )
