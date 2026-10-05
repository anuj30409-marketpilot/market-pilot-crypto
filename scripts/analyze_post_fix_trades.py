import sqlite3
import pandas as pd
import datetime

db_path = "data/crypto_research_vm2.db"
conn = sqlite3.connect(db_path)

deploy_time_ms = 1790876973000  # 2026-10-01 17:49:33 UTC

print("=" * 80)
print("POST-FIX LIVE PERFORMANCE AUDIT (DEVICES ON VM2 SINCE 2026-10-01 17:49 UTC)")
print("=" * 80)

# Check all positions opened after deploy
query_all = f"""
SELECT position_id, desk, strategy_id, symbol, direction, notional_usdt,
       entry_price, exit_price, realised_pnl, total_fees_usdt, exit_reason, status,
       opened_at_ms, closed_at_ms, candidate_id
FROM crypto_paper_positions
WHERE opened_at_ms >= {deploy_time_ms} AND candidate_id NOT LIKE '%TEST%'
ORDER BY opened_at_ms ASC
"""
df_post = pd.read_sql_query(query_all, conn)
print(f"Total post-fix positions: {len(df_post)}")

if len(df_post) > 0:
    df_post['realised_pnl'] = pd.to_numeric(df_post['realised_pnl'], errors='coerce')
    df_post['total_fees_usdt'] = pd.to_numeric(df_post['total_fees_usdt'], errors='coerce')
    df_post['opened_utc'] = pd.to_datetime(df_post['opened_at_ms'], unit='ms')
    df_post['closed_utc'] = pd.to_datetime(df_post['closed_at_ms'], unit='ms')
    df_post['hold_mins'] = (df_post['closed_at_ms'] - df_post['opened_at_ms']) / (1000 * 60)

    print("\n--- All Post-Fix Positions ---")
    cols_to_print = ['desk', 'strategy_id', 'symbol', 'direction', 'status', 'exit_reason', 'realised_pnl', 'total_fees_usdt', 'hold_mins', 'opened_utc']
    print(df_post[cols_to_print].to_string())

    closed = df_post[df_post['status'] == 'CLOSED'].copy()
    print(f"\n--- Closed Positions Performance ({len(closed)} trades) ---")
    if len(closed) > 0:
        desk_summary = closed.groupby('desk').agg(
            trades=('position_id', 'count'),
            wins=('realised_pnl', lambda x: (x > 0).sum()),
            losses=('realised_pnl', lambda x: (x <= 0).sum()),
            win_rate=('realised_pnl', lambda x: f"{round((x > 0).mean()*100, 1)}%"),
            net_pnl=('realised_pnl', lambda x: round(x.sum(), 2)),
            total_fees=('total_fees_usdt', lambda x: round(x.sum(), 2)),
            gross_pnl=('realised_pnl', lambda x: round(x.sum() + closed.loc[x.index, 'total_fees_usdt'].sum(), 2))
        )
        print("\nBy Desk:")
        print(desk_summary)

        strat_summary = closed.groupby(['desk', 'strategy_id']).agg(
            trades=('position_id', 'count'),
            wins=('realised_pnl', lambda x: (x > 0).sum()),
            losses=('realised_pnl', lambda x: (x <= 0).sum()),
            win_rate=('realised_pnl', lambda x: f"{round((x > 0).mean()*100, 1)}%"),
            net_pnl=('realised_pnl', lambda x: round(x.sum(), 2)),
            total_fees=('total_fees_usdt', lambda x: round(x.sum(), 2))
        )
        print("\nBy Strategy:")
        print(strat_summary)

        exit_summary = closed.groupby(['exit_reason']).agg(
            trades=('position_id', 'count'),
            wins=('realised_pnl', lambda x: (x > 0).sum()),
            net_pnl=('realised_pnl', lambda x: round(x.sum(), 2))
        )
        print("\nBy Exit Reason:")
        print(exit_summary)

    # Check Open Positions
    open_pos = df_post[df_post['status'] == 'OPEN'].copy()
    print(f"\n--- Currently Open Positions ({len(open_pos)}) ---")
    if len(open_pos) > 0:
        print(open_pos[['desk', 'strategy_id', 'symbol', 'direction', 'notional_usdt', 'entry_price', 'opened_utc']].to_string())

# Check candidate ledger since deploy
query_cands = f"""
SELECT origin, strategy_id, decision, count(*) as count
FROM crypto_candidate_ledger
WHERE timestamp_ms >= {deploy_time_ms} AND candidate_id NOT LIKE '%TEST%'
GROUP BY origin, strategy_id, decision
ORDER BY origin, strategy_id, decision
"""
df_cands = pd.read_sql_query(query_cands, conn)
print("\n" + "=" * 80)
print("POST-FIX CANDIDATE EVALUATIONS")
print("=" * 80)
print(df_cands.to_string())

# Check liquidation stream health
query_liq = f"""
SELECT max(liquidation_notional_60s) as max_liq, count(*) as total_states
FROM crypto_derivatives_state
WHERE state_time_ms >= {deploy_time_ms}
"""
df_liq = pd.read_sql_query(query_liq, conn)
print("\n--- Liquidation Ingestion Health Since Deploy ---")
print(df_liq.to_string())

conn.close()
