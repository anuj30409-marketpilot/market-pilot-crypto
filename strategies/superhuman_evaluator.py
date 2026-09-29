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


def evaluate_superhuman_macro_regime(deriv: DerivativesState, ob: OrderbookSnapshot) -> CandidateRecord:
    now_ms = now_utc_ms()
    cand_id = f"CAND-SH1-{deriv.symbol}-{now_ms}"
    rejections = []

    spread_bps = ob.spread_bps
    friction_bps = 8.0 + spread_bps + 2.0  # Taker 8 bps + spread + 2 bps slip
    cvd_z = deriv.cvd_notional_usd_zscore
    regime = deriv.regime

    # Thesis: Regime must be trending or high volatility, or strong CVD impulse
    if regime not in ["TRENDING_UP", "TRENDING_DOWN", "HIGH_VOLATILITY"]:
        rejections.append("REJECT_REGIME_UNFAVORABLE")

    if abs(cvd_z) < 2.5:
        rejections.append("REJECT_SIGNAL_THRESHOLD_NOT_MET")

    # Edge calculation
    expected_edge_bps = abs(cvd_z) * 6.0  # e.g. 2.5 * 6 = 15 bps
    expected_net_edge_bps = expected_edge_bps - friction_bps

    if expected_net_edge_bps < 3.0:
        rejections.append("REJECT_EDGE_TOO_SMALL")

    decision = "ACCEPT" if not rejections else "REJECT"
    decision_reason = "Accepted: Superhuman Macro Regime Impulse Aligned" if not rejections else f"Rejected: {', '.join(rejections)}"

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

    expected_edge_bps = (imb5 * 10.0) + (micro_edge * 2.0)
    expected_net_edge_bps = expected_edge_bps - friction_bps

    if expected_net_edge_bps < 3.0:
        rejections.append("REJECT_EDGE_TOO_SMALL")

    decision = "ACCEPT" if not rejections else "REJECT"
    decision_reason = "Accepted: Superhuman Volatility Expansion Breakout" if not rejections else f"Rejected: {', '.join(rejections)}"

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
        self.registry.register_strategy(sh1)
        self.registry.register_strategy(sh2)
        self.registry.register_strategy(sh3)
        logger.info("Superhuman strategies SH1, SH2, SH3 registered.")

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

            c1 = evaluate_superhuman_macro_regime(deriv, ob)
            c2 = evaluate_superhuman_cross_venue(deriv, ob)
            c3 = evaluate_superhuman_vol_expansion(deriv, ob)

            for cand in [c1, c2, c3]:
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
