"""SQLite Operational Storage for Crypto Research Desk.

Stores 1-minute aggregated candles, feed health events, and paper trading state.
Configured with WAL mode and busy timeouts for high reliability on low-resource VMs.
"""
import sqlite3
from pathlib import Path
from typing import List, Optional
from config.settings import settings
from core.contracts import Candle1m, DerivativesState
from core.state_machine import FeedStatus

class SQLiteStore:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or settings.DB_FILE
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS crypto_candles_1m (
                symbol TEXT NOT NULL,
                market_type TEXT NOT NULL,
                open_time_ms INTEGER NOT NULL,
                close_time_ms INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                quote_volume REAL NOT NULL,
                trades_count INTEGER NOT NULL,
                taker_buy_base_volume REAL NOT NULL,
                taker_buy_quote_volume REAL NOT NULL,
                event_time_ms INTEGER NOT NULL,
                received_at_ms INTEGER NOT NULL,
                PRIMARY KEY (symbol, market_type, open_time_ms)
            );

            CREATE INDEX IF NOT EXISTS idx_crypto_candles_time 
            ON crypto_candles_1m (symbol, market_type, open_time_ms DESC);

            CREATE TABLE IF NOT EXISTS crypto_feed_status (
                feed_name TEXT NOT NULL,
                symbol TEXT NOT NULL,
                state TEXT NOT NULL,
                last_event_time_ms INTEGER NOT NULL,
                last_received_time_ms INTEGER NOT NULL,
                gap_count INTEGER NOT NULL,
                quarantine_reason TEXT,
                updated_at_iso TEXT NOT NULL,
                PRIMARY KEY (feed_name, symbol)
            );

            CREATE TABLE IF NOT EXISTS crypto_derivatives_state (
                symbol TEXT NOT NULL,
                mark_price REAL NOT NULL,
                index_price REAL NOT NULL,
                basis_bps REAL NOT NULL,
                open_interest REAL NOT NULL,
                funding_rate REAL NOT NULL,
                funding_zscore_7d REAL DEFAULT 0.0,
                cvd_1m REAL DEFAULT 0.0,
                taker_buy_ratio_1m REAL DEFAULT 0.5,
                quarantine_state TEXT NOT NULL,
                state_time_ms INTEGER NOT NULL,
                created_at_iso TEXT NOT NULL,
                PRIMARY KEY (symbol, state_time_ms)
            );

            CREATE INDEX IF NOT EXISTS idx_crypto_deriv_state_time 
            ON crypto_derivatives_state (symbol, state_time_ms DESC);
            """)
            conn.commit()

    def upsert_candle(self, c: Candle1m):
        with self._get_connection() as conn:
            conn.execute("""
            INSERT INTO crypto_candles_1m (
                symbol, market_type, open_time_ms, close_time_ms,
                open, high, low, close, volume, quote_volume,
                trades_count, taker_buy_base_volume, taker_buy_quote_volume,
                event_time_ms, received_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, market_type, open_time_ms) DO UPDATE SET
                close = excluded.close,
                high = max(crypto_candles_1m.high, excluded.high),
                low = min(crypto_candles_1m.low, excluded.low),
                volume = excluded.volume,
                quote_volume = excluded.quote_volume,
                trades_count = excluded.trades_count,
                taker_buy_base_volume = excluded.taker_buy_base_volume,
                event_time_ms = excluded.event_time_ms,
                received_at_ms = excluded.received_at_ms
            """, (
                c.symbol, c.market_type, c.open_time_ms, c.close_time_ms,
                c.open, c.high, c.low, c.close, c.volume, c.quote_volume,
                c.trades_count, c.taker_buy_base_volume, c.taker_buy_quote_volume,
                c.event_time_ms, c.received_at_ms
            ))
            conn.commit()

    def update_feed_status(self, status: FeedStatus):
        with self._get_connection() as conn:
            conn.execute("""
            INSERT INTO crypto_feed_status (
                feed_name, symbol, state, last_event_time_ms,
                last_received_time_ms, gap_count, quarantine_reason, updated_at_iso
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(feed_name, symbol) DO UPDATE SET
                state = excluded.state,
                last_event_time_ms = excluded.last_event_time_ms,
                last_received_time_ms = excluded.last_received_time_ms,
                gap_count = excluded.gap_count,
                quarantine_reason = excluded.quarantine_reason,
                updated_at_iso = excluded.updated_at_iso
            """, (
                status.feed_name, status.symbol, status.state.value,
                status.last_event_time_ms, status.last_received_time_ms,
                status.gap_count, status.quarantine_reason, status.updated_at_iso
            ))
            conn.commit()

    def get_latest_candles(self, symbol: str, market_type: str = "SPOT", limit: int = 100) -> List[dict]:
        with self._get_connection() as conn:
            cur = conn.execute("""
            SELECT * FROM crypto_candles_1m
            WHERE symbol = ? AND market_type = ?
            ORDER BY open_time_ms DESC
            LIMIT ?
            """, (symbol.upper(), market_type.upper(), limit))
            rows = cur.fetchall()
            return [dict(r) for r in reversed(rows)]

    def get_all_feed_statuses(self) -> List[dict]:
        with self._get_connection() as conn:
            cur = conn.execute("SELECT * FROM crypto_feed_status ORDER BY feed_name, symbol")
            return [dict(r) for r in cur.fetchall()]

sqlite_store = SQLiteStore()
