import sqlite3
import pandas as pd
import numpy as np

conn = sqlite3.connect('data/crypto_research_vm2.db')
df = pd.read_sql_query('SELECT * FROM crypto_paper_positions WHERE status = "CLOSED" AND candidate_id NOT LIKE "%TEST%"', conn)

for col in ['realised_pnl', 'total_fees_usdt', 'entry_slippage_bps', 'exit_slippage_bps', 'notional_usdt', 'margin_usdt']:
    df[col] = pd.to_numeric(df[col], errors='coerce')

df['hold_duration_mins'] = (df['closed_at_ms'] - df['opened_at_ms']) / (1000 * 60)

pd.set_option('display.max_columns', 20)
pd.set_option('display.width', 1200)

print("=" * 90)
print("REAL CLOSED TRADES PERFORMANCE BY STRATEGY")
print("=" * 90)
strat_agg = df.groupby(['desk', 'strategy_id']).agg(
    trades=('position_id', 'count'),
    wins=('realised_pnl', lambda x: (x > 0).sum()),
    losses=('realised_pnl', lambda x: (x <= 0).sum()),
    win_rate=('realised_pnl', lambda x: f"{round((x > 0).mean()*100, 1)}%"),
    net_pnl=('realised_pnl', lambda x: round(x.sum(), 2)),
    avg_pnl=('realised_pnl', lambda x: round(x.mean(), 2)),
    total_fees=('total_fees_usdt', lambda x: round(x.sum(), 2)),
    avg_hold_mins=('hold_duration_mins', lambda x: round(x.mean(), 1))
)
print(strat_agg)

print("\n" + "=" * 90)
print("REAL CLOSED TRADES BY STRATEGY AND EXIT REASON")
print("=" * 90)
exit_agg = df.groupby(['desk', 'strategy_id', 'exit_reason']).agg(
    trades=('position_id', 'count'),
    wins=('realised_pnl', lambda x: (x > 0).sum()),
    losses=('realised_pnl', lambda x: (x <= 0).sum()),
    win_rate=('realised_pnl', lambda x: f"{round((x > 0).mean()*100, 1)}%"),
    net_pnl=('realised_pnl', lambda x: round(x.sum(), 2)),
    avg_pnl=('realised_pnl', lambda x: round(x.mean(), 2)),
    total_fees=('total_fees_usdt', lambda x: round(x.sum(), 2)),
    avg_hold_mins=('hold_duration_mins', lambda x: round(x.mean(), 1))
)
print(exit_agg)

print("\n" + "=" * 90)
print("REAL CLOSED TRADES BY SYMBOL & DIRECTION")
print("=" * 90)
sym_agg = df.groupby(['symbol', 'direction']).agg(
    trades=('position_id', 'count'),
    wins=('realised_pnl', lambda x: (x > 0).sum()),
    losses=('realised_pnl', lambda x: (x <= 0).sum()),
    win_rate=('realised_pnl', lambda x: f"{round((x > 0).mean()*100, 1)}%"),
    net_pnl=('realised_pnl', lambda x: round(x.sum(), 2)),
    avg_pnl=('realised_pnl', lambda x: round(x.mean(), 2)),
    total_fees=('total_fees_usdt', lambda x: round(x.sum(), 2))
)
print(sym_agg)

print("\n" + "=" * 90)
print("REAL TRADES BY REGIME")
print("=" * 90)
regime_agg = df.groupby(['regime']).agg(
    trades=('position_id', 'count'),
    wins=('realised_pnl', lambda x: (x > 0).sum()),
    losses=('realised_pnl', lambda x: (x <= 0).sum()),
    win_rate=('realised_pnl', lambda x: f"{round((x > 0).mean()*100, 1)}%"),
    net_pnl=('realised_pnl', lambda x: round(x.sum(), 2)),
    avg_pnl=('realised_pnl', lambda x: round(x.mean(), 2))
)
print(regime_agg)

print("\n" + "=" * 90)
print("AVERAGE WIN VS AVERAGE LOSS")
print("=" * 90)
winners = df[df['realised_pnl'] > 0]
losers = df[df['realised_pnl'] <= 0]
print(f"Total Winners: {len(winners)} | Total Losers: {len(losers)}")
print(f"Avg Win: ${winners['realised_pnl'].mean():.2f} | Avg Loss: ${losers['realised_pnl'].mean():.2f}")
print(f"Profit Factor: {abs(winners['realised_pnl'].sum() / losers['realised_pnl'].sum()):.3f}")
print(f"Gross PnL (before fees): ${df['realised_pnl'].sum() + df['total_fees_usdt'].sum():.2f}")
print(f"Total Fees Paid: ${df['total_fees_usdt'].sum():.2f}")
print(f"Net Realized PnL: ${df['realised_pnl'].sum():.2f}")

conn.close()
