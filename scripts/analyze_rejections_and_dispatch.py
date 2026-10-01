import sqlite3
import pandas as pd

conn = sqlite3.connect('data/crypto_research_vm2.db')

for strat in ['STRAT_FUNDING_REVERSION_V1', 'STRAT_LIQUIDATION_FADER_V1', 'STRAT_SUPERHUMAN_VOLATILITY_EXPANSION_V1']:
    print(f"=== {strat} REJECTIONS ===")
    query = f"""
        SELECT rejection_codes, count(*) as cnt
        FROM crypto_candidate_ledger
        WHERE strategy_id = '{strat}'
        GROUP BY rejection_codes
        ORDER BY cnt DESC
        LIMIT 5
    """
    df = pd.read_sql_query(query, conn)
    print(df.to_string())
    print()

print("=== ACCEPTED CANDIDATES vs DISPATCHED POSITIONS ===")
df_acc = pd.read_sql_query("""
    SELECT strategy_id, count(*) as accepted_candidates
    FROM crypto_candidate_ledger
    WHERE decision = 'ACCEPT' AND candidate_id NOT LIKE '%TEST%'
    GROUP BY strategy_id
""", conn)
print(df_acc.to_string())

df_pos = pd.read_sql_query("""
    SELECT strategy_id, count(*) as positions_opened
    FROM crypto_paper_positions
    WHERE candidate_id NOT LIKE '%TEST%'
    GROUP BY strategy_id
""", conn)
print("\nPositions opened:")
print(df_pos.to_string())

conn.close()
