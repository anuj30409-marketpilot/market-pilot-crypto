"""Phase 4 Verification Suite: Autonomous Paper Dispatcher & Portfolio Risk Controller.

Tests:
1. Risk-as-Loss Position Sizing (0.25% equity dollar loss target).
2. Portfolio-Level Constraints (concurrency, gross notional, daily DD circuit breaker).
3. End-to-End Candidate Dispatch -> Orderbook Fill -> Candidate Ledger Sync.
4. Strategy Lifecycle & Time-Stop Exit Triggering.
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.clock import now_utc_ms, ms_to_iso
from core.contracts import CandidateRecord
from collectors.orderbook import BoundedOrderbook
from paper.dispatcher import paper_dispatcher, PortfolioRiskController
from paper.paper_engine import paper_engine, PAPER_CAPITAL_USDT
from storage.sqlite_store import sqlite_store


async def run_phase4_tests():
    print("=" * 60)
    print("Running Phase 4: Paper Dispatcher & Risk Controller Test Suite")
    print("=" * 60)

    # Use isolated test DB to prevent polluting production DB or colliding with live positions
    test_db = Path("data/test_phase4.db")
    if test_db.exists():
        try:
            test_db.unlink()
        except Exception:
            pass
    orig_db = sqlite_store.db_path
    sqlite_store.db_path = test_db
    sqlite_store._init_db()
    sqlite_store.init_paper_tables()
    paper_engine._positions.clear()

    try:
        # 1. Test Risk Sizer
        print("[1/4] Testing Risk-as-Loss Position Sizing...")
        equity = 10_000.0
        mid_price = 83_000.0

        notional, sl_pct, tp_pct, max_hold = paper_dispatcher.calculate_position_size(
            strategy_id="STRAT_ORDERBOOK_MOMENTUM_V1",
            equity_usd=equity,
            entry_price=mid_price
        )
        expected_risk_loss = notional * ((sl_pct / 100.0) + (0.13 / 100.0))
        assert notional <= 4000.0, f"Notional ${notional} exceeded max $4000 cap!"
        assert expected_risk_loss <= 52.0, f"Risk loss ${expected_risk_loss} exceeded $52 risk budget target!"
        assert max_hold == 5 * 60 * 1000, "S2 max hold must be 5 minutes"
        print("      [OK] Risk-as-loss position sizing and notional cap verified.")

        # 2. Test Portfolio Risk Controller Limits
        print("[2/4] Testing Portfolio Risk Boundaries & Circuit Breakers...")
        risk_ctrl = PortfolioRiskController()
        now_ms = now_utc_ms()

        # 2a. Concurrency limit
        fake_positions = [{"symbol": "BTCUSDT", "direction": "LONG", "notional_usdt": 1000.0} for _ in range(4)]
        allowed, reason = risk_ctrl.check_portfolio_limits(
            symbol="BTCUSDT", direction="LONG", candidate_notional=1000.0,
            open_positions=fake_positions, closed_positions=[], account_equity=equity, now_ms=now_ms
        )
        assert not allowed and "MAX_CONCURRENT_EXCEEDED" in reason, f"Should reject 5th position: {reason}"

        # 2b. Daily Drawdown Circuit Breaker (2.5% of $10k = -$250)
        fake_closed_losses = [
            {"realised_pnl": -150.0, "closed_at_ms": now_ms - 1000},
            {"realised_pnl": -120.0, "closed_at_ms": now_ms - 2000},
        ]
        allowed, reason = risk_ctrl.check_portfolio_limits(
            symbol="BTCUSDT", direction="LONG", candidate_notional=500.0,
            open_positions=[], closed_positions=fake_closed_losses, account_equity=equity, now_ms=now_ms
        )
        assert not allowed and "CIRCUIT_BREAKER_DAILY_DD" in reason, f"Circuit breaker should trip: {reason}"

        # 2c. Consecutive Loss Circuit Breaker (3 consecutive losses -> 4h cooldown)
        risk_ctrl_streak = PortfolioRiskController()
        fake_streak_losses = [
            {"realised_pnl": -10.0, "closed_at_ms": now_ms - 1000},
            {"realised_pnl": -15.0, "closed_at_ms": now_ms - 2000},
            {"realised_pnl": -20.0, "closed_at_ms": now_ms - 3000},
        ]
        allowed, reason = risk_ctrl_streak.check_portfolio_limits(
            symbol="ETHUSDT", direction="LONG", candidate_notional=500.0,
            open_positions=[], closed_positions=fake_streak_losses, account_equity=equity, now_ms=now_ms
        )
        assert not allowed and "CONSECUTIVE_LOSS_COOLDOWN" in reason, f"Consecutive loss breaker should trip: {reason}"

        # 2d. Canonical Direction Parsing Verification (Amendment 1)
        mock_cand_s1_long = CandidateRecord(
            candidate_id="C-S1-L", timestamp_ms=now_ms, symbol="BTCUSDT", origin="QUANT",
            strategy_id="STRAT_FUNDING_REVERSION_V1", decision="ACCEPT",
            decision_reason="Valid LONG carry signal: Funding z-score=-2.05, Basis=-4.0 bps, Net edge=10.3 bps",
            engine_version="v2", model_version="v2", signal_version="v2", strategy_version="v1",
            feature_version="v1", parameter_version="v1", signal_score=1.0, expected_edge_bps=20,
            estimated_cost_bps=10, expected_net_edge_bps=10, regime="RANGE", rejection_codes=[],
            conversion_rate_applied=1.0, research_venue="BINANCE", execution_venue="DELTA_INDIA",
            hypothetical_entry=83000.0, created_at_iso=""
        )
        assert paper_dispatcher.infer_direction(mock_cand_s1_long) == "LONG", "S1 carry LONG must infer LONG!"

        mock_cand_s2_short = CandidateRecord(
            candidate_id="C-S2-S", timestamp_ms=now_ms, symbol="ETHUSDT", origin="QUANT",
            strategy_id="STRAT_ORDERBOOK_MOMENTUM_V1", decision="ACCEPT",
            decision_reason="Valid SHORT momentum flow: Imbalance_5=-0.40, CVD Z-score=-1.50, Net edge=8.5 bps",
            engine_version="v2", model_version="v2", signal_version="v2", strategy_version="v1",
            feature_version="v1", parameter_version="v1", signal_score=1.0, expected_edge_bps=20,
            estimated_cost_bps=10, expected_net_edge_bps=10, regime="RANGE", rejection_codes=[],
            conversion_rate_applied=1.0, research_venue="BINANCE", execution_venue="DELTA_INDIA",
            hypothetical_entry=2500.0, created_at_iso=""
        )
        assert paper_dispatcher.infer_direction(mock_cand_s2_short) == "SHORT", "S2 flow SHORT must infer SHORT!"

        # 2e. Cross-Asset Directional Beta Mutex Verification (Part 3 Institutional Calibration)
        open_pos_beta = [{"symbol": "BTCUSDT", "direction": "LONG", "notional_usdt": 3000.0}]
        allowed_beta_same, reason_beta_same = risk_ctrl_streak.check_portfolio_limits("ETHUSDT", "LONG", 3000.0, open_pos_beta, [], equity, now_ms)
        assert not allowed_beta_same and "RISK_REJECT_CORRELATED_BETA_EXPOSURE" in reason_beta_same, f"Beta Mutex failed to block correlated ETH LONG: {reason_beta_same}"

        allowed_beta_opp, _ = risk_ctrl_streak.check_portfolio_limits("ETHUSDT", "SHORT", 3000.0, open_pos_beta, [], equity, now_ms)
        assert allowed_beta_opp, "Beta Mutex should permit opposite-direction pairs hedge!"

        # 2f. Floating Time-Stop for Armored Runners Verification (Part 2 Institutional Calibration)
        from paper.paper_engine import PaperPosition
        pos_armored = PaperPosition(
            position_id="TEST-FLOAT-P4", symbol="BTCUSDT", direction="LONG", notional_usdt=3000.0,
            leverage=5, entry_price=80000.0, entry_slippage_bps=1.0, entry_fee_usdt=0.6,
            margin_usdt=600.0, mark_price=80400.0, unrealised_pnl=15.0, funding_paid_usdt=0.0,
            total_fees_usdt=0.6, opened_at_ms=now_ms - (301 * 60 * 1000), # > 5 hours
            last_funding_at_ms=now_ms, strategy_id="STRAT_SWING_MOMENTUM_V1",
            max_hold_ms=300 * 60 * 1000, stop_loss_price=80280.0, # Tier-1 locked (+35 bps)
            take_profit_price=81280.0
        )
        paper_engine._positions[pos_armored.position_id] = pos_armored
        paper_engine._check_sl_tp(pos_armored)
        assert pos_armored.status == "OPEN", f"Armored runner should remain OPEN past 5h, got {pos_armored.status}"
        del paper_engine._positions[pos_armored.position_id]

        print("      [OK] Concurrency limits, Daily DD, Consecutive Loss, Direction Parsing, Beta Mutex & Floating Time-Stop verified.")

        # 3. Test End-to-End Candidate Dispatch -> Orderbook Execution -> Provenance Sync
        print("[3/4] Testing Candidate Dispatch & Candidate Ledger Sync...")
        ob = BoundedOrderbook("BTCUSDT", market_type="FUTURES", max_levels=20)
        bids = [[str(83000.0 - i * 10), str(1.5)] for i in range(10)]
        asks = [[str(83000.0 + i * 10), str(1.5)] for i in range(10)]
        ob.apply_snapshot(bids, asks, last_update_id=1)
        orderbooks = {"BTCUSDT": ob}

        cand_id = f"TEST-DISPATCH-{now_ms}"
        candidate = CandidateRecord(
            candidate_id=cand_id,
            timestamp_ms=now_ms,
            symbol="BTCUSDT",
            origin="QUANT",
            engine_version="v2.0",
            model_version="v2.0",
            signal_version="v2.0",
            decision="ACCEPT",
            decision_reason="Strong CVD & Orderbook Imbalance Aligned",
            hypothetical_entry=83000.0,
            strategy_id="STRAT_ORDERBOOK_MOMENTUM_V1",
            strategy_version="1.0.0",
            feature_version="1.0",
            parameter_version="1.0",
            signal_score=85.0,
            expected_edge_bps=22.5,
            estimated_cost_bps=11.5,
            expected_net_edge_bps=11.0,
            regime="RANGE",
            rejection_codes=[],
            conversion_rate_applied=89.50,
            research_venue="BINANCE",
            execution_venue="DELTA_INDIA",
            created_at_iso=ms_to_iso(now_ms),
        )
        sqlite_store.insert_candidate(candidate)

        res = await paper_dispatcher.dispatch_candidate(candidate, orderbooks)
        assert res["status"] == "EXECUTED", f"Dispatch failed: {res}"
        pos_data = res["position"]
        pos_id = pos_data["position_id"]
        print(f"      Position opened: ID={pos_id[:8]}... Price=${pos_data['entry_price']:.2f} Slippage={pos_data['entry_slippage_bps']:.2f}bps")

        cand_record = sqlite_store.get_candidate_by_id(cand_id)
        assert cand_record is not None, "Candidate record missing from DB"
        assert cand_record["actual_paper_entry"] == pos_data["entry_price"], "Candidate actual entry not synced!"
        print("      [OK] Candidate ledger sync verified.")

        # 4. Test Strategy Lifecycle & Position Exit Sync
        print("[4/4] Testing Strategy Lifecycle & PnL Reconciliation...")
        close_res = await paper_engine.close_position(
            position_id=pos_id,
            reason="TAKE_PROFIT",
            orderbooks=orderbooks
        )
        assert close_res["status"] == "CLOSED"
        print(f"      Position closed: Exit=${close_res['exit_price']:.2f}, Realised PnL=${close_res['realised_pnl']:.4f}")

        cand_record_after_close = sqlite_store.get_candidate_by_id(cand_id)
        assert cand_record_after_close is not None
        assert cand_record_after_close["actual_paper_exit"] == close_res["exit_price"]
        assert cand_record_after_close["actual_pnl"] == close_res["realised_pnl"]
        print("      [OK] Candidate ledger exit and PnL reconciliation verified.")

        print("=" * 60)
        print("ALL PHASE 4 PAPER DISPATCHER & RISK TESTS PASSED CLEANLY (100%)")
        print("=" * 60)
    finally:
        sqlite_store.db_path = orig_db
        if test_db.exists():
            try:
                test_db.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(run_phase4_tests())
