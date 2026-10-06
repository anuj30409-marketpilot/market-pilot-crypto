import sqlite3
import pandas as pd

conn = sqlite3.connect("data/crypto_research_vm2.db")
df = pd.read_sql("SELECT * FROM crypto_paper_positions WHERE status = 'CLOSED' ORDER BY opened_at_ms ASC", conn)
df["opened_dt"] = pd.to_datetime(df["opened_at_ms"], unit="ms")

v1 = df[df["opened_dt"] < "2026-10-06 06:35:00"]
v2 = df[df["opened_dt"] >= "2026-10-06 06:35:00"]

print("==================================================================")
print(f"LIFETIME DATABASE METRICS ({len(df)} trades total)")
print("==================================================================")
print(f"Wins: {(df['realised_pnl']>0).sum()} | Losses: {(df['realised_pnl']<=0).sum()} | Win Rate: {(df['realised_pnl']>0).mean()*100:.1f}% | Net PnL: ${df['realised_pnl'].sum():.2f}")

print("\n==================================================================")
print(f"LEGACY V1 TRADES BEFORE FIX ({len(v1)} trades)")
print("==================================================================")
print(f"Wins: {(v1['realised_pnl']>0).sum()} | Losses: {(v1['realised_pnl']<=0).sum()} | Win Rate: {(v1['realised_pnl']>0).mean()*100:.1f}% | Net PnL: ${v1['realised_pnl'].sum():.2f}")
print(f"Average Win:  ${v1[v1['realised_pnl']>0]['realised_pnl'].mean():.2f}")
print(f"Average Loss: ${v1[v1['realised_pnl']<=0]['realised_pnl'].mean():.2f}")

print("\n==================================================================")
print(f"NEW V2 TRADES AFTER FIX ({len(v2)} trades)")
print("==================================================================")
if len(v2) > 0:
    print(f"Wins: {(v2['realised_pnl']>0).sum()} | Losses: {(v2['realised_pnl']<=0).sum()} | Win Rate: {(v2['realised_pnl']>0).mean()*100:.1f}% | Net PnL: ${v2['realised_pnl'].sum():.2f}")
    if (v2['realised_pnl']>0).sum() > 0:
        print(f"Average Win:  ${v2[v2['realised_pnl']>0]['realised_pnl'].mean():.2f}")
    print(f"Average Loss: ${v2[v2['realised_pnl']<=0]['realised_pnl'].mean():.2f}")
    print("\nDetailed list of new V2 trades:")
    for idx, r in v2.iterrows():
        print(f"  [{r['opened_dt'].strftime('%H:%M')}] {r['strategy_id']:<32} | {r['symbol']} {r['direction']:<5} | Exit: {r['exit_reason']:<15} | Net PnL: ${r['realised_pnl']:>6.2f} (Fees: ${r['total_fees_usdt']:.2f})")
