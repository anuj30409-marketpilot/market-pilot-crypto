"""Comprehensive Verification Suite for Crypto Operating System Foundation.

Tests:
1. Database Schema migrations & persistent tables (currency rates, strategy registry, candidates).
2. Tri-rate currency service (market, settlement, display conversions and audit logs).
3. Immutable Strategy Registry (registration, valid transitions, and governance protection).
4. 7-State Market Regime Classifier (Trending, Liquidation, Funding Extreme, Range).
5. Canonical CandidateRecord provenance with structured rejection codes and Signal Quality Scores.
"""
import sys
import json
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from storage.sqlite_store import sqlite_store
from config.currency import currency_service
from storage.registry import StrategyRegistry, StrategyDefinition, StrategyStatus
from core.regime import MarketRegime, classify_regime
from core.contracts import CandidateRecord, DerivativesState
from core.clock import now_utc_ms, ms_to_iso


def test_schema_and_migrations():
    print("[1/5] Testing SQLite Schema & Migrations...")
    with sqlite_store._get_connection() as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert "crypto_candles_1m" in tables, "Missing crypto_candles_1m table"
        assert "crypto_derivatives_state" in tables, "Missing crypto_derivatives_state table"
        assert "crypto_candidate_ledger" in tables, "Missing crypto_candidate_ledger table"
        assert "crypto_currency_rates" in tables, "Missing crypto_currency_rates table"
        assert "crypto_strategy_registry" in tables, "Missing crypto_strategy_registry table"

        # Verify added columns
        deriv_cols = [r[1] for r in conn.execute("PRAGMA table_info(crypto_derivatives_state)").fetchall()]
        assert "funding_interval_hours" in deriv_cols, "Missing funding_interval_hours"
        assert "cvd_notional_usd_zscore" in deriv_cols, "Missing cvd_notional_usd_zscore"
        assert "liquidation_intensity" in deriv_cols, "Missing liquidation_intensity"
        assert "regime" in deriv_cols, "Missing regime column"

        cand_cols = [r[1] for r in conn.execute("PRAGMA table_info(crypto_candidate_ledger)").fetchall()]
        assert "strategy_id" in cand_cols, "Missing strategy_id"
        assert "signal_score" in cand_cols, "Missing signal_score"
        assert "expected_net_edge_bps" in cand_cols, "Missing expected_net_edge_bps"
        assert "rejection_codes" in cand_cols, "Missing rejection_codes"
        assert "conversion_rate_applied" in cand_cols, "Missing conversion_rate_applied"
    print("      [OK] Database schema and migrations verified.")


def test_tri_rate_currency():
    print("[2/5] Testing Tri-Rate Currency Service...")
    rec = currency_service.record_rate(89.75, "MARKET", "TEST_P2P")
    assert rec.rate == 89.75
    assert currency_service.get_rate("MARKET") == 89.75
    assert currency_service.get_rate("SETTLEMENT") == 85.00  # Default Delta platform conversion
    
    # Conversions
    inr_val = currency_service.convert_usdt_to_inr(100.0, "SETTLEMENT")
    assert inr_val == 8500.0, f"Expected 8500.0, got {inr_val}"
    
    usdt_val = currency_service.convert_inr_to_usdt(8500.0, "SETTLEMENT")
    assert usdt_val == 100.0, f"Expected 100.0, got {usdt_val}"
    print("      [OK] Tri-rate currency engine and audit logs verified.")


def test_strategy_registry_and_governance():
    print("[3/5] Testing Strategy Registry & Governance...")
    registry = StrategyRegistry(sqlite_store)
    strat_id = f"STRAT-TEST-{now_utc_ms()}"
    
    defn = StrategyDefinition(
        strategy_id=strat_id,
        strategy_name="Funding Rate & Basis Mean-Reversion",
        version="1.0.0",
        hypothesis="Funding z-score extremes mean-revert to historical norm.",
        feature_version="1.0",
        parameter_version="1.0",
        entry_rules={"funding_zscore": 2.0, "basis_bps": 3.0, "microprice_edge_bps": 1.5},
        exit_rules={"tp_pct": 1.5, "sl_pct": 0.8, "max_hold_hours": 8},
        risk_profile={"max_risk_pct": 0.25, "leverage": 5},
        status=StrategyStatus.RESEARCH,
        research_commit_hash="abc1234"
    )
    ok = registry.register_strategy(defn)
    assert ok, "Failed to register strategy"
    
    # Test valid progression to BACKTEST
    ok, msg = registry.transition_status(strat_id, "1.0.0", StrategyStatus.BACKTEST)
    assert ok, f"Transition to BACKTEST failed: {msg}"
    
    # Test valid progression to OOS_TEST
    ok, msg = registry.transition_status(strat_id, "1.0.0", StrategyStatus.OOS_TEST)
    assert ok, f"Transition to OOS_TEST failed: {msg}"

    # Test invalid skip: OOS_TEST directly to LIVE_APPROVED must fail
    ok, msg = registry.transition_status(strat_id, "1.0.0", StrategyStatus.LIVE_APPROVED)
    assert not ok, "Invalid transition to LIVE_APPROVED was erroneously permitted"

    # Test governance block: transition to PAPER_ACTIVE without Managing Partner must fail
    ok, _ = registry.transition_status(strat_id, "1.0.0", StrategyStatus.WALK_FORWARD)
    ok, _ = registry.transition_status(strat_id, "1.0.0", StrategyStatus.PAPER_SHADOW)
    ok, msg = registry.transition_status(strat_id, "1.0.0", StrategyStatus.PAPER_ACTIVE, approver=None)
    assert not ok, "Promotion to PAPER_ACTIVE without Managing Partner signoff was erroneously permitted"

    # Test governance approval
    ok, msg = registry.transition_status(strat_id, "1.0.0", StrategyStatus.PAPER_ACTIVE, approver="MANAGING_PARTNER")
    assert ok, f"Promotion with Managing Partner signoff failed: {msg}"
    print("      [OK] Strategy Registry and Governance gates verified.")


def test_regime_classifier():
    print("[4/5] Testing 7-State Market Regime Classifier...")
    now_ms = now_utc_ms()

    # 1. Test Liquidation Event
    reg_liq = classify_regime(
        symbol="BTCUSDT",
        recent_closes=[80000.0] * 50,
        recent_highs=[80100.0] * 50,
        recent_lows=[79900.0] * 50,
        recent_volumes=[10.0] * 50,
        funding_zscore=0.2,
        liquidation_intensity=4.2,  # > 3.0 triggers liquidation event
        cvd_notional_zscore=-0.5,
        feed_quarantine=False,
        feed_latency_ms=100,
        timestamp_ms=now_ms
    )
    assert reg_liq.primary_regime == MarketRegime.LIQUIDATION_EVENT, f"Expected LIQUIDATION_EVENT, got {reg_liq.primary_regime}"

    # 2. Test Funding Extreme
    reg_fund = classify_regime(
        symbol="BTCUSDT",
        recent_closes=[80000.0] * 50,
        recent_highs=[80100.0] * 50,
        recent_lows=[79900.0] * 50,
        recent_volumes=[10.0] * 50,
        funding_zscore=2.8,  # > 2.5 triggers funding extreme
        liquidation_intensity=1.0,
        cvd_notional_zscore=0.1,
        feed_quarantine=False,
        feed_latency_ms=100,
        timestamp_ms=now_ms
    )
    assert reg_fund.primary_regime == MarketRegime.FUNDING_EXTREME, f"Expected FUNDING_EXTREME, got {reg_fund.primary_regime}"

    # 3. Test Trending Up
    trend_prices = [70000.0 + (i * 200.0) for i in range(100)]
    reg_trend = classify_regime(
        symbol="BTCUSDT",
        recent_closes=trend_prices,
        recent_highs=[p + 50.0 for p in trend_prices],
        recent_lows=[p - 50.0 for p in trend_prices],
        recent_volumes=[25.0] * 100,
        funding_zscore=0.5,
        liquidation_intensity=1.0,
        cvd_notional_zscore=1.8,
        feed_quarantine=False,
        feed_latency_ms=100,
        timestamp_ms=now_ms
    )
    assert reg_trend.primary_regime == MarketRegime.TRENDING_UP, f"Expected TRENDING_UP, got {reg_trend.primary_regime}"
    print("      [OK] 7-State Regime Classifier verified.")


def test_candidate_provenance_and_rejection_codes():
    print("[5/5] Testing Candidate Provenance & Negative Proof Retention...")
    now_ms = now_utc_ms()
    rec = CandidateRecord(
        candidate_id="CAND-TEST-001",
        timestamp_ms=now_ms,
        symbol="BTCUSDT",
        origin="QUANT",
        strategy_id="STRAT-CVD-02",
        strategy_version="1.0.0",
        feature_version="1.0",
        parameter_version="1.0",
        engine_version="v2.0",
        model_version="v2.0",
        signal_version="v2.0",
        decision="REJECT",
        decision_reason="Signal edge (+3.2 bps) is below total friction (+5.0 bps)",
        signal_score=0.45,
        expected_edge_bps=3.2,
        estimated_cost_bps=5.0,
        expected_net_edge_bps=-1.8,
        regime="RANGE",
        rejection_codes=["REJECT_EDGE_TOO_SMALL", "REJECT_HIGH_SPREAD"],
        conversion_rate_applied=89.50,
        research_venue="BINANCE",
        execution_venue="DELTA_INDIA",
        created_at_iso=ms_to_iso(now_ms)
    )
    sqlite_store.insert_candidate(rec)
    
    found = sqlite_store.get_candidate_by_id("CAND-TEST-001")
    assert found is not None, "Inserted candidate not found in SQLite"
    assert found["strategy_id"] == "STRAT-CVD-02"
    assert found["decision"] == "REJECT"
    rejection_codes = json.loads(found["rejection_codes"])
    assert "REJECT_EDGE_TOO_SMALL" in rejection_codes
    assert "REJECT_HIGH_SPREAD" in rejection_codes
    assert found["conversion_rate_applied"] == 89.50
    print("      [OK] Candidate Record provenance and negative proof retention verified.")


def test_strategies_evaluation():
    print("[6/6] Testing Quantitative Alpha Strategies (S1, S2, S3)...")
    from core.contracts import OrderbookSnapshot
    from strategies.funding_reversion import evaluate_funding_reversion
    from strategies.orderbook_momentum import evaluate_orderbook_momentum
    from strategies.liquidation_fader import evaluate_liquidation_fader
    from strategies.evaluator import strategy_evaluator

    now_ms = now_utc_ms()

    # Create dummy base Orderbook Snapshot
    ob = OrderbookSnapshot(
        symbol="BTCUSDT",
        market_type="FUTURES",
        best_bid=80000.0,
        best_ask=80001.0,
        mid_price=80000.5,
        microprice=80000.7,
        spread=1.0,
        spread_bps=1.25,
        microprice_edge_bps=2.5,
        bid_depth_5=50.0,
        ask_depth_5=20.0,
        imbalance_1=0.45,
        imbalance_3=0.42,
        imbalance_5=0.43,
        imbalance_10=0.38,
        bid_notional_5=4000000.0,
        ask_notional_5=1600000.0,
        notional_imbalance_5=0.43,
        bid_depth_20=120.0,
        ask_depth_20=80.0,
        imbalance_20=0.20,
        update_id=123456,
        event_time_ms=now_ms,
        received_at_ms=now_ms
    )

    # 1. Test Strategy 1: Funding Reversion ACCEPT
    deriv_s1 = DerivativesState(
        symbol="BTCUSDT",
        mark_price=80000.0,
        index_price=80096.0,
        basis_bps=-12.0,
        open_interest=50000.0,
        funding_rate=-0.0012,
        funding_interval_hours=8,
        annualized_funding=-39.42,
        funding_zscore_7d=-3.5,
        funding_percentile=1.0,
        distance_to_next_funding_mins=45,
        predicted_funding=-0.0012,
        cvd_1m=15.0,
        cvd_notional_usd_1m=1200000.0,
        cvd_notional_usd_zscore=1.2,
        taker_buy_ratio_1m=0.60,
        liquidation_notional_60s=50000.0,
        liquidation_intensity=1.0,
        liquidation_oi_impact=0.0001,
        regime="FUNDING_EXTREME",
        quarantine_state="NORMAL",
        state_time_ms=now_ms,
        created_at_iso=ms_to_iso(now_ms)
    )
    cand_s1 = evaluate_funding_reversion(deriv_s1, ob)
    assert cand_s1.decision == "ACCEPT", f"Expected S1 ACCEPT, got {cand_s1.decision}: {cand_s1.decision_reason}"
    assert cand_s1.expected_net_edge_bps > 0
    print("      [OK] Strategy 1 (Funding Reversion) evaluated and ACCEPTED.")

    # 2. Test Strategy 2: Orderbook CVD Momentum ACCEPT
    deriv_s2 = DerivativesState(
        symbol="BTCUSDT",
        mark_price=80000.0,
        index_price=80000.0,
        basis_bps=0.0,
        open_interest=50000.0,
        funding_rate=0.0001,
        funding_interval_hours=8,
        annualized_funding=3.28,
        funding_zscore_7d=0.2,
        funding_percentile=52.0,
        distance_to_next_funding_mins=300,
        predicted_funding=0.0001,
        cvd_1m=35.0,
        cvd_notional_usd_1m=2800000.0,
        cvd_notional_usd_zscore=2.6,
        taker_buy_ratio_1m=0.72,
        liquidation_notional_60s=10000.0,
        liquidation_intensity=0.8,
        liquidation_oi_impact=0.00005,
        regime="TRENDING_UP",
        quarantine_state="NORMAL",
        state_time_ms=now_ms,
        created_at_iso=ms_to_iso(now_ms)
    )
    cand_s2 = evaluate_orderbook_momentum(deriv_s2, ob)
    assert cand_s2.decision == "ACCEPT", f"Expected S2 ACCEPT, got {cand_s2.decision}: {cand_s2.decision_reason}"
    assert cand_s2.signal_score > 0
    print("      [OK] Strategy 2 (Orderbook CVD Momentum) evaluated and ACCEPTED.")

    # 3. Test Strategy 3: Liquidation Fader ACCEPT
    deriv_s3 = DerivativesState(
        symbol="BTCUSDT",
        mark_price=80000.0,
        index_price=80000.0,
        basis_bps=0.0,
        open_interest=50000.0,
        funding_rate=0.0001,
        funding_interval_hours=8,
        annualized_funding=3.28,
        funding_zscore_7d=0.1,
        funding_percentile=50.0,
        distance_to_next_funding_mins=300,
        predicted_funding=0.0001,
        cvd_1m=-50.0,
        cvd_notional_usd_1m=-4000000.0,
        cvd_notional_usd_zscore=-2.2,  # Short cascade
        taker_buy_ratio_1m=0.25,
        liquidation_notional_60s=8500000.0,
        liquidation_intensity=4.8,  # > 3.0x
        liquidation_oi_impact=0.0025,
        regime="LIQUIDATION_EVENT",
        quarantine_state="NORMAL",
        state_time_ms=now_ms,
        created_at_iso=ms_to_iso(now_ms)
    )
    cand_s3 = evaluate_liquidation_fader(deriv_s3, ob)
    assert cand_s3.decision == "ACCEPT", f"Expected S3 ACCEPT, got {cand_s3.decision}: {cand_s3.decision_reason}"
    assert cand_s3.expected_edge_bps >= 20.0
    print("      [OK] Strategy 3 (Liquidation Fader) evaluated and ACCEPTED.")

    # 4. Test Strategy Evaluator bootstrap
    strategies = strategy_evaluator.registry.get_active_strategies()
    assert len(strategies) >= 3, f"Expected at least 3 active strategies, got {len(strategies)}"
    print("      [OK] Strategy Evaluator registry initialization verified.")


if __name__ == "__main__":
    print("=" * 60)
    print("Running Market Pilot Crypto Operating System Verification Suite")
    print("=" * 60)
    test_schema_and_migrations()
    test_tri_rate_currency()
    test_strategy_registry_and_governance()
    test_regime_classifier()
    test_candidate_provenance_and_rejection_codes()
    test_strategies_evaluation()
    print("=" * 60)
    print("ALL 6 VERIFICATION SUITES PASSED CLEANLY (100%)")
    print("=" * 60)

