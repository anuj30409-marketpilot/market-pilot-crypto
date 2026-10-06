import sqlite3
import pandas as pd

conn = sqlite3.connect("data/crypto_research_vm2.db")
trades = pd.read_sql("""
    SELECT position_id, symbol, direction, entry_price, exit_price, stop_loss_price, take_profit_price, 
           realised_pnl, exit_reason, opened_at_ms, closed_at_ms, strategy_id, desk
    FROM crypto_paper_positions 
    WHERE opened_at_ms >= 1791204300000 AND status = 'CLOSED'
    ORDER BY opened_at_ms ASC
""", conn)

print(f"--- Trade Price Excursions Analysis ({len(trades)} trades) ---")
for _, t in trades.iterrows():
    candles = pd.read_sql("""
        SELECT open_time_ms, high, low, close 
        FROM crypto_candles_1m 
        WHERE symbol = ? AND market_type = 'FUTURES' AND open_time_ms >= ? AND open_time_ms <= ?
        ORDER BY open_time_ms ASC
    """, conn, params=(t["symbol"], t["opened_at_ms"], t["closed_at_ms"]))
    
    if len(candles) > 0:
        if t["direction"] == "LONG":
            mfe_price = candles["high"].max()
            mae_price = candles["low"].min()
            mfe_bps = (mfe_price - t["entry_price"]) / t["entry_price"] * 10000
            mae_bps = (t["entry_price"] - mae_price) / t["entry_price"] * 10000
            final_bps = (t["exit_price"] - t["entry_price"]) / t["entry_price"] * 10000
        else:
            mfe_price = candles["low"].min()
            mae_price = candles["high"].max()
            mfe_bps = (t["entry_price"] - mfe_price) / t["entry_price"] * 10000
            mae_bps = (mae_price - t["entry_price"]) / t["entry_price"] * 10000
            final_bps = (t["entry_price"] - t["exit_price"]) / t["entry_price"] * 10000
        duration_m = (t["closed_at_ms"] - t["opened_at_ms"]) / 60000
        print(f"[{t['desk']:<10}] {t['symbol']} {t['direction']:<5} {t['strategy_id']:<32} | Dur: {duration_m:>4.0f}m | Exit: {t['exit_reason']:<9} | MFE: +{mfe_bps:>5.1f} bps | MAE: -{mae_bps:>5.1f} bps | Final: {final_bps:>+6.1f} bps | PnL: ${t['realised_pnl']:>6.2f}")
    else:
        print(f"[{t['desk']:<10}] {t['symbol']} {t['direction']:<5} (no candle data) | Exit: {t['exit_reason']}")

print("\n--- SUMMARY OF EXCURSIONS ---")
