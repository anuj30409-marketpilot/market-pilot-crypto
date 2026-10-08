import sqlite3
import datetime

conn = sqlite3.connect('data/crypto_pilot_latest.db')
c = conn.cursor()

deploy_ms = 1791359600000

# Fetch all closed trades in Phase 2
trades = c.execute("""
    SELECT position_id, symbol, direction, desk, strategy_id, entry_price, exit_price, 
           realised_pnl, total_fees_usdt, exit_reason, opened_at_ms, closed_at_ms, notional_usdt
    FROM crypto_paper_positions 
    WHERE opened_at_ms >= ? AND status = "CLOSED"
    ORDER BY opened_at_ms ASC
""", (deploy_ms,)).fetchall()

print("=" * 100)
print(f"EXCURSION & COUNTERFACTUAL AUDIT FOR {len(trades)} TRADES IN PHASE 2 (POST OCT 7 DEPLOY)")
print("=" * 100)

premature_stagnation_cut_count = 0
stagnation_would_have_won = 0
stagnation_would_have_tp = 0
stagnation_would_have_sl = 0

for t in trades:
    pid, sym, dirn, desk, strat, ep, xp, pnl, fee, rsn, op, cl, notional = t
    dur_m = (cl - op) / 1000 / 60.0
    
    # Load candles during position and up to 4 hours after entry
    post_end_ms = op + (4 * 3600 * 1000)
    candles = c.execute("""
        SELECT open_time_ms, open, high, low, close
        FROM crypto_candles_1m
        WHERE symbol = ? AND open_time_ms >= ? AND open_time_ms <= ?
        ORDER BY open_time_ms ASC
    """, (sym, op, post_end_ms)).fetchall()
    
    if not candles:
        continue
        
    # MFE and MAE during trade lifetime
    mfe_bps = 0.0
    mae_bps = 0.0
    mfe_time_m = 0
    mae_time_m = 0
    
    # Counterfactual MFE/MAE up to 2 hours and 4 hours
    mfe_2h_bps = 0.0
    mae_2h_bps = 0.0
    
    for row in candles:
        t_ms, c_open, c_high, c_low, c_close = row
        mins = (t_ms - op) / 1000 / 60.0
        
        if dirn == "LONG":
            high_bps = (c_high - ep) / ep * 10000
            low_bps = (c_low - ep) / ep * 10000
        else:
            high_bps = (ep - c_low) / ep * 10000
            low_bps = (ep - c_high) / ep * 10000
            
        if mins <= dur_m:
            if high_bps > mfe_bps:
                mfe_bps = high_bps
                mfe_time_m = mins
            if low_bps < mae_bps:
                mae_bps = low_bps
                mae_time_m = mins
                
        if mins <= 120:
            if high_bps > mfe_2h_bps:
                mfe_2h_bps = high_bps
            if low_bps < mae_2h_bps:
                mae_2h_bps = low_bps
                
    short_strat = strat.replace("STRAT_", "").replace("_V1", "")
    dt_open = datetime.datetime.utcfromtimestamp(op/1000).strftime("%m-%d %H:%M")
    
    # Counterfactual analysis for STAGNATION_EXIT
    cf_note = ""
    if rsn == "STAGNATION_EXIT":
        premature_stagnation_cut_count += 1
        if mfe_2h_bps >= 120.0:
            stagnation_would_have_tp += 1
            cf_note = f"[WOULD HIT TP] (+{mfe_2h_bps:.0f}bps within 2h!)"
        elif mfe_2h_bps >= 40.0:
            stagnation_would_have_won += 1
            cf_note = f"[PROFITABLE RUNNER] (Reached +{mfe_2h_bps:.0f}bps in 2h)"
        elif mae_2h_bps <= -60.0:
            stagnation_would_have_sl += 1
            cf_note = f"[WOULD HIT SL] ({mae_2h_bps:.0f}bps)"
        else:
            cf_note = f"[CHOP] ({mae_2h_bps:.0f} to +{mfe_2h_bps:.0f}bps)"

    print(f"{dt_open} | {sym:8s} {dirn:5s} | {short_strat:24s} | {rsn:16s} ({dur_m:4.0f}m) | PnL: ${pnl:+6.2f} | MFE: +{mfe_bps:5.1f}bps | MAE: {mae_bps:5.1f}bps | {cf_note}")

print("\n" + "=" * 100)
print(f"SUMMARY OF STAGNATION EXITS ({premature_stagnation_cut_count} trades):")
print(f"  - Would have hit Take Profit (+120 bps) if given 2h: {stagnation_would_have_tp} ({stagnation_would_have_tp/premature_stagnation_cut_count*100:.1f}%)")
print(f"  - Would have reached +40 to +119 bps within 2h   : {stagnation_would_have_won} ({stagnation_would_have_won/premature_stagnation_cut_count*100:.1f}%)")
print(f"  - Would have hit full Stop Loss (-60 bps) in 2h   : {stagnation_would_have_sl} ({stagnation_would_have_sl/premature_stagnation_cut_count*100:.1f}%)")
print(f"  - Stayed in chop between -60 and +40 bps in 2h    : {premature_stagnation_cut_count - stagnation_would_have_tp - stagnation_would_have_won - stagnation_would_have_sl}")
