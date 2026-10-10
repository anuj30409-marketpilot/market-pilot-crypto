import sqlite3
import datetime

conn = sqlite3.connect("data/crypto_research.db")
c = conn.cursor()

c.execute("""
    SELECT position_id, symbol, direction, desk, strategy_id, entry_price, exit_price,
           realised_pnl, total_fees_usdt, exit_reason, opened_at_ms, closed_at_ms,
           notional_usdt, candidate_id, regime
    FROM crypto_paper_positions
    WHERE status = 'CLOSED'
    ORDER BY opened_at_ms ASC
""")
rows = c.fetchall()

# V2 start was Oct 6 06:35 UTC (1791268500000 ms)
v2_start_ms = 1791268500000

v1 = [r for r in rows if r[10] < v2_start_ms and "TEST" not in (r[13] or "")]
v2 = [r for r in rows if r[10] >= v2_start_ms]

def stats(subset, title):
    if not subset:
        print(f"[{title}] No trades.")
        return
    wins = [r for r in subset if r[7] and r[7] > 0]
    losses = [r for r in subset if r[7] and r[7] <= 0]
    net_pnl = sum(r[7] for r in subset if r[7] is not None)
    fees = sum(r[8] for r in subset if r[8] is not None)
    gross_pnl = sum((r[7] + (r[8] or 0)) for r in subset if r[7] is not None)
    wr = len(wins) / len(subset) * 100
    avg_win = sum(r[7] for r in wins) / len(wins) if wins else 0
    avg_loss = sum(r[7] for r in losses) / len(losses) if losses else 0
    payoff = avg_win / abs(avg_loss) if avg_loss != 0 else 0
    pf = sum(r[7] for r in wins) / abs(sum(r[7] for r in losses)) if losses and sum(r[7] for r in losses) != 0 else 0
    durations = [(r[11] - r[10]) / 60000.0 for r in subset if r[11] and r[10]]
    avg_dur = sum(durations) / len(durations) if durations else 0

    print(f"[{title}]")
    print(f"  Trades: {len(subset):3d} | Wins: {len(wins):2d} | Losses: {len(losses):2d} | Win Rate: {wr:5.2f}%")
    print(f"  Net PnL: ${net_pnl:8.2f} | Gross PnL: ${gross_pnl:8.2f} | Fees: ${fees:8.2f}")
    print(f"  Avg Win: ${avg_win:6.2f} | Avg Loss: ${avg_loss:6.2f} | Payoff: {payoff:4.2f}:1 | PF: {pf:4.2f}x")
    print(f"  Avg Hold Duration: {avg_dur:5.1f} min")

print("=" * 80)
print("V1 vs V2 OVERALL METRICS")
print("=" * 80)
stats(v1, "V1 LIFETIME (BEFORE OCT 6 06:35 UTC)")
print()
stats(v2, "V2 LIFETIME (SINCE OCT 6 06:35 UTC)")

print("\n" + "=" * 80)
print(f"V2 BREAKDOWN BY EXIT REASON ({len(v2)} trades)")
print("=" * 80)
by_reason = {}
for r in v2:
    rsn = r[9] or "UNKNOWN"
    by_reason.setdefault(rsn, []).append(r)

for rsn, plist in sorted(by_reason.items(), key=lambda x: len(x[1]), reverse=True):
    rwins = [p for p in plist if p[7] and p[7] > 0]
    rpnl = sum(p[7] for p in plist if p[7] is not None)
    rgross = sum((p[7] + (p[8] or 0)) for p in plist if p[7] is not None)
    rfees = sum(p[8] for p in plist if p[8] is not None)
    durs = [(p[11] - p[10]) / 60000.0 for p in plist if p[11] and p[10]]
    avg_d = sum(durs) / len(durs) if durs else 0
    print(f"  {rsn:18s}: {len(plist):3d} trades | {len(rwins):2d} wins ({len(rwins)/len(plist)*100:5.1f}%) | Net: ${rpnl:8.2f} | Gross: ${rgross:8.2f} | Fees: ${rfees:6.2f} | Avg Dur: {avg_d:5.1f}m")

print("\n" + "=" * 80)
print("V2 BREAKDOWN BY STRATEGY")
print("=" * 80)
by_strat = {}
for r in v2:
    st = r[4] or "UNKNOWN"
    by_strat.setdefault(st, []).append(r)

for st, plist in sorted(by_strat.items(), key=lambda x: len(x[1]), reverse=True):
    swins = [p for p in plist if p[7] and p[7] > 0]
    spnl = sum(p[7] for p in plist if p[7] is not None)
    sgross = sum((p[7] + (p[8] or 0)) for p in plist if p[7] is not None)
    sfees = sum(p[8] for p in plist if p[8] is not None)
    short_st = st.replace("STRAT_", "").replace("_V1", "")
    print(f"  {short_st:28s}: {len(plist):3d} trades | {len(swins):2d} wins ({len(swins)/len(plist)*100:5.1f}%) | Net: ${spnl:8.2f} | Gross: ${sgross:8.2f} | Fees: ${sfees:6.2f}")

print("\n" + "=" * 80)
print("V2 BREAKDOWN BY DESK")
print("=" * 80)
by_desk = {}
for r in v2:
    d = r[3] or "UNKNOWN"
    by_desk.setdefault(d, []).append(r)
for d, plist in by_desk.items():
    stats(plist, f"Desk: {d}")
    print()

print("=" * 80)
print("V2 DETAILED STRATEGY x EXIT REASON MATRIX")
print("=" * 80)
matrix = {}
for r in v2:
    st = r[4].replace("STRAT_", "").replace("_V1", "")
    rsn = r[9] or "UNKNOWN"
    matrix.setdefault(st, {}).setdefault(rsn, []).append(r)

reasons_all = sorted(list(by_reason.keys()))
header = f"{'Strategy':<26} | " + " | ".join(f"{r[:10]:<10}" for r in reasons_all) + " | Total"
print(header)
print("-" * len(header))
for st, rdict in sorted(matrix.items()):
    row_counts = [len(rdict.get(r, [])) for r in reasons_all]
    row_str = f"{st:<26} | " + " | ".join(f"{c:<10d}" for c in row_counts) + f" | {sum(row_counts)}"
    print(row_str)
