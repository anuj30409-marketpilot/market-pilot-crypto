"""Verification Suite: Parallel Paper Desks (Quant vs Superhuman) & Capital Readiness.

Tests:
1. Capital Readiness Service flags (locked vs ready status).
2. Dual-Desk Paper Engine isolation (independent portfolios and summaries).
3. Superhuman Strategy Engine candidate generation (origin="SUPERHUMAN").
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.clock import now_utc_ms, ms_to_iso
from core.contracts import DerivativesState, OrderbookSnapshot, Candle1m
from collectors.orderbook import BoundedOrderbook
from paper.paper_engine import paper_engine, QUANT_CAPITAL_USDT, SUPERHUMAN_CAPITAL_USDT
from core.readiness import capital_readiness_service
from strategies.superhuman_evaluator import (
    evaluate_superhuman_macro_regime,
    evaluate_superhuman_cross_venue,
    evaluate_superhuman_vol_expansion,
)
from storage.sqlite_store import sqlite_store


async def run_dual_desk_tests():
    print("=" * 60)
    print("Running Dual-Desk Paper Trading & Capital Readiness Test Suite")
    print("=" * 60)

    # 1. Test Capital Readiness Service Flags
    print("[1/3] Testing Capital Readiness & Real-Capital Promotion Gate Flags...")
    report = capital_readiness_service.get_readiness_report()
    print(f"      Overall Mode: {report['overall_mode']} (Status: {report['overall_status']})")
    print(f"      Quant Desk Flag: {report['quant']['status_badge']} (Ready: {report['quant']['ready_for_real_capital']})")
    print(f"      Superhuman Desk Flag: {report['superhuman']['status_badge']} (Ready: {report['superhuman']['ready_for_real_capital']})")

    assert report["overall_status"] == "LOCKED", "Real capital must be LOCKED during paper simulation"
    assert report["quant"]["ready_for_real_capital"] is False, "Quant desk must not be ready before 120 trades"
    assert report["superhuman"]["ready_for_real_capital"] is False, "Superhuman desk must not be ready before 120 trades"
    assert len(report["quant"]["unmet_conditions"]) > 0, "Quant desk should list unmet gates"
    print("      [OK] Capital Readiness Flags verified.")

    # 2. Test Dual-Desk Paper Engine Isolation
    print("[2/3] Testing Parallel Paper Desk Isolation (Quant vs Superhuman)...")
    ob = BoundedOrderbook("BTCUSDT", market_type="FUTURES", max_levels=20)
    bids = [[str(83000.0 - i * 10), str(2.0)] for i in range(10)]
    asks = [[str(83000.0 + i * 10), str(2.0)] for i in range(10)]
    ob.apply_snapshot(bids, asks, last_update_id=1)
    orderbooks = {"BTCUSDT": ob}

    # Open Quant position
    q_pos = await paper_engine.open_position(
        symbol="BTCUSDT",
        direction="LONG",
        notional_usdt=2000.0,
        leverage=5,
        orderbooks=orderbooks,
        strategy_id="STRAT_ORDERBOOK_MOMENTUM_V1",
        desk="QUANT"
    )
    assert q_pos["desk"] == "QUANT", "Position desk must be QUANT"

    # Open Superhuman position
    sh_pos = await paper_engine.open_position(
        symbol="BTCUSDT",
        direction="SHORT",
        notional_usdt=1500.0,
        leverage=5,
        orderbooks=orderbooks,
        strategy_id="STRAT_SUPERHUMAN_MACRO_REGIME_V1",
        desk="SUPERHUMAN"
    )
    assert sh_pos["desk"] == "SUPERHUMAN", "Position desk must be SUPERHUMAN"

    # Verify Summaries
    summary_q = paper_engine.get_summary(desk="QUANT")
    summary_sh = paper_engine.get_summary(desk="SUPERHUMAN")
    summary_all = paper_engine.get_summary(desk="ALL")

    print(f"      Quant Desk: Capital=${summary_q['capital_usdt']}, Margin Used=${summary_q['margin_used_usdt']}, Open Pos={summary_q['open_positions']}")
    print(f"      Superhuman Desk: Capital=${summary_sh['capital_usdt']}, Margin Used=${summary_sh['margin_used_usdt']}, Open Pos={summary_sh['open_positions']}")
    print(f"      Combined: Capital=${summary_all['capital_usdt']}, Margin Used=${summary_all['margin_used_usdt']}, Open Pos={summary_all['open_positions']}")

    assert summary_q["margin_used_usdt"] == 400.0, f"Quant margin used expected $400, got {summary_q['margin_used_usdt']}"
    assert summary_sh["margin_used_usdt"] == 300.0, f"Superhuman margin used expected $300, got {summary_sh['margin_used_usdt']}"
    assert summary_all["margin_used_usdt"] == 700.0, f"Combined margin used expected $700, got {summary_all['margin_used_usdt']}"

    # Verify position queries
    q_positions = paper_engine.get_positions(desk="QUANT")
    sh_positions = paper_engine.get_positions(desk="SUPERHUMAN")
    assert all(p["desk"] == "QUANT" for p in q_positions), "Non-quant position leaked into Quant desk query"
    assert all(p["desk"] == "SUPERHUMAN" for p in sh_positions), "Non-superhuman position leaked into Superhuman desk query"

    # Close positions
    await paper_engine.close_position(q_pos["position_id"], reason="TEST_CLOSE", orderbooks=orderbooks)
    await paper_engine.close_position(sh_pos["position_id"], reason="TEST_CLOSE", orderbooks=orderbooks)
    print("      [OK] Parallel paper desks successfully isolated and verified.")

    # 3. Test Superhuman Strategy Evaluation Engine
    print("[3/3] Testing Superhuman AI Strategy Evaluation & Negative Proof Retention...")
    deriv = DerivativesState(
        symbol="BTCUSDT",
        mark_price=83000.0,
        index_price=83005.0,
        basis_bps=-0.6,
        open_interest=50000.0,
        funding_rate=0.0001,
        funding_interval_hours=8,
        annualized_funding=10.95,
        funding_zscore_7d=0.5,
        funding_percentile=50.0,
        distance_to_next_funding_mins=240,
        predicted_funding=0.0001,
        cvd_1m=10.5,
        cvd_notional_usd_1m=871500.0,
        cvd_notional_usd_zscore=0.8,
        taker_buy_ratio_1m=0.55,
        liquidation_notional_60s=5000.0,
        liquidation_intensity=1.0,
        liquidation_oi_impact=0.0001,
        regime="RANGE",
        quarantine_state="NORMAL",
        state_time_ms=now_utc_ms(),
        created_at_iso=ms_to_iso(now_utc_ms()),
    )
    ob_snap = ob.get_snapshot()

    c1 = evaluate_superhuman_macro_regime(deriv, ob_snap)
    c2 = evaluate_superhuman_cross_venue(deriv, ob_snap)
    c3 = evaluate_superhuman_vol_expansion(deriv, ob_snap)

    assert c1.origin == "SUPERHUMAN"
    assert c2.origin == "SUPERHUMAN"
    assert c3.origin == "SUPERHUMAN"
    assert c1.decision == "REJECT", "Weak signal should be rejected with negative proof"
    assert len(c1.rejection_codes) > 0, "Rejection codes must be populated"
    print(f"      SH1 Candidate: {c1.candidate_id} -> {c1.decision} ({c1.decision_reason})")
    print(f"      SH2 Candidate: {c2.candidate_id} -> {c2.decision} ({c2.decision_reason})")
    print(f"      SH3 Candidate: {c3.candidate_id} -> {c3.decision} ({c3.decision_reason})")
    print("      [OK] Superhuman strategy evaluation & negative proof retention verified.")

    print("=" * 60)
    print("ALL DUAL-DESK & CAPITAL READINESS TESTS PASSED CLEANLY (100%)")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_dual_desk_tests())
