"""Canonical Event-Time Clock Engine for Crypto Research.

Implements Invariants:
- INV-CRYPTO-004: All market data uses UTC canonical timestamps.
- INV-CRYPTO-005: Event time is distinct from receive/process time.
"""
from datetime import datetime, timezone
import time

def now_utc_ms() -> int:
    """Returns current UTC epoch milliseconds."""
    return int(time.time() * 1000)

def now_utc_iso() -> str:
    """Returns ISO 8601 formatted UTC timestamp with millisecond resolution."""
    return datetime.now(timezone.utc).isoformat()

def ms_to_iso(epoch_ms: int) -> str:
    """Converts epoch ms to ISO 8601 string."""
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
    return dt.isoformat()

def ms_to_ist_str(epoch_ms: int) -> str:
    """Converts epoch ms to Indian Standard Time formatted string for display."""
    from datetime import timedelta
    # UTC + 5:30
    ist_offset = timezone(timedelta(hours=5, minutes=30))
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=ist_offset)
    return dt.strftime("%Y-%m-%d %H:%M:%S IST")
