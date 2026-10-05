"""Superhuman AI Strategy Engine for Crypto Perpetual Desk.

Runs in parallel with Strong Quant, maintaining an independent paper portfolio:
- origin="SUPERHUMAN"
- desk="SUPERHUMAN"
- Capital: $10,000 USDT

Strategies:
1. STRAT_SUPERHUMAN_MACRO_REGIME_V1: Regime inflection & multi-timeframe CVD momentum.
2. STRAT_SUPERHUMAN_CROSS_VENUE_V1: Basis dislocation & convergence arbitration.
3. STRAT_SUPERHUMAN_VOLATILITY_EXPANSION_V1: Compression-to-expansion breakout thesis.
"""
import asyncio
import logging
from typing import Dict, List, Optional
from config.settings import settings
from config.currency import currency_service
from core.clock import now_utc_ms, ms_to_iso
from core.contracts import DerivativesState, OrderbookSnapshot, CandidateRecord
from storage.registry import StrategyDefinition, StrategyStatus, strategy_registry
from storage.sqlite_store import sqlite_store
from collectors.orderbook import BoundedOrderbook
from collectors.derivatives import derivatives_engine
from collectors.binance_ws import collector
from execution.parity import parity_engine

logger = logging.getLogger("superhuman_evaluator")

SH1_ID = "STRAT_SUPERHUMAN_MACRO_REGIME_V1"
SH2_ID = "STRAT_SUPERHUMAN_CROSS_VENUE_V1"
SH3_ID = "STRAT_SUPERHUMAN_VOLATILITY_EXPANSION_V1"
SH4_ID = "STRAT_SUPERHUMAN_SWING_V1"


def evaluate_superhuman_macro_regime(deriv: DerivativesState, ob: OrderbookSnapshot) -> CandidateRecord:
    now_ms = now_utc_ms()
    cand_id = f"CAND-SH1-{deriv.symbol}-{now_ms}"
    rejections = []

    spread_bps = ob.spread_bps
    friction_bps = 8.0 + spread_bps + 2.0  # Taker 8 bps + spread + 2 bps slip
    cvd_z = deriv.cvd_notional_usd_zscore
    regime = deriv.regime

    # Thesis: Trending/high-volatility regimes are ideal, but RANGE with strong CVD impulse
    # (>= 1.60σ) signals early breakout — regime lags price by definition.
    HARD_REGIMES = {"TRENDING_UP", "TRENDING_DOWN", "HIGH_VOLATILITY"}
    in_hard_regime = regime in HARD_REGIMES
    range_breakout  = (regime == "RANGE" and abs(cvd_z) >= 1.60)

    if not (in_hard_regime or range_breakout):
        rejections.append("REJECT_REGIME_UNFAVORABLE")

    # CVD impulse threshold lowered: 2.5σ was a 1-in-160 event per 60s snapshot.
    # 1.6σ captures the leading edge of a breakout while remaining above ~94th percentile.
    if abs(cvd_z) < 1.60:
        rejections.append("REJECT_SIGNAL_THRESHOLD_NOT_MET")

    # Edge calculation: 1.6σ CVD yields ~13.6 bps gross edge; friction is ~10-11 bps
    expected_edge_bps = abs(cvd_z) * 8.5  # e.g. 1.6 * 8.5 = 13.6 bps
    expected_net_edge_bps = expected_edge_bps - friction_bps

    if expected_net_edge_bps < 1.5:
        rejections.append("REJECT_EDGE_TOO_SMALL")

    direction = "LONG" if cvd_z >= 0 else "SHORT"
    decision = "ACCEPT" if not rejections else "REJECT"
    decision_reason = (
        f"Accepted: Superhuman Macro Regime Impulse {direction} (cvd_z={cvd_z:+.2f})"
        if not rejections else f"Rejected: {', '.join(rejections)}"
    )

    return CandidateRecord(
        candidate_id=cand_id,
        timestamp_ms=now_ms,
        symbol=deriv.symbol,
        origin="SUPERHUMAN",
        engine_version="v2.0-SH",
        model_version="deepseek-r1-crypto",
        signal_version="v2.0",
        decision=decision,
        decision_reason=decision_reason,
        hypothetical_entry=(ob.best_ask if direction == "LONG" else ob.best_bid) if not rejections else ob.mid_price,
        strategy_id=SH1_ID,
        strategy_version="1.0.0",
        feature_version="1.0",
        parameter_version="1.0",
        signal_score=min(100.0, max(0.0, (expected_edge_bps / 25.0) * 100.0)) if not rejections else 0.0,
        expected_edge_bps=expected_edge_bps,
        estimated_cost_bps=friction_bps,
        expected_net_edge_bps=expected_net_edge_bps,
        regime=regime,
        rejection_codes=rejections,
        conversion_rate_applied=currency_service.get_rate("MARKET"),
        research_venue="BINANCE",
        execution_venue="DELTA_INDIA",
        created_at_iso=ms_to_iso(now_ms),
    )


def evaluate_superhuman_cross_venue(deriv: DerivativesState, ob: OrderbookSnapshot) -> CandidateRecord:
    now_ms = now_utc_ms()
    cand_id = f"CAND-SH2-{deriv.symbol}-{now_ms}"
    rejections = []

    spread_bps = ob.spread_bps
    friction_bps = 8.0 + spread_bps + 3.0

    # Cross-venue parity verification
    ok_parity, parity_code, parity_tel = parity_engine.evaluate_parity(deriv.symbol, now_ms)
    dislocation_bps = abs(parity_tel.get("dislocation_bps", 0.0))

    if not ok_parity:
        rejections.append("REJECT_EXECUTION_UNCERTAINTY")

    if dislocation_bps < 10.0:
        rejections.append("REJECT_SIGNAL_THRESHOLD_NOT_MET")

    expected_edge_bps = dislocation_bps * 1.5
    expected_net_edge_bps = expected_edge_bps - friction_bps

    if expected_net_edge_bps < 3.0:
        rejections.append("REJECT_EDGE_TOO_SMALL")

    decision = "ACCEPT" if not rejections else "REJECT"
    decision_reason = "Accepted: Superhuman Cross-Venue Dislocation Edge" if not rejections else f"Rejected: {', '.join(rejections)}"

    return CandidateRecord(
        candidate_id=cand_id,
        timestamp_ms=now_ms,
        symbol=deriv.symbol,
        origin="SUPERHUMAN",
        engine_version="v2.0-SH",
        model_version="deepseek-r1-crypto",
        signal_version="v2.0",
        decision=decision,
        decision_reason=decision_reason,
        hypothetical_entry=ob.mid_price,
        strategy_id=SH2_ID,
        strategy_version="1.0.0",
        feature_version="1.0",
        parameter_version="1.0",
        signal_score=min(100.0, max(0.0, (expected_edge_bps / 25.0) * 100.0)) if not rejections else 0.0,
        expected_edge_bps=expected_edge_bps,
        estimated_cost_bps=friction_bps,
        expected_net_edge_bps=expected_net_edge_bps,
        regime=deriv.regime,
        rejection_codes=rejections,
        conversion_rate_applied=currency_service.get_rate("MARKET"),
        research_venue="BINANCE",
        execution_venue="DELTA_INDIA",
        created_at_iso=ms_to_iso(now_ms),
    )


def evaluate_superhuman_vol_expansion(deriv: DerivativesState, ob: OrderbookSnapshot) -> CandidateRecord:
    now_ms = now_utc_ms()
    cand_id = f"CAND-SH3-{deriv.symbol}-{now_ms}"
    rejections = []

    spread_bps = ob.spread_bps
    friction_bps = 8.0 + spread_bps + 2.5
    regime = deriv.regime

    # Thesis: Market was compressed in LOW_VOLATILITY, now book imbalance breaks out
    imb5 = abs(ob.imbalance_5)
    micro_edge = abs(ob.microprice_edge_bps)

    if regime not in ["LOW_VOLATILITY", "RANGE"]:
        rejections.append("REJECT_REGIME_UNFAVORABLE")

    if imb5 < 0.50 or micro_edge < 1.5:
        rejections.append("REJECT_SIGNAL_THRESHOLD_NOT_MET")

    direction = "LONG" if ob.imbalance_5 >= 0 else "SHORT"
    expected_edge_bps = (imb5 * 20.0) + (micro_edge * 3.0)
    expected_net_edge_bps = expected_edge_bps - friction_bps

    if expected_net_edge_bps < 3.0:
        rejections.append("REJECT_EDGE_TOO_SMALL")

    decision = "ACCEPT" if not rejections else "REJECT"
    decision_reason = (
        f"Accepted: Superhuman Volatility Expansion Breakout {direction}"
        if not rejections else f"Rejected: {', '.join(rejections)}"
    )

    return CandidateRecord(
        candidate_id=cand_id,
        timestamp_ms=now_ms,
        symbol=deriv.symbol,
        origin="SUPERHUMAN",
        engine_version="v2.0-SH",
        model_version="deepseek-r1-crypto",
        signal_version="v2.0",
        decision=decision,
        decision_reason=decision_reason,
        hypothetical_entry=(ob.best_ask if direction == "LONG" else ob.best_bid) if not rejections else ob.mid_price,
        strategy_id=SH3_ID,
        strategy_version="1.0.0",
        feature_version="1.0",
        parameter_version="1.0",
        signal_score=min(100.0, max(0.0, (expected_edge_bps / 25.0) * 100.0)) if not rejections else 0.0,
        expected_edge_bps=expected_edge_bps,
        estimated_cost_bps=friction_bps,
        expected_net_edge_bps=expected_net_edge_bps,
        regime=regime,
        rejection_codes=rejections,
        conversion_rate_applied=currency_service.get_rate("MARKET"),
        research_venue="BINANCE",
        execution_venue="DELTA_INDIA",
        created_at_iso=ms_to_iso(now_ms),
    )


def evaluate_superhuman_swing(deriv: DerivativesState, ob: OrderbookSnapshot, tracker) -> CandidateRecord:
    """SH4: Superhuman Multi-Timeframe 15m EMA-Pullback — Multi-TF confirmation with OI & book integrity.

    Thesis:
    Confirms S4 Quant 15m EMA-pullback with Superhuman-layer checks:
    - OI must not be in liquidation contraction (oi_pct_1h >= -1.0%).
    - No counter-trend book trap (depth skew <= 0.35 against trade).
    - CVD confirms absorption at support/resistance (|cvd_z| >= 0.25).
    origin="SUPERHUMAN", desk="SUPERHUMAN"
    """
    from strategies.swing_momentum import (
        resample_1m_to_15m, _ema, _adx,
        BAR_SIZE_MINUTES, EMA_FAST_15M, EMA_SLOW_15M, ADX_PERIOD_15M, MIN_15M_BARS
    )

    now_ms = now_utc_ms()
    cand_id = f"CAND-SH4-15M-{deriv.symbol}-{now_ms}"
    rejections = []

    spread_bps = ob.spread_bps
    friction_bps = 8.0 + spread_bps + 2.0
    regime = deriv.regime
    cvd_z = deriv.cvd_notional_usd_zscore

    # 1. Data feed health
    if deriv.quarantine_state != "NORMAL":
        rejections.append("REJECT_DATA_STALE")

    # 2. Pull raw 1m lists
    closes  = list(getattr(tracker, "recent_closes",  []) or [])
    highs   = list(getattr(tracker, "recent_highs",   []) or [])
    lows    = list(getattr(tracker, "recent_lows",    []) or [])
    volumes = list(getattr(tracker, "recent_volumes", []) or [])

    # Resample to 15m bars
    c_15m, h_15m, l_15m, v_15m = resample_1m_to_15m(closes, highs, lows, volumes, bar_size=BAR_SIZE_MINUTES)

    if len(c_15m) < MIN_15M_BARS:
        rejections.append("REJECT_INSUFFICIENT_DATA")
        direction = None
        adx_val = None
        expected_edge_bps = 0.0
    else:
        ema_fast = _ema(c_15m, EMA_FAST_15M)
        ema_slow = _ema(c_15m, EMA_SLOW_15M)
        adx_val  = _adx(h_15m, l_15m, c_15m, ADX_PERIOD_15M)

        current_close = closes[-1] if closes else ob.mid_price
        recent_15m_lows = lows[-30:] if len(lows) >= 30 else lows
        recent_15m_highs = highs[-30:] if len(highs) >= 30 else highs

        trend_bull = (ema_fast is not None and ema_slow is not None and ema_fast > ema_slow)
        trend_bear = (ema_fast is not None and ema_slow is not None and ema_fast < ema_slow)
        adx_ok = (adx_val is not None and adx_val >= 18.0)

        # Pullback test & bounce
        pullback_tested_long = (ema_fast is not None and min(recent_15m_lows) <= ema_fast * 1.003)
        reclaiming_long = (ema_fast is not None and current_close >= ema_fast * 0.9995)
        long_setup = trend_bull and pullback_tested_long and reclaiming_long

        pullback_tested_short = (ema_fast is not None and max(recent_15m_highs) >= ema_fast * 0.997)
        rejecting_short = (ema_fast is not None and current_close <= ema_fast * 1.0005)
        short_setup = trend_bear and pullback_tested_short and rejecting_short

        if not (long_setup or short_setup):
            rejections.append("REJECT_SIGNAL_THRESHOLD_NOT_MET")
            direction = None
        elif not adx_ok:
            rejections.append("REJECT_LOW_ADX_STRENGTH")
            direction = None
        else:
            direction = "LONG" if long_setup else "SHORT"

        # 3. Superhuman Layer — OI Contraction Trap Filter
        oi_pct_1h = getattr(deriv, "oi_change_pct_1h", 0.0)
        if direction is not None and oi_pct_1h < -1.0:
            rejections.append("REJECT_OI_CONTRACTION_UNWIND")
            direction = None

        # 4. Book trap filter: reject if counter-trend depth skew > 0.35
        if direction == "LONG" and ob.imbalance_5 < -0.35:
            rejections.append("REJECT_COUNTER_TREND_BOOK_TRAP")
            direction = None
        elif direction == "SHORT" and ob.imbalance_5 > 0.35:
            rejections.append("REJECT_COUNTER_TREND_BOOK_TRAP")
            direction = None

        # 5. CVD direction confirmation: absorption flow (|z| >= 0.25)
        if direction == "LONG" and cvd_z < 0.25:
            rejections.append("REJECT_CVD_DIRECTION_MISMATCH")
            direction = None
        elif direction == "SHORT" and cvd_z > -0.25:
            rejections.append("REJECT_CVD_DIRECTION_MISMATCH")
            direction = None

        # 6. Edge
        adx_factor = (adx_val / 20.0) if adx_val else 1.0
        expected_edge_bps = 55.0 * adx_factor if direction else 0.0

    friction_bps_total = friction_bps
    expected_net_edge_bps = expected_edge_bps - friction_bps_total

    if direction is not None and expected_net_edge_bps < 4.0:
        rejections.append("REJECT_EDGE_TOO_SMALL")
        direction = None
        expected_edge_bps = 0.0
        expected_net_edge_bps = -friction_bps_total

    decision = "ACCEPT" if not rejections else "REJECT"
    if decision == "ACCEPT":
        decision_reason = (
            f"SH4 15m {direction} swing pullback: ADX={adx_val:.1f}, CVD_z={cvd_z:+.2f}, "
            f"net_edge={expected_net_edge_bps:.1f} bps, book_imb={ob.imbalance_5:+.2f}"
        )
        hypothetical_entry = ob.best_ask if direction == "LONG" else ob.best_bid
    else:
        decision_reason = f"Rejected: {', '.join(rejections)}"
        hypothetical_entry = ob.mid_price

    return CandidateRecord(
        candidate_id=cand_id,
        timestamp_ms=now_ms,
        symbol=deriv.symbol,
        origin="SUPERHUMAN",
        engine_version="v2.0-SH4-15M",
        model_version="deepseek-r1-pullback-v2",
        signal_version="v2.0",
        decision=decision,
        decision_reason=decision_reason,
        hypothetical_entry=hypothetical_entry,
        strategy_id=SH4_ID,
        strategy_version="2.0.0",
        feature_version="2.0",
        parameter_version="2.0",
        signal_score=min(100.0, max(0.0, (expected_edge_bps / 50.0) * 100.0)) if decision == "ACCEPT" else 0.0,
        expected_edge_bps=expected_edge_bps,
        estimated_cost_bps=friction_bps_total,
        expected_net_edge_bps=expected_net_edge_bps,
        regime=regime,
        rejection_codes=rejections,
        conversion_rate_applied=currency_service.get_rate("MARKET"),
        research_venue="BINANCE",
        execution_venue="DELTA_INDIA",
        created_at_iso=ms_to_iso(now_ms),
    )


class SuperhumanEvaluator:
    """Continuous evaluation runner for Superhuman AI strategies."""

    def __init__(self):
        self.registry = strategy_registry
        self._bootstrap_strategies()
        self.is_running = False

    def _bootstrap_strategies(self):
        sh1 = StrategyDefinition(
            strategy_id=SH1_ID,
            strategy_name="Superhuman Macro Regime & CVD Impulse",
            version="1.0.0",
            hypothesis="Synthesizes 1m CVD flow with macro 7-state regime transitions.",
            feature_version="1.0",
            parameter_version="1.0",
            entry_rules={"regime": ["TRENDING_UP", "TRENDING_DOWN", "HIGH_VOLATILITY"], "cvd_zscore": 2.5},
            exit_rules={"tp_pct": 1.5, "sl_pct": 0.6, "max_hold_hours": 2},
            risk_profile={"max_risk_pct": 0.25, "leverage": 5},
            status=StrategyStatus.PAPER_ACTIVE,
            approved_by="MANAGING_PARTNER",
            research_commit_hash="HEAD"
        )
        sh2 = StrategyDefinition(
            strategy_id=SH2_ID,
            strategy_name="Superhuman Cross-Venue Dislocation Fader",
            version="1.0.0",
            hypothesis="Exploits basis dislocation between Binance and Delta when basis expands.",
            feature_version="1.0",
            parameter_version="1.0",
            entry_rules={"dislocation_bps": 10.0, "parity_status": "PARITY_VERIFIED"},
            exit_rules={"tp_pct": 0.80, "sl_pct": 0.40, "max_hold_mins": 45},
            risk_profile={"max_risk_pct": 0.25, "leverage": 5},
            status=StrategyStatus.PAPER_ACTIVE,
            approved_by="MANAGING_PARTNER",
            research_commit_hash="HEAD"
        )
        sh3 = StrategyDefinition(
            strategy_id=SH3_ID,
            strategy_name="Superhuman Volatility Compression Breakout",
            version="1.0.0",
            hypothesis="Exploits compression breakout when book imbalance spikes during low volatility.",
            feature_version="1.0",
            parameter_version="1.0",
            entry_rules={"regime": "LOW_VOLATILITY", "imbalance_5": 0.50, "micro_edge_bps": 1.5},
            exit_rules={"tp_pct": 2.0, "sl_pct": 0.70, "max_hold_mins": 90},
            risk_profile={"max_risk_pct": 0.25, "leverage": 5},
            status=StrategyStatus.PAPER_ACTIVE,
            approved_by="MANAGING_PARTNER",
            research_commit_hash="HEAD"
        )
        sh4 = StrategyDefinition(
            strategy_id=SH4_ID,
            strategy_name="Superhuman Intraday Swing Momentum",
            version="1.0.0",
            hypothesis="Multi-TF EMA/Donchian breakout with OI expansion, book integrity, and CVD direction confirmation.",
            feature_version="1.0",
            parameter_version="1.0",
            entry_rules={"ema_fast": 20, "ema_slow": 50, "donchian": 20, "adx_min": 22.0, "vol_ratio_min": 1.25, "cvd_z_min": 0.80},
            exit_rules={"tp_pct": 1.50, "sl_pct": 0.75, "max_hold_hours": 4},
            risk_profile={"max_risk_pct": 0.25, "leverage": 5},
            status=StrategyStatus.PAPER_ACTIVE,
            approved_by="MANAGING_PARTNER",
            research_commit_hash="HEAD"
        )
        self.registry.register_strategy(sh1)
        self.registry.register_strategy(sh2)
        self.registry.register_strategy(sh3)
        self.registry.register_strategy(sh4)
        logger.info("Superhuman strategies SH1, SH2, SH3, SH4 registered.")

    async def evaluate_once(self) -> List[CandidateRecord]:
        records: List[CandidateRecord] = []
        for symbol in settings.SYMBOLS_FUTURES:
            upper = symbol.upper()
            if upper not in collector.orderbooks or upper not in derivatives_engine.trackers:
                continue

            try:
                ob = collector.orderbooks[upper].get_snapshot()
            except Exception:
                continue

            deriv = derivatives_engine.trackers[upper].get_state()
            if not deriv:
                continue

            tracker = derivatives_engine.trackers.get(upper)

            c1 = evaluate_superhuman_macro_regime(deriv, ob)
            # SH2 (CROSS_VENUE) PARKED 2026-09-29 - requires a live Delta India venue feed
            # that is not wired: parity_engine.delta_prices is never populated, so
            # evaluate_parity always returns PARITY_DELTA_FEED_DISCONNECTED. See
            # CRYPTO_LOGBOOK.md FM-3. Re-enable when execution/parity.py has a Delta feed.
            # c2 = evaluate_superhuman_cross_venue(deriv, ob)
            c3 = evaluate_superhuman_vol_expansion(deriv, ob)
            c4 = evaluate_superhuman_swing(deriv, ob, tracker)

            for cand in [c1, c3, c4]:
                sqlite_store.insert_candidate(cand)
                records.append(cand)
                if cand.decision == "ACCEPT":
                    try:
                        from paper.dispatcher import paper_dispatcher
                        await paper_dispatcher.dispatch_candidate(cand, collector.orderbooks)
                    except Exception as e:
                        logger.error("Error dispatching Superhuman candidate %s: %s", cand.candidate_id, e)

        return records

    async def run_loop(self):
        self.is_running = True
        logger.info("Starting SuperhumanEvaluator 60s background loop.")
        while self.is_running:
            try:
                records = await self.evaluate_once()
                accepted = [r for r in records if r.decision == "ACCEPT"]
                if accepted:
                    logger.info("Superhuman cycle: %d evaluated, %d ACCEPTED!", len(records), len(accepted))
                else:
                    logger.info("Superhuman cycle: %d evaluated, 0 accepted (negative proofs recorded).", len(records))
            except Exception as e:
                logger.error("Error in SuperhumanEvaluator loop: %s", e, exc_info=True)
            await asyncio.sleep(60.0)


superhuman_evaluator = SuperhumanEvaluator()
