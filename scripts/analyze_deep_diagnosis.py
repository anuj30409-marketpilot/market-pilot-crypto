import sqlite3
import pandas as pd
import numpy as np

conn = sqlite3.connect('data/crypto_research_vm2.db')
df = pd.read_sql("SELECT * FROM crypto_paper_positions WHERE candidate_id NOT LIKE '%TEST%'", conn)

numeric_cols = ['realised_pnl', 'total_fees_usdt', 'notional_usdt', 'entry_price', 'exit_price', 
                'stop_loss_price', 'take_profit_price', 'entry_slippage_bps', 'exit_slippage_bps']
for col in numeric_cols:
    df[col] = pd.to_numeric(df[col], errors='coerce')

df['opened_time'] = pd.to_datetime(df['opened_at_ms'], unit='ms')
df['closed_time'] = pd.to_datetime(df['closed_at_ms'], unit='ms')
df['duration_mins'] = (df['closed_at_ms'] - df['opened_at_ms']) / (60 * 1000)

closed = df[df['status'] == 'CLOSED'].copy()

print("=" * 80)
print("TOTAL PERFORMANCE BREAKDOWN")
print("=" * 80)
print(f"Total Trades: {len(closed)}")
print(f"Total Realised Net PnL: {closed['realised_pnl'].sum():.2f} USDT")
print(f"Total Fees Incurred: {closed['total_fees_usdt'].sum():.2f} USDT")
print(f"Gross PnL (before fees): {(closed['realised_pnl'] + closed['total_fees_usdt']).sum():.2f} USDT")
print(f"Overall Win Rate: {(closed['realised_pnl'] > 0).mean()*100:.1f}%")

print("\n" + "=" * 80)
print("STRATEGY PERFORMANCE BREAKDOWN")
print("=" * 80)
for strat, grp in closed.groupby('strategy_id'):
    wins = grp[grp['realised_pnl'] > 0]
    losses = grp[grp['realised_pnl'] <= 0]
    n_win = len(wins)
    n_loss = len(losses)
    n_tot = len(grp)
    pnl = grp['realised_pnl'].sum()
    fees = grp['total_fees_usdt'].sum()
    gross = pnl + fees
    avg_win = wins['realised_pnl'].mean() if n_win > 0 else 0
    avg_loss = losses['realised_pnl'].mean() if n_loss > 0 else 0
    pf = abs(wins['realised_pnl'].sum() / losses['realised_pnl'].sum()) if losses['realised_pnl'].sum() != 0 else 0
    print(f"\nStrategy: {strat}")
    print(f"  Trades: {n_tot} | Win: {n_win} | Loss: {n_loss} | Win Rate: {n_win/n_tot*100:.1f}%")
    print(f"  Net PnL: {pnl:.2f} USDT | Gross PnL: {gross:.2f} USDT | Total Fees: {fees:.2f} USDT")
    print(f"  Avg Win: +{avg_win:.2f} USDT | Avg Loss: {avg_loss:.2f} USDT | Payoff Ratio: {abs(avg_win/avg_loss) if avg_loss!=0 else 0:.2f}")
    print(f"  Profit Factor: {pf:.2f}")
    print(f"  Exits: {dict(grp['exit_reason'].value_counts())}")

print("\n" + "=" * 80)
print("DEEP DIVE: STOP LOSS VS TIME STOP VS TAKE PROFIT")
print("=" * 80)
print(closed.groupby('exit_reason').agg(
    trades=('position_id', 'count'),
    net_pnl=('realised_pnl', 'sum'),
    avg_pnl=('realised_pnl', 'mean'),
    fees=('total_fees_usdt', 'sum'),
    win_rate=('realised_pnl', lambda x: f"{(x > 0).mean()*100:.1f}%"),
    avg_duration_mins=('duration_mins', 'mean')
))

print("\n" + "=" * 80)
print("DEEP DIVE: PERFORMANCE BY MARKET REGIME")
print("=" * 80)
print(closed.groupby('regime').agg(
    trades=('position_id', 'count'),
    net_pnl=('realised_pnl', 'sum'),
    fees=('total_fees_usdt', 'sum'),
    win_rate=('realised_pnl', lambda x: f"{(x > 0).mean()*100:.1f}%"),
    avg_pnl=('realised_pnl', 'mean')
))

print("\n" + "=" * 80)
print("DEEP DIVE: ALL STOP-LOSS TRADES (ROOT CAUSE OF DRAWDOWN)")
print("=" * 80)
sl_df = closed[closed['exit_reason'] == 'STOP_LOSS']
print(sl_df[['opened_time', 'desk', 'strategy_id', 'symbol', 'direction', 'regime', 'entry_price', 'exit_price', 'realised_pnl', 'duration_mins']].to_string())

print("\n" + "=" * 80)
print("DEEP DIVE: TIME STOP TRADES (DRAG VS PROFIT)")
print("=" * 80)
ts_df = closed[closed['exit_reason'] == 'TIME_STOP']
print(f"Time Stop trades: {len(ts_df)}")
print(f"Time Stop Net PnL: {ts_df['realised_pnl'].sum():.2f} USDT")
print(f"Time Stop Gross PnL: {(ts_df['realised_pnl'] + ts_df['total_fees_usdt']).sum():.2f} USDT")
print(f"Time Stop Total Fees: {ts_df['total_fees_usdt'].sum():.2f} USDT")
print(f"Time Stop Avg Net PnL per trade: {ts_df['realised_pnl'].mean():.2f} USDT")

conn.close()
