import sqlite3
import pandas as pd
import numpy as np

conn = sqlite3.connect("data/crypto_research_vm2.db")
df = pd.read_sql("SELECT * FROM crypto_paper_positions ORDER BY opened_at_ms ASC", conn)

print("==================================================================")
print(f"DATABASE COMPOSITION AUDIT (Total: {len(df)} positions)")
print("==================================================================")
df["opened_dt"] = pd.to_datetime(df["opened_at_ms"], unit="ms")
pre_deploy = df[df["opened_dt"] < "2026-10-05 12:45:00"]
post_deploy = df[df["opened_dt"] >= "2026-10-05 12:45:00"]

print(f"Pre-Deployment Trades:  {len(pre_deploy[pre_deploy['status']=='CLOSED'])} trades | Net PnL: ${pre_deploy['realised_pnl'].sum():.2f} | WR: {(pre_deploy['realised_pnl']>0).mean()*100:.1f}%")
print(f"Post-Deployment Trades: {len(post_deploy[post_deploy['status']=='CLOSED'])} trades | Net PnL: ${post_deploy['realised_pnl'].sum():.2f} | WR: {(post_deploy['realised_pnl']>0).mean()*100:.1f}%")
print(f"Total Combined Trades:  {len(df[df['status']=='CLOSED'])} trades | Net PnL: ${df['realised_pnl'].sum():.2f} | WR: {(df['realised_pnl']>0).mean()*100:.1f}%")

print("\n==================================================================")
print("POST-DEPLOYMENT TRADES DETAILED AUDIT TABLE")
print("==================================================================")
for idx, r in post_deploy.iterrows():
    if r["status"] == "CLOSED":
        print(f"[{r['opened_dt'].strftime('%m-%d %H:%M')}] {r['symbol']} {r['direction']:<5} | Regime: {r['regime']:<16} | Strat: {r['strategy_id']:<32} | Exit: {r['exit_reason']:<9} | Net: ${r['realised_pnl']:>6.2f} | Fees: ${r['total_fees_usdt']:.2f}")

print("\n==================================================================")
print("WHAT HAPPENS IF WE BLOCK SWING TRADING IN RANGE / LOW_VOL?")
print("==================================================================")
# If swing strategies only trade in TRENDING_UP and TRENDING_DOWN:
swing_in_chop = post_deploy[(post_deploy["strategy_id"].str.contains("SWING")) & (~post_deploy["regime"].isin(["TRENDING_UP", "TRENDING_DOWN"]))]
print(f"Swing trades taken in chop/range/low_vol: {len(swing_in_chop)} trades")
print(f"Losses from swing trades in chop: ${swing_in_chop['realised_pnl'].sum():.2f}")
print(f"Fees paid on swing trades in chop: ${swing_in_chop['total_fees_usdt'].sum():.2f}")
