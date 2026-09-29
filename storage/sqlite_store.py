"""SQLite Operational Storage for Crypto Research Desk.

Stores 1-minute aggregated candles, feed health events, and paper trading state.
Configured with WAL mode and busy timeouts for high reliability on low-resource VMs.
"""
import json
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

    def _add_column_if_missing(self, conn: sqlite3.Connection, table: str, column: str, col_type: str):
        table_check = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
        if not table_check:
            return
        cur = conn.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in cur.fetchall()]
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")

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

            CREATE TABLE IF NOT EXISTS crypto_currency_rates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_ms INTEGER NOT NULL,
                source TEXT NOT NULL,
                base_currency TEXT NOT NULL,
                quote_currency TEXT NOT NULL,
                rate REAL NOT NULL,
                rate_type TEXT NOT NULL,
                confidence REAL NOT NULL,
                is_stale INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_curr_rate_ts 
            ON crypto_currency_rates(timestamp_ms DESC);

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
            """)

            # Run migrations for derivatives state
            self._add_column_if_missing(conn, "crypto_derivatives_state", "funding_interval_hours", "INTEGER DEFAULT 8")
            self._add_column_if_missing(conn, "crypto_derivatives_state", "annualized_funding", "REAL DEFAULT 0.0")
            self._add_column_if_missing(conn, "crypto_derivatives_state", "funding_percentile", "REAL DEFAULT 50.0")
            self._add_column_if_missing(conn, "crypto_derivatives_state", "distance_to_next_funding_mins", "INTEGER DEFAULT 0")
            self._add_column_if_missing(conn, "crypto_derivatives_state", "predicted_funding", "REAL DEFAULT 0.0")
            self._add_column_if_missing(conn, "crypto_derivatives_state", "cvd_notional_usd_1m", "REAL DEFAULT 0.0")
            self._add_column_if_missing(conn, "crypto_derivatives_state", "cvd_notional_usd_zscore", "REAL DEFAULT 0.0")
            self._add_column_if_missing(conn, "crypto_derivatives_state", "liquidation_notional_60s", "REAL DEFAULT 0.0")
            self._add_column_if_missing(conn, "crypto_derivatives_state", "liquidation_intensity", "REAL DEFAULT 1.0")
            self._add_column_if_missing(conn, "crypto_derivatives_state", "liquidation_oi_impact", "REAL DEFAULT 0.0")
            self._add_column_if_missing(conn, "crypto_derivatives_state", "regime", "TEXT DEFAULT 'RANGE'")

            # Run migrations for candidate ledger
            self._add_column_if_missing(conn, "crypto_candidate_ledger", "strategy_id", "TEXT DEFAULT 'STRAT_UNKNOWN'")
            self._add_column_if_missing(conn, "crypto_candidate_ledger", "strategy_version", "TEXT DEFAULT 'v1.0'")
            self._add_column_if_missing(conn, "crypto_candidate_ledger", "feature_version", "TEXT DEFAULT 'v1.0'")
            self._add_column_if_missing(conn, "crypto_candidate_ledger", "parameter_version", "TEXT DEFAULT 'v1.0'")
            self._add_column_if_missing(conn, "crypto_candidate_ledger", "signal_score", "REAL DEFAULT 0.0")
            self._add_column_if_missing(conn, "crypto_candidate_ledger", "expected_edge_bps", "REAL DEFAULT 0.0")
            self._add_column_if_missing(conn, "crypto_candidate_ledger", "estimated_cost_bps", "REAL DEFAULT 0.0")
            self._add_column_if_missing(conn, "crypto_candidate_ledger", "expected_net_edge_bps", "REAL DEFAULT 0.0")
            self._add_column_if_missing(conn, "crypto_candidate_ledger", "regime", "TEXT DEFAULT 'RANGE'")
            self._add_column_if_missing(conn, "crypto_candidate_ledger", "rejection_codes", "TEXT DEFAULT '[]'")
            self._add_column_if_missing(conn, "crypto_candidate_ledger", "conversion_rate_applied", "REAL DEFAULT 89.50")
            self._add_column_if_missing(conn, "crypto_candidate_ledger", "research_venue", "TEXT DEFAULT 'BINANCE'")
            self._add_column_if_missing(conn, "crypto_candidate_ledger", "execution_venue", "TEXT DEFAULT 'DELTA_INDIA'")

            # Run migrations for paper positions
            self._add_column_if_missing(conn, "crypto_paper_positions", "strategy_id", "TEXT DEFAULT 'STRAT_UNKNOWN'")
            self._add_column_if_missing(conn, "crypto_paper_positions", "candidate_id", "TEXT DEFAULT ''")
            self._add_column_if_missing(conn, "crypto_paper_positions", "signal_score", "REAL DEFAULT 0.0")
            self._add_column_if_missing(conn, "crypto_paper_positions", "expected_edge_bps", "REAL DEFAULT 0.0")
            self._add_column_if_missing(conn, "crypto_paper_positions", "regime", "TEXT DEFAULT 'RANGE'")
            self._add_column_if_missing(conn, "crypto_paper_positions", "max_hold_ms", "INTEGER")
            self._add_column_if_missing(conn, "crypto_paper_positions", "risk_loss_usd", "REAL DEFAULT 0.0")
            self._add_column_if_missing(conn, "crypto_paper_positions", "stop_loss_distance_usd", "REAL DEFAULT 0.0")
            self._add_column_if_missing(conn, "crypto_paper_positions", "desk", "TEXT DEFAULT 'QUANT'")

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
                open_interest, funding_rate, funding_interval_hours,
                annualized_funding, funding_zscore_7d, funding_percentile,
                distance_to_next_funding_mins, predicted_funding,
                cvd_1m, cvd_notional_usd_1m, cvd_notional_usd_zscore,
                taker_buy_ratio_1m, liquidation_notional_60s,
                liquidation_intensity, liquidation_oi_impact,
                regime, quarantine_state, state_time_ms, created_at_iso
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, state_time_ms) DO UPDATE SET
                mark_price = excluded.mark_price,
                index_price = excluded.index_price,
                basis_bps = excluded.basis_bps,
                open_interest = excluded.open_interest,
                funding_rate = excluded.funding_rate,
                funding_interval_hours = excluded.funding_interval_hours,
                annualized_funding = excluded.annualized_funding,
                funding_zscore_7d = excluded.funding_zscore_7d,
                funding_percentile = excluded.funding_percentile,
                distance_to_next_funding_mins = excluded.distance_to_next_funding_mins,
                predicted_funding = excluded.predicted_funding,
                cvd_1m = excluded.cvd_1m,
                cvd_notional_usd_1m = excluded.cvd_notional_usd_1m,
                cvd_notional_usd_zscore = excluded.cvd_notional_usd_zscore,
                taker_buy_ratio_1m = excluded.taker_buy_ratio_1m,
                liquidation_notional_60s = excluded.liquidation_notional_60s,
                liquidation_intensity = excluded.liquidation_intensity,
                liquidation_oi_impact = excluded.liquidation_oi_impact,
                regime = excluded.regime,
                quarantine_state = excluded.quarantine_state,
                created_at_iso = excluded.created_at_iso
            """, (
                s.symbol, s.mark_price, s.index_price, s.basis_bps,
                s.open_interest, s.funding_rate, s.funding_interval_hours,
                s.annualized_funding, s.funding_zscore_7d, s.funding_percentile,
                s.distance_to_next_funding_mins, s.predicted_funding,
                s.cvd_1m, s.cvd_notional_usd_1m, s.cvd_notional_usd_zscore,
                s.taker_buy_ratio_1m, s.liquidation_notional_60s,
                s.liquidation_intensity, s.liquidation_oi_impact,
                s.regime, s.quarantine_state,
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
                strategy_id, strategy_version, feature_version, parameter_version,
                engine_version, model_version, signal_version,
                decision, decision_reason, signal_score,
                expected_edge_bps, estimated_cost_bps, expected_net_edge_bps,
                regime, rejection_codes, conversion_rate_applied,
                research_venue, execution_venue,
                hypothetical_entry, hypothetical_exit, hypothetical_pnl,
                actual_paper_entry, actual_paper_exit, actual_pnl,
                created_at_iso
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(candidate_id) DO UPDATE SET
                decision = excluded.decision,
                decision_reason = excluded.decision_reason,
                signal_score = excluded.signal_score,
                expected_edge_bps = excluded.expected_edge_bps,
                estimated_cost_bps = excluded.estimated_cost_bps,
                expected_net_edge_bps = excluded.expected_net_edge_bps,
                regime = excluded.regime,
                rejection_codes = excluded.rejection_codes,
                conversion_rate_applied = excluded.conversion_rate_applied,
                hypothetical_entry = excluded.hypothetical_entry,
                hypothetical_exit = excluded.hypothetical_exit,
                hypothetical_pnl = excluded.hypothetical_pnl,
                actual_paper_entry = excluded.actual_paper_entry,
                actual_paper_exit = excluded.actual_paper_exit,
                actual_pnl = excluded.actual_pnl
            """, (
                c.candidate_id, c.timestamp_ms, c.symbol, c.origin,
                c.strategy_id, c.strategy_version, c.feature_version, c.parameter_version,
                c.engine_version, c.model_version, c.signal_version,
                c.decision, c.decision_reason, c.signal_score,
                c.expected_edge_bps, c.estimated_cost_bps, c.expected_net_edge_bps,
                c.regime, json.dumps(c.rejection_codes), c.conversion_rate_applied,
                c.research_venue, c.execution_venue,
                c.hypothetical_entry, c.hypothetical_exit, c.hypothetical_pnl,
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

    def init_paper_tables(self):
        """Create paper trading tables (idempotent, called at engine startup)."""
        with self._get_connection() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS crypto_paper_positions (
                position_id       TEXT PRIMARY KEY,
                symbol            TEXT NOT NULL,
                direction         TEXT NOT NULL,
                notional_usdt     REAL NOT NULL,
                leverage          INTEGER NOT NULL,
                entry_price       REAL NOT NULL,
                entry_slippage_bps REAL NOT NULL,
                entry_fee_usdt    REAL NOT NULL,
                margin_usdt       REAL NOT NULL,
                mark_price        REAL NOT NULL DEFAULT 0,
                unrealised_pnl    REAL NOT NULL DEFAULT 0,
                funding_paid_usdt REAL NOT NULL DEFAULT 0,
                total_fees_usdt   REAL NOT NULL DEFAULT 0,
                exit_price        REAL,
                exit_slippage_bps REAL,
                exit_fee_usdt     REAL,
                realised_pnl      REAL,
                exit_reason       TEXT,
                status            TEXT NOT NULL DEFAULT 'OPEN',
                stop_loss_price   REAL,
                take_profit_price REAL,
                opened_at_ms      INTEGER NOT NULL,
                closed_at_ms      INTEGER,
                last_funding_at_ms INTEGER NOT NULL,
                strategy_id       TEXT DEFAULT 'STRAT_UNKNOWN',
                candidate_id      TEXT DEFAULT '',
                signal_score      REAL DEFAULT 0.0,
                expected_edge_bps REAL DEFAULT 0.0,
                regime            TEXT DEFAULT 'RANGE',
                max_hold_ms       INTEGER,
                risk_loss_usd     REAL DEFAULT 0.0,
                stop_loss_distance_usd REAL DEFAULT 0.0,
                desk              TEXT NOT NULL DEFAULT 'QUANT'
            );

            CREATE INDEX IF NOT EXISTS idx_paper_pos_symbol_status
            ON crypto_paper_positions (symbol, status, opened_at_ms DESC);

            CREATE INDEX IF NOT EXISTS idx_paper_pos_desk
            ON crypto_paper_positions (desk, status, opened_at_ms DESC);
            """)
            conn.commit()

    def upsert_paper_position(self, p: dict):
        payload = dict(p)
        payload.setdefault("strategy_id", "STRAT_UNKNOWN")
        payload.setdefault("candidate_id", "")
        payload.setdefault("signal_score", 0.0)
        payload.setdefault("expected_edge_bps", 0.0)
        payload.setdefault("regime", "RANGE")
        payload.setdefault("max_hold_ms", None)
        payload.setdefault("risk_loss_usd", 0.0)
        payload.setdefault("stop_loss_distance_usd", 0.0)
        payload.setdefault("desk", "QUANT")

        with self._get_connection() as conn:
            conn.execute("""
            INSERT INTO crypto_paper_positions (
                position_id, symbol, direction, notional_usdt, leverage,
                entry_price, entry_slippage_bps, entry_fee_usdt, margin_usdt,
                mark_price, unrealised_pnl, funding_paid_usdt, total_fees_usdt,
                exit_price, exit_slippage_bps, exit_fee_usdt, realised_pnl,
                exit_reason, status, stop_loss_price, take_profit_price,
                opened_at_ms, closed_at_ms, last_funding_at_ms,
                strategy_id, candidate_id, signal_score, expected_edge_bps,
                regime, max_hold_ms, risk_loss_usd, stop_loss_distance_usd,
                desk
            ) VALUES (
                :position_id, :symbol, :direction, :notional_usdt, :leverage,
                :entry_price, :entry_slippage_bps, :entry_fee_usdt, :margin_usdt,
                :mark_price, :unrealised_pnl, :funding_paid_usdt, :total_fees_usdt,
                :exit_price, :exit_slippage_bps, :exit_fee_usdt, :realised_pnl,
                :exit_reason, :status, :stop_loss_price, :take_profit_price,
                :opened_at_ms, :closed_at_ms, :last_funding_at_ms,
                :strategy_id, :candidate_id, :signal_score, :expected_edge_bps,
                :regime, :max_hold_ms, :risk_loss_usd, :stop_loss_distance_usd,
                :desk
            )
            ON CONFLICT(position_id) DO UPDATE SET
                mark_price         = excluded.mark_price,
                unrealised_pnl     = excluded.unrealised_pnl,
                funding_paid_usdt  = excluded.funding_paid_usdt,
                total_fees_usdt    = excluded.total_fees_usdt,
                exit_price         = excluded.exit_price,
                exit_slippage_bps  = excluded.exit_slippage_bps,
                exit_fee_usdt      = excluded.exit_fee_usdt,
                realised_pnl       = excluded.realised_pnl,
                exit_reason        = excluded.exit_reason,
                status             = excluded.status,
                closed_at_ms       = excluded.closed_at_ms,
                last_funding_at_ms = excluded.last_funding_at_ms,
                desk               = excluded.desk
            """, payload)
            conn.commit()

    def update_candidate_paper_execution(
        self,
        candidate_id: str,
        entry_price: Optional[float] = None,
        exit_price: Optional[float] = None,
        pnl: Optional[float] = None,
    ):
        with self._get_connection() as conn:
            cur = conn.cursor()
            if entry_price is not None:
                cur.execute(
                    "UPDATE crypto_candidate_ledger SET actual_paper_entry = ? WHERE candidate_id = ?",
                    (entry_price, candidate_id)
                )
            if exit_price is not None:
                cur.execute(
                    "UPDATE crypto_candidate_ledger SET actual_paper_exit = ?, actual_pnl = ? WHERE candidate_id = ?",
                    (exit_price, pnl, candidate_id)
                )
            conn.commit()

    def get_paper_positions(
        self,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
        desk: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        with self._get_connection() as conn:
            clauses = []
            params: list = []
            if symbol:
                clauses.append("symbol = ?")
                params.append(symbol.upper())
            if status:
                clauses.append("status = ?")
                params.append(status.upper())
            if desk and desk.upper() != "ALL":
                clauses.append("desk = ?")
                params.append(desk.upper())
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            params.append(limit)
            cur = conn.execute(
                f"SELECT * FROM crypto_paper_positions {where} "
                f"ORDER BY opened_at_ms DESC LIMIT ?",
                params,
            )
            return [dict(r) for r in cur.fetchall()]


sqlite_store = SQLiteStore()

