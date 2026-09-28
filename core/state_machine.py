"""Data Quarantine and Feed Lifecycle State Machine.

Implements Invariants:
- INV-CRYPTO-006: No ML training sample may originate from quarantined data.
- INV-CRYPTO-007: Superhuman cannot generate candidates from quarantined data.
- INV-CRYPTO-008: Paper execution is blocked on failed critical data-quality gates.
"""
from enum import Enum
from pydantic import BaseModel
from typing import Optional
from core.clock import now_utc_ms, ms_to_iso

class FeedState(str, Enum):
    NORMAL = "NORMAL"
    DEGRADED = "DEGRADED"
    QUARANTINED = "QUARANTINED"

class FeedStatus(BaseModel):
    feed_name: str
    symbol: str
    state: FeedState = FeedState.NORMAL
    last_event_time_ms: int = 0
    last_received_time_ms: int = 0
    gap_count: int = 0
    quarantine_reason: Optional[str] = None
    updated_at_iso: str = ""

    def mark_event(self, event_time_ms: int, received_ms: int):
        self.last_event_time_ms = event_time_ms
        self.last_received_time_ms = received_ms
        self.updated_at_iso = ms_to_iso(received_ms)

    def transition_to(self, new_state: FeedState, reason: Optional[str] = None):
        self.state = new_state
        self.quarantine_reason = reason
        self.updated_at_iso = ms_to_iso(now_utc_ms())

    @property
    def is_eligible_for_paper_execution(self) -> bool:
        return self.state == FeedState.NORMAL

    @property
    def is_eligible_for_ml_training(self) -> bool:
        return self.state == FeedState.NORMAL
