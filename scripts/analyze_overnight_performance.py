import sqlite3
import pandas as pd
import datetime

conn = sqlite3.connect("data/crypto_research_vm2.db")
df = pd.read_sql("SELECT * FROM crypto_paper_positions ORDER BY opened_at_ms ASC", conn)
print(f"Total rows in crypto_paper_positions: {len(df)}")
df["opened_dt"] = pd.to_datetime(df["opened_at_ms"], unit="ms")
df["closed_dt"] = pd.to_datetime(df["closed_at_ms"], unit="ms")

# Filter for recent trades opened after Oct 5 12:45 UTC (deployment time)
deploy_ts = pd.to_datetime("2026-10-05 12:45:00")
new_trades = df[df["opened_dt"] >= deploy_ts]
print(f"\n=======================================================")
print(f"NEW TRADES SINCE DEPLOYMENT ({len(new_trades)} total, {len(new_trades[new_trades['status']=='OPEN'])} OPEN)")
print(f"=======================================================")
for idx, r in new_trades.iterrows():
    pnl = r["realised_pnl"]
    fees = r["total_fees_usdt"]
    gross = (pnl + fees) if pnl is not None else 0
    closed_str = r["closed_dt"].strftime("%m-%d %H:%M") if pd.notnull(r["closed_dt"]) else "STILL OPEN"
    pnl_str = f"${pnl:>7.2f}" if pnl is not None else "   N/A  "
    print(f"[{r['opened_dt'].strftime('%m-%d %H:%M')} -> {closed_str}] {r['desk']:<10} | {r['strategy_id']:<32} | {r['symbol']} {r['direction']:<5} | Status: {r['status']:<6} | Exit: {str(r['exit_reason']):<12} | Entry: {r['entry_price']} -> Exit: {str(r['exit_price'])} | Net PnL: {pnl_str} | Fees: ${fees:.2f}")

print("\n=======================================================")
print("ALL-TIME STATS BY STRATEGY (166 TRADES)")
print("=======================================================")
print(df[df["status"] == "CLOSED"].groupby("strategy_id").agg(
    trades=("position_id", "count"),
    wins=("realised_pnl", lambda x: (x > 0).sum()),
    losses=("realised_pnl", lambda x: (x <= 0).sum()),
    win_rate=("realised_pnl", lambda x: f"{(x > 0).mean()*100:.1f}%"),
    net_pnl=("realised_pnl", "sum"),
    total_fees=("total_fees_usdt", "sum"),
))

print("\n=======================================================")
print("POST-DEPLOYMENT STATS (OCT 5 12:45 UTC ONWARDS)")
print("=======================================================")
post_closed = new_trades[new_trades["status"] == "CLOSED"]
if len(post_closed) > 0:
    print(f"Post-deployment closed trades: {len(post_closed)}")
    print(f"Wins: {(post_closed['realised_pnl'] > 0).sum()} | Losses: {(post_closed['realised_pnl'] <= 0).sum()}")
    print(f"Post-Deployment Win Rate: {(post_closed['realised_pnl'] > 0).mean()*100:.1f}%")
    print(f"Post-Deployment Net PnL: ${post_closed['realised_pnl'].sum():.2f}")
    print(f"Post-Deployment Fees: ${post_closed['total_fees_usdt'].sum():.2f}")
    print("\nBreakdown by Strategy:")
    print(post_closed.groupby("strategy_id").agg(
        trades=("position_id", "count"),
        wins=("realised_pnl", lambda x: (x > 0).sum()),
        losses=("realised_pnl", lambda x: (x <= 0).sum()),
        win_rate=("realised_pnl", lambda x: f"{(x > 0).mean()*100:.1f}%"),
        net_pnl=("realised_pnl", "sum"),
        fees=("total_fees_usdt", "sum"),
    ))
    print("\nBreakdown by Exit Reason:")
    print(post_closed.groupby("exit_reason").agg(
        trades=("position_id", "count"),
        wins=("realised_pnl", lambda x: (x > 0).sum()),
        losses=("realised_pnl", lambda x: (x <= 0).sum()),
        win_rate=("realised_pnl", lambda x: f"{(x > 0).mean()*100:.1f}%"),
        net_pnl=("realised_pnl", "sum"),
        fees=("total_fees_usdt", "sum"),
    ))
else:
    print("No closed trades post-deployment.")
