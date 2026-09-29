"""Continuous Alpha Strategy Evaluator for Crypto Research Desk.

Runs every 60 seconds on VM 2.
1. Registers and synchronizes Strategy 1, 2, and 3 in the Strategy Registry.
2. Pulls latest DerivativesState and OrderbookSnapshot.
3. Evaluates all active strategies against current market features and regime.
4. Emits canonical CandidateRecords (both ACCEPTED and REJECTED) with provenance directly into SQLite.
"""
import asyncio
import logging
from typing import Dict, List
from config.settings import settings
from core.contracts import CandidateRecord
from storage.sqlite_store import sqlite_store
from storage.registry import StrategyRegistry, StrategyDefinition, StrategyStatus
from collectors.binance_ws import collector
from collectors.derivatives import derivatives_engine

from strategies.funding_reversion import evaluate_funding_reversion, STRATEGY_ID as S1_ID
from strategies.orderbook_momentum import evaluate_orderbook_momentum, STRATEGY_ID as S2_ID
from strategies.liquidation_fader import evaluate_liquidation_fader, STRATEGY_ID as S3_ID
from strategies.swing_momentum import evaluate_swing_momentum, STRATEGY_ID as S4_ID

logger = logging.getLogger("crypto_evaluator")


class StrategyEvaluator:
    def __init__(self):
        self.registry = StrategyRegistry(sqlite_store)
        self.is_running = False
        self._bootstrap_registry()

    def _bootstrap_registry(self):
        """Ensures the 3 canonical strategies are defined in the registry."""
        s1 = StrategyDefinition(
            strategy_id=S1_ID,
            strategy_name="Funding Rate & Basis Mean-Reversion",
            version="1.0.0",
            hypothesis="Extreme funding rates (> 2.0 sigma) combined with basis divergence mean-reverts.",
            feature_version="1.0",
            parameter_version="1.0",
            entry_rules={"funding_zscore": 2.0, "basis_bps": 3.0, "microprice_edge_bps": 1.0},
            exit_rules={"tp_pct": 1.5, "sl_pct": 0.8, "max_hold_hours": 8},
            risk_profile={"max_risk_pct": 0.25, "leverage": 5},
            status=StrategyStatus.PAPER_ACTIVE,
            approved_by="MANAGING_PARTNER",
            research_commit_hash="HEAD"
        )
        s2 = StrategyDefinition(
            strategy_id=S2_ID,
            strategy_name="Orderbook Imbalance & Portable CVD Momentum Scalper",
            version="1.0.0",
            hypothesis="5-level depth imbalance aligned with CVD z-score predicts directional drift.",
            feature_version="1.0",
            parameter_version="1.0",
            entry_rules={"imbalance_5": 0.40, "cvd_zscore": 2.0, "taker_buy_ratio": 0.65},
            exit_rules={"tp_pct": 0.75, "sl_pct": 0.40, "max_hold_mins": 30},
            risk_profile={"max_risk_pct": 0.25, "leverage": 5},
            status=StrategyStatus.PAPER_ACTIVE,
            approved_by="MANAGING_PARTNER",
            research_commit_hash="HEAD"
        )
        s3 = StrategyDefinition(
            strategy_id=S3_ID,
            strategy_name="Volume/OI-Adaptive Liquidation Exhaustion Fader",
            version="1.0.0",
            hypothesis="Cascading liquidation momentum exhausts into a vacuum and snaps back to mid.",
            feature_version="1.0",
            parameter_version="1.0",
            entry_rules={"liquidation_intensity": 3.0, "oi_impact": 0.001},
            exit_rules={"tp_pct": 1.0, "sl_pct": 0.50, "max_hold_mins": 60},
            risk_profile={"max_risk_pct": 0.25, "leverage": 5},
            status=StrategyStatus.PAPER_ACTIVE,
            approved_by="MANAGING_PARTNER",
            research_commit_hash="HEAD"
        )
        s4 = StrategyDefinition(
            strategy_id=S4_ID,
            strategy_name="Intraday Trend & Swing Momentum Breakout",
            version="1.0.0",
            hypothesis="EMA-aligned Donchian breakout with volume expansion and ADX confirmation captures multi-hour swings.",
            feature_version="1.0",
            parameter_version="1.0",
            entry_rules={"ema_fast": 20, "ema_slow": 50, "donchian": 20, "adx_min": 22.0, "vol_ratio_min": 1.25},
            exit_rules={"tp_pct": 1.50, "sl_pct": 0.75, "max_hold_hours": 4},
            risk_profile={"max_risk_pct": 0.25, "leverage": 5},
            status=StrategyStatus.PAPER_ACTIVE,
            approved_by="MANAGING_PARTNER",
            research_commit_hash="HEAD"
        )
        self.registry.register_strategy(s1)
        self.registry.register_strategy(s2)
        self.registry.register_strategy(s3)
        self.registry.register_strategy(s4)
        logger.info("Bootstrap complete: Strategies S1, S2, S3, S4 initialized in Strategy Registry.")

    async def evaluate_once(self) -> List[CandidateRecord]:
        """Runs a single evaluation sweep across all tracked symbols and strategies."""
        records: List[CandidateRecord] = []

        for symbol in settings.SYMBOLS_FUTURES:
            upper = symbol.upper()
            
            # Fetch orderbook snapshot
            if upper not in collector.orderbooks:
                logger.warning("Symbol %s not in collector.orderbooks", upper)
                continue
            try:
                ob = collector.orderbooks[upper].get_snapshot()
            except Exception as e:
                logger.warning("Orderbook snapshot failed for %s: %s", upper, e)
                continue

            # Fetch derivatives state
            if upper not in derivatives_engine.trackers:
                logger.warning("Symbol %s not in derivatives_engine.trackers", upper)
                continue
            deriv = derivatives_engine.trackers[upper].get_state()
            if not deriv:
                tracker = derivatives_engine.trackers.get(upper)
                logger.warning("Symbol %s has no deriv state (mark=%s, last_candle=%s)", upper, getattr(tracker, 'mark_price', None), getattr(tracker, 'last_candle_time_ms', None))
                continue

            # Get tracker for S4 raw list access (recent_closes, highs, lows, volumes)
            tracker = derivatives_engine.trackers.get(upper)

            # Evaluate Strategy 1, 2, 3, 4
            cand1 = evaluate_funding_reversion(deriv, ob)
            cand2 = evaluate_orderbook_momentum(deriv, ob)
            cand3 = evaluate_liquidation_fader(deriv, ob)
            cand4 = evaluate_swing_momentum(deriv, ob, tracker)

            for cand in [cand1, cand2, cand3, cand4]:
                sqlite_store.insert_candidate(cand)
                records.append(cand)
                if cand.decision == "ACCEPT":
                    try:
                        from paper.dispatcher import paper_dispatcher
                        await paper_dispatcher.dispatch_candidate(cand, collector.orderbooks)
                    except Exception as e:
                        logger.error("Error dispatching candidate %s to paper desk: %s", cand.candidate_id, e)

        return records

    async def run_loop(self):
        """Continuous 60-second evaluation loop."""
        self.is_running = True
        logger.info("Starting StrategyEvaluator 60s background loop.")
        while self.is_running:
            try:
                records = await self.evaluate_once()
                accepted = [r for r in records if r.decision == "ACCEPT"]
                if accepted:
                    logger.info("Evaluator cycle: %d candidates evaluated, %d ACCEPTED!", len(records), len(accepted))
                else:
                    logger.info("Evaluator cycle: %d candidates evaluated, 0 accepted (negative proofs recorded).", len(records))
            except Exception as e:
                logger.error("Error in StrategyEvaluator loop: %s", e, exc_info=True)
            await asyncio.sleep(60.0)


strategy_evaluator = StrategyEvaluator()
