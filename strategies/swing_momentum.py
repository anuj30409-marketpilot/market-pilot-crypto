"""Strategy 4: Multi-Timeframe 15m Trend & EMA-Pullback (S4 — QUANT Desk).

Hypothesis:
In established trending regimes on 15m aggregated bars (EMA_FAST > EMA_SLOW with ADX >= 18),
entering on shallow mean-reversion pullbacks towards the 15m Fast EMA when Cumulative Volume Delta (CVD)
confirms absorption support/resistance produces high-expectancy trend continuations with favorable reward-to-risk.

Regime constraints:
Allowed in TRENDING_UP, TRENDING_DOWN, RANGE, and HIGH_VOLATILITY.
Explicitly rejected in DATA_DEGRADED.

Data source:
- tracker.recent_closes  : List[float] — 1m close prices, up to 500 items
- tracker.recent_highs   : List[float] — 1m high prices
- tracker.recent_lows    : List[float] — 1m low prices
- tracker.recent_volumes : List[float] — 1m volumes
"""
import math
import logging
from typing import List, Optional, Tuple

from core.contracts import DerivativesState, OrderbookSnapshot, CandidateRecord
from core.clock import now_utc_ms, ms_to_iso

logger = logging.getLogger("swing_momentum")

STRATEGY_ID = "STRAT_SWING_MOMENTUM_V1"
VERSION      = "2.0.0"
FEATURE_VERSION   = "2.0"
PARAMETER_VERSION = "2.0"

# Multi-Timeframe Parameters (15m Aggregated Bars)
BAR_SIZE_MINUTES  = 15
EMA_FAST_15M      = 6    # 6 x 15m = 1.5 hours
EMA_SLOW_15M      = 16   # 16 x 15m = 4 hours
ADX_PERIOD_15M    = 8    # 8 x 15m = 2 hours
MIN_15M_BARS      = 12   # Need at least 3 hours of 1m data (12 x 15m bars)

# Execution Risk Parameters
TP_PCT        = 1.20   # 1.20% Take-Profit
SL_PCT        = 0.60   # 0.60% Stop-Loss (2:1 Reward:Risk)
MAX_HOLD_MS   = 2 * 3600 * 1000   # 2 hours

REJECTED_REGIMES = {"RANGE", "LOW_VOLATILITY", "FUNDING_EXTREME", "LIQUIDATION_EVENT", "DATA_DEGRADED"}


# ──────────────────────────────────────────────────────────────────────────────
# Multi-Timeframe Resampling & Technical Indicators
# ──────────────────────────────────────────────────────────────────────────────

def resample_1m_to_15m(
    closes: List[float], highs: List[float], lows: List[float], volumes: List[float],
    bar_size: int = 15
) -> Tuple[List[float], List[float], List[float], List[float]]:
    """Resamples 1-minute arrays into aggregated bar_size (15m) OHLCV series."""
    n = len(closes)
    if n < bar_size:
        return [], [], [], []

    remainder = n % bar_size
    start_idx = remainder  # Align to full complete historical buckets

    c_15m, h_15m, l_15m, v_15m = [], [], [], []
    for i in range(start_idx, n, bar_size):
        chunk_c = closes[i:i + bar_size]
        chunk_h = highs[i:i + bar_size]
        chunk_l = lows[i:i + bar_size]
        chunk_v = volumes[i:i + bar_size]
        if chunk_c:
            c_15m.append(chunk_c[-1])
            h_15m.append(max(chunk_h))
            l_15m.append(min(chunk_l))
            v_15m.append(sum(chunk_v))

    return c_15m, h_15m, l_15m, v_15m


def _ema(prices: List[float], period: int) -> Optional[float]:
    """Returns the current EMA value for the given period. Returns None if insufficient data."""
    if len(prices) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(prices[:period]) / period       # seed with SMA
    for price in prices[period:]:
        ema = price * k + ema * (1.0 - k)
    return ema


def _true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _adx(highs: List[float], lows: List[float], closes: List[float], period: int = 8) -> Optional[float]:
    """Wilder-smoothed ADX for 15m aggregated bars."""
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
    """Evaluates the S4 Multi-Timeframe 15m Trend & EMA-Pullback strategy."""
    now_ms = now_utc_ms()
    candidate_id = f"CAND-SWING-15M-{deriv.symbol}-{now_ms}"
    rejection_codes: List[str] = []

    # 1. Strict Directional Regime Gate (Reject Chop, Range, and Low Volatility)
    regime = deriv.regime
    if regime in REJECTED_REGIMES:
        rejection_codes.append("REJECT_REGIME_UNFAVORABLE")

    # 2. Feed Health
    if deriv.quarantine_state != "NORMAL":
        rejection_codes.append("REJECT_DATA_STALE")

    # 3. Pull raw 1m price lists from tracker
    closes  = list(getattr(tracker, "recent_closes",  []) or [])
    highs   = list(getattr(tracker, "recent_highs",   []) or [])
    lows    = list(getattr(tracker, "recent_lows",    []) or [])
    volumes = list(getattr(tracker, "recent_volumes", []) or [])

    # Resample to 15m bars
    c_15m, h_15m, l_15m, v_15m = resample_1m_to_15m(closes, highs, lows, volumes, bar_size=BAR_SIZE_MINUTES)

    if len(c_15m) < MIN_15M_BARS:
        rejection_codes.append("REJECT_INSUFFICIENT_DATA")
        direction = None
        ema_fast = ema_slow = adx_val = None
    else:
        # 4. Compute 15m Multi-Timeframe Trend Filters
        ema_fast = _ema(c_15m, EMA_FAST_15M)
        ema_slow = _ema(c_15m, EMA_SLOW_15M)
        adx_val  = _adx(h_15m, l_15m, c_15m, ADX_PERIOD_15M)

        current_close = closes[-1] if closes else ob.mid_price
        recent_15m_lows = lows[-30:] if len(lows) >= 30 else lows
        recent_15m_highs = highs[-30:] if len(highs) >= 30 else highs

        trend_bull = (ema_fast is not None and ema_slow is not None and ema_fast > ema_slow and regime == "TRENDING_UP")
        trend_bear = (ema_fast is not None and ema_slow is not None and ema_fast < ema_slow and regime == "TRENDING_DOWN")
        adx_ok = (adx_val is not None and adx_val >= 22.0)

        # 5. Pullback + Absorption Setup
        # Long Setup: Uptrend + TRENDING_UP -> Price tested Fast EMA support -> Reclaiming above EMA
        pullback_tested_long = (ema_fast is not None and min(recent_15m_lows) <= ema_fast * 1.003)
        reclaiming_long = (ema_fast is not None and current_close >= ema_fast * 0.9995)
        long_setup = trend_bull and pullback_tested_long and reclaiming_long

        # Short Setup: Downtrend + TRENDING_DOWN -> Price rallied to Fast EMA resistance -> Rejecting below EMA
        pullback_tested_short = (ema_fast is not None and max(recent_15m_highs) >= ema_fast * 0.997)
        rejecting_short = (ema_fast is not None and current_close <= ema_fast * 1.0005)
        short_setup = trend_bear and pullback_tested_short and rejecting_short

        # CVD flow & Orderbook confirmation (>= 0.25σ absorption)
        cvd_z = getattr(deriv, "cvd_notional_usd_zscore", 0.0)
        cvd_ok = (cvd_z >= 0.25) if long_setup else ((cvd_z <= -0.25) if short_setup else False)
        book_ok = (ob.imbalance_5 >= -0.20) if long_setup else ((ob.imbalance_5 <= 0.20) if short_setup else False)

        direction = None
        if long_setup or short_setup:
            if not adx_ok:
                rejection_codes.append("REJECT_LOW_ADX_STRENGTH")
            if not cvd_ok:
                rejection_codes.append("REJECT_CVD_DIRECTION_MISMATCH")
            if not book_ok:
                rejection_codes.append("REJECT_COUNTER_TREND_BOOK_TRAP")
            if adx_ok and cvd_ok and book_ok:
                direction = "LONG" if long_setup else "SHORT"
        else:
            rejection_codes.append("REJECT_SIGNAL_THRESHOLD_NOT_MET")

    # 6. Edge calculation
    if direction is not None:
        adx_factor = (adx_val / 20.0) if adx_val else 1.0
        expected_edge_bps = 50.0 * adx_factor
    else:
        expected_edge_bps = 0.0

    friction_bps = ob.spread_bps + 8.0 + 2.0  # Taker 8 bps + spread + 2 bps slip
    expected_net_edge_bps = expected_edge_bps - friction_bps

    if direction is not None and expected_net_edge_bps < 4.0:
        rejection_codes.append("REJECT_EDGE_TOO_SMALL")
        direction = None

    # 7. Final decision
    is_accepted = (len(rejection_codes) == 0 and direction is not None)
    decision = "ACCEPT" if is_accepted else "REJECT"

    if is_accepted:
        hypothetical_entry = ob.best_ask if direction == "LONG" else ob.best_bid
        decision_reason = (
            f"S4 15m {direction} EMA-pullback: "
            f"EMA_15m_fast={ema_fast:.2f}, EMA_15m_slow={ema_slow:.2f}, "
            f"ADX_15m={adx_val:.1f}, CVD_z={cvd_z:+.2f}, "
            f"net_edge={expected_net_edge_bps:.1f} bps"
        )
    else:
        hypothetical_entry = ob.mid_price
        decision_reason = f"Rejected: {', '.join(rejection_codes)}"

    signal_score = min(100.0, max(0.0, (expected_edge_bps / 50.0) * 100.0)) if is_accepted else 0.0

    from config.currency import currency_service
    return CandidateRecord(
        candidate_id=candidate_id,
        timestamp_ms=now_ms,
        symbol=deriv.symbol,
        origin="QUANT",
        engine_version="v2.0-S4-15M",
        model_version="rule-based-15m-pullback",
        signal_version="v2.0",
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
