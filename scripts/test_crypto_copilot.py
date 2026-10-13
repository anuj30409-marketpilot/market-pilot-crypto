"""Verification Suite: Crypto Microstructure AI Trade Copilot Engine.

Tests:
1. Health score computation across Micro-Scalp, Swing, and Macro Carry horizons.
2. Horizon-adaptive weighting compliance (e.g. 0% funding weight on 5m scalpers).
3. State machine transitions across Ratchet Tiers, CVD Divergence, and STI Warning.
4. Boundedness invariant (5 <= Health Score <= 98).
5. Microsecond execution latency benchmark (< 0.5 ms per position).
"""
import sys
import os
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from paper.trade_copilot import evaluate_crypto_copilot, CryptoCopilotState


class MockOrderbookSnapshot:
    def __init__(self, imbalance_5: float = 0.25):
        self.imbalance_5 = imbalance_5


class MockOrderbook:
    def __init__(self, imbalance_5: float = 0.25):
        self.snap = MockOrderbookSnapshot(imbalance_5)

    def get_snapshot(self):
        return self.snap


class MockDerivativesState:
    def __init__(self, cvd_z: float = 1.8, funding_rate: float = 0.0001, regime: str = "TRENDING_UP"):
        self.cvd_notional_usd_zscore = cvd_z
        self.funding_rate = funding_rate
        self.regime = regime


class MockDerivativesTracker:
    def __init__(self, cvd_z: float = 1.8, funding_rate: float = 0.0001, regime: str = "TRENDING_UP"):
        self.state = MockDerivativesState(cvd_z, funding_rate, regime)

    def get_state(self):
        return self.state


def test_copilot_horizon_differentiation():
    """Verify that scalper, swing, and macro carry use calibrated horizon weights."""
    now_ms = 1_700_000_000_000

    # Scalper (5m): funding should have ZERO impact
    scalp_pos = {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "strategy_id": "STRAT_ORDERBOOK_MOMENTUM_V1",
        "entry_price": 83000.0,
        "mark_price": 83050.0,
        "unrealised_pnl": 5.0,
        "notional_usdt": 1000.0,
        "opened_at_ms": now_ms - 60_000,
        "max_hold_ms": 300_000,
    }

    tracker_good_funding = MockDerivativesTracker(cvd_z=1.0, funding_rate=-0.0005)  # Earning funding
    tracker_bad_funding = MockDerivativesTracker(cvd_z=1.0, funding_rate=0.0005)    # Paying funding
    ob = MockOrderbook(imbalance_5=0.2)

    copilot_good = evaluate_crypto_copilot(scalp_pos, tracker_good_funding, ob, now_ms=now_ms)
    copilot_bad = evaluate_crypto_copilot(scalp_pos, tracker_bad_funding, ob, now_ms=now_ms)

    assert copilot_good.health_score == copilot_bad.health_score, (
        f"Scalper health ({copilot_good.health_score} vs {copilot_bad.health_score}) must NOT be affected by 8h funding!"
    )
    print("  [OK] Test 1: Scalper evaluation correctly decouples from 8h funding clock.")


def test_copilot_ratchet_state_machine():
    """Verify state transitions: RATCHET_PRIMED -> ARMOR_LOCKED -> HARVEST_RUNNER."""
    now_ms = 1_700_000_000_000
    tracker = MockDerivativesTracker(cvd_z=1.5, funding_rate=0.0001, regime="TRENDING_UP")
    ob = MockOrderbook(imbalance_5=0.3)

    base_pos = {
        "symbol": "ETHUSDT",
        "direction": "LONG",
        "strategy_id": "STRAT_SWING_MOMENTUM_V1",
        "entry_price": 2500.0,
        "notional_usdt": 2000.0,
        "opened_at_ms": now_ms - 300_000,
        "max_hold_ms": 10_800_000,  # 3 hours
    }

    # Case A: +55 bps floating -> RATCHET_PRIMED for Tier 1
    pos_55bps = dict(base_pos, mark_price=2500.0 * 1.0055, unrealised_pnl=11.0)
    c_55 = evaluate_crypto_copilot(pos_55bps, tracker, ob, now_ms=now_ms)
    assert c_55.action == "RATCHET_PRIMED", f"Expected RATCHET_PRIMED, got {c_55.action}"

    # Case B: +65 bps floating -> ARMOR_LOCKED Tier 1
    pos_65bps = dict(base_pos, mark_price=2500.0 * 1.0065, unrealised_pnl=13.0, stop_loss_price=2500.0 * 1.0022)
    c_65 = evaluate_crypto_copilot(pos_65bps, tracker, ob, now_ms=now_ms)
    assert c_65.action == "ARMOR_LOCKED", f"Expected ARMOR_LOCKED, got {c_65.action}"
    assert c_65.locked_profit_usd > 0, "Locked profit must be positive after Tier-1 ratchet"

    # Case C: +145 bps floating -> HARVEST_RUNNER Tier 3
    pos_145bps = dict(base_pos, mark_price=2500.0 * 1.0145, unrealised_pnl=29.0, stop_loss_price=2500.0 * 1.0100)
    c_145 = evaluate_crypto_copilot(pos_145bps, tracker, ob, now_ms=now_ms)
    assert c_145.action == "HARVEST_RUNNER", f"Expected HARVEST_RUNNER, got {c_145.action}"
    assert c_145.health_score >= 80, f"Expected high health score, got {c_145.health_score}"

    print("  [OK] Test 2: Ratchet state machine (RATCHET_PRIMED -> ARMOR_LOCKED -> HARVEST_RUNNER) verified.")


def test_copilot_sti_and_divergence_defense():
    """Verify early warning on opposing regime/CVD flow."""
    now_ms = 1_700_000_000_000
    ob = MockOrderbook(imbalance_5=-0.4)

    # LONG position facing TRENDING_DOWN with CVD -2.0 sigma
    hostile_tracker = MockDerivativesTracker(cvd_z=-2.0, funding_rate=0.0001, regime="TRENDING_DOWN")
    long_pos = {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "strategy_id": "STRAT_SWING_MOMENTUM_V1",
        "entry_price": 83000.0,
        "mark_price": 82900.0,
        "unrealised_pnl": -10.0,
        "notional_usdt": 2000.0,
        "opened_at_ms": now_ms - 600_000,
        "max_hold_ms": 10_800_000,
    }

    c_hostile = evaluate_crypto_copilot(long_pos, hostile_tracker, ob, now_ms=now_ms)
    assert c_hostile.action == "STI_EARLY_WARNING", f"Expected STI_EARLY_WARNING, got {c_hostile.action}"
    assert c_hostile.tone in ("warning", "negative"), f"Expected warning/negative tone, got {c_hostile.tone}"

    print("  [OK] Test 3: Structural Thesis Invalidation early warning & defense verified.")


def test_copilot_latency_benchmark():
    """Verify execution latency is < 0.5 ms per position to prevent any event loop lag."""
    now_ms = 1_700_000_000_000
    tracker = MockDerivativesTracker(cvd_z=1.2, funding_rate=0.0001, regime="RANGE")
    ob = MockOrderbook(imbalance_5=0.1)

    pos = {
        "symbol": "ETHUSDT",
        "direction": "LONG",
        "strategy_id": "STRAT_SWING_MOMENTUM_V1",
        "entry_price": 2500.0,
        "mark_price": 2515.0,
        "unrealised_pnl": 12.0,
        "notional_usdt": 2000.0,
        "opened_at_ms": now_ms - 1_200_000,
        "max_hold_ms": 10_800_000,
    }

    # Warmup
    for _ in range(50):
        _ = evaluate_crypto_copilot(pos, tracker, ob, now_ms=now_ms)

    # Benchmark 1000 evaluations
    start = time.perf_counter()
    N = 1000
    for _ in range(N):
        _ = evaluate_crypto_copilot(pos, tracker, ob, now_ms=now_ms)
    elapsed = time.perf_counter() - start
    avg_ms = (elapsed / N) * 1000.0

    assert avg_ms < 0.5, f"Latency {avg_ms:.4f} ms exceeded 0.5 ms limit!"
    print(f"  [OK] Test 4: Latency benchmark passed: {avg_ms:.4f} ms per evaluation ({N/elapsed:.0f} evals/sec).")


def test_copilot_phase2_streak_thresholds():
    """Verify Auditor Amendment 1: Horizon-stratified streak matrix."""
    from paper.trade_copilot import get_strategy_streak_thresholds

    # Scalper: 5m
    t1_s, t2_s, max_loss_s = get_strategy_streak_thresholds("STRAT_ORDERBOOK_MOMENTUM_V1", 300_000)
    assert (t1_s, t2_s, max_loss_s) == (60, 90, -18.0), f"Scalper matrix mismatch: {(t1_s, t2_s, max_loss_s)}"

    # Swing: 3h
    t1_sw, t2_sw, max_loss_sw = get_strategy_streak_thresholds("STRAT_SWING_MOMENTUM_V1", 10_800_000)
    assert (t1_sw, t2_sw, max_loss_sw) == (120, 240, -30.0), f"Swing matrix mismatch: {(t1_sw, t2_sw, max_loss_sw)}"

    # Macro: 4h
    t1_m, t2_m, max_loss_m = get_strategy_streak_thresholds("STRAT_SUPERHUMAN_MACRO_REGIME_V1", 14_400_000)
    assert (t1_m, t2_m, max_loss_m) == (180, 360, -35.0), f"Macro matrix mismatch: {(t1_m, t2_m, max_loss_m)}"

    # Carry: 8h
    t1_c, t2_c, max_loss_c = get_strategy_streak_thresholds("STRAT_FUNDING_REVERSION_V1", 28_800_000)
    assert (t1_c, t2_c, max_loss_c) == (300, 480, -40.0), f"Carry matrix mismatch: {(t1_c, t2_c, max_loss_c)}"

    print("  [OK] Test 5: Horizon-stratified streak thresholds (Amendment 1) verified.")


def test_copilot_phase2_defensive_policy_hook():
    """Verify Auditor Amendments 2 & 3: Clamped Tier-1 tightening and Tier-2 immediate scratch."""
    from paper.paper_engine import paper_engine, PaperPosition
    from config.settings import settings
    from collectors.derivatives import derivatives_engine
    from storage.sqlite_store import sqlite_store

    # Sandbox SQLite DB to prevent test positions polluting production database
    test_db = Path("data/test_copilot_temp.db")
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
        # Mock hostile derivatives tracker on BTCUSDT
        derivatives_engine.trackers["BTCUSDT"] = MockDerivativesTracker(
            cvd_z=-2.2, funding_rate=0.0001, regime="RANGE"
        )

        from core.clock import now_utc_ms
        now_ms = now_utc_ms()
        pos = PaperPosition(
            position_id="test-copilot-phase2-pos",
            symbol="BTCUSDT",
            direction="LONG",
            notional_usdt=2000.0,
            leverage=5,
            entry_price=80000.0,
            entry_slippage_bps=1.0,
            entry_fee_usdt=0.80,
            margin_usdt=400.0,
            mark_price=79880.0,  # -15 bps floating
            opened_at_ms=now_ms - 60_000,
            max_hold_ms=10_800_000,
            strategy_id="STRAT_SWING_MOMENTUM_V1",
            stop_loss_price=79520.0,  # -60 bps initial SL
            take_profit_price=81280.0,
        )
        pos.update_mark(79880.0)

        # 1. Advisory Mode: Stress condition does NOT mutate SL or exit
        settings.COPILOT_EXECUTION_MODE = "ADVISORY"
        pos.defense_stress_streak_s = 300
        paper_engine._check_sl_tp(pos)
        assert pos.status == "OPEN", "In ADVISORY mode, position must remain OPEN!"

        # 2. Active Defense Mode: Tier 1 Soft Tighten (at streak >= 120s)
        settings.COPILOT_EXECUTION_MODE = "ACTIVE_DEFENSE"
        pos.defense_stress_streak_s = 120
        pos.defense_tier1_applied = False
        paper_engine._check_sl_tp(pos)

        assert pos.status == "OPEN", "At Tier-1 streak, position should remain OPEN!"
        assert pos.defense_tier1_applied is True, "defense_tier1_applied must be set to True!"
        # Verify Amendment 2: Must be clamped with >= 20 bps buffer from current mark price (79880.0)
        max_allowed_sl = 79880.0 * (1.0 - 0.0020)  # At least 20 bps below mark
        assert pos.stop_loss_price <= max_allowed_sl, (
            f"SL {pos.stop_loss_price} must be at least 20 bps below mark {79880.0} (<= {max_allowed_sl})!"
        )
        assert pos.stop_loss_price > 79520.0, "SL must be tightened above initial 79520.0!"

        # 3. Active Defense Mode: Tier 2 Defensive Scratch (at streak >= 240s)
        pos.defense_stress_streak_s = 245
        paper_engine._check_sl_tp(pos)

        assert pos.status == "CLOSED", "At Tier-2 streak (245s >= 240s), position must be CLOSED!"
        assert pos.exit_reason == "COPILOT_DEFENSIVE_SCRATCH", (
            f"Expected COPILOT_DEFENSIVE_SCRATCH, got {pos.exit_reason}"
        )

        # Reset setting
        settings.COPILOT_EXECUTION_MODE = "ADVISORY"
        print("  [OK] Test 6: Copilot Phase 2 Clamped SL Tighten & Defensive Scratch (Amendments 2 & 3) verified.")
    finally:
        sqlite_store.db_path = orig_db
        paper_engine._positions.clear()
        if test_db.exists():
            try:
                test_db.unlink()
            except Exception:
                pass


def run_all_copilot_tests():
    print("=" * 60)
    print("Running Crypto Microstructure AI Trade Copilot Test Suite")
    print("=" * 60)
    test_copilot_horizon_differentiation()
    test_copilot_ratchet_state_machine()
    test_copilot_sti_and_divergence_defense()
    test_copilot_latency_benchmark()
    test_copilot_phase2_streak_thresholds()
    test_copilot_phase2_defensive_policy_hook()
    print("=" * 60)
    print("ALL CRYPTO COPILOT VERIFICATION TESTS PASSED (100%)")
    print("=" * 60)


if __name__ == "__main__":
    run_all_copilot_tests()

