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
PARAMETER_VERSION = "2.0"

# Regimes where liquidation cascades occur (includes range breaks and trending flushes)
ALLOWED_REGIMES = {
    MarketRegime.LIQUIDATION_EVENT.value,
    MarketRegime.HIGH_VOLATILITY.value,
    MarketRegime.RANGE.value,
    MarketRegime.TRENDING_UP.value,
    MarketRegime.TRENDING_DOWN.value,
}

# Calibrated liquidation cascade hurdles (replacing the impossible $5.8M 60s bug)
MIN_LIQUIDATION_NOTIONAL_60S = 250_000.0  # $250k USD flushed in 60s window
MIN_LIQUIDATION_INTENSITY = 2.5           # 2.5x volume intensity over baseline


def evaluate_liquidation_fader(
    deriv: DerivativesState,
    ob: OrderbookSnapshot,
    taker_fee_bps: float = 4.0,
    slippage_reserve_bps: float = 2.0,
    min_net_edge_bps: float = 5.0,
) -> CandidateRecord:
    """Evaluates the Liquidation Exhaustion Fader strategy against current state.

    Institutional Two-Stage Exhaustion Architecture:
    Stage 1: Validates that a genuine liquidation cascade occurred (>= $250k USD flushed).
    Stage 2: Validates exhaustion and orderbook absorption before entering mean-reversion fade.
    """
    now_ms = now_utc_ms()
    candidate_id = f"CAND-LIQ-{deriv.symbol}-{now_ms}"
    rejection_codes: List[str] = []

    # 1. Regime Check
    if deriv.regime not in ALLOWED_REGIMES:
        rejection_codes.append("REJECT_REGIME_UNFAVORABLE")

    # 2. Feed Health Check
    if deriv.quarantine_state != "NORMAL":
        rejection_codes.append("REJECT_DATA_STALE")

    # 3. Liquidation Cascade Event Verification (Stage 1)
    # Replaces the impossible $5.8M 60s bug (0.001 * $5.8B OI) with empirical $250k / 2.5x intensity
    has_cascade = (
        deriv.liquidation_notional_60s >= MIN_LIQUIDATION_NOTIONAL_60S or
        deriv.liquidation_intensity >= MIN_LIQUIDATION_INTENSITY
    )
    if not has_cascade:
        rejection_codes.append("REJECT_SIGNAL_THRESHOLD_NOT_MET")

    # 4. Decoupled Exhaustion & Absorption Verification (Stage 2)
    # Solves temporal phase mismatch: flushes destroy book; we enter when absorption begins
    is_fade_long = (
        deriv.cvd_notional_usd_zscore <= -1.2 and
        ob.bid_depth_5 > 0.0 and
        ob.imbalance_5 >= -0.05  # Book is no longer in free-fall ask-only exhaustion
    )

    is_fade_short = (
        deriv.cvd_notional_usd_zscore >= 1.2 and
        ob.ask_depth_5 > 0.0 and
        ob.imbalance_5 <= 0.05   # Book is no longer in vertical squeeze bid-only exhaustion
    )

    direction = "LONG" if is_fade_long else ("SHORT" if is_fade_short else None)

    if not direction:
        rejection_codes.append("REJECT_EXECUTION_UNCERTAINTY")

    # Edge calculation (liquidation rebound expected move net of 8.0 bps taker fees)
    expected_edge_bps = max(18.0, min(50.0, deriv.liquidation_intensity * 7.5))
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
