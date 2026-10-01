import sqlite3
import pandas as pd
import numpy as np
import datetime

db_path = "data/crypto_research_vm2.db"
conn = sqlite3.connect(db_path)

print("=" * 80)
print("1. DATABASE TABLES SUMMARY")
print("=" * 80)
tables = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table'", conn)
for t in tables['name']:
    cnt = pd.read_sql_query(f"SELECT count(*) as cnt FROM {t}", conn)['cnt'].iloc[0]
    print(f"  {t:<35}: {cnt:>10,d} rows")

print("\n" + "=" * 80)
print("2. PAPER TRADES & POSITIONS ANALYSIS")
print("=" * 80)
df_pos = pd.read_sql_query("SELECT * FROM crypto_paper_positions", conn)
print(f"Total rows: {len(df_pos)}")

print("\n--- Positions by Desk & Status ---")
print(df_pos.groupby(['desk', 'status']).size())

print("\n--- Positions by Strategy & Desk ---")
strat_summary = df_pos.groupby(['desk', 'strategy_id', 'status']).size().unstack(fill_value=0)
print(strat_summary)

closed = df_pos[df_pos['status'] == 'CLOSED'].copy()
print(f"\nTotal Closed Positions: {len(closed)}")

for col in ['realised_pnl', 'total_fees_usdt', 'entry_slippage_bps', 'exit_slippage_bps', 
            'entry_price', 'exit_price', 'notional_usdt', 'margin_usdt', 'funding_paid_usdt']:
    if col in closed.columns:
        closed[col] = pd.to_numeric(closed[col], errors='coerce')

# Calculate hold time in minutes
if 'opened_at_ms' in closed.columns and 'closed_at_ms' in closed.columns:
    closed['hold_duration_mins'] = (closed['closed_at_ms'] - closed['opened_at_ms']) / (1000 * 60)

pd.set_option('display.max_columns', 15)
pd.set_option('display.width', 1000)

print("\n--- Desk Level Performance (Closed Trades) ---")
desk_perf = closed.groupby('desk').agg(
    trades=('position_id', 'count'),
    wins=('realised_pnl', lambda x: (x > 0).sum()),
    losses=('realised_pnl', lambda x: (x <= 0).sum()),
    win_rate_pct=('realised_pnl', lambda x: (x > 0).mean() * 100),
    net_pnl_usd=('realised_pnl', 'sum'),
    mean_pnl_usd=('realised_pnl', 'mean'),
    median_pnl_usd=('realised_pnl', 'median'),
    total_fees_usd=('total_fees_usdt', 'sum'),
    avg_hold_mins=('hold_duration_mins', 'mean')
)
print(desk_perf)

print("\n--- Strategy Level Performance (Closed Trades) ---")
strat_perf = closed.groupby(['desk', 'strategy_id']).agg(
    trades=('position_id', 'count'),
    wins=('realised_pnl', lambda x: (x > 0).sum()),
    losses=('realised_pnl', lambda x: (x <= 0).sum()),
    win_rate_pct=('realised_pnl', lambda x: (x > 0).mean() * 100),
    net_pnl=('realised_pnl', 'sum'),
    mean_pnl=('realised_pnl', 'mean'),
    max_win=('realised_pnl', 'max'),
    max_loss=('realised_pnl', 'min'),
    total_fees=('total_fees_usdt', 'sum'),
    avg_hold_mins=('hold_duration_mins', 'mean')
)
print(strat_perf)

print("\n--- Exit Reason Breakdown by Strategy ---")
exit_perf = closed.groupby(['desk', 'strategy_id', 'exit_reason']).agg(
    count=('position_id', 'count'),
    win_count=('realised_pnl', lambda x: (x > 0).sum()),
    sum_pnl=('realised_pnl', 'sum'),
    mean_pnl=('realised_pnl', 'mean')
)
print(exit_perf.to_string())

print("\n--- Symbol Breakdown ---")
sym_perf = closed.groupby(['symbol', 'direction']).agg(
    count=('position_id', 'count'),
    win_rate=('realised_pnl', lambda x: (x > 0).mean() * 100),
    sum_pnl=('realised_pnl', 'sum'),
    mean_pnl=('realised_pnl', 'mean')
)
print(sym_perf)

print("\n" + "=" * 80)
print("3. CANDIDATE LEDGER ANALYSIS (SIGNALS, ACCEPTANCES, REJECTIONS)")
print("=" * 80)
cand_summary = pd.read_sql_query("""
    SELECT origin, strategy_id, decision, count(*) as count,
           round(avg(expected_edge_bps), 2) as avg_edge_bps,
           round(avg(estimated_cost_bps), 2) as avg_cost_bps,
           round(avg(expected_net_edge_bps), 2) as avg_net_edge_bps
    FROM crypto_candidate_ledger
    GROUP BY origin, strategy_id, decision
    ORDER BY origin, strategy_id, decision
""", conn)
print(cand_summary.to_string())

print("\n--- Rejection Code Breakdown (Top 25) ---")
rejections = pd.read_sql_query("""
    SELECT rejection_codes, count(*) as count
    FROM crypto_candidate_ledger
    WHERE decision = 'REJECT'
    GROUP BY rejection_codes
    ORDER BY count DESC
    LIMIT 25
""", conn)
print(rejections.to_string())

print("\n--- Regimes Observed ---")
regimes = pd.read_sql_query("""
    SELECT regime, count(*) as count,
           round(100.0 * count(*) / (SELECT count(*) FROM crypto_candidate_ledger), 2) as pct
    FROM crypto_candidate_ledger
    GROUP BY regime
    ORDER BY count DESC
""", conn)
print(regimes.to_string())

print("\n" + "=" * 80)
print("4. OPEN POSITIONS CURRENT STATUS")
print("=" * 80)
open_pos = df_pos[df_pos['status'] == 'OPEN'].copy()
if len(open_pos) > 0:
    for col in ['entry_price', 'mark_price', 'unrealised_pnl', 'notional_usdt', 'stop_loss_price', 'take_profit_price']:
        open_pos[col] = pd.to_numeric(open_pos[col], errors='coerce')
    cols_to_show = ['position_id', 'desk', 'strategy_id', 'symbol', 'direction', 'notional_usdt', 'entry_price', 'mark_price', 'unrealised_pnl', 'stop_loss_price', 'take_profit_price']
    print(open_pos[cols_to_show].to_string())
else:
    print("No open positions.")

print("\n" + "=" * 80)
print("5. DEEP DIVE: TAKE PROFIT LOSING TRADES")
print("=" * 80)
tp_losers = closed[(closed['exit_reason'] == 'TAKE_PROFIT') & (closed['realised_pnl'] <= 0)]
print(f"Number of TP trades with PnL <= 0: {len(tp_losers)}")
if len(tp_losers) > 0:
    cols_tp = ['position_id', 'candidate_id', 'symbol', 'direction', 'entry_price', 'exit_price', 'take_profit_price', 'realised_pnl', 'total_fees_usdt', 'hold_duration_mins']
    print(tp_losers[cols_tp].head(15).to_string())

print("\n" + "=" * 80)
print("6. DEEP DIVE: STOP LOSS TRADES")
print("=" * 80)
sl_trades = closed[closed['exit_reason'] == 'STOP_LOSS']
print(f"Number of SL trades: {len(sl_trades)}")
if len(sl_trades) > 0:
    cols_sl = ['position_id', 'strategy_id', 'symbol', 'direction', 'entry_price', 'exit_price', 'stop_loss_price', 'realised_pnl', 'total_fees_usdt', 'hold_duration_mins']
    print(sl_trades[cols_sl].head(15).to_string())

conn.close()
