"""Realistic crypto paper trading engine.

Simulates perpetual futures positions with:
- Orderbook-walk slippage on entry and exit
- Taker fees (0.04%) on both legs
- 8-hour funding rate charges at real rates from derivatives engine
- Gap-risk multiplier on forced SL exits
- Cross-margin liquidation guard (position wiped if margin < 0)

All positions stored in SQLite crypto_paper_positions table.
In-memory position cache for fast P&L updates on every mark price tick.
"""
import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from collectors.orderbook import BoundedOrderbook
from collectors.derivatives import derivatives_engine
from core.clock import now_utc_ms, now_utc_iso
from paper.slippage import (
    simulate_fill, get_ask_levels, get_bid_levels,
    TAKER_FEE_BPS, FillResult
)
from storage.sqlite_store import sqlite_store

logger = logging.getLogger("paper.engine")

# Capital allocation per paper desk (USDT)
QUANT_CAPITAL_USDT = 10_000.0
SUPERHUMAN_CAPITAL_USDT = 10_000.0
PAPER_CAPITAL_USDT = QUANT_CAPITAL_USDT  # Default backward compat

# Default leverage (5x = 20% margin requirement)
DEFAULT_LEVERAGE = 5

# Maintenance margin ratio — position liquidated below this
MAINTENANCE_MARGIN_RATIO = 0.005  # 0.5%

# Funding interval in seconds (8 hours)
FUNDING_INTERVAL_S = 8 * 3600


@dataclass
class PaperPosition:
    position_id: str
    symbol: str
    direction: str            # "LONG" or "SHORT"
    notional_usdt: float      # Total notional value
    leverage: int
    entry_price: float
    entry_slippage_bps: float
    entry_fee_usdt: float
    margin_usdt: float        # notional / leverage

    # Live-updated fields
    mark_price: float = 0.0
    unrealised_pnl: float = 0.0
    funding_paid_usdt: float = 0.0
    total_fees_usdt: float = 0.0

    # Exit fields (filled on close)
    exit_price: Optional[float] = None
    exit_slippage_bps: Optional[float] = None
    exit_fee_usdt: Optional[float] = None
    realised_pnl: Optional[float] = None
    exit_reason: Optional[str] = None
    status: str = "OPEN"      # "OPEN" | "CLOSED" | "LIQUIDATED"

    # Timestamps
    opened_at_ms: int = field(default_factory=now_utc_ms)
    closed_at_ms: Optional[int] = None
    last_funding_at_ms: int = field(default_factory=now_utc_ms)

    # Risk parameters set at open
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    max_hold_ms: Optional[int] = None

    # Quantitative Alpha & Risk Controller Provenance
    strategy_id: str = "STRAT_UNKNOWN"
    candidate_id: str = ""
    signal_score: float = 0.0
    expected_edge_bps: float = 0.0
    regime: str = "RANGE"
    risk_loss_usd: float = 0.0
    stop_loss_distance_usd: float = 0.0
    desk: str = "QUANT"  # "QUANT" or "SUPERHUMAN"

    def update_mark(self, mark: float):
        self.mark_price = mark
        if self.direction == "LONG":
            self.unrealised_pnl = (mark - self.entry_price) / self.entry_price * self.notional_usdt
        else:
            self.unrealised_pnl = (self.entry_price - mark) / self.entry_price * self.notional_usdt

    def equity(self) -> float:
        """Current margin equity = initial margin + unrealised PnL - fees paid."""
        return self.margin_usdt + self.unrealised_pnl - self.total_fees_usdt - self.funding_paid_usdt

    def is_liquidated(self) -> bool:
        return self.equity() < self.notional_usdt * MAINTENANCE_MARGIN_RATIO

    def to_dict(self) -> dict:
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "notional_usdt": self.notional_usdt,
            "leverage": self.leverage,
            "entry_price": self.entry_price,
            "entry_slippage_bps": self.entry_slippage_bps,
            "entry_fee_usdt": self.entry_fee_usdt,
            "margin_usdt": self.margin_usdt,
            "mark_price": self.mark_price,
            "unrealised_pnl": self.unrealised_pnl,
            "funding_paid_usdt": self.funding_paid_usdt,
            "total_fees_usdt": self.total_fees_usdt,
            "exit_price": self.exit_price,
            "exit_slippage_bps": self.exit_slippage_bps,
            "exit_fee_usdt": self.exit_fee_usdt,
            "realised_pnl": self.realised_pnl,
            "exit_reason": self.exit_reason,
            "status": self.status,
            "stop_loss_price": self.stop_loss_price,
            "take_profit_price": self.take_profit_price,
            "opened_at_ms": self.opened_at_ms,
            "closed_at_ms": self.closed_at_ms,
            "last_funding_at_ms": self.last_funding_at_ms,
            "equity": self.equity(),
            "roe_pct": (self.unrealised_pnl / self.margin_usdt * 100) if self.margin_usdt else 0.0,
            "strategy_id": self.strategy_id,
            "candidate_id": self.candidate_id,
            "signal_score": self.signal_score,
            "expected_edge_bps": self.expected_edge_bps,
            "regime": self.regime,
            "max_hold_ms": self.max_hold_ms,
            "risk_loss_usd": self.risk_loss_usd,
            "stop_loss_distance_usd": self.stop_loss_distance_usd,
            "desk": self.desk,
        }


class PaperEngine:
    """Realistic paper trading engine for crypto perpetual futures."""

    def __init__(self):
        # In-memory position store — position_id → PaperPosition
        self._positions: Dict[str, PaperPosition] = {}
        self._lock = asyncio.Lock()
        sqlite_store.init_paper_tables()
        self.hydrate_from_db()

    def hydrate_from_db(self):
        """Hydrates in-memory positions from SQLite database on startup or restart."""
        try:
            rows = sqlite_store.get_paper_positions(limit=1000)
            for r in rows:
                pid = r.get("position_id")
                if pid and pid not in self._positions:
                    pos = PaperPosition(
                        position_id=pid,
                        symbol=r["symbol"],
                        direction=r["direction"],
                        notional_usdt=float(r.get("notional_usdt") or 0.0),
                        leverage=int(r.get("leverage") or DEFAULT_LEVERAGE),
                        entry_price=float(r.get("entry_price") or 0.0),
                        entry_slippage_bps=float(r.get("entry_slippage_bps") or 0.0),
                        entry_fee_usdt=float(r.get("entry_fee_usdt") or 0.0),
                        margin_usdt=float(r.get("margin_usdt") or 0.0),
                        mark_price=float(r.get("mark_price") or r.get("entry_price") or 0.0),
                        unrealised_pnl=float(r.get("unrealised_pnl") or 0.0),
                        funding_paid_usdt=float(r.get("funding_paid_usdt") or 0.0),
                        total_fees_usdt=float(r.get("total_fees_usdt") or 0.0),
                        exit_price=float(r["exit_price"]) if r.get("exit_price") is not None else None,
                        exit_slippage_bps=float(r["exit_slippage_bps"]) if r.get("exit_slippage_bps") is not None else None,
                        exit_fee_usdt=float(r["exit_fee_usdt"]) if r.get("exit_fee_usdt") is not None else None,
                        realised_pnl=float(r["realised_pnl"]) if r.get("realised_pnl") is not None else None,
                        exit_reason=r.get("exit_reason"),
                        status=r.get("status", "OPEN"),
                        opened_at_ms=int(r.get("opened_at_ms") or 0),
                        closed_at_ms=int(r["closed_at_ms"]) if r.get("closed_at_ms") is not None else None,
                        last_funding_at_ms=int(r.get("last_funding_at_ms") or 0),
                        stop_loss_price=float(r["stop_loss_price"]) if r.get("stop_loss_price") is not None else None,
                        take_profit_price=float(r["take_profit_price"]) if r.get("take_profit_price") is not None else None,
                        strategy_id=r.get("strategy_id", "STRAT_UNKNOWN"),
                        candidate_id=r.get("candidate_id", ""),
                        signal_score=float(r.get("signal_score") or 0.0),
                        expected_edge_bps=float(r.get("expected_edge_bps") or 0.0),
                        regime=r.get("regime", "RANGE"),
                        max_hold_ms=int(r["max_hold_ms"]) if r.get("max_hold_ms") is not None else None,
                        risk_loss_usd=float(r.get("risk_loss_usd") or 0.0),
                        stop_loss_distance_usd=float(r.get("stop_loss_distance_usd") or 0.0),
                        desk=r.get("desk", "QUANT").upper(),
                    )
                    self._positions[pos.position_id] = pos
            logger.info("Hydrated %d positions from SQLite into memory.", len(self._positions))
        except Exception as e:
            logger.warning("Failed to hydrate positions from SQLite: %s", e)

    # ── Public API ─────────────────────────────────────────────────────────

    async def open_position(
        self,
        symbol: str,
        direction: str,          # "LONG" or "SHORT"
        notional_usdt: float,    # Size in USDT (e.g. 500)
        leverage: int = DEFAULT_LEVERAGE,
        stop_loss_price: Optional[float] = None,
        take_profit_price: Optional[float] = None,
        orderbooks: Optional[Dict[str, BoundedOrderbook]] = None,
        strategy_id: str = "STRAT_UNKNOWN",
        candidate_id: str = "",
        signal_score: float = 0.0,
        expected_edge_bps: float = 0.0,
        regime: str = "RANGE",
        max_hold_ms: Optional[int] = None,
        risk_loss_usd: float = 0.0,
        stop_loss_distance_usd: float = 0.0,
        desk: str = "QUANT",
    ) -> dict:
        """Open a new paper position with realistic orderbook-walk fill."""
        symbol = symbol.upper()

        # Get live orderbook levels for slippage
        fill = self._simulate_entry(symbol, direction, notional_usdt, orderbooks)
        if fill is None:
            return {"error": "Orderbook unavailable — cannot simulate entry"}

        margin = notional_usdt / leverage

        pos = PaperPosition(
            position_id=str(uuid.uuid4()),
            symbol=symbol,
            direction=direction.upper(),
            notional_usdt=notional_usdt,
            leverage=leverage,
            entry_price=fill.avg_price,
            entry_slippage_bps=fill.slippage_bps,
            entry_fee_usdt=fill.fee_usdt,
            margin_usdt=margin,
            mark_price=fill.avg_price,
            total_fees_usdt=fill.fee_usdt,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            strategy_id=strategy_id,
            candidate_id=candidate_id,
            signal_score=signal_score,
            expected_edge_bps=expected_edge_bps,
            regime=regime,
            max_hold_ms=max_hold_ms,
            risk_loss_usd=risk_loss_usd,
            stop_loss_distance_usd=stop_loss_distance_usd,
            desk=desk.upper(),
        )

        async with self._lock:
            self._positions[pos.position_id] = pos
            sqlite_store.upsert_paper_position(pos.to_dict())
            if candidate_id:
                sqlite_store.update_candidate_paper_execution(candidate_id, entry_price=fill.avg_price)

        logger.info(
            f"[PAPER OPEN] {symbol} {direction} ${notional_usdt} "
            f"strat={strategy_id} cand={candidate_id} "
            f"entry={fill.avg_price:.2f} slippage={fill.slippage_bps:.2f}bps "
            f"fee=${fill.fee_usdt:.4f}"
        )
        return pos.to_dict()

    async def close_position(
        self,
        position_id: str,
        reason: str = "MANUAL",
        orderbooks: Optional[Dict[str, BoundedOrderbook]] = None,
        gap_risk: bool = False,
    ) -> dict:
        """Close an open position with realistic exit slippage."""
        async with self._lock:
            pos = self._positions.get(position_id)
            if pos is None:
                return {"error": f"Position {position_id} not found"}
            if pos.status != "OPEN":
                return {"error": f"Position already {pos.status}"}

            fill = self._simulate_exit(pos, orderbooks, gap_risk=gap_risk)
            exit_price = fill.avg_price if fill else pos.mark_price
            exit_fee = fill.fee_usdt if fill else pos.notional_usdt * TAKER_FEE_BPS / 10_000
            exit_slippage = fill.slippage_bps if fill else 0.0

            # Realised PnL = gross price move - both fees - funding paid
            if pos.direction == "LONG":
                gross_pnl = (exit_price - pos.entry_price) / pos.entry_price * pos.notional_usdt
            else:
                gross_pnl = (pos.entry_price - exit_price) / pos.entry_price * pos.notional_usdt

            total_fees = pos.entry_fee_usdt + exit_fee + pos.funding_paid_usdt
            realised_pnl = gross_pnl - total_fees

            pos.exit_price = exit_price
            pos.exit_slippage_bps = exit_slippage
            pos.exit_fee_usdt = exit_fee
            pos.total_fees_usdt = pos.entry_fee_usdt + exit_fee
            pos.realised_pnl = realised_pnl
            pos.exit_reason = reason
            pos.status = "CLOSED"
            pos.closed_at_ms = now_utc_ms()

            sqlite_store.upsert_paper_position(pos.to_dict())
            if pos.candidate_id:
                sqlite_store.update_candidate_paper_execution(
                    candidate_id=pos.candidate_id,
                    exit_price=exit_price,
                    pnl=realised_pnl
                )

        logger.info(
            f"[PAPER CLOSE] {pos.symbol} {pos.direction} reason={reason} "
            f"exit={exit_price:.2f} slippage={exit_slippage:.2f}bps "
            f"pnl=${realised_pnl:.4f} fees=${total_fees:.4f}"
        )
        return pos.to_dict()

    def get_positions(self, symbol: Optional[str] = None, status: str = "OPEN", desk: Optional[str] = None, version: Optional[str] = None) -> List[dict]:
        v2_start = 1791268500000  # Oct 6, 2026 06:35:00 UTC
        positions = [
            p.to_dict() for p in self._positions.values()
            if p.status == status
            and (symbol is None or p.symbol == symbol.upper())
            and (desk is None or desk.upper() == "ALL" or p.desk == desk.upper())
            and (version is None or version.upper() == "ALL" or (version.upper() == "V2" and p.opened_at_ms >= v2_start) or (version.upper() == "V1" and p.opened_at_ms < v2_start))
        ]
        return sorted(positions, key=lambda x: x["opened_at_ms"], reverse=True)

    def _calc_summary(self, positions_list: List[PaperPosition], capital: float, desk_label: str) -> dict:
        open_pos = [p for p in positions_list if p.status == "OPEN"]
        closed_pos = [p for p in positions_list if p.status == "CLOSED"]

        total_unrealised = sum(p.unrealised_pnl for p in open_pos)
        total_realised = sum(p.realised_pnl or 0.0 for p in closed_pos)
        total_fees = sum(p.total_fees_usdt for p in positions_list)
        total_funding = sum(p.funding_paid_usdt for p in positions_list)
        margin_used = sum(p.margin_usdt for p in open_pos)
        
        win_positions = [p for p in closed_pos if (p.realised_pnl or 0.0) > 0]
        loss_positions = [p for p in closed_pos if (p.realised_pnl or 0.0) <= 0]
        wins = len(win_positions)
        losses = len(loss_positions)
        win_rate = (wins / len(closed_pos) * 100) if closed_pos else 0.0

        net_wins = sum(p.realised_pnl or 0.0 for p in win_positions)
        net_losses = abs(sum(p.realised_pnl or 0.0 for p in loss_positions))
        avg_win = (net_wins / wins) if wins else 0.0
        avg_loss = (net_losses / losses) if losses else 0.0
        payoff_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else (round(avg_win, 2) if avg_win > 0 else 0.0)
        profit_factor = round(net_wins / net_losses, 2) if net_losses > 0 else (round(net_wins, 2) if net_wins > 0 else 0.0)

        return {
            "desk": desk_label,
            "capital_usdt": capital,
            "margin_used_usdt": round(margin_used, 4),
            "margin_free_usdt": round(capital - margin_used, 4),
            "open_positions": len(open_pos),
            "total_trades": len(closed_pos),
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": profit_factor,
            "payoff_ratio": payoff_ratio,
            "avg_win_usdt": round(avg_win, 2),
            "avg_loss_usdt": round(avg_loss, 2),
            "total_unrealised_pnl": round(total_unrealised, 4),
            "total_realised_pnl": round(total_realised, 4),
            "total_fees_paid_usdt": round(total_fees, 4),
            "total_funding_paid_usdt": round(total_funding, 4),
            "net_pnl": round(total_realised + total_unrealised, 4),
        }

    def get_summary(self, desk: str = "ALL", version: str = "ALL") -> dict:
        v2_start = 1791268500000  # Oct 6, 2026 06:35:00 UTC
        raw_positions = list(self._positions.values())

        if version and version.upper() == "V2":
            all_positions = [p for p in raw_positions if p.opened_at_ms >= v2_start]
        elif version and version.upper() == "V1":
            all_positions = [p for p in raw_positions if p.opened_at_ms < v2_start]
        else:
            all_positions = raw_positions

        quant_positions = [p for p in all_positions if p.desk == "QUANT"]
        superhuman_positions = [p for p in all_positions if p.desk == "SUPERHUMAN"]

        quant_summary = self._calc_summary(quant_positions, QUANT_CAPITAL_USDT, "QUANT")
        superhuman_summary = self._calc_summary(superhuman_positions, SUPERHUMAN_CAPITAL_USDT, "SUPERHUMAN")
        combined_summary = self._calc_summary(all_positions, QUANT_CAPITAL_USDT + SUPERHUMAN_CAPITAL_USDT, "ALL")

        if desk.upper() == "QUANT":
            res = dict(quant_summary)
        elif desk.upper() == "SUPERHUMAN":
            res = dict(superhuman_summary)
        else:
            res = dict(combined_summary)

        res["version"] = version.upper() if version else "ALL"
        res["quant"] = quant_summary
        res["superhuman"] = superhuman_summary
        return res

    # ── Background loops ────────────────────────────────────────────────────

    async def run_mark_price_updater(self):
        """Update unrealised PnL and check SL/TP on every mark price tick (1s)."""
        while True:
            await asyncio.sleep(1)
            async with self._lock:
                for pos in list(self._positions.values()):
                    if pos.status != "OPEN":
                        continue
                    tracker = derivatives_engine.trackers.get(pos.symbol)
                    if tracker and tracker.mark_price:
                        pos.update_mark(tracker.mark_price)
                        self._check_sl_tp(pos)
                        if pos.is_liquidated():
                            pos.status = "LIQUIDATED"
                            pos.exit_price = pos.mark_price
                            pos.realised_pnl = -pos.margin_usdt
                            pos.exit_reason = "LIQUIDATION"
                            pos.closed_at_ms = now_utc_ms()
                            sqlite_store.upsert_paper_position(pos.to_dict())
                            logger.warning(f"[PAPER LIQUIDATED] {pos.symbol} {pos.direction} position_id={pos.position_id}")

    async def run_funding_charger(self):
        """Charge real funding rates every 8 hours to all open positions."""
        while True:
            await asyncio.sleep(60)  # Check every minute
            now_ms = now_utc_ms()
            async with self._lock:
                for pos in self._positions.values():
                    if pos.status != "OPEN":
                        continue
                    elapsed_s = (now_ms - pos.last_funding_at_ms) / 1000
                    if elapsed_s < FUNDING_INTERVAL_S:
                        continue

                    tracker = derivatives_engine.trackers.get(pos.symbol)
                    funding_rate = tracker.funding_rate if tracker else 0.0001
                    # Funding charge: LONG pays rate, SHORT receives rate
                    charge = pos.notional_usdt * abs(funding_rate)
                    if pos.direction == "LONG" and funding_rate >= 0:
                        pos.funding_paid_usdt += charge
                    elif pos.direction == "SHORT" and funding_rate < 0:
                        pos.funding_paid_usdt += charge
                    else:
                        pos.funding_paid_usdt -= charge  # Received funding

                    pos.last_funding_at_ms = now_ms
                    sqlite_store.upsert_paper_position(pos.to_dict())
                    logger.info(
                        f"[FUNDING] {pos.symbol} {pos.direction} "
                        f"rate={funding_rate:.6f} charge=${charge:.4f}"
                    )

    # ── Internal helpers ────────────────────────────────────────────────────

    def _simulate_entry(
        self, symbol: str, direction: str, notional: float,
        orderbooks: Optional[Dict]
    ) -> Optional[FillResult]:
        if not orderbooks or symbol not in orderbooks:
            return None
        ob = orderbooks[symbol]
        snap = ob.get_snapshot()
        mid = snap.mid_price
        if direction.upper() == "LONG":
            levels = get_ask_levels(ob)
        else:
            levels = get_bid_levels(ob)
        return simulate_fill(direction.upper(), notional, levels, mid)

    def _simulate_exit(
        self, pos: PaperPosition,
        orderbooks: Optional[Dict],
        gap_risk: bool = False,
    ) -> Optional[FillResult]:
        if not orderbooks or pos.symbol not in orderbooks:
            return None
        ob = orderbooks[pos.symbol]
        snap = ob.get_snapshot()
        mid = snap.mid_price
        # Closing LONG = selling, closing SHORT = buying
        exit_side = "SELL" if pos.direction == "LONG" else "BUY"
        if exit_side == "SELL":
            levels = get_bid_levels(ob)
        else:
            levels = get_ask_levels(ob)
        return simulate_fill(exit_side, pos.notional_usdt, levels, mid, gap_risk=gap_risk)

    def _check_sl_tp(self, pos: PaperPosition):
        """Trigger SL/TP exits synchronously within the mark-price loop lock."""
        if pos.status != "OPEN":
            return
        mark = pos.mark_price
        hit = False
        reason = ""
        gap = False

        # Two-Tier Dynamic Ratchet:
        # Tier 1 (+35 bps floating profit): Ratchet SL to Entry + 12 bps friction (locks Breakeven)
        # Tier 2 (+70 bps floating profit): Ratchet SL to Entry + 35 bps (locks +23 bps net profit)
        if pos.direction == "LONG":
            t1_sl = pos.entry_price * 1.0012  # Entry + 12 bps friction
            t2_sl = pos.entry_price * 1.0035  # Entry + 35 bps profit

            # Tier 2 check (+70 bps)
            if mark >= pos.entry_price * 1.0070 and (not pos.stop_loss_price or pos.stop_loss_price < t2_sl):
                pos.stop_loss_price = t2_sl
                sqlite_store.upsert_paper_position(pos.to_dict())
                logger.info(f"[PAPER RATCHET TIER-2] {pos.symbol} LONG SL raised to lock in +35bps profit @ {pos.stop_loss_price:.2f}")
            # Tier 1 check (+35 bps)
            elif mark >= pos.entry_price * 1.0035 and (not pos.stop_loss_price or pos.stop_loss_price < t1_sl):
                pos.stop_loss_price = t1_sl
                sqlite_store.upsert_paper_position(pos.to_dict())
                logger.info(f"[PAPER RATCHET TIER-1] {pos.symbol} LONG SL raised to lock in BE+friction @ {pos.stop_loss_price:.2f}")

            if pos.stop_loss_price and mark <= pos.stop_loss_price:
                hit, reason, gap = True, "STOP_LOSS", True
            elif pos.take_profit_price and mark >= pos.take_profit_price:
                hit, reason, gap = True, "TAKE_PROFIT", False
        else:
            t1_sl = pos.entry_price * 0.9988  # Entry - 12 bps friction
            t2_sl = pos.entry_price * 0.9965  # Entry - 35 bps profit

            # Tier 2 check (+70 bps short)
            if mark <= pos.entry_price * 0.9930 and (not pos.stop_loss_price or pos.stop_loss_price > t2_sl):
                pos.stop_loss_price = t2_sl
                sqlite_store.upsert_paper_position(pos.to_dict())
                logger.info(f"[PAPER RATCHET TIER-2] {pos.symbol} SHORT SL lowered to lock in +35bps profit @ {pos.stop_loss_price:.2f}")
            # Tier 1 check (+35 bps short)
            elif mark <= pos.entry_price * 0.9965 and (not pos.stop_loss_price or pos.stop_loss_price > t1_sl):
                pos.stop_loss_price = t1_sl
                sqlite_store.upsert_paper_position(pos.to_dict())
                logger.info(f"[PAPER RATCHET TIER-1] {pos.symbol} SHORT SL lowered to lock in BE+friction @ {pos.stop_loss_price:.2f}")

            if pos.stop_loss_price and mark >= pos.stop_loss_price:
                hit, reason, gap = True, "STOP_LOSS", True
            elif pos.take_profit_price and mark <= pos.take_profit_price:
                hit, reason, gap = True, "TAKE_PROFIT", False

        # Stagnation Momentum Hurdle: If held >= 45m and floating move has not reached +20 bps
        elapsed_ms = now_utc_ms() - pos.opened_at_ms
        if not hit and pos.max_hold_ms and pos.max_hold_ms >= 7200000:  # for 2h+ swing trades
            if elapsed_ms >= 45 * 60 * 1000:
                floating_bps = (mark - pos.entry_price) / pos.entry_price * 10000 if pos.direction == "LONG" else (pos.entry_price - mark) / pos.entry_price * 10000
                if floating_bps < 20.0:
                    hit, reason, gap = True, "STAGNATION_EXIT", False

        # Time-stop check based on strategy lifecycle (e.g. 20m, 60m, 2h, 8h)
        if not hit and pos.max_hold_ms and elapsed_ms >= pos.max_hold_ms:
            hit, reason, gap = True, "TIME_STOP", False

        if hit:
            # Close inline — we're already under the lock
            exit_price = mark
            exit_fee = pos.notional_usdt * TAKER_FEE_BPS / 10_000
            if gap:
                exit_fee *= 1.5  # Gap risk on SL
            if pos.direction == "LONG":
                gross = (exit_price - pos.entry_price) / pos.entry_price * pos.notional_usdt
            else:
                gross = (pos.entry_price - exit_price) / pos.entry_price * pos.notional_usdt
            total_fees = pos.entry_fee_usdt + exit_fee + pos.funding_paid_usdt
            pos.exit_price = exit_price
            pos.exit_fee_usdt = exit_fee
            pos.realised_pnl = gross - total_fees
            pos.exit_reason = reason
            pos.status = "CLOSED"
            pos.closed_at_ms = now_utc_ms()
            sqlite_store.upsert_paper_position(pos.to_dict())
            if pos.candidate_id:
                sqlite_store.update_candidate_paper_execution(
                    candidate_id=pos.candidate_id,
                    exit_price=exit_price,
                    pnl=pos.realised_pnl
                )
            logger.info(f"[PAPER {reason}] {pos.symbol} {pos.direction} exit={exit_price:.2f} pnl=${pos.realised_pnl:.4f}")


paper_engine = PaperEngine()
