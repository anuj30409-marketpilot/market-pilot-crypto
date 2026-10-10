import sqlite3
import datetime

conn = sqlite3.connect("data/crypto_research.db")
c = conn.cursor()

# Get stagnation trades
c.execute("""
    SELECT position_id, symbol, direction, entry_price, exit_price, stop_loss_price, take_profit_price,
           opened_at_ms, closed_at_ms, realised_pnl, notional_usdt, strategy_id
    FROM crypto_paper_positions
    WHERE exit_reason = 'STAGNATION_EXIT' AND opened_at_ms >= 1791471100000
    ORDER BY opened_at_ms ASC
""")
stag_trades = c.fetchall()

print(f"=== COUNTERFACTUAL AUDIT OF {len(stag_trades)} STAGNATION EXITS ===")
for t in stag_trades:
    pid, sym, dirn, ep, xp, sl, tp, op, cl, pnl, notional, strat = t
    short_strat = strat.replace("STRAT_", "").replace("_V1", "")
    op_dt = datetime.datetime.utcfromtimestamp(op/1000).strftime("%m-%d %H:%M")
    
    # Query candles after closure up to 4 hours later (or max hold)
    c.execute("""
        SELECT open_time_ms, high, low, close
        FROM crypto_candles_1m
        WHERE symbol = ? AND open_time_ms >= ? AND open_time_ms <= ?
        ORDER BY open_time_ms ASC
    """, (sym, cl, cl + 4 * 3600 * 1000))
    candles = c.fetchall()
    
    if not candles:
        print(f"[{op_dt}] {sym} {dirn} ({short_strat}) - No subsequent candles found")
        continue

    # Did it hit SL first, TP first, or what was max favorable excursion (MFE)?
    hit_sl = False
    hit_tp = False
    best_move_bps = 0.0
    worst_move_bps = 0.0
    price_at_2h = None # price at 120m from open
    two_hr_ms = op + 2 * 3600 * 1000

    for cd in candles:
        t_ms, chigh, clow, cclose = cd
        if t_ms <= two_hr_ms:
            price_at_2h = cclose
            
        if dirn == "LONG":
            move_h = (chigh - ep) / ep * 10000
            move_l = (clow - ep) / ep * 10000
            best_move_bps = max(best_move_bps, move_h)
            worst_move_bps = min(worst_move_bps, move_l)
            if sl and clow <= sl:
                hit_sl = True
                break
            if tp and chigh >= tp:
                hit_tp = True
                break
        else:
            move_h = (ep - clow) / ep * 10000
            move_l = (ep - chigh) / ep * 10000
            best_move_bps = max(best_move_bps, move_h)
            worst_move_bps = min(worst_move_bps, move_l)
            if sl and chigh >= sl:
                hit_sl = True
                break
            if tp and clow <= tp:
                hit_tp = True
                break

    move_at_exit = (xp - ep)/ep * 10000 if dirn == "LONG" else (ep - xp)/ep * 10000
    move_at_2h = ((price_at_2h - ep)/ep * 10000 if dirn == "LONG" else (ep - price_at_2h)/ep * 10000) if price_at_2h else None
    
    print(f"[{op_dt}] {sym} {dirn} ({short_strat}) | Actual PnL: ${pnl:.2f} (Move: {move_at_exit:+.1f} bps)")
    print(f"      Subsequent 4h: Best: {best_move_bps:+.1f} bps | Worst: {worst_move_bps:+.1f} bps | Hit SL: {hit_sl} | Hit TP: {hit_tp}")
    if move_at_2h is not None:
        pnl_at_2h = (move_at_2h / 10000.0) * notional - 2.0 # approx fees
        print(f"      At 2h (normal time stop): Move: {move_at_2h:+.1f} bps -> Projected PnL: ${pnl_at_2h:.2f}")
