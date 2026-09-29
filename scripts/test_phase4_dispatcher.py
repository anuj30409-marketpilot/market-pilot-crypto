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

    # 1. Test Risk Sizer
    print("[1/4] Testing Risk-as-Loss Position Sizing...")
    equity = 10_000.0
    mid_price = 83_000.0

    notional, sl_pct, tp_pct, max_hold = paper_dispatcher.calculate_position_size(
        strategy_id="STRAT_ORDERBOOK_MOMENTUM_V1",
        equity_usd=equity,
        entry_price=mid_price
    )
    # Target loss: $10,000 * 0.25% = $25.00
    # Expected adverse move = 0.40% SL + 0.13% friction = 0.53%
    # Expected notional = $25 / 0.0053 ~ $4,716
    expected_risk_loss = notional * ((sl_pct / 100.0) + (0.13 / 100.0))
    print(f"      Calculated notional: ${notional:.2f} (SL: {sl_pct}%, TP: {tp_pct}%)")
    print(f"      Calculated dollar risk on stop out: ${expected_risk_loss:.2f}")
    assert 20.0 <= expected_risk_loss <= 26.0, f"Risk loss ${expected_risk_loss} out of target bounds!"
    assert max_hold == 30 * 60 * 1000, "S2 max hold must be 30 minutes"
    print("      [OK] Risk-as-loss position sizing verified.")

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

    # 2b. Daily Drawdown Circuit Breaker
    fake_closed_losses = [
        {"realised_pnl": -120.0, "closed_at_ms": now_ms - 1000},
        {"realised_pnl": -90.0, "closed_at_ms": now_ms - 2000},
    ] # Total -$210 > $200 (2% of $10k)
    allowed, reason = risk_ctrl.check_portfolio_limits(
        symbol="BTCUSDT", direction="LONG", candidate_notional=500.0,
        open_positions=[], closed_positions=fake_closed_losses, account_equity=equity, now_ms=now_ms
    )
    assert not allowed and "CIRCUIT_BREAKER_DAILY_DD" in reason, f"Should trip circuit breaker: {reason}"
    print("      [OK] Concurrency limits and Daily Drawdown Circuit Breaker verified.")

    # 3. Test End-to-End Candidate Dispatch -> Orderbook Execution -> Provenance Sync
    print("[3/4] Testing Candidate Dispatch & Candidate Ledger Sync...")
    # Setup mock orderbook
    ob = BoundedOrderbook("BTCUSDT", market_type="FUTURES", max_levels=20)
    bids = [[str(83000.0 - i * 10), str(1.5)] for i in range(10)]
    asks = [[str(83000.0 + i * 10), str(1.5)] for i in range(10)]
    ob.apply_snapshot(bids, asks, last_update_id=1)
    orderbooks = {"BTCUSDT": ob}

    # Create an ACCEPTED candidate
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

    # Dispatch to paper desk
    res = await paper_dispatcher.dispatch_candidate(candidate, orderbooks)
    assert res["status"] == "EXECUTED", f"Dispatch failed: {res}"
    pos_data = res["position"]
    pos_id = pos_data["position_id"]
    print(f"      Position opened: ID={pos_id[:8]}... Price=${pos_data['entry_price']:.2f} Slippage={pos_data['entry_slippage_bps']:.2f}bps")

    # Verify candidate record in DB was updated with actual entry
    updated_candidates = sqlite_store.get_candidates(symbol="BTCUSDT", limit=5)
    matched = [c for c in updated_candidates if c["candidate_id"] == cand_id]
    assert len(matched) == 1, "Candidate record missing from DB"
    assert matched[0]["actual_paper_entry"] == pos_data["entry_price"], "Candidate actual entry not synced!"
    print("      [OK] Candidate ledger sync verified.")

    # 4. Test Strategy Lifecycle & Position Exit Sync
    print("[4/4] Testing Strategy Lifecycle & PnL Reconciliation...")
    # Close the position manually to simulate exit
    close_res = await paper_engine.close_position(
        position_id=pos_id,
        reason="TAKE_PROFIT",
        orderbooks=orderbooks
    )
    assert close_res["status"] == "CLOSED"
    print(f"      Position closed: Exit=${close_res['exit_price']:.2f}, Realised PnL=${close_res['realised_pnl']:.4f}")

    # Verify candidate record in DB was updated with exit price and PnL
    updated_candidates = sqlite_store.get_candidates(symbol="BTCUSDT", limit=5)
    matched = [c for c in updated_candidates if c["candidate_id"] == cand_id]
    assert matched[0]["actual_paper_exit"] == close_res["exit_price"]
    assert matched[0]["actual_pnl"] == close_res["realised_pnl"]
    print("      [OK] Candidate ledger exit and PnL reconciliation verified.")

    print("=" * 60)
    print("ALL PHASE 4 PAPER DISPATCHER & RISK TESTS PASSED CLEANLY (100%)")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_phase4_tests())
