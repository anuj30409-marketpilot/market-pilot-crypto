import sqlite3
import pandas as pd
import datetime

conn = sqlite3.connect('data/crypto_research_vm2.db')
df = pd.read_sql_query('SELECT * FROM crypto_paper_positions WHERE status = "CLOSED" AND candidate_id NOT LIKE "%TEST%"', conn)

for col in ['realised_pnl', 'total_fees_usdt', 'entry_slippage_bps', 'exit_slippage_bps', 'notional_usdt', 'margin_usdt']:
    df[col] = pd.to_numeric(df[col], errors='coerce')

df['opened_time'] = pd.to_datetime(df['opened_at_ms'], unit='ms')
df['closed_time'] = pd.to_datetime(df['closed_at_ms'], unit='ms')

print("=" * 100)
print("1. TRADES IN TRENDING_UP REGIME")
print("=" * 100)
up_trades = df[df['regime'] == 'TRENDING_UP']
print(up_trades[['strategy_id', 'symbol', 'direction', 'entry_price', 'exit_price', 'exit_reason', 'realised_pnl', 'opened_time']].to_string())

print("\n" + "=" * 100)
print("2. ETHUSDT SHORT TRADES")
print("=" * 100)
eth_shorts = df[(df['symbol'] == 'ETHUSDT') & (df['direction'] == 'SHORT')]
print(eth_shorts[['strategy_id', 'entry_price', 'exit_price', 'exit_reason', 'realised_pnl', 'regime', 'opened_time']].to_string())

print("\n" + "=" * 100)
print("3. ALL STOP LOSS TRADES (WHERE 100% OF DESTRUCTIVE LOSSES OCCUR)")
print("=" * 100)
sl_trades = df[df['exit_reason'] == 'STOP_LOSS']
print(sl_trades[['desk', 'strategy_id', 'symbol', 'direction', 'entry_price', 'stop_loss_price', 'exit_price', 'realised_pnl', 'regime', 'opened_time']].to_string())

print("\n" + "=" * 100)
print("4. CORRELATION & CO-TRIGGERING (SIMULTANEOUS TRADES)")
print("=" * 100)
# Check how many trades were open at the same time
df['open_epoch_s'] = df['opened_at_ms'] // 1000
open_clusters = df.groupby(['open_epoch_s', 'symbol']).size()
print(f"Max trades opened in exact same second: {open_clusters.max()}")
print(f"Clusters with > 1 trade opened simultaneously: {(open_clusters > 1).sum()}")

conn.close()
