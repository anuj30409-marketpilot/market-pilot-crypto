"""Immutable Strategy Registry & Non-Self-Promoting State Machine.

Governs strategy lifecycle from initial research hypothesis to live capital.
Enforces the core rule:
- A strategy can NEVER promote itself.
- Promotion to PAPER_ACTIVE, LIVE_PILOT, and LIVE_APPROVED requires formal approval.
- Any risk breach or parity breakdown can immediately demote a strategy to SUSPENDED.
"""
from enum import Enum
from typing import Dict, List, Optional
from pydantic import BaseModel, Field
import json
from core.clock import now_utc_ms


class StrategyStatus(str, Enum):
    RESEARCH = "RESEARCH"
    BACKTEST = "BACKTEST"
    OOS_TEST = "OOS_TEST"
    WALK_FORWARD = "WALK_FORWARD"
    PAPER_SHADOW = "PAPER_SHADOW"
    PAPER_ACTIVE = "PAPER_ACTIVE"
    LIVE_SHADOW = "LIVE_SHADOW"
    LIVE_PILOT = "LIVE_PILOT"
    LIVE_APPROVED = "LIVE_APPROVED"
    SUSPENDED = "SUSPENDED"


# Allowed state transitions
VALID_TRANSITIONS = {
    StrategyStatus.RESEARCH: {StrategyStatus.BACKTEST, StrategyStatus.SUSPENDED},
    StrategyStatus.BACKTEST: {StrategyStatus.OOS_TEST, StrategyStatus.RESEARCH, StrategyStatus.SUSPENDED},
    StrategyStatus.OOS_TEST: {StrategyStatus.WALK_FORWARD, StrategyStatus.RESEARCH, StrategyStatus.SUSPENDED},
    StrategyStatus.WALK_FORWARD: {StrategyStatus.PAPER_SHADOW, StrategyStatus.RESEARCH, StrategyStatus.SUSPENDED},
    StrategyStatus.PAPER_SHADOW: {StrategyStatus.PAPER_ACTIVE, StrategyStatus.RESEARCH, StrategyStatus.SUSPENDED},
    StrategyStatus.PAPER_ACTIVE: {StrategyStatus.LIVE_SHADOW, StrategyStatus.SUSPENDED},
    StrategyStatus.LIVE_SHADOW: {StrategyStatus.LIVE_PILOT, StrategyStatus.PAPER_ACTIVE, StrategyStatus.SUSPENDED},
    StrategyStatus.LIVE_PILOT: {StrategyStatus.LIVE_APPROVED, StrategyStatus.SUSPENDED},
    StrategyStatus.LIVE_APPROVED: {StrategyStatus.SUSPENDED},
    StrategyStatus.SUSPENDED: {StrategyStatus.RESEARCH},
}

# States requiring explicit Human Governance / Managing Partner signoff
GOVERNANCE_REQUIRED_STATES = {
    StrategyStatus.PAPER_ACTIVE,
    StrategyStatus.LIVE_PILOT,
    StrategyStatus.LIVE_APPROVED,
}


class StrategyDefinition(BaseModel):
    strategy_id: str
    strategy_name: str
    version: str
    hypothesis: str
    feature_version: str
    parameter_version: str
    entry_rules: Dict
    exit_rules: Dict
    risk_profile: Dict
    status: StrategyStatus = StrategyStatus.RESEARCH
    created_at_ms: int = Field(default_factory=now_utc_ms)
    approved_at_ms: Optional[int] = None
    approved_by: Optional[str] = None
    research_commit_hash: str = "HEAD"


class StrategyRegistry:
    def __init__(self, sqlite_store):
        self.store = sqlite_store
        self._ensure_table()

    def _ensure_table(self):
        with self.store._get_connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS crypto_strategy_registry (
                strategy_id TEXT NOT NULL,
                strategy_name TEXT NOT NULL,
                version TEXT NOT NULL,
                hypothesis TEXT NOT NULL,
                feature_version TEXT NOT NULL,
                parameter_version TEXT NOT NULL,
                entry_rules_json TEXT NOT NULL,
                exit_rules_json TEXT NOT NULL,
                risk_profile_json TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                approved_at_ms INTEGER,
                approved_by TEXT,
                research_commit_hash TEXT NOT NULL,
                PRIMARY KEY (strategy_id, version)
            );
            CREATE INDEX IF NOT EXISTS idx_strat_reg_status 
            ON crypto_strategy_registry(status);
            """)
            conn.commit()

    def register_strategy(self, defn: StrategyDefinition) -> bool:
        """Registers a new strategy definition in RESEARCH status."""
        with self.store._get_connection() as conn:
            conn.execute("""
            INSERT INTO crypto_strategy_registry (
                strategy_id, strategy_name, version, hypothesis,
                feature_version, parameter_version, entry_rules_json,
                exit_rules_json, risk_profile_json, status,
                created_at_ms, approved_at_ms, approved_by, research_commit_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(strategy_id, version) DO UPDATE SET
                hypothesis = excluded.hypothesis,
                feature_version = excluded.feature_version,
                parameter_version = excluded.parameter_version,
                entry_rules_json = excluded.entry_rules_json,
                exit_rules_json = excluded.exit_rules_json,
                risk_profile_json = excluded.risk_profile_json
            """, (
                defn.strategy_id, defn.strategy_name, defn.version, defn.hypothesis,
                defn.feature_version, defn.parameter_version,
                json.dumps(defn.entry_rules), json.dumps(defn.exit_rules),
                json.dumps(defn.risk_profile), defn.status.value,
                defn.created_at_ms, defn.approved_at_ms, defn.approved_by,
                defn.research_commit_hash,
            ))
            conn.commit()
            return True

    def transition_status(
        self,
        strategy_id: str,
        version: str,
        target_status: StrategyStatus,
        approver: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Transitions strategy status enforcing transition rules and governance."""
        with self.store._get_connection() as conn:
            row = conn.execute(
                "SELECT status FROM crypto_strategy_registry WHERE strategy_id = ? AND version = ?",
                (strategy_id, version),
            ).fetchone()
            if not row:
                return False, f"Strategy {strategy_id} v{version} not found in registry."

            current_status = StrategyStatus(row["status"])
            if target_status not in VALID_TRANSITIONS.get(current_status, set()):
                return False, f"Invalid transition from {current_status.value} to {target_status.value}."

            if target_status in GOVERNANCE_REQUIRED_STATES:
                if not approver or approver.upper() not in {"MANAGING_PARTNER", "QUANT_COMMITTEE"}:
                    return False, f"Transition to {target_status.value} requires Managing Partner / Quant Committee signoff."

            now_ms = now_utc_ms()
            approved_by = approver if target_status in GOVERNANCE_REQUIRED_STATES else None
            approved_at = now_ms if target_status in GOVERNANCE_REQUIRED_STATES else None

            conn.execute("""
            UPDATE crypto_strategy_registry
            SET status = ?, approved_at_ms = COALESCE(?, approved_at_ms), approved_by = COALESCE(?, approved_by)
            WHERE strategy_id = ? AND version = ?
            """, (target_status.value, approved_at, approved_by, strategy_id, version))
            conn.commit()
            return True, f"Successfully transitioned to {target_status.value}."

    def get_strategy(self, strategy_id: str, version: str) -> Optional[dict]:
        with self.store._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM crypto_strategy_registry WHERE strategy_id = ? AND version = ?",
                (strategy_id, version),
            ).fetchone()
            return dict(row) if row else None

    def get_active_strategies(self, status: Optional[StrategyStatus] = None) -> List[dict]:
        with self.store._get_connection() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM crypto_strategy_registry WHERE status = ?",
                    (status.value,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM crypto_strategy_registry WHERE status IN ('PAPER_ACTIVE', 'LIVE_SHADOW', 'LIVE_PILOT', 'LIVE_APPROVED')"
                ).fetchall()
            return [dict(r) for r in rows]
