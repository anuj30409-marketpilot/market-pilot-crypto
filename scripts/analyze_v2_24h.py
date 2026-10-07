import sqlite3
import pandas as pd
import datetime

conn = sqlite3.connect("data/crypto_research_vm2.db")
df = pd.read_sql("SELECT * FROM crypto_paper_positions ORDER BY opened_at_ms ASC", conn)
df["opened_dt"] = pd.to_datetime(df["opened_at_ms"], unit="ms")
df["closed_dt"] = pd.to_datetime(df["closed_at_ms"], unit="ms")

v2_ts = pd.to_datetime("2026-10-06 06:35:00")
v2_trades = df[df["opened_dt"] >= v2_ts]

print("==================================================================")
print(f"COMPLETE V2 ENGINE AUDIT (SINCE OCT 6 06:35 UTC) — {len(v2_trades)} POSITIONS")
print("==================================================================")
closed_v2 = v2_trades[v2_trades["status"] == "CLOSED"]
open_v2 = v2_trades[v2_trades["status"] == "OPEN"]

print(f"Total V2 Positions: {len(v2_trades)} | Closed: {len(closed_v2)} | Open: {len(open_v2)}")
if len(closed_v2) > 0:
    wins = closed_v2[closed_v2["realised_pnl"] > 0]
    losses = closed_v2[closed_v2["realised_pnl"] <= 0]
    print(f"Wins: {len(wins)} | Losses: {len(losses)} | Win Rate: {len(wins)/len(closed_v2)*100:.1f}%")
    print(f"Total Net PnL: ${closed_v2['realised_pnl'].sum():.2f}")
    print(f"Total Fees Paid: ${closed_v2['total_fees_usdt'].sum():.2f}")
    print(f"Gross PnL (before fees): ${closed_v2['realised_pnl'].sum() + closed_v2['total_fees_usdt'].sum():.2f}")

print("\n------------------------------------------------------------------")
print("EVERY V2 TRADE IN CHRONOLOGICAL ORDER:")
print("------------------------------------------------------------------")
for idx, r in v2_trades.iterrows():
    pnl = r["realised_pnl"]
    fees = r["total_fees_usdt"]
    dur_m = (r["closed_at_ms"] - r["opened_at_ms"]) / 60000 if pd.notnull(r["closed_at_ms"]) else (datetime.datetime.now(datetime.timezone.utc).timestamp()*1000 - r["opened_at_ms"]) / 60000
    closed_str = r["closed_dt"].strftime("%m-%d %H:%M") if pd.notnull(r["closed_dt"]) else "STILL OPEN"
    pnl_str = f"${pnl:>7.2f}" if pnl is not None else "   N/A  "
    print(f"[{r['opened_dt'].strftime('%m-%d %H:%M')} -> {closed_str} ({dur_m:>4.0f}m)] {r['desk']:<10} | {r['strategy_id']:<32} | {r['symbol']} {r['direction']:<5} | Regime: {r['regime']:<14} | Exit: {str(r['exit_reason']):<15} | Entry: {r['entry_price']} -> Exit: {str(r['exit_price'])} | Net: {pnl_str} | Fees: ${fees:.2f}")

print("\n------------------------------------------------------------------")
print("BREAKDOWN BY STRATEGY (V2):")
print("------------------------------------------------------------------")
if len(closed_v2) > 0:
    print(closed_v2.groupby("strategy_id").agg(
        trades=("position_id", "count"),
        wins=("realised_pnl", lambda x: (x > 0).sum()),
        losses=("realised_pnl", lambda x: (x <= 0).sum()),
        win_rate=("realised_pnl", lambda x: f"{(x > 0).mean()*100:.1f}%"),
        net_pnl=("realised_pnl", "sum"),
        fees=("total_fees_usdt", "sum"),
    ))

print("\n------------------------------------------------------------------")
print("BREAKDOWN BY EXIT REASON (V2):")
print("------------------------------------------------------------------")
if len(closed_v2) > 0:
    print(closed_v2.groupby("exit_reason").agg(
        trades=("position_id", "count"),
        wins=("realised_pnl", lambda x: (x > 0).sum()),
        losses=("realised_pnl", lambda x: (x <= 0).sum()),
        win_rate=("realised_pnl", lambda x: f"{(x > 0).mean()*100:.1f}%"),
        net_pnl=("realised_pnl", "sum"),
        fees=("total_fees_usdt", "sum"),
    ))
