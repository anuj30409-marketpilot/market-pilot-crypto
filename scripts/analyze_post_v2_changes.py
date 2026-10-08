import sqlite3
import datetime
import os
import sys

def analyze_db(db_path):
    if not os.path.exists(db_path):
        print(f"File not found: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    c = conn.cursor()

    # V2 start was Oct 6 06:35 UTC (1791268500000)
    # Our latest deployment was Oct 7 07:53 UTC (1791359600000)
    v2_start_ms = 1791268500000
    deploy_ms = 1791359600000

    print("=" * 80)
    print("CRYPTO DEEP AUDIT: PERFORMANCE BEFORE vs AFTER OCT 7 DEPLOYMENT")
    print("=" * 80)

    for label, start_time, end_time in [
        ("V2 Phase 1 (Oct 6 06:35 -> Oct 7 07:53)", v2_start_ms, deploy_ms),
        ("V2 Phase 2 [LATEST CHANGES] (Oct 7 07:53 -> Present)", deploy_ms, 9999999999999),
        ("All V2 Combined (Oct 6 06:35 -> Present)", v2_start_ms, 9999999999999),
    ]:
        print(f"\n>>> {label} <<<")
        rows = c.execute("""
            SELECT position_id, symbol, direction, desk, strategy_id, entry_price, exit_price, 
                   realised_pnl, total_fees_usdt, exit_reason, opened_at_ms, closed_at_ms,
                   (closed_at_ms - opened_at_ms)/1000/60.0 as duration_min, notional_usdt
            FROM crypto_paper_positions 
            WHERE opened_at_ms >= ? AND opened_at_ms < ? AND status = "CLOSED"
            ORDER BY opened_at_ms ASC
        """, (start_time, end_time)).fetchall()

        if not rows:
            print("  No closed trades found.")
            continue

        wins = [r for r in rows if r[7] and r[7] > 0]
        losses = [r for r in rows if r[7] and r[7] <= 0]
        win_rate = len(wins) / len(rows) * 100
        gross_pnl = sum((r[7] + (r[8] or 0)) for r in rows if r[7] is not None)
        net_pnl = sum(r[7] for r in rows if r[7] is not None)
        total_fees = sum(r[8] for r in rows if r[8] is not None)
        
        net_wins = sum(r[7] for r in wins)
        net_losses = abs(sum(r[7] for r in losses))
        avg_win = (net_wins / len(wins)) if wins else 0
        avg_loss = (net_losses / len(losses)) if losses else 0
        payoff = avg_win / avg_loss if avg_loss > 0 else 0
        profit_factor = net_wins / net_losses if net_losses > 0 else 0

        print(f"  Total Trades : {len(rows):4d} (Wins: {len(wins)}, Losses: {len(losses)})")
        print(f"  Win Rate     : {win_rate:6.2f}%")
        print(f"  Gross PnL    : ${gross_pnl:8.2f}")
        print(f"  Total Fees   : ${total_fees:8.2f} ({(total_fees/abs(gross_pnl)*100 if gross_pnl else 0):.1f}% of gross)")
        print(f"  Net PnL      : ${net_pnl:8.2f}")
        print(f"  Avg Win      : ${avg_win:8.2f} | Avg Loss: ${avg_loss:8.2f} | Payoff: {payoff:.2f}:1")
        print(f"  Profit Factor: {profit_factor:6.2f}x")

        print("\n  --- Breakdown by Exit Reason ---")
        by_reason = {}
        for r in rows:
            reason = r[9] or "UNKNOWN"
            by_reason.setdefault(reason, []).append(r)

        for reason, plist in sorted(by_reason.items(), key=lambda x: len(x[1]), reverse=True):
            rwins = [p for p in plist if p[7] and p[7] > 0]
            rpnl = sum(p[7] for p in plist if p[7])
            rfees = sum(p[8] for p in plist if p[8])
            avg_dur = sum(p[12] for p in plist) / len(plist)
            avg_move_bps = sum(
                ((p[6] - p[5]) / p[5] * 10000 if p[2] == "LONG" else (p[5] - p[6]) / p[5] * 10000) 
                for p in plist if p[5] and p[6]
            ) / len(plist)
            print(f"    {reason:18s}: {len(plist):3d} trades | {len(rwins):2d} wins | Net PnL: ${rpnl:8.2f} | Fees: ${rfees:6.2f} | Avg Move: {avg_move_bps:+6.1f} bps | Avg Dur: {avg_dur:4.1f}m")

        print("\n  --- Breakdown by Strategy ---")
        by_strat = {}
        for r in rows:
            strat = r[4] or "UNKNOWN"
            by_strat.setdefault(strat, []).append(r)

        for strat, plist in sorted(by_strat.items(), key=lambda x: len(x[1]), reverse=True):
            swins = [p for p in plist if p[7] and p[7] > 0]
            spnl = sum(p[7] for p in plist if p[7])
            sfees = sum(p[8] for p in plist if p[8])
            short_name = strat.replace("STRAT_", "").replace("_V1", "")
            print(f"    {short_name:30s}: {len(plist):3d} trades | {len(swins):2d} wins ({len(swins)/len(plist)*100:5.1f}%) | Net PnL: ${spnl:8.2f} | Fees: ${sfees:6.2f}")

    print("\n" + "=" * 80)
    print("DETAILED LOG OF TRADES SINCE LATEST DEPLOYMENT (Oct 7 07:53 UTC)")
    print("=" * 80)
    recent_rows = c.execute("""
        SELECT position_id, symbol, direction, desk, strategy_id, entry_price, exit_price, 
               realised_pnl, total_fees_usdt, exit_reason, opened_at_ms, closed_at_ms,
               (closed_at_ms - opened_at_ms)/1000/60.0 as duration_min, notional_usdt
        FROM crypto_paper_positions 
        WHERE opened_at_ms >= ? AND status = "CLOSED"
        ORDER BY opened_at_ms ASC
    """, (deploy_ms,)).fetchall()

    for r in recent_rows:
        pid, sym, dirn, desk, strat, ep, xp, pnl, fee, rsn, op, cl, dur, notional = r
        dt_open = datetime.datetime.utcfromtimestamp(op/1000).strftime("%m-%d %H:%M")
        move_bps = ((xp - ep) / ep * 10000 if dirn == "LONG" else (ep - xp) / ep * 10000) if ep and xp else 0.0
        short_strat = strat.replace("STRAT_", "").replace("_V1", "")
        print(f"{dt_open} | {sym:8s} {dirn:5s} | ${notional:4.0f} | {short_strat:25s} | {rsn:16s} | {dur:4.0f}m | Move: {move_bps:+6.1f} bps | Net PnL: ${pnl:+6.2f} | Fee: ${fee:5.2f}")

if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "data/crypto_pilot_latest.db"
    analyze_db(db)
