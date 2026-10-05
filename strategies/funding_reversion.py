"""Strategy 1: Normalized Funding Rate & Basis Mean-Reversion.

Hypothesis:
Extreme funding rates (> 2.0 standard deviations from 7-day rolling mean)
combined with spot-futures basis divergence indicate excessive retail leverage crowding
that predictably mean-reverts or squeezes.

Regime constraints:
Allowed only in FUNDING_EXTREME, RANGE, or LOW_VOLATILITY.
Explicitly rejected in LIQUIDATION_EVENT or DATA_DEGRADED.
"""
from typing import Optional, Tuple, List
from core.contracts import DerivativesState, OrderbookSnapshot, CandidateRecord
from core.regime import MarketRegime
from core.clock import now_utc_ms, ms_to_iso


STRATEGY_ID = "STRAT_FUNDING_REVERSION_V1"
VERSION = "1.0.0"
FEATURE_VERSION = "1.0"
PARAMETER_VERSION = "1.0"

ALLOWED_REGIMES = {
    MarketRegime.FUNDING_EXTREME.value,
    MarketRegime.RANGE.value,
    MarketRegime.LOW_VOLATILITY.value,
}


def evaluate_funding_reversion(
    deriv: DerivativesState,
    ob: OrderbookSnapshot,
    taker_fee_bps: float = 5.0,
    slippage_reserve_bps: float = 2.0,
    min_net_edge_bps: float = 3.0,
) -> CandidateRecord:
    """Evaluates the Funding & Basis Mean-Reversion strategy against current state."""
    now_ms = now_utc_ms()
    candidate_id = f"CAND-FUND-{deriv.symbol}-{now_ms}"
    rejection_codes: List[str] = []

    # 1. Regime Check
    if deriv.regime not in ALLOWED_REGIMES:
        rejection_codes.append("REJECT_REGIME_UNFAVORABLE")

    # 2. Feed / Data Quality Check
    if deriv.quarantine_state != "NORMAL":
        rejection_codes.append("REJECT_DATA_STALE")

    # 3. Spread Filter
    if ob.spread_bps > 3.5:
        rejection_codes.append("REJECT_HIGH_SPREAD")

    # Directional Signals (Normalized Funding Dislocation + Basis Divergence)
    is_long = (
        deriv.funding_zscore_7d <= -1.8 and
        deriv.basis_bps <= -1.5 and
        ob.microprice_edge_bps >= 0.0 and
        deriv.distance_to_next_funding_mins <= 240
    )

    is_short = (
        deriv.funding_zscore_7d >= 1.8 and
        deriv.basis_bps >= 1.5 and
        ob.microprice_edge_bps <= 0.0 and
        deriv.distance_to_next_funding_mins <= 240
    )

    cvd_z = getattr(deriv, "cvd_notional_usd_zscore", 0.0)
    if is_short and cvd_z >= 0.8:
        rejection_codes.append("REJECT_OPPOSING_CVD_MOMENTUM")
        is_short = False
    elif is_long and cvd_z <= -0.8:
        rejection_codes.append("REJECT_OPPOSING_CVD_MOMENTUM")
        is_long = False

    direction = "LONG" if is_long else ("SHORT" if is_short else None)

    if not direction and not rejection_codes:
        rejection_codes.append("REJECT_SIGNAL_THRESHOLD_NOT_MET")

    # 4. Edge and Cost Calculation
    # Expected gross edge combines funding carry yield + spot-futures basis convergence
    abs_z = abs(deriv.funding_zscore_7d)
    abs_basis = abs(deriv.basis_bps)
    expected_edge_bps = (abs_z * 7.0) + (abs_basis * 2.0)

    # Total estimated execution friction (spread + 2x taker fee + slippage reserve)
    estimated_cost_bps = ob.spread_bps + (taker_fee_bps * 2.0) + slippage_reserve_bps
    expected_net_edge_bps = expected_edge_bps - estimated_cost_bps

    if expected_net_edge_bps < min_net_edge_bps:
        rejection_codes.append("REJECT_EDGE_TOO_SMALL")

    # Final Decision
    is_accepted = (len(rejection_codes) == 0 and direction is not None)
    decision = "ACCEPT" if is_accepted else "REJECT"

    if is_accepted:
        decision_reason = (
            f"Valid {direction} carry signal: Funding z-score={deriv.funding_zscore_7d:.2f}, "
            f"Basis={deriv.basis_bps:.1f} bps, Microprice edge={ob.microprice_edge_bps:.1f} bps, "
            f"Net edge={expected_net_edge_bps:.1f} bps"
        )
        hypothetical_entry = ob.best_ask if direction == "LONG" else ob.best_bid
    else:
        decision_reason = f"Rejected: {', '.join(rejection_codes)}"
        hypothetical_entry = None

    signal_score = max(0.0, min(1.0, (expected_net_edge_bps / 20.0))) if is_accepted else 0.0

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
