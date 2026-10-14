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
from paper.paper_engine import paper_engine, PAPER_CAPITAL_USDT, DEFAULT_LEVERAGE, normalize_desk
from storage.sqlite_store import sqlite_store

logger = logging.getLogger("paper_dispatcher")

# ── Institutional Risk Configuration ─────────────────────────────────────────

DEFAULT_RISK_PER_TRADE_PCT = 0.50  # 0.50% of equity ($50.00 on $10,000 account)
ESTIMATED_ROUNDTRIP_FRICTION_PCT = 0.13  # 13 bps (fees + slippage)
MAX_GROSS_NOTIONAL_PCT = 200.0  # 200% gross exposure max ($20,000 on $10k)
MAX_NET_DIRECTIONAL_PCT = 100.0  # 100% net long/short max ($10,000 on $10k)
MAX_CONCURRENT_POSITIONS = 4
MAX_POSITIONS_PER_SYMBOL = 1   # Strict Asset Mutex: Max 1 position per asset across ALL desks
MAX_POSITION_NOTIONAL_USDT = 4000.0  # 40% max equity notional cap ($4,000 on $10k)
DAILY_DRAWDOWN_LIMIT_PCT = 2.50  # 2.50% daily DD circuit breaker ($250 on $10k)
CONSECUTIVE_LOSS_LIMIT = 3
COOLDOWN_AFTER_LOSSES_MS = 4 * 3600 * 1000  # 4 hours

# Strategy-specific risk & lifecycle profiles
STRATEGY_PROFILES = {
    "STRAT_FUNDING_REVERSION_V1": {
        "sl_pct": 0.75,
        "tp_pct": 1.90,
        "max_hold_ms": 8 * 3600 * 1000,  # 8 hours carry horizon
        "leverage": 5,
    },
    "STRAT_ORDERBOOK_MOMENTUM_V1": {
        "sl_pct": 0.18,
        "tp_pct": 0.25,
        "max_hold_ms": 5 * 60 * 1000,  # 5 minutes (honours Invariant 3 alpha decay half-life <= 5m)
        "leverage": 5,
    },
    "STRAT_LIQUIDATION_FADER_V1": {
        "sl_pct": 0.60,
        "tp_pct": 1.20,
        "max_hold_ms": 60 * 60 * 1000,  # 60 minutes
        "leverage": 5,
    },
    "STRAT_SUPERHUMAN_MACRO_REGIME_V1": {
        "sl_pct": 0.75,
        "tp_pct": 2.00,
        "max_hold_ms": 240 * 60 * 1000,  # 4 hours (allows 200 bps macro move to develop)
        "leverage": 5,
    },
    "STRAT_SUPERHUMAN_CROSS_VENUE_V1": {
        "sl_pct": 0.50,
        "tp_pct": 1.00,
        "max_hold_ms": 45 * 60 * 1000,  # 45 minutes
        "leverage": 5,
    },
    "STRAT_SUPERHUMAN_VOLATILITY_EXPANSION_V1": {
        "sl_pct": 0.80,
        "tp_pct": 2.00,
        "max_hold_ms": 90 * 60 * 1000,  # 90 minutes
        "leverage": 5,
    },
    "STRAT_SWING_MOMENTUM_V1": {
        "sl_pct": 0.60,
        "tp_pct": 1.60,
        "max_hold_ms": 300 * 60 * 1000,  # 5 hours (volatility diffusion envelope for 160 bps = ₹5,000 INR)
        "leverage": 5,
    },
    "STRAT_SUPERHUMAN_SWING_V1": {
        "sl_pct": 0.60,
        "tp_pct": 1.60,
        "max_hold_ms": 300 * 60 * 1000,  # 5 hours (volatility diffusion envelope for 160 bps = ₹5,000 INR)
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

        # 6. Consecutive Loss Circuit Breaker Check (Amendment 4)
        sorted_closed = sorted(
            [p for p in closed_positions if p.get("closed_at_ms")],
            key=lambda x: x.get("closed_at_ms", 0),
            reverse=True,
        )
        streak_losses = 0
        for p in sorted_closed:
            if (p.get("realised_pnl") or 0.0) < 0.0:
                streak_losses += 1
            else:
                break

        if streak_losses >= CONSECUTIVE_LOSS_LIMIT:
            last_loss_time = sorted_closed[0].get("closed_at_ms", 0)
            cooldown_end = last_loss_time + COOLDOWN_AFTER_LOSSES_MS
            if now_ms < cooldown_end:
                remaining_m = (cooldown_end - now_ms) / 60000.0
                return False, f"RISK_REJECT_CONSECUTIVE_LOSS_COOLDOWN ({streak_losses} consecutive losses; {remaining_m:.1f}m cooldown remaining)"

        # 7. Differentiated Post-Exit Symbol Cooldown Check
        recent_sym_closed = [p for p in closed_positions if p.get("symbol") == symbol.upper() and p.get("closed_at_ms")]
        if recent_sym_closed:
            latest = max(recent_sym_closed, key=lambda x: x.get("closed_at_ms", 0))
            last_exit_time = latest.get("closed_at_ms", 0)
            exit_reason = latest.get("exit_reason", "")

            # Cooldown duration based on exit reason:
            if exit_reason in ("TIME_STOP", "STAGNATION_EXIT"):
                cooldown_ms = 30 * 60 * 1000  # 30 mins for dead chop / stagnation
            elif exit_reason == "STOP_LOSS":
                cooldown_ms = 20 * 60 * 1000  # 20 mins for adverse volatility
            elif exit_reason == "TAKE_PROFIT":
                cooldown_ms = 5 * 60 * 1000   # 5 mins for active runner
            else:
                cooldown_ms = 15 * 60 * 1000  # 15 mins default

            elapsed_cooldown = now_ms - last_exit_time
            if 0 <= elapsed_cooldown < cooldown_ms:
                remaining_m = (cooldown_ms - elapsed_cooldown) / 60000
                return False, f"RISK_REJECT_SYMBOL_COOLDOWN ({symbol} in {exit_reason} cooldown: {remaining_m:.1f}m remaining)"

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

        # Guardrails: Min $50, Max 40% of account equity or MAX_POSITION_NOTIONAL_USDT
        max_allowed_notional = min(MAX_POSITION_NOTIONAL_USDT, equity_usd * 0.40)
        notional_usdt = max(50.0, min(notional_usdt, max_allowed_notional))
        return notional_usdt, sl_pct, tp_pct, max_hold_ms

    def infer_direction(self, candidate: CandidateRecord) -> str:
        """Infers trade direction (LONG or SHORT) canonically from candidate metadata and hypothesis."""
        reason = candidate.decision_reason.upper()
        reason_padded = f" {reason} "

        # Priority 1: Check explicit canonical direction tokens
        if " SHORT " in reason_padded or reason.startswith("SHORT") or "SHORT:" in reason or "SHORT " in reason:
            return "SHORT"
        if " LONG " in reason_padded or reason.startswith("LONG") or "LONG:" in reason or "LONG " in reason:
            return "LONG"

        # Priority 2: Strategy-specific semantic fallbacks
        strat = candidate.strategy_id
        if "FUNDING" in strat:
            return "LONG" if ("NEGATIVE" in reason or "DISCOUNT" in reason) else "SHORT"
        if "ORDERBOOK" in strat or "CVD" in strat:
            return "SHORT" if ("BEARISH" in reason or "NEGATIVE" in reason) else "LONG"
        if "LIQUIDATION" in strat:
            return "LONG" if ("CASCADE_DOWN" in reason or "DUMP" in reason) else "SHORT"

        return "LONG"

    async def dispatch_candidate(
        self,
        candidate: CandidateRecord,
        orderbooks: Dict[str, BoundedOrderbook],
    ) -> dict:
        """Evaluates risk controls and executes candidate on the paper desk."""
        if candidate.decision != "ACCEPT":
            return {"status": "SKIPPED", "reason": f"Candidate decision is {candidate.decision}"}

        # Gate: Enforce minimum net edge hurdle (5.0 bps) to prevent taker fee friction bleed
        min_net_edge_bps = 5.0
        cand_net_edge = getattr(candidate, "expected_net_edge_bps", 0.0)
        if cand_net_edge < min_net_edge_bps:
            logger.warning(
                "Paper dispatcher rejected %s (%s): net edge %.2f bps < minimum %.2f bps hurdle",
                candidate.candidate_id, candidate.strategy_id, cand_net_edge, min_net_edge_bps
            )
            return {"status": "REJECTED", "reason": f"INSUFFICIENT_NET_EDGE_{cand_net_edge:.1f}BPS_LT_5BPS"}

        now_ms = now_utc_ms()
        symbol = candidate.symbol.upper()
        strat_id = candidate.strategy_id

        # Determine desk: QUANT or OVERMIND
        desk = normalize_desk(getattr(candidate, "origin", "QUANT"))

        # 1. Fetch current portfolio state
        summary = paper_engine.get_summary(desk=desk)
        account_equity = summary["capital_usdt"] + summary["net_pnl"]
        # Query open positions across ALL desks for global concurrency & collision protection
        global_open_positions = sqlite_store.get_paper_positions(status="OPEN", desk="ALL")
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

        # 4. Check Portfolio-level Risk Limits (Global Cross-Desk Check)
        allowed, risk_reason = self.risk_controller.check_portfolio_limits(
            symbol=symbol,
            direction=direction,
            candidate_notional=notional_usdt,
            open_positions=global_open_positions,
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

        # Strategy execution style:
        # S1 (Funding Carry), SH4 (Overmind Swing), and S2 (Orderbook Scalper) use passive MAKER limits (cuts entry fee by 50%)
        MAKER_STRATEGIES = {
            "STRAT_FUNDING_REVERSION_V1",
            "STRAT_SUPERHUMAN_SWING_V1",
            "STRAT_ORDERBOOK_MOMENTUM_V1",
        }
        order_type = "MAKER" if strat_id in MAKER_STRATEGIES else "TAKER"

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
            order_type=order_type,
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
