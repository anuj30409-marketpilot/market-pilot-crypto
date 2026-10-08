import sqlite3

conn = sqlite3.connect('data/crypto_pilot_latest.db')
c = conn.cursor()

v2_start_ms = 1791268500000
deploy_ms = 1791359600000

for label, s_ms, e_ms in [
    ('PHASE 1 (Oct 6 06:35 -> Oct 7 07:53 - 25.3 hours)', v2_start_ms, deploy_ms), 
    ('PHASE 2 (Oct 7 07:53 -> Oct 8 14:35 - 30.7 hours)', deploy_ms, 9999999999999)
]:
    rows = c.execute('''
        SELECT realised_pnl, total_fees_usdt, exit_reason, strategy_id, symbol, direction,
               entry_price, exit_price, (closed_at_ms - opened_at_ms)/1000/60.0, opened_at_ms
        FROM crypto_paper_positions 
        WHERE opened_at_ms >= ? AND opened_at_ms < ? AND status = 'CLOSED'
        ORDER BY opened_at_ms ASC
    ''', (s_ms, e_ms)).fetchall()
    
    wins = [r for r in rows if r[0] and r[0] > 0]
    losses = [r for r in rows if r[0] and r[0] <= 0]
    net_pnl = sum(r[0] for r in rows if r[0])
    fees = sum(r[1] for r in rows if r[1])
    win_pnl = sum(r[0] for r in wins)
    loss_pnl = abs(sum(r[0] for r in losses))
    avg_w = win_pnl/len(wins) if wins else 0
    avg_l = loss_pnl/len(losses) if losses else 0
    pf = win_pnl/loss_pnl if loss_pnl else 0
    payoff = avg_w/avg_l if avg_l else 0
    win_rate = len(wins)/len(rows)*100 if rows else 0
    
    print(f'=== {label} ===')
    print(f'Trades: {len(rows)} | Wins: {len(wins)} ({win_rate:.2f}%) | Losses: {len(losses)}')
    print(f'Net PnL: ${net_pnl:.2f} | Fees: ${fees:.2f} | Profit Factor: {pf:.2f}x | Payoff: {payoff:.2f}:1')
    print(f'Avg Win: ${avg_w:.2f} | Avg Loss: ${avg_l:.2f}')
    
    reasons = {}
    for r in rows:
        reasons[r[2]] = reasons.get(r[2], 0) + 1
    print(f'Exits: {reasons}')
    
    strats = {}
    for r in rows:
        strat = r[3].replace('STRAT_', '').replace('_V1', '')
        strats.setdefault(strat, []).append(r)
    print('Strategies:')
    for st, slist in strats.items():
        sw = [r for r in slist if r[0] > 0]
        spnl = sum(r[0] for r in slist)
        print(f'  {st:25s}: {len(slist):2d} trades | {len(sw):2d} wins ({len(sw)/len(slist)*100:.1f}%) | PnL: ${spnl:+6.2f}')
    print()
