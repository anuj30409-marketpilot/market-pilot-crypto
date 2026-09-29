"""Strategy 3: Volume/OI-Adaptive Liquidation Exhaustion Fader.

Hypothesis:
Massive liquidation cascades exhaust aggressive momentum into a liquidity vacuum.
Once orderbook depth begins replenishing, price predictably mean-reverts back to the pre-cascade mid.

Regime constraints:
Active strictly during LIQUIDATION_EVENT and HIGH_VOLATILITY.
Explicitly rejected during standard RANGE, LOW_VOLATILITY, or DATA_DEGRADED.
"""
from typing import Optional, List
from core.contracts import DerivativesState, OrderbookSnapshot, CandidateRecord
from core.regime import MarketRegime
from core.clock import now_utc_ms, ms_to_iso


STRATEGY_ID = "STRAT_LIQUIDATION_FADER_V1"
VERSION = "1.0.0"
FEATURE_VERSION = "1.0"
PARAMETER_VERSION = "1.0"

ALLOWED_REGIMES = {
    MarketRegime.LIQUIDATION_EVENT.value,
    MarketRegime.HIGH_VOLATILITY.value,
}


def evaluate_liquidation_fader(
    deriv: DerivativesState,
    ob: OrderbookSnapshot,
    taker_fee_bps: float = 5.0,
    slippage_reserve_bps: float = 3.0,
    min_net_edge_bps: float = 4.0,
) -> CandidateRecord:
    """Evaluates the Liquidation Exhaustion Fader strategy against current state."""
    now_ms = now_utc_ms()
    candidate_id = f"CAND-LIQ-{deriv.symbol}-{now_ms}"
    rejection_codes: List[str] = []

    # 1. Regime Check
    if deriv.regime not in ALLOWED_REGIMES:
        rejection_codes.append("REJECT_REGIME_UNFAVORABLE")

    # 2. Feed Health Check
    if deriv.quarantine_state != "NORMAL":
        rejection_codes.append("REJECT_DATA_STALE")

    # 3. Dynamic Liquidation Intensity Check
    if deriv.liquidation_intensity < 3.0:
        rejection_codes.append("REJECT_SIGNAL_THRESHOLD_NOT_MET")

    # 4. Open Interest Impact Check (at least 0.1% of OI flushed)
    if deriv.liquidation_oi_impact < 0.001:
        rejection_codes.append("REJECT_EDGE_TOO_SMALL")

    # Direction: Fade long liquidations (buy) vs fade short liquidations (sell)
    # If CVD is deeply negative during a liquidation spike but book depth is skewing bid (buyers stepping in)
    is_fade_long = (
        deriv.cvd_notional_usd_zscore <= -1.5 and
        ob.imbalance_5 >= 0.15 and
        ob.microprice_edge_bps > 0.5
    )

    is_fade_short = (
        deriv.cvd_notional_usd_zscore >= 1.5 and
        ob.imbalance_5 <= -0.15 and
        ob.microprice_edge_bps < -0.5
    )

    direction = "LONG" if is_fade_long else ("SHORT" if is_fade_short else None)

    if not direction:
        rejection_codes.append("REJECT_EXECUTION_UNCERTAINTY")

    # Edge calculation (liquidation rebound expected move)
    expected_edge_bps = min(40.0, deriv.liquidation_intensity * 6.0)
    estimated_cost_bps = ob.spread_bps + (taker_fee_bps * 2.0) + slippage_reserve_bps
    expected_net_edge_bps = expected_edge_bps - estimated_cost_bps

    if expected_net_edge_bps < min_net_edge_bps:
        rejection_codes.append("REJECT_EDGE_TOO_SMALL")

    # Final Decision
    is_accepted = (len(rejection_codes) == 0 and direction is not None)
    decision = "ACCEPT" if is_accepted else "REJECT"

    if is_accepted:
        decision_reason = (
            f"Valid liquidation fade {direction}: Intensity={deriv.liquidation_intensity:.1f}x, "
            f"OI Impact={deriv.liquidation_oi_impact*100:.2f}%, Imbalance_5={ob.imbalance_5:+.2f}, "
            f"Net edge={expected_net_edge_bps:.1f} bps"
        )
        hypothetical_entry = ob.best_ask if direction == "LONG" else ob.best_bid
    else:
        decision_reason = f"Rejected: {', '.join(rejection_codes)}"
        hypothetical_entry = None

    signal_score = max(0.0, min(1.0, (expected_net_edge_bps / 25.0))) if is_accepted else 0.0

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
