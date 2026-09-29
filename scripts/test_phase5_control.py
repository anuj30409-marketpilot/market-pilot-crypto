"""Phase 5 Verification Suite: Cross-Venue Parity, Order Recovery & Reconciliation.

Tests:
1. CrossVenueParityEngine (INV-CRYPTO-021 basis and liquidity tolerances).
2. OrderRecoveryEngine (unknown-order state machine and quarantine).
3. ReconciliationEngine (position drift and phantom trade detection).
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from core.clock import now_utc_ms
from execution.parity import CrossVenueParityEngine
from execution.recovery import OrderRecoveryEngine, OrderRecoveryState
from reconciliation.engine import ReconciliationEngine


async def run_phase5_tests():
    print("=" * 60)
    print("Running Phase 5: Parity, Recovery & Reconciliation Test Suite")
    print("=" * 60)

    # 1. Test Cross-Venue Parity Engine
    print("[1/3] Testing Cross-Venue Basis & Execution Parity Engine (INV-CRYPTO-021)...")
    parity = CrossVenueParityEngine()
    now_ms = now_utc_ms()

    # Case A: Perfect parity
    parity.update_binance_price("BTCUSDT", mark=83000.0, bid=82999.5, ask=83000.5, timestamp_ms=now_ms)
    parity.update_delta_price("BTCUSDT", mark=83005.0, bid=83000.0, ask=83010.0, timestamp_ms=now_ms)
    ok, code, tel = parity.evaluate_parity("BTCUSDT", now_ms)
    print(f"      Case A (Tight Basis): status={code}, dislocation={tel.get('dislocation_bps')}bps, ok={ok}")
    assert ok and code == "PARITY_VERIFIED", f"Expected PARITY_VERIFIED, got {code}"

    # Case B: Dislocated basis (> 15 bps)
    parity.update_delta_price("BTCUSDT", mark=83200.0, bid=83195.0, ask=83205.0, timestamp_ms=now_ms)
    ok, code, tel = parity.evaluate_parity("BTCUSDT", now_ms)
    print(f"      Case B (Dislocated Basis): status={code}, dislocation={tel.get('dislocation_bps')}bps, ok={ok}")
    assert not ok and code == "PARITY_DISLOCATED", f"Expected PARITY_DISLOCATED, got {code}"

    # Case C: Wide spread / illiquid Delta
    parity.update_delta_price("BTCUSDT", mark=83002.0, bid=82900.0, ask=83100.0, timestamp_ms=now_ms)
    ok, code, tel = parity.evaluate_parity("BTCUSDT", now_ms)
    print(f"      Case C (Illiquid Delta Spread): status={code}, spread={tel.get('delta_spread_bps'):.1f}bps, ok={ok}")
    assert not ok and code == "PARITY_DELTA_ILLIQUID", f"Expected PARITY_DELTA_ILLIQUID, got {code}"
    print("      [OK] Cross-venue parity invariants verified.")

    # 2. Test Order Recovery State Machine
    print("[2/3] Testing Unknown-Order Recovery State Machine...")
    recovery = OrderRecoveryEngine()

    # Case A: Simulating successful recovery of filled order
    recovery.register_unknown_order(
        client_order_id="CL-ORD-001",
        symbol="BTCUSDT",
        side="BUY",
        quantity=0.05,
        price=83000.0,
        strategy_id="STRAT_ORDERBOOK_MOMENTUM_V1",
    )

    async def mock_exchange_query_filled(cid: str):
        return {"status": "FILLED", "order_id": "EX-9988", "avg_fill_price": 83002.0}

    state = await recovery.recover_order("CL-ORD-001", mock_exchange_query_filled)
    assert state == OrderRecoveryState.RESOLVED_FILLED
    print(f"      Resolved filled order: state={state.value}")

    # Case B: Exhausted retries -> Quarantine
    recovery.register_unknown_order(
        client_order_id="CL-ORD-002",
        symbol="BTCUSDT",
        side="BUY",
        quantity=0.05,
        price=83000.0,
        strategy_id="STRAT_ORDERBOOK_MOMENTUM_V1",
    )

    async def mock_exchange_query_failing(cid: str):
        raise ConnectionResetError("Exchange network socket dropped")

    state = await recovery.recover_order("CL-ORD-002", mock_exchange_query_failing)
    assert state == OrderRecoveryState.QUARANTINED_CRITICAL
    assert recovery.quarantined_count == 1
    print(f"      Exhausted retry order: state={state.value} (Quarantine counter={recovery.quarantined_count})")
    print("      [OK] Unknown order recovery state machine verified.")

    # 3. Test Reconciliation Engine
    print("[3/3] Testing Position & Order Reconciliation Engine...")
    reconciler = ReconciliationEngine(size_tolerance_pct=0.02)

    internal_positions = [
        {"symbol": "BTCUSDT", "direction": "LONG", "notional_usdt": 5000.0, "status": "OPEN"},
        {"symbol": "ETHUSDT", "direction": "SHORT", "notional_usdt": 2000.0, "status": "OPEN"},
    ]

    # Exchange reports: BTC matched, ETH missing, SOL is a phantom position
    exchange_positions = [
        {"symbol": "BTCUSDT", "direction": "LONG", "notional_usdt": 5050.0},  # within 2% tolerance
        {"symbol": "SOLUSDT", "direction": "LONG", "notional_usdt": 1500.0},  # phantom
    ]

    discrepancies = reconciler.reconcile_positions(internal_positions, exchange_positions)
    disc_types = [d["discrepancy_type"] for d in discrepancies]
    print(f"      Detected {len(discrepancies)} discrepancies: {disc_types}")
    assert "MISSING_ON_EXCHANGE" in disc_types, "Failed to detect missing ETHUSDT position"
    assert "PHANTOM_POSITION_ON_EXCHANGE" in disc_types, "Failed to detect phantom SOLUSDT position"
    print("      [OK] Reconciliation audits and phantom detection verified.")

    print("=" * 60)
    print("ALL PHASE 5 CONTROL TESTS PASSED CLEANLY (100%)")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_phase5_tests())
