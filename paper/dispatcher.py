"""Institutional Autonomous Paper Dispatcher and Portfolio Risk Controller.

Implements Phase 4 of the Quantitative Alpha OS:
1. Risk defined as actual dollar loss (0.25% of equity / adverse stop-out move).
2. Strategy-specific lifecycles (S1: 8h, S2: 30m, S3: 60m).
3. Portfolio-level constraints (200% max gross notional, 100% net directional, 2% daily DD circuit breaker).
4. Orderbook-walk execution simulation with complete provenance linking to crypto_candidate_ledger.
"""
import logging
from typing import Dict, List, Optional, Tuple
from core.clock import now_utc_ms
from core.contracts import CandidateRecord
from collectors.orderbook import BoundedOrderbook
from paper.paper_engine import paper_engine, PAPER_CAPITAL_USDT, DEFAULT_LEVERAGE
from storage.sqlite_store import sqlite_store

logger = logging.getLogger("paper_dispatcher")

# ── Institutional Risk Configuration ─────────────────────────────────────────

DEFAULT_RISK_PER_TRADE_PCT = 0.25  # 0.25% of equity ($25.00 on $10,000 account)
ESTIMATED_ROUNDTRIP_FRICTION_PCT = 0.13  # 13 bps (fees + slippage)
MAX_GROSS_NOTIONAL_PCT = 200.0  # 200% gross exposure max ($20,000 on $10k)
MAX_NET_DIRECTIONAL_PCT = 100.0  # 100% net long/short max ($10,000 on $10k)
MAX_CONCURRENT_POSITIONS = 4
MAX_POSITIONS_PER_SYMBOL = 2
DAILY_DRAWDOWN_LIMIT_PCT = 2.0  # 2.0% daily DD circuit breaker ($200 on $10k)
CONSECUTIVE_LOSS_LIMIT = 3
COOLDOWN_AFTER_LOSSES_MS = 4 * 3600 * 1000  # 4 hours

# Strategy-specific risk & lifecycle profiles
STRATEGY_PROFILES = {
    "STRAT_FUNDING_REVERSION_V1": {
        "sl_pct": 0.80,
        "tp_pct": 1.50,
        "max_hold_ms": 8 * 3600 * 1000,  # 8 hours
        "leverage": 5,
    },
    "STRAT_ORDERBOOK_MOMENTUM_V1": {
        "sl_pct": 0.40,
        "tp_pct": 0.75,
        "max_hold_ms": 30 * 60 * 1000,  # 30 minutes
        "leverage": 5,
    },
    "STRAT_LIQUIDATION_FADER_V1": {
        "sl_pct": 0.50,
        "tp_pct": 1.00,
        "max_hold_ms": 60 * 60 * 1000,  # 60 minutes
        "leverage": 5,
    },
    "STRAT_SUPERHUMAN_MACRO_REGIME_V1": {
        "sl_pct": 0.60,
        "tp_pct": 1.50,
        "max_hold_ms": 120 * 60 * 1000,  # 2 hours
        "leverage": 5,
    },
    "STRAT_SUPERHUMAN_CROSS_VENUE_V1": {
        "sl_pct": 0.40,
        "tp_pct": 0.80,
        "max_hold_ms": 45 * 60 * 1000,  # 45 minutes
        "leverage": 5,
    },
    "STRAT_SUPERHUMAN_VOLATILITY_EXPANSION_V1": {
        "sl_pct": 0.70,
        "tp_pct": 2.00,
        "max_hold_ms": 90 * 60 * 1000,  # 90 minutes
        "leverage": 5,
    },
    "STRAT_SWING_MOMENTUM_V1": {
        "sl_pct": 0.75,
        "tp_pct": 1.50,
        "max_hold_ms": 4 * 3600 * 1000,  # 4 hours
        "leverage": 5,
    },
    "STRAT_SUPERHUMAN_SWING_V1": {
        "sl_pct": 0.75,
        "tp_pct": 1.50,
        "max_hold_ms": 4 * 3600 * 1000,  # 4 hours
        "leverage": 5,
    },
}


class PortfolioRiskController:
    """Enforces multi-level portfolio risk boundaries and circuit breakers."""

    def __init__(self):
        self.circuit_breaker_active = False
        self.circuit_breaker_reason = ""
        self.circuit_breaker_until_ms = 0
        self.strategy_cooldowns: Dict[str, int] = {}

    def is_healthy(self, now_ms: int) -> Tuple[bool, str]:
        if self.circuit_breaker_active:
            if now_ms < self.circuit_breaker_until_ms:
                return False, f"CIRCUIT_BREAKER_ACTIVE: {self.circuit_breaker_reason}"
            else:
                self.circuit_breaker_active = False
                self.circuit_breaker_reason = ""
        return True, "HEALTHY"

    def check_portfolio_limits(
        self,
        symbol: str,
        direction: str,
        candidate_notional: float,
        open_positions: List[dict],
        closed_positions: List[dict],
        account_equity: float,
        now_ms: int,
    ) -> Tuple[bool, str]:
        healthy, reason = self.is_healthy(now_ms)
        if not healthy:
            return False, reason

        # 1. Total concurrent positions check
        if len(open_positions) >= MAX_CONCURRENT_POSITIONS:
            return False, f"RISK_REJECT_MAX_CONCURRENT_EXCEEDED ({len(open_positions)}/{MAX_CONCURRENT_POSITIONS})"

        # 2. Per-symbol concurrency check
        sym_positions = [p for p in open_positions if p["symbol"] == symbol.upper()]
        if len(sym_positions) >= MAX_POSITIONS_PER_SYMBOL:
            return False, f"RISK_REJECT_SYMBOL_CONCURRENCY_EXCEEDED ({len(sym_positions)}/{MAX_POSITIONS_PER_SYMBOL})"

        # 3. Gross exposure check
        current_gross = sum(p["notional_usdt"] for p in open_positions)
        new_gross = current_gross + candidate_notional
        max_gross_allowed = account_equity * (MAX_GROSS_NOTIONAL_PCT / 100.0)
        if new_gross > max_gross_allowed:
            return False, f"RISK_REJECT_GROSS_NOTIONAL_EXCEEDED (${new_gross:.1f} > ${max_gross_allowed:.1f})"

        # 4. Net directional exposure check
        current_longs = sum(p["notional_usdt"] for p in open_positions if p["direction"] == "LONG")
        current_shorts = sum(p["notional_usdt"] for p in open_positions if p["direction"] == "SHORT")
        if direction == "LONG":
            new_longs = current_longs + candidate_notional
            new_net = abs(new_longs - current_shorts)
        else:
            new_shorts = current_shorts + candidate_notional
            new_net = abs(current_longs - new_shorts)
        
        max_net_allowed = account_equity * (MAX_NET_DIRECTIONAL_PCT / 100.0)
        if new_net > max_net_allowed:
            return False, f"RISK_REJECT_NET_DIRECTIONAL_EXCEEDED (${new_net:.1f} > ${max_net_allowed:.1f})"

        # 5. Daily Drawdown Circuit Breaker Check (last 24 hours)
        one_day_ago_ms = now_ms - (24 * 3600 * 1000)
        recent_closed = [p for p in closed_positions if (p.get("closed_at_ms") or 0) >= one_day_ago_ms]
        realised_24h = sum(p.get("realised_pnl") or 0.0 for p in recent_closed)
        unrealised_24h = sum(p.get("unrealised_pnl") or 0.0 for p in open_positions)
        net_24h_pnl = realised_24h + unrealised_24h

        max_daily_loss = -(account_equity * (DAILY_DRAWDOWN_LIMIT_PCT / 100.0))
        if net_24h_pnl < max_daily_loss:
            self.circuit_breaker_active = True
            self.circuit_breaker_reason = f"24h Drawdown limit hit (${net_24h_pnl:.2f} < ${max_daily_loss:.2f})"
            self.circuit_breaker_until_ms = now_ms + (24 * 3600 * 1000)
            logger.critical("PORTFOLIO CIRCUIT BREAKER TRIGGERED: %s", self.circuit_breaker_reason)
            return False, f"RISK_REJECT_CIRCUIT_BREAKER_DAILY_DD ({self.circuit_breaker_reason})"

        return True, "ACCEPT"


class PaperDispatcher:
    """Dispatches qualified candidates to paper execution desk with institutional risk controls."""

    def __init__(self):
        self.risk_controller = PortfolioRiskController()

    def calculate_position_size(
        self,
        strategy_id: str,
        equity_usd: float,
        entry_price: float,
    ) -> Tuple[float, float, float, int]:
        """Calculates notional size where total stopped-out dollar loss == 0.25% of equity.

        Returns:
            (notional_usdt, sl_price, tp_price, max_hold_ms)
        """
        profile = STRATEGY_PROFILES.get(strategy_id, {
            "sl_pct": 0.50,
            "tp_pct": 1.00,
            "max_hold_ms": 60 * 60 * 1000,
            "leverage": DEFAULT_LEVERAGE,
        })

        sl_pct = profile["sl_pct"]
        tp_pct = profile["tp_pct"]
        max_hold_ms = profile["max_hold_ms"]
        leverage = profile.get("leverage", DEFAULT_LEVERAGE)

        # Risk budget in dollars = equity * 0.25%
        risk_budget_usd = equity_usd * (DEFAULT_RISK_PER_TRADE_PCT / 100.0)

        # Total loss percent if stopped out = SL distance + round-trip friction
        total_adverse_move_pct = (sl_pct / 100.0) + (ESTIMATED_ROUNDTRIP_FRICTION_PCT / 100.0)

        # Notional = risk_budget / total_adverse_move_pct
        notional_usdt = risk_budget_usd / max(0.001, total_adverse_move_pct)

        # Guardrails: Min $50, Max 50% of account equity
        notional_usdt = max(50.0, min(notional_usdt, equity_usd * 0.50))
        return notional_usdt, sl_pct, tp_pct, max_hold_ms

    def infer_direction(self, candidate: CandidateRecord) -> str:
        """Infers trade direction (LONG or SHORT) from candidate metadata and hypothesis."""
        strat = candidate.strategy_id
        reason = candidate.decision_reason.upper()
        
        # S1: If basis is positive / funding is high positive -> SHORT. If funding negative -> LONG.
        if "FUNDING" in strat:
            if "NEGATIVE" in reason or "DISCOUNT" in reason:
                return "LONG"
            return "SHORT"

        # S2: CVD & Imbalance
        if "ORDERBOOK" in strat or "CVD" in strat:
            if "BEARISH" in reason or "NEGATIVE" in reason:
                return "SHORT"
            return "LONG"

        # S3: Liquidation Exhaustion
        if "LIQUIDATION" in strat:
            if "LONG" in reason or "CASCADE_DOWN" in reason or "DUMP" in reason:
                return "LONG"  # Fading a dump
            return "SHORT"

        # S4/SH4: Swing Momentum — direction is explicit in the decision reason
        if "SWING" in strat:
            if "SHORT" in reason:
                return "SHORT"
            return "LONG"

        return "LONG"

    async def dispatch_candidate(
        self,
        candidate: CandidateRecord,
        orderbooks: Dict[str, BoundedOrderbook],
    ) -> dict:
        """Evaluates risk controls and executes candidate on the paper desk."""
        if candidate.decision != "ACCEPT":
            return {"status": "SKIPPED", "reason": f"Candidate decision is {candidate.decision}"}

        now_ms = now_utc_ms()
        symbol = candidate.symbol.upper()
        strat_id = candidate.strategy_id

        # Determine desk: QUANT or SUPERHUMAN
        desk = "SUPERHUMAN" if getattr(candidate, "origin", "QUANT").upper() == "SUPERHUMAN" else "QUANT"

        # 1. Fetch current portfolio state for this specific desk
        summary = paper_engine.get_summary(desk=desk)
        account_equity = summary["capital_usdt"] + summary["net_pnl"]
        open_positions = sqlite_store.get_paper_positions(status="OPEN", desk=desk)
        closed_positions = sqlite_store.get_paper_positions(status="CLOSED", desk=desk, limit=50)

        # 2. Get live orderbook
        if symbol not in orderbooks:
            return {"status": "REJECTED", "reason": f"Orderbook unavailable for {symbol}"}
        
        try:
            ob = orderbooks[symbol].get_snapshot()
            mid_price = ob.mid_price
        except Exception as e:
            return {"status": "REJECTED", "reason": f"Orderbook snapshot error: {e}"}

        # 3. Calculate Risk-as-Loss Position Sizing
        notional_usdt, sl_pct, tp_pct, max_hold_ms = self.calculate_position_size(
            strategy_id=strat_id,
            equity_usd=account_equity,
            entry_price=mid_price,
        )

        direction = self.infer_direction(candidate)

        # 4. Check Portfolio-level Risk Limits
        allowed, risk_reason = self.risk_controller.check_portfolio_limits(
            symbol=symbol,
            direction=direction,
            candidate_notional=notional_usdt,
            open_positions=open_positions,
            closed_positions=closed_positions,
            account_equity=account_equity,
            now_ms=now_ms,
        )

        if not allowed:
            logger.warning("Paper dispatcher risk check failed for %s (%s): %s", candidate.candidate_id, strat_id, risk_reason)
            return {"status": "REJECTED", "reason": risk_reason}

        # 5. Calculate SL and TP prices
        if direction == "LONG":
            sl_price = mid_price * (1.0 - (sl_pct / 100.0))
            tp_price = mid_price * (1.0 + (tp_pct / 100.0))
        else:
            sl_price = mid_price * (1.0 + (sl_pct / 100.0))
            tp_price = mid_price * (1.0 - (tp_pct / 100.0))

        sl_distance_usd = abs(mid_price - sl_price)
        risk_loss_usd = notional_usdt * ((sl_pct / 100.0) + (ESTIMATED_ROUNDTRIP_FRICTION_PCT / 100.0))

        # 6. Execute Order on Paper Desk
        logger.info(
            "Dispatching ACCEPTED candidate %s (%s) %s %s [%s Desk]: notional=$%.2f, risk_loss=$%.2f (%.2f%% eq)",
            candidate.candidate_id, strat_id, symbol, direction, desk, notional_usdt, risk_loss_usd, DEFAULT_RISK_PER_TRADE_PCT
        )

        exec_result = await paper_engine.open_position(
            symbol=symbol,
            direction=direction,
            notional_usdt=notional_usdt,
            leverage=DEFAULT_LEVERAGE,
            stop_loss_price=sl_price,
            take_profit_price=tp_price,
            orderbooks=orderbooks,
            strategy_id=strat_id,
            candidate_id=candidate.candidate_id,
            signal_score=candidate.signal_score,
            expected_edge_bps=candidate.expected_edge_bps,
            regime=candidate.regime,
            max_hold_ms=max_hold_ms,
            risk_loss_usd=risk_loss_usd,
            stop_loss_distance_usd=sl_distance_usd,
            desk=desk,
        )

        if "error" in exec_result:
            logger.error("Paper execution error for %s: %s", candidate.candidate_id, exec_result["error"])
            return {"status": "FAILED", "reason": exec_result["error"]}

        # Update candidate hypothetical and actual entry
        candidate.hypothetical_entry = mid_price
        candidate.actual_paper_entry = exec_result.get("entry_price")
        sqlite_store.update_candidate_paper_execution(
            candidate_id=candidate.candidate_id,
            entry_price=exec_result.get("entry_price")
        )

        return {
            "status": "EXECUTED",
            "position": exec_result,
            "candidate_id": candidate.candidate_id,
            "risk_loss_usd": risk_loss_usd,
        }


paper_dispatcher = PaperDispatcher()
