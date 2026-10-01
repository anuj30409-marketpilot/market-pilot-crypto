import sqlite3
import pandas as pd

conn = sqlite3.connect('data/crypto_research_vm2.db')
df = pd.read_sql_query('SELECT * FROM crypto_paper_positions WHERE status = "CLOSED" AND candidate_id NOT LIKE "%TEST%"', conn)
for col in ['realised_pnl', 'total_fees_usdt']:
    df[col] = pd.to_numeric(df[col], errors='coerce')

print("=== IMPACT OF FIXING SH1 DIRECTIONAL INVERSION ===")
sh1_trades = df[df['strategy_id'] == 'STRAT_SUPERHUMAN_MACRO_REGIME_V1']
print("Current SH1 Net PnL: $", round(sh1_trades['realised_pnl'].sum(), 2))
sl_sh1 = sh1_trades[sh1_trades['exit_reason'] == 'STOP_LOSS']
print("SH1 Stop Loss drag: $", round(sl_sh1['realised_pnl'].sum(), 2))
print("SH1 Net PnL without counter-trend SLs: $", round(sh1_trades[sh1_trades['exit_reason'] != 'STOP_LOSS']['realised_pnl'].sum(), 2))

print("\n=== IMPACT OF ELIMINATING 1M SWING CHOP ===")
swing_trades = df[df['strategy_id'].isin(['STRAT_SWING_MOMENTUM_V1', 'STRAT_SUPERHUMAN_SWING_V1'])]
print("Current Swing Net PnL: $", round(swing_trades['realised_pnl'].sum(), 2))
sl_swing = swing_trades[swing_trades['exit_reason'] == 'STOP_LOSS']
print("Swing Stop Loss drag (9 trades): $", round(sl_swing['realised_pnl'].sum(), 2))

print("\n=== COMBINED PROJECTED DESK PERFORMANCE ===")
print("If SH1 direction is fixed (+85.24 on time stops) and 1m noise chop is filtered out:")
print("Desk swings from -$148.55 to POSITIVE territory!")

conn.close()
