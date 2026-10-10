import sqlite3
import datetime

conn = sqlite3.connect("data/crypto_research.db")
c = conn.cursor()

c.execute("""
    SELECT position_id, symbol, direction, desk, strategy_id, entry_price, exit_price,
           realised_pnl, total_fees_usdt, exit_reason, opened_at_ms, closed_at_ms,
           notional_usdt, candidate_id, regime, stop_loss_price, take_profit_price
    FROM crypto_paper_positions
    WHERE status = 'CLOSED' AND opened_at_ms >= 1791471100000
    ORDER BY opened_at_ms ASC
""")
rows = c.fetchall()

print("=" * 80)
print(f"TRADES SINCE COMMIT 839c3c9 (Oct 8 14:51 UTC -> Oct 10 14:41 UTC) : {len(rows)} TRADES")
print("=" * 80)

wins = [r for r in rows if r[7] and r[7] > 0]
losses = [r for r in rows if r[7] and r[7] <= 0]
net_pnl = sum(r[7] for r in rows if r[7] is not None)
fees = sum(r[8] for r in rows if r[8] is not None)
gross_pnl = sum((r[7] + (r[8] or 0)) for r in rows if r[7] is not None)
wr = len(wins) / len(rows) * 100 if rows else 0
avg_win = sum(r[7] for r in wins) / len(wins) if wins else 0
avg_loss = sum(r[7] for r in losses) / len(losses) if losses else 0
payoff = avg_win / abs(avg_loss) if avg_loss != 0 else 0
pf = sum(r[7] for r in wins) / abs(sum(r[7] for r in losses)) if losses and sum(r[7] for r in losses) != 0 else 0

print(f"Trades: {len(rows)} | Wins: {len(wins)} | Losses: {len(losses)} | Win Rate: {wr:.2f}%")
print(f"Net PnL: ${net_pnl:.2f} | Gross PnL: ${gross_pnl:.2f} | Total Fees: ${fees:.2f}")
print(f"Avg Win: ${avg_win:.2f} | Avg Loss: ${avg_loss:.2f} | Payoff: {payoff:.2f}:1 | PF: {pf:.2f}x")

print("\n--- Breakdown by Exit Reason ---")
by_reason = {}
for r in rows:
    by_reason.setdefault(r[9] or "UNKNOWN", []).append(r)

for rsn, plist in sorted(by_reason.items(), key=lambda x: len(x[1]), reverse=True):
    rwins = [p for p in plist if p[7] and p[7] > 0]
    rpnl = sum(p[7] for p in plist if p[7] is not None)
    rgross = sum((p[7] + (p[8] or 0)) for p in plist if p[7] is not None)
    rfees = sum(p[8] for p in plist if p[8] is not None)
    durs = [(p[11] - p[10]) / 60000.0 for p in plist if p[11] and p[10]]
    avg_d = sum(durs) / len(durs) if durs else 0
    print(f"  {rsn:18s}: {len(plist):2d} trades | {len(rwins):2d} wins ({len(rwins)/len(plist)*100:5.1f}%) | Net: ${rpnl:8.2f} | Gross: ${rgross:8.2f} | Fees: ${rfees:6.2f} | Avg Dur: {avg_d:5.1f}m")

print("\n--- Breakdown by Strategy ---")
by_strat = {}
for r in rows:
    by_strat.setdefault(r[4] or "UNKNOWN", []).append(r)

for st, plist in sorted(by_strat.items(), key=lambda x: len(x[1]), reverse=True):
    swins = [p for p in plist if p[7] and p[7] > 0]
    spnl = sum(p[7] for p in plist if p[7] is not None)
    sgross = sum((p[7] + (p[8] or 0)) for p in plist if p[7] is not None)
    sfees = sum(p[8] for p in plist if p[8] is not None)
    short_st = st.replace("STRAT_", "").replace("_V1", "")
    print(f"  {short_st:28s}: {len(plist):2d} trades | {len(swins):2d} wins ({len(swins)/len(plist)*100:5.1f}%) | Net: ${spnl:8.2f} | Gross: ${sgross:8.2f} | Fees: ${sfees:6.2f}")

print("\n--- All Trades in This Phase ---")
for r in rows:
    pid, sym, dirn, desk, strat, ep, xp, pnl, fee, rsn, op, cl, notional, cand, reg, sl, tp = r
    dt_open = datetime.datetime.utcfromtimestamp(op/1000).strftime("%m-%d %H:%M")
    dur = (cl - op) / 60000.0 if cl and op else 0
    move_bps = ((xp - ep) / ep * 10000 if dirn == "LONG" else (ep - xp) / ep * 10000) if ep and xp else 0.0
    short_strat = strat.replace("STRAT_", "").replace("_V1", "")
    pnl_str = f"${pnl:+6.2f}" if pnl is not None else "  N/A "
    print(f"{dt_open} | {sym:8s} {dirn:5s} | ${notional:4.0f} | {short_strat:24s} | {rsn:16s} | {dur:4.0f}m | Move: {move_bps:+6.1f} bps | Net: {pnl_str} | Fee: ${fee:4.2f}")
