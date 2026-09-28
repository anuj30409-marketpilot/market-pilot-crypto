"""SQLite Operational Storage for Crypto Research Desk.

Stores 1-minute aggregated candles, feed health events, and paper trading state.
Configured with WAL mode and busy timeouts for high reliability on low-resource VMs.
"""
import sqlite3
from pathlib import Path
from typing import List, Optional
from config.settings import settings
from core.contracts import Candle1m, DerivativesState, CandidateRecord
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

            CREATE TABLE IF NOT EXISTS crypto_candidate_ledger (
                candidate_id TEXT PRIMARY KEY,
                timestamp_ms INTEGER NOT NULL,
                symbol TEXT NOT NULL,
                origin TEXT NOT NULL,
                engine_version TEXT NOT NULL,
                model_version TEXT NOT NULL,
                signal_version TEXT NOT NULL,
                decision TEXT NOT NULL,
                decision_reason TEXT NOT NULL,
                hypothetical_entry REAL,
                hypothetical_exit REAL,
                hypothetical_pnl REAL,
                actual_paper_entry REAL,
                actual_paper_exit REAL,
                actual_pnl REAL,
                created_at_iso TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_crypto_candidates_time 
            ON crypto_candidate_ledger (symbol, timestamp_ms DESC);
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

    def upsert_derivatives_state(self, s: DerivativesState):
        with self._get_connection() as conn:
            conn.execute("""
            INSERT INTO crypto_derivatives_state (
                symbol, mark_price, index_price, basis_bps,
                open_interest, funding_rate, funding_zscore_7d,
                cvd_1m, taker_buy_ratio_1m, quarantine_state,
                state_time_ms, created_at_iso
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, state_time_ms) DO UPDATE SET
                mark_price = excluded.mark_price,
                index_price = excluded.index_price,
                basis_bps = excluded.basis_bps,
                open_interest = excluded.open_interest,
                funding_rate = excluded.funding_rate,
                funding_zscore_7d = excluded.funding_zscore_7d,
                cvd_1m = excluded.cvd_1m,
                taker_buy_ratio_1m = excluded.taker_buy_ratio_1m,
                quarantine_state = excluded.quarantine_state,
                created_at_iso = excluded.created_at_iso
            """, (
                s.symbol, s.mark_price, s.index_price, s.basis_bps,
                s.open_interest, s.funding_rate, s.funding_zscore_7d,
                s.cvd_1m, s.taker_buy_ratio_1m, s.quarantine_state,
                s.state_time_ms, s.created_at_iso
            ))
            conn.commit()

    def get_latest_derivatives_states(self, symbol: str, limit: int = 50) -> List[dict]:
        with self._get_connection() as conn:
            cur = conn.execute("""
            SELECT * FROM crypto_derivatives_state
            WHERE symbol = ?
            ORDER BY state_time_ms DESC
            LIMIT ?
            """, (symbol.upper(), limit))
            rows = cur.fetchall()
            return [dict(r) for r in reversed(rows)]

    def insert_candidate(self, c: CandidateRecord):
        with self._get_connection() as conn:
            conn.execute("""
            INSERT INTO crypto_candidate_ledger (
                candidate_id, timestamp_ms, symbol, origin,
                engine_version, model_version, signal_version,
                decision, decision_reason, hypothetical_entry,
                hypothetical_exit, hypothetical_pnl,
                actual_paper_entry, actual_paper_exit, actual_pnl,
                created_at_iso
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                decision = excluded.decision,
                decision_reason = excluded.decision_reason,
                hypothetical_entry = excluded.hypothetical_entry,
                hypothetical_exit = excluded.hypothetical_exit,
                hypothetical_pnl = excluded.hypothetical_pnl,
                actual_paper_entry = excluded.actual_paper_entry,
                actual_paper_exit = excluded.actual_paper_exit,
                actual_pnl = excluded.actual_pnl
            """, (
                c.candidate_id, c.timestamp_ms, c.symbol, c.origin,
                c.engine_version, c.model_version, c.signal_version,
                c.decision, c.decision_reason, c.hypothetical_entry,
                c.hypothetical_exit, c.hypothetical_pnl,
                c.actual_paper_entry, c.actual_paper_exit, c.actual_pnl,
                c.created_at_iso
            ))
            conn.commit()

    def get_candidates(self, symbol: Optional[str] = None, limit: int = 50) -> List[dict]:
        with self._get_connection() as conn:
            if symbol:
                cur = conn.execute("""
                SELECT * FROM crypto_candidate_ledger
                WHERE symbol = ?
                ORDER BY timestamp_ms DESC
                LIMIT ?
                """, (symbol.upper(), limit))
            else:
                cur = conn.execute("""
                SELECT * FROM crypto_candidate_ledger
                ORDER BY timestamp_ms DESC
                LIMIT ?
                """, (limit,))
            return [dict(r) for r in cur.fetchall()]

sqlite_store = SQLiteStore()
