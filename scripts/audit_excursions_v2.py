import sqlite3
import datetime

conn = sqlite3.connect("data/crypto_research.db")
c = conn.cursor()

c.execute("""
    SELECT position_id, symbol, direction, entry_price, exit_price, realised_pnl,
           total_fees_usdt, exit_reason, opened_at_ms, closed_at_ms, notional_usdt, strategy_id
    FROM crypto_paper_positions
    WHERE status = 'CLOSED' AND opened_at_ms >= 1791268500000
    ORDER BY opened_at_ms ASC
""")
trades = c.fetchall()

print(f"=== MFE / MAE EXCURSION ANALYSIS FOR {len(trades)} V2 TRADES ===")

mfe_list = []
mae_list = []
time_to_mfe_mins = []

for t in trades:
    pid, sym, dirn, ep, xp, pnl, fee, rsn, op, cl, notional, strat = t
    
    # Query candles during the trade
    c.execute("""
        SELECT open_time_ms, high, low, close
        FROM crypto_candles_1m
        WHERE symbol = ? AND open_time_ms >= ? AND open_time_ms <= ?
        ORDER BY open_time_ms ASC
    """, (sym, op, cl))
    candles = c.fetchall()
    
    if not candles:
        continue

    max_fav = 0.0
    max_adv = 0.0
    mfe_time_ms = op

    for cd in candles:
        t_ms, chigh, clow, cclose = cd
        if dirn == "LONG":
            fav = (chigh - ep) / ep * 10000.0
            adv = (clow - ep) / ep * 10000.0
        else:
            fav = (ep - clow) / ep * 10000.0
            adv = (ep - chigh) / ep * 10000.0
        
        if fav > max_fav:
            max_fav = fav
            mfe_time_ms = t_ms
        if adv < max_adv:
            max_adv = adv

    dur_m = (cl - op) / 60000.0
    time_to_mfe = (mfe_time_ms - op) / 60000.0
    mfe_list.append((max_fav, dur_m, time_to_mfe, pnl, rsn, strat, sym, dirn))
    mae_list.append(max_adv)

mfes = [m[0] for m in mfe_list]
maes = mae_list

print(f"Average MFE: +{sum(mfes)/len(mfes):.1f} bps | Median MFE: +{sorted(mfes)[len(mfes)//2]:.1f} bps")
print(f"Average MAE: {sum(maes)/len(maes):.1f} bps | Median MAE: {sorted(maes)[len(maes)//2]:.1f} bps")

# How many trades reached +20 bps, +30 bps, +40 bps, +50 bps, +60 bps?
for thresh in [15, 20, 25, 30, 40, 50, 60, 80, 100]:
    reached = [m for m in mfe_list if m[0] >= thresh]
    actually_won = [m for m in reached if m[3] > 0]
    print(f"Reached MFE >= +{thresh:2d} bps: {len(reached):2d}/{len(mfe_list)} ({len(reached)/len(mfe_list)*100:4.1f}%) | But won: {len(actually_won):2d} ({len(actually_won)/len(reached)*100:4.1f}%)")

print("\n--- Strategy-wise MFE vs Realized Win Rate ---")
by_strat = {}
for m in mfe_list:
    st = m[5].replace("STRAT_", "").replace("_V1", "")
    by_strat.setdefault(st, []).append(m)

for st, items in by_strat.items():
    avg_mfe = sum(i[0] for i in items) / len(items)
    wins = len([i for i in items if i[3] > 0])
    mfe_ge_25 = len([i for i in items if i[0] >= 25])
    mfe_ge_40 = len([i for i in items if i[0] >= 40])
    print(f"{st:<26}: {len(items):2d} trades | Win Rate: {wins/len(items)*100:4.1f}% | Avg MFE: +{avg_mfe:4.1f} bps | MFE >= 25bps: {mfe_ge_25}/{len(items)} | MFE >= 40bps: {mfe_ge_40}/{len(items)}")
