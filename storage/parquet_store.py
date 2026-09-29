"""Parquet Lake Writer for High-Throughput Crypto Events.

Implements Invariant INV-CRYPTO-017:
Raw high-volume data and normalized research data have separate storage responsibilities.
"""
from pathlib import Path
from typing import List
from datetime import datetime, timezone
try:
    import pyarrow as pa
    import pyarrow.parquet as pq
    HAS_PYARROW = True
except ImportError:
    pa = None
    pq = None
    HAS_PYARROW = False

from config.settings import settings
from core.contracts import AggTrade

class ParquetStore:
    def __init__(self, base_dir: Path = settings.PARQUET_BASE):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def write_agg_trades_batch(self, trades: List[AggTrade]):
        """Flushes a batch of aggregated trades into hourly partitioned Parquet files."""
        if not HAS_PYARROW or not trades:
            return

        # Group trades by symbol and date/hour
        groups = {}
        for t in trades:
            dt = datetime.fromtimestamp(t.event_time_ms / 1000.0, tz=timezone.utc)
            date_str = dt.strftime("%Y-%m-%d")
            hour_str = dt.strftime("%H")
            key = (t.symbol.upper(), date_str, hour_str)
            if key not in groups:
                groups[key] = []
            groups[key].append({
                "trade_id": t.trade_id,
                "symbol": t.symbol,
                "market_type": t.market_type,
                "price": t.price,
                "quantity": t.quantity,
                "is_buyer_maker": t.is_buyer_maker,
                "event_time_ms": t.event_time_ms,
                "received_at_ms": t.received_at_ms
            })

        for (sym, date_str, hour_str), rows in groups.items():
            out_dir = self.base_dir / "aggtrades" / f"symbol={sym}" / f"date={date_str}"
            out_dir.mkdir(parents=True, exist_ok=True)
            file_path = out_dir / f"hour={hour_str}.parquet"

            table = pa.Table.from_pylist(rows)
            if file_path.exists():
                # Append to existing Parquet file
                existing = pq.read_table(file_path)
                combined = pa.concat_tables([existing, table])
                pq.write_table(combined, file_path, compression="zstd")
            else:
                pq.write_table(table, file_path, compression="zstd")

parquet_store = ParquetStore()
