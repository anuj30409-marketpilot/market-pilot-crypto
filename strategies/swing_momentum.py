"""Strategy 4: Intraday Trend & Swing Momentum (S4 — QUANT Desk).

Hypothesis:
When price breaks above/below the 20-period Donchian channel while EMA_20 is aligned
with EMA_50 (trend filter) AND volume expands above the 20-period average AND ADX >= 22
(confirming directional strength), the move has momentum to sustain 1.5% drift within
the next 4 hours.

This strategy is designed to capture the 15m/1h chart swings the user observes visually
but that S1/S2/S3 cannot catch because they focus on funding cycles, book micro-structure,
and liquidation cascades respectively.

Regime constraints:
Allowed in ALL regimes EXCEPT DATA_DEGRADED.
Rationale: Trending, ranging breakouts, and high-vol moves are all valid swing contexts.

Data source:
- tracker.recent_closes  : List[float] — 1m close prices, up to 200 items
- tracker.recent_highs   : List[float] — 1m high prices
- tracker.recent_lows    : List[float] — 1m low prices
- tracker.recent_volumes : List[float] — 1m volumes
"""
import math
import logging
from typing import List, Optional

from core.contracts import DerivativesState, OrderbookSnapshot, CandidateRecord
from core.clock import now_utc_ms, ms_to_iso

logger = logging.getLogger("swing_momentum")

STRATEGY_ID = "STRAT_SWING_MOMENTUM_V1"
VERSION      = "1.0.0"
FEATURE_VERSION   = "1.0"
PARAMETER_VERSION = "1.0"

# Entry thresholds
EMA_FAST  = 20
EMA_SLOW  = 50
DONCHIAN_PERIOD   = 20
ADX_PERIOD        = 14
VOLUME_RATIO_MIN  = 1.25    # Volume must be >= 1.25× the 20-period SMA of volume

# Execution
TP_PCT        = 1.50
SL_PCT        = 0.75
MAX_HOLD_MS   = 4 * 3600 * 1000   # 4 hours

REJECTED_REGIMES = {"DATA_DEGRADED"}


# ──────────────────────────────────────────────────────────────────────────────
# Technical Indicator Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _ema(prices: List[float], period: int) -> Optional[float]:
    """Returns the current EMA value for the given period. Returns None if insufficient data."""
    if len(prices) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period       # seed with SMA
    for price in prices[period:]:
        ema = price * k + ema * (1.0 - k)
    return ema


def _donchian(highs: List[float], lows: List[float], period: int):
    """Returns (dc_high, dc_low) of the most recent `period` bars (excluding current)."""
    if len(highs) < period or len(lows) < period:
        return None, None
    window_h = highs[-period - 1:-1]   # exclude current bar
    window_l = lows[-period - 1:-1]
    if not window_h or not window_l:
        return None, None
    return max(window_h), min(window_l)


def _sma(values: List[float], period: int) -> Optional[float]:
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _adx(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> Optional[float]:
    """Wilder-smoothed ADX. Returns float or None if insufficient data."""
    required = period * 2 + 1
    if len(closes) < required:
        return None

    plus_dm_list, minus_dm_list, tr_list = [], [], []
    for i in range(1, len(closes)):
        h, l, c = highs[i], lows[i], closes[i]
        ph, pl = highs[i - 1], lows[i - 1]
        pc = closes[i - 1]

        up_move   = h - ph
        down_move = pl - l
        plus_dm  = up_move   if (up_move > down_move and up_move   > 0) else 0.0
        minus_dm = down_move if (down_move > up_move  and down_move > 0) else 0.0
        tr = _true_range(h, l, pc)
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
        tr_list.append(tr)

    # Wilder smoothing seed
    smoothed_tr       = sum(tr_list[:period])
    smoothed_plus_dm  = sum(plus_dm_list[:period])
    smoothed_minus_dm = sum(minus_dm_list[:period])

    dx_list = []
    for i in range(period, len(tr_list)):
        smoothed_tr       = smoothed_tr       - (smoothed_tr / period)       + tr_list[i]
        smoothed_plus_dm  = smoothed_plus_dm  - (smoothed_plus_dm / period)  + plus_dm_list[i]
        smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_list[i]

        if smoothed_tr == 0.0:
            continue
        pdi = 100.0 * smoothed_plus_dm / smoothed_tr
        mdi = 100.0 * smoothed_minus_dm / smoothed_tr
        if (pdi + mdi) == 0.0:
            continue
        dx = 100.0 * abs(pdi - mdi) / (pdi + mdi)
        dx_list.append(dx)

    if len(dx_list) < period:
        return None

    # Wilder smooth DX -> ADX
    adx = sum(dx_list[:period]) / period
    for dx in dx_list[period:]:
        adx = (adx * (period - 1) + dx) / period
    return adx


# ──────────────────────────────────────────────────────────────────────────────
# Main Evaluation Function
# ──────────────────────────────────────────────────────────────────────────────

def evaluate_swing_momentum(
    deriv: DerivativesState,
    ob: OrderbookSnapshot,
    tracker,                  # DerivativesTracker — provides raw list attributes
) -> CandidateRecord:
    """Evaluates the S4 Intraday Trend & Swing Momentum strategy."""
    now_ms = now_utc_ms()
    candidate_id = f"CAND-SWING-{deriv.symbol}-{now_ms}"
    rejection_codes: List[str] = []

    # 1. Regime gate (only reject DATA_DEGRADED)
    regime = deriv.regime
    if regime in REJECTED_REGIMES:
        rejection_codes.append("REJECT_DATA_STALE")

    # 2. Feed Health
    if deriv.quarantine_state != "NORMAL":
        rejection_codes.append("REJECT_DATA_STALE")

    # 3. Pull raw price lists from the tracker
    closes  = list(getattr(tracker, "recent_closes",  []) or [])
    highs   = list(getattr(tracker, "recent_highs",   []) or [])
    lows    = list(getattr(tracker, "recent_lows",    []) or [])
    volumes = list(getattr(tracker, "recent_volumes", []) or [])

    MIN_BARS = EMA_SLOW + ADX_PERIOD + 5   # Need enough history for all indicators
    if len(closes) < MIN_BARS or len(highs) < MIN_BARS or len(lows) < MIN_BARS:
        rejection_codes.append("REJECT_INSUFFICIENT_DATA")
        direction = None
        ema_fast = ema_slow = dc_high = dc_low = vol_ratio = adx_val = None
    else:
        # 4. Compute EMA_20 and EMA_50
        ema_fast = _ema(closes, EMA_FAST)
        ema_slow = _ema(closes, EMA_SLOW)

        # 5. Donchian Channel (20-period, excluding current bar)
        dc_high, dc_low = _donchian(highs, lows, DONCHIAN_PERIOD)

        # 6. Volume expansion check
        # Use max of latest completed bar and current in-progress bar so evaluation timing within minute doesn't penalize
        vol_sma = _sma(volumes[-21:-1] if len(volumes) >= 22 else volumes[:-1], DONCHIAN_PERIOD)
        eval_vol = max(volumes[-1], volumes[-2]) if len(volumes) >= 2 else (volumes[-1] if volumes else 0.0)
        vol_ratio = (eval_vol / vol_sma) if vol_sma and vol_sma > 0 else 0.0

        # 7. ADX directional strength
        adx_val = _adx(highs, lows, closes, ADX_PERIOD)

        current_close = closes[-1] if closes else ob.mid_price

        # 8. Direction determination
        trend_bull = (ema_fast is not None and ema_slow is not None and ema_fast > ema_slow)
        trend_bear = (ema_fast is not None and ema_slow is not None and ema_fast < ema_slow)
        broke_dc_high = (dc_high is not None and current_close > dc_high)
        broke_dc_low  = (dc_low  is not None and current_close < dc_low)

        long_setup  = trend_bull and broke_dc_high
        short_setup = trend_bear and broke_dc_low

        # Volume confirmation
        vol_ok = (vol_ratio >= VOLUME_RATIO_MIN) if vol_ratio else False

        # ADX confirmation
        adx_ok = (adx_val is not None and adx_val >= 22.0)

        direction = None
        if long_setup or short_setup:
            if not vol_ok:
                rejection_codes.append("REJECT_LOW_VOLUME_EXPANSION")
            if not adx_ok:
                rejection_codes.append("REJECT_LOW_ADX_STRENGTH")
            if vol_ok and adx_ok:
                direction = "LONG" if long_setup else "SHORT"
        else:
            rejection_codes.append("REJECT_SIGNAL_THRESHOLD_NOT_MET")

    # 9. Edge estimation: Swing moves typically 20-80 bps per hour; we target 1.5% (150 bps) TP
    #    Conservative estimate: EMA alignment + breakout = ~40-80 bps expected drift first 30m
    if direction is not None:
        adx_factor = (adx_val / 22.0) if adx_val else 1.0
        expected_edge_bps = 45.0 * adx_factor
    else:
        expected_edge_bps = 0.0

    friction_bps = ob.spread_bps + 10.0 + 2.0   # spread + taker 10 bps + slip
    expected_net_edge_bps = expected_edge_bps - friction_bps

    if direction is not None and expected_net_edge_bps < 5.0:
        rejection_codes.append("REJECT_EDGE_TOO_SMALL")
        direction = None   # invalidate direction

    # 10. Final decision
    is_accepted = (len(rejection_codes) == 0 and direction is not None)
    decision = "ACCEPT" if is_accepted else "REJECT"

    if is_accepted:
        hypothetical_entry = ob.best_ask if direction == "LONG" else ob.best_bid
        decision_reason = (
            f"S4 {direction} swing breakout: "
            f"EMA_fast={ema_fast:.2f}, EMA_slow={ema_slow:.2f}, "
            f"DC_high={dc_high:.2f}, DC_low={dc_low:.2f}, "
            f"vol_ratio={vol_ratio:.2f}x, ADX={adx_val:.1f}, "
            f"net_edge={expected_net_edge_bps:.1f} bps"
        )
    else:
        hypothetical_entry = ob.mid_price
        decision_reason = f"Rejected: {', '.join(rejection_codes)}"

    signal_score = min(100.0, max(0.0, (expected_edge_bps / 60.0) * 100.0)) if is_accepted else 0.0

    # Runtime currency (required by CandidateRecord)
    from config.currency import currency_service
    return CandidateRecord(
        candidate_id=candidate_id,
        timestamp_ms=now_ms,
        symbol=deriv.symbol,
        origin="QUANT",
        engine_version="v2.0-S4",
        model_version="rule-based-swing-v1",
        signal_version="v1.0",
        decision=decision,
        decision_reason=decision_reason,
        hypothetical_entry=hypothetical_entry,
        strategy_id=STRATEGY_ID,
        strategy_version=VERSION,
        feature_version=FEATURE_VERSION,
        parameter_version=PARAMETER_VERSION,
        signal_score=signal_score,
        expected_edge_bps=expected_edge_bps,
        estimated_cost_bps=friction_bps,
        expected_net_edge_bps=expected_net_edge_bps,
        regime=regime,
        rejection_codes=rejection_codes,
        conversion_rate_applied=currency_service.get_rate("MARKET"),
        research_venue="BINANCE",
        execution_venue="DELTA_INDIA",
        created_at_iso=ms_to_iso(now_ms),
    )
