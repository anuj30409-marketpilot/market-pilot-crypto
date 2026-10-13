"""Crypto Microstructure AI Trade Copilot Engine.

Real-time situational telemetry and dynamic trade management engine for
BTCUSDT and ETHUSDT perpetual futures. Evaluates open paper positions on
demand with zero event-loop latency.

Guardrails:
1. Purely Advisory in Phase 1: Generates telemetry, health scores, and dynamic
   levels; does NOT autonomously mutate orders or exit positions.
2. Horizon-Stratified Adaptive Weights: Calibrates score weights differently
   for Scalpers (5m), Swings (3h), and Macro Carry (8h).
3. Zero-Latency Pull Architecture: Stateless evaluation reading in-memory state.
"""
from dataclasses import dataclass, asdict, field
from typing import Dict, List, Any, Optional, Tuple
from core.clock import now_utc_ms


@dataclass
class CryptoCopilotState:
    health_score: int              # 0 to 100
    action: str                    # HOLD_CONVICTION | RATCHET_PRIMED | ARMOR_LOCKED | DEFENSE_DIVERGENCE | STI_EARLY_WARNING | HARVEST_RUNNER
    action_label: str              # UI Display Label
    tone: str                      # "positive" | "warning" | "negative" | "neutral"
    suggested_stop: Optional[float]
    suggested_target: Optional[float]
    locked_profit_usd: float
    current_unrealised_usd: float
    floating_bps: float
    reasoning: str
    flow_align: float = 0.0
    telemetry_crumbs: List[Dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_strategy_streak_thresholds(strategy_id: str, max_hold_ms: Optional[int] = None) -> Tuple[int, int, float]:
    """Returns (tier1_tighten_streak_s, tier2_scratch_streak_s, max_scratch_bps) calibrated by horizon.
    
    Auditor Amendment 1 Matrix:
    - Micro-Scalper (S2): 60s tighten, 90s scratch, -18.0 bps max scratch
    - Funding Carry (S1): 300s tighten, 480s scratch, -40.0 bps max scratch
    - Macro Regime (SH1): 180s tighten, 360s scratch, -35.0 bps max scratch
    - Swing Momentum (SH4, S4 / Default): 120s tighten, 240s scratch, -30.0 bps max scratch
    """
    strat = (strategy_id or "").upper()
    hold = max_hold_ms or 7_200_000

    if "ORDERBOOK" in strat or hold <= 300_000:
        return 60, 90, -18.0
    elif "FUNDING" in strat or hold >= 28_800_000:
        return 300, 480, -40.0
    elif "MACRO" in strat or hold >= 14_400_000:
        return 180, 360, -35.0
    else:
        return 120, 240, -30.0


def evaluate_crypto_copilot(
    position: Dict[str, Any],
    derivatives_tracker: Any = None,
    orderbook: Any = None,
    now_ms: Optional[int] = None,
) -> CryptoCopilotState:
    """Computes horizon-adaptive microstructure health score and dynamic action advice."""
    now_ms = now_ms or now_utc_ms()

    # 1. Extract position telemetry
    symbol = str(position.get("symbol") or "BTCUSDT").upper()
    direction = str(position.get("direction") or "LONG").upper()
    strategy_id = str(position.get("strategy_id") or "")
    entry_price = float(position.get("entry_price") or 1.0)
    mark_price = float(position.get("mark_price") or entry_price)
    unrealised_pnl = float(position.get("unrealised_pnl") or 0.0)
    opened_at_ms = int(position.get("opened_at_ms") or now_ms)
    max_hold_ms = int(position.get("max_hold_ms") or 7_200_000)
    stop_loss_price = position.get("stop_loss_price")
    take_profit_price = position.get("take_profit_price")

    if direction == "LONG":
        floating_bps = (mark_price - entry_price) / entry_price * 10_000.0
    else:
        floating_bps = (entry_price - mark_price) / entry_price * 10_000.0

    elapsed_ms = max(0, now_ms - opened_at_ms)

    # 2. Extract Microstructural Derivatives (safe attribute fallback)
    state = derivatives_tracker.get_state() if hasattr(derivatives_tracker, "get_state") else derivatives_tracker
    cvd_z = getattr(state, "cvd_notional_usd_zscore", 0.0) if state else 0.0
    funding_rate = getattr(state, "funding_rate", 0.0001) if state else 0.0001
    regime = getattr(state, "regime", "RANGE") if state else "RANGE"

    # Orderbook queue depth metrics
    imbalance_5 = 0.0
    if orderbook and hasattr(orderbook, "get_snapshot"):
        try:
            snap = orderbook.get_snapshot()
            imbalance_5 = snap.imbalance_5
        except Exception:
            imbalance_5 = 0.0

    # 3. Determine Horizon Segment & Weight Calibration
    is_scalp = "ORDERBOOK" in strategy_id or max_hold_ms <= 300_000  # <= 5 mins
    is_carry = "FUNDING" in strategy_id or max_hold_ms >= 14_400_000  # >= 4 hours

    if is_scalp:
        w_flow, w_book, w_pnl, w_fund, w_life = 0.35, 0.35, 0.20, 0.00, 0.10
    elif is_carry:
        w_flow, w_book, w_pnl, w_fund, w_life = 0.20, 0.05, 0.25, 0.35, 0.15
    else:  # Swings (SH1, SH4, S4)
        w_flow, w_book, w_pnl, w_fund, w_life = 0.35, 0.15, 0.30, 0.05, 0.15

    # 4. Compute Sub-Scores (0 to 100)
    # S_flow: aligned aggressor CVD momentum
    flow_align = cvd_z if direction == "LONG" else -cvd_z
    s_flow = max(5, min(95, int(50 + (flow_align * 25))))

    # S_book: aligned orderbook queue depth
    book_align = imbalance_5 if direction == "LONG" else -imbalance_5
    s_book = max(5, min(95, int(50 + (book_align * 50))))

    # S_pnl: profit trajectory & ratchet stage
    if floating_bps >= 140:
        s_pnl = 100
    elif floating_bps >= 95:
        s_pnl = 90
    elif floating_bps >= 60:
        s_pnl = 80
    elif floating_bps >= 25:
        s_pnl = 68
    elif floating_bps >= 0:
        s_pnl = 50
    elif floating_bps >= -25:
        s_pnl = 35
    elif floating_bps >= -50:
        s_pnl = 20
    else:
        s_pnl = 10

    # S_funding: carry clock yield vs drag
    earning_funding = (direction == "SHORT" and funding_rate > 0) or (direction == "LONG" and funding_rate < 0)
    s_fund = 85 if earning_funding else 40

    # S_lifecycle: time decay vs progression
    life_pct = min(1.0, elapsed_ms / max(1, max_hold_ms))
    if floating_bps >= 60:
        s_life = 85  # Protected by ratchet armor, lifecycle decay mitigated
    elif floating_bps >= 20:
        s_life = max(35, int(85 - (life_pct * 40)))
    else:
        s_life = max(15, int(90 - (life_pct * 70)))

    # Composite Health Score
    health = int(w_flow * s_flow + w_book * s_book + w_pnl * s_pnl + w_fund * s_fund + w_life * s_life)
    health = max(5, min(98, health))

    # 5. Dynamic Levels & Locked Profit
    locked_profit_usd = 0.0
    if stop_loss_price:
        if direction == "LONG" and stop_loss_price > entry_price:
            locked_profit_usd = (stop_loss_price - entry_price) / entry_price * float(position.get("notional_usdt") or 0.0)
        elif direction == "SHORT" and stop_loss_price < entry_price:
            locked_profit_usd = (entry_price - stop_loss_price) / entry_price * float(position.get("notional_usdt") or 0.0)

    # 6. Action State Machine & Breadcrumbs
    crumbs: List[Dict[str, str]] = []

    # Telemetry crumb: Flow
    crumbs.append({
        "tag": "CVD Flow",
        "text": f"z={cvd_z:+.2f}σ ({'aligned' if flow_align >= 0 else 'opposing'})"
    })

    # Telemetry crumb: Book depth
    crumbs.append({
        "tag": "Book Queue",
        "text": f"imb5={imbalance_5:+.2f} ({'support' if book_align >= 0 else 'pressure'})"
    })

    # Telemetry crumb: Funding
    funding_bps = funding_rate * 10_000.0
    crumbs.append({
        "tag": "Funding Carry",
        "text": f"{funding_bps:+.2f} bps ({'credit' if earning_funding else 'debit'})"
    })

    # State Machine Evaluation
    if floating_bps >= 140:
        action = "HARVEST_RUNNER"
        action_label = "Runner Free-Roll (+140 bps)"
        tone = "positive"
        reason = f"Tier-3 locked ({floating_bps:+.1f} bps). Stop locked at +100 bps; runner targeting +2.00% TP."
        crumbs.append({"tag": "Ratchet", "text": "Tier-3 locked (+100 bps)"})
    elif floating_bps >= 95:
        action = "ARMOR_LOCKED"
        action_label = "Armor Active (Tier-2)"
        tone = "positive"
        reason = f"Tier-2 profit armor active ({floating_bps:+.1f} bps). Stop locked at +60 bps net."
        crumbs.append({"tag": "Ratchet", "text": "Tier-2 locked (+60 bps)"})
    elif floating_bps >= 60:
        action = "ARMOR_LOCKED"
        action_label = "Armor Active (Tier-1)"
        tone = "positive"
        reason = f"Tier-1 breakeven armor active ({floating_bps:+.1f} bps). Stop locked at +22 bps net."
        crumbs.append({"tag": "Ratchet", "text": "Tier-1 locked (+22 bps)"})
    elif 48 <= floating_bps < 60:
        action = "RATCHET_PRIMED"
        action_label = "Approaching Tier-1 Armor"
        tone = "positive"
        pts_away = 60.0 - floating_bps
        reason = f"Floating {floating_bps:+.1f} bps, within {pts_away:.1f} bps of Tier-1 Breakeven Armor (+60 bps)."
        crumbs.append({"tag": "Ratchet", "text": f"{pts_away:.1f} bps to Tier-1"})
    elif 85 <= floating_bps < 95:
        action = "RATCHET_PRIMED"
        action_label = "Approaching Tier-2 Armor"
        tone = "positive"
        pts_away = 95.0 - floating_bps
        reason = f"Floating {floating_bps:+.1f} bps, within {pts_away:.1f} bps of Tier-2 Profit Armor (+95 bps)."
        crumbs.append({"tag": "Ratchet", "text": f"{pts_away:.1f} bps to Tier-2"})
    elif flow_align <= -1.5 or (regime == "TRENDING_DOWN" and direction == "LONG") or (regime == "TRENDING_UP" and direction == "SHORT"):
        action = "STI_EARLY_WARNING"
        action_label = "STI Invalidation Watch"
        tone = "negative" if flow_align <= -1.8 else "warning"
        reason = f"Opposing macro orderflow detected (flow_align={flow_align:+.2f}, regime={regime}). Pre-STI warning."
        crumbs.append({"tag": "STI Warning", "text": f"Regime {regime} opposing {direction}"})
    elif floating_bps > 15 and flow_align < -0.4:
        action = "DEFENSE_DIVERGENCE"
        action_label = "Flow Absorption Divergence"
        tone = "warning"
        reason = f"Position floating green ({floating_bps:+.1f} bps), but taker CVD momentum is flattening."
        crumbs.append({"tag": "Absorption", "text": "Taker momentum diverging"})
    else:
        action = "HOLD_CONVICTION"
        action_label = "Holding Conviction"
        tone = "positive" if health >= 65 else ("neutral" if health >= 45 else "warning")
        reason = f"Position structure healthy (Health={health}%). Orderflow and lifecycle in alignment."

    return CryptoCopilotState(
        health_score=health,
        action=action,
        action_label=action_label,
        tone=tone,
        suggested_stop=stop_loss_price,
        suggested_target=take_profit_price,
        locked_profit_usd=locked_profit_usd,
        current_unrealised_usd=unrealised_pnl,
        floating_bps=floating_bps,
        reasoning=reason,
        flow_align=round(flow_align, 4),
        telemetry_crumbs=crumbs,
    )
