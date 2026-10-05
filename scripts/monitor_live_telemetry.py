"""Live Telemetry & Forward PnL Monitor for Market Pilot Crypto Desk.

Monitors:
1. Real-time active regimes & feed status
2. Live open positions, mark prices, floating PnL, and Breakeven Ratchets
3. Closed trade performance (win rate, gross PnL, fees, net PnL)
4. Recent candidate evaluations and negative proofs across all 5 strategies
5. Continuous auto-refresh terminal dashboard

Usage:
    python scripts/monitor_live_telemetry.py [--interval 5] [--once]
"""
import sys
import os
import time
import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


if sys.stdout.encoding != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass


def get_db_connection():
    for p in ["data/crypto_research_vm2.db", "data/crypto_research.db"]:
        if os.path.exists(p):
            conn = sqlite3.connect(p)
            conn.row_factory = sqlite3.Row
            return conn, p
    return None, None


def render_dashboard():
    conn, db_path = get_db_connection()
    if not conn:
        print("[ERROR] No database found in data/.")
        return

    cur = conn.cursor()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    print("\033[2J\033[H", end="")  # Clear terminal
    print("=" * 105)
    print(f"[MARKET PILOT CRYPTO] LIVE TRADING TELEMETRY DASHBOARD | {now_utc}")
    print(f"📁 Database: {db_path}")
    print("=" * 105)

    # 1. Market Regimes & Derivatives Flow
    cur.execute("""
        SELECT symbol, mark_price, regime, funding_zscore_7d, cvd_notional_usd_zscore, liquidation_notional_60s, state_time_ms
        FROM crypto_derivatives_state
        GROUP BY symbol
        HAVING state_time_ms = MAX(state_time_ms)
        ORDER BY symbol ASC
    """)
    states = [dict(r) for r in cur.fetchall()]
    print("\n[1] REAL-TIME ASSET & REGIME STATES:")
    print("-" * 105)
    print(f"{'SYMBOL':<10} | {'MARK PRICE':<14} | {'REGIME':<18} | {'FUNDING Z':<12} | {'CVD Z-SCORE':<14} | {'LIQ (60s)':<12}")
    print("-" * 105)
    for s in states:
        sym = s.get("symbol", "N/A")
        mark = f"${float(s.get('mark_price') or 0.0):,.2f}"
        regime = s.get("regime", "UNKNOWN")
        fz = f"{float(s.get('funding_zscore_7d') or 0.0):+.2f}"
        cvdz = f"{float(s.get('cvd_notional_usd_zscore') or 0.0):+.2f}"
        liq = f"${float(s.get('liquidation_notional_60s') or 0.0):,.2f}"
        print(f"{sym:<10} | {mark:<14} | {regime:<18} | {fz:<12} | {cvdz:<14} | {liq:<12}")

    # 2. Currently Open Positions
    cur.execute("""
        SELECT position_id, desk, strategy_id, symbol, direction, notional_usdt,
               entry_price, mark_price, unrealised_pnl, stop_loss_price, take_profit_price,
               total_fees_usdt, opened_at_ms
        FROM crypto_paper_positions
        WHERE status = 'OPEN'
        ORDER BY opened_at_ms DESC
    """)
    open_pos = [dict(r) for r in cur.fetchall()]
    print(f"\n[2] OPEN PAPER POSITIONS ({len(open_pos)} ACTIVE — STRICT ASSET MUTEX ENFORCED):")
    print("-" * 105)
    if not open_pos:
        print("  No open positions currently active. Ready for qualified candidate triggers.")
    else:
        print(f"{'DESK':<10} | {'STRATEGY':<30} | {'PAIR':<8} | {'DIR':<6} | {'NOTIONAL':<10} | {'ENTRY':<10} | {'UNREAL PNL':<12} | {'BE RATCHET'}")
        print("-" * 105)
        for p in open_pos:
            desk = p.get("desk", "QUANT")
            strat = p.get("strategy_id", "")[:28]
            pair = p.get("symbol", "")
            dirc = p.get("direction", "")
            notional = f"${float(p.get('notional_usdt') or 0.0):,.1f}"
            entry = f"{float(p.get('entry_price') or 0.0):.2f}"
            unreal = float(p.get("unrealised_pnl") or 0.0)
            unreal_str = f"{unreal:+.2f} USDT"
            sl = float(p.get("stop_loss_price") or 0.0)
            entry_f = float(p.get("entry_price") or 0.0)
            # Check if breakeven ratchet is active
            if dirc == "LONG" and sl >= entry_f * 1.001:
                ratchet = "[ACTIVE: LOCKED BE]"
            elif dirc == "SHORT" and sl <= entry_f * 0.999:
                ratchet = "[ACTIVE: LOCKED BE]"
            else:
                ratchet = "[PENDING +75bps]"
            print(f"{desk:<10} | {strat:<30} | {pair:<8} | {dirc:<6} | {notional:<10} | {entry:<10} | {unreal_str:<12} | {ratchet}")

    # 3. Closed Trades Performance Summary
    cur.execute("""
        SELECT position_id, realised_pnl, total_fees_usdt, exit_reason, desk, strategy_id
        FROM crypto_paper_positions
        WHERE status = 'CLOSED' AND candidate_id NOT LIKE '%TEST%'
    """)
    closed = [dict(r) for r in cur.fetchall()]
    print(f"\n[3] CLOSED TRADES AUDIT ({len(closed)} TOTAL REAL PRODUCTION TRADES):")
    print("-" * 105)
    if closed:
        pnls = [float(c.get("realised_pnl") or 0.0) for c in closed]
        fees = [float(c.get("total_fees_usdt") or 0.0) for c in closed]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        total_pnl = sum(pnls)
        total_fees = sum(fees)
        gross_pnl = total_pnl + total_fees
        win_rate = (len(wins) / len(closed)) * 100.0 if closed else 0.0
        print(f"  Trades: {len(closed)} (Wins: {len(wins)}, Losses: {len(losses)}) | Win Rate: {win_rate:.1f}%")
        print(f"  Net Realized PnL: {total_pnl:+.2f} USDT | Gross PnL: {gross_pnl:+.2f} USDT | Total Fees Paid: {total_fees:.2f} USDT")

    # 4. Recent Candidate Ledger Records (Last 8)
    cur.execute("""
        SELECT candidate_id, origin, strategy_id, symbol, decision, decision_reason, timestamp_ms
        FROM crypto_candidate_ledger
        WHERE candidate_id NOT LIKE '%TEST%'
        ORDER BY timestamp_ms DESC
        LIMIT 8
    """)
    cands = [dict(r) for r in cur.fetchall()]
    print(f"\n[4] RECENT CANDIDATE EVALUATIONS & AUDIT TRAIL:")
    print("-" * 105)
    if not cands:
        print("  No candidate records logged yet.")
    else:
        for c in cands:
            t_iso = datetime.fromtimestamp(c["timestamp_ms"] / 1000, tz=timezone.utc).strftime("%H:%M:%S")
            dec = c.get("decision", "")
            icon = "[ACCEPT]" if dec == "ACCEPT" else "[REJECT]"
            strat = c.get("strategy_id", "")
            sym = c.get("symbol", "")
            reason = (c.get("decision_reason") or "")[:55]
            print(f"  [{t_iso}] {icon:<8} | {c.get('origin', 'QUANT'):<10} | {strat:<32} | {sym:<8} | {reason}")

    print("=" * 105)
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Market Pilot Crypto Live Telemetry Dashboard")
    parser.add_argument("--interval", type=int, default=5, help="Refresh interval in seconds (default: 5)")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    args = parser.parse_args()

    if args.once:
        render_dashboard()
        return

    try:
        while True:
            render_dashboard()
            time.sleep(args.interval)
    except (KeyboardInterrupt, SystemExit):
        print("\nTelemetry dashboard stopped.")


if __name__ == "__main__":
    main()
