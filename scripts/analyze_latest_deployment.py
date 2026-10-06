import sqlite3
import pandas as pd
import datetime

conn = sqlite3.connect("data/crypto_research_vm2.db")
df = pd.read_sql("SELECT * FROM crypto_paper_positions ORDER BY opened_at_ms ASC", conn)
print(f"Total rows in crypto_paper_positions: {len(df)}")
df["opened_dt"] = pd.to_datetime(df["opened_at_ms"], unit="ms")
df["closed_dt"] = pd.to_datetime(df["closed_at_ms"], unit="ms")

# Filter for trades opened after Oct 6 06:35 UTC (latest deployment)
deploy_ts = pd.to_datetime("2026-10-06 06:35:00")
new_trades = df[df["opened_dt"] >= deploy_ts]
print(f"\n=======================================================")
print(f"TRADES OPENED SINCE LATEST DEPLOYMENT (06:35 UTC): {len(new_trades)}")
print(f"=======================================================")
for idx, r in new_trades.iterrows():
    pnl = r["realised_pnl"]
    fees = r["total_fees_usdt"]
    closed_str = r["closed_dt"].strftime("%m-%d %H:%M") if pd.notnull(r["closed_dt"]) else "STILL OPEN"
    pnl_str = f"${pnl:>7.2f}" if pnl is not None else "   N/A  "
    print(f"[{r['opened_dt'].strftime('%m-%d %H:%M')} -> {closed_str}] {r['desk']:<10} | {r['strategy_id']:<32} | {r['symbol']} {r['direction']:<5} | Status: {r['status']:<6} | Exit: {str(r['exit_reason']):<15} | Entry: {r['entry_price']} -> Exit: {str(r['exit_price'])} | Net: {pnl_str} | Fees: ${fees:.2f}")

# Also check trades that were OPEN during deployment and closed afterwards
pre_existing_closed = df[(df["opened_dt"] < deploy_ts) & (df["closed_dt"] >= deploy_ts)]
print(f"\n=======================================================")
print(f"TRADES OPEN BEFORE 06:35 UTC THAT CLOSED AFTER DEPLOYMENT: {len(pre_existing_closed)}")
print(f"=======================================================")
for idx, r in pre_existing_closed.iterrows():
    pnl = r["realised_pnl"]
    fees = r["total_fees_usdt"]
    closed_str = r["closed_dt"].strftime("%m-%d %H:%M") if pd.notnull(r["closed_dt"]) else "STILL OPEN"
    pnl_str = f"${pnl:>7.2f}" if pnl is not None else "   N/A  "
    print(f"[{r['opened_dt'].strftime('%m-%d %H:%M')} -> {closed_str}] {r['desk']:<10} | {r['strategy_id']:<32} | {r['symbol']} {r['direction']:<5} | Status: {r['status']:<6} | Exit: {str(r['exit_reason']):<15} | Entry: {r['entry_price']} -> Exit: {str(r['exit_price'])} | Net: {pnl_str} | Fees: ${fees:.2f}")

print(f"\n=======================================================")
print("CANDIDATES EVALUATED SINCE 06:35 UTC")
print(f"=======================================================")
cands = pd.read_sql("SELECT * FROM crypto_candidate_ledger WHERE timestamp_ms >= 1791268500000 ORDER BY timestamp_ms ASC", conn)
print(f"Total candidates evaluated: {len(cands)}")
if len(cands) > 0:
    print(f"Accepted: {len(cands[cands['decision']=='ACCEPT'])} | Rejected: {len(cands[cands['decision']=='REJECT'])}")
    print("\nDecision Breakdown by Strategy:")
    print(cands.groupby(['strategy_id', 'decision']).size())
    print("\nTop Rejection Reasons:")
    print(cands['decision_reason'].value_counts().head(10))
