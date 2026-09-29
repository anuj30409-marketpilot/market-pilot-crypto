import sqlite3
import pprint

conn = sqlite3.connect("/home/ubuntu/market-pilot-crypto/data/crypto_research.db")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=== TABLES ===")
for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    print("Table:", r["name"])

print("\n=== FEED STATUS ===")
for r in cur.execute("SELECT * FROM crypto_feed_status"):
    print(dict(r))

print("\n=== CANDLE SUMMARY ===")
rows = cur.execute("SELECT symbol, market_type, count(*), min(open_time_ms), max(open_time_ms) FROM crypto_candles_1m GROUP BY symbol, market_type").fetchall()
print("Groups count:", len(rows))
for r in rows:
    print(dict(r))

print("\n=== CANDLE SAMPLE ===")
for r in cur.execute("SELECT * FROM crypto_candles_1m ORDER BY open_time_ms DESC LIMIT 3"):
    print(dict(r))

print("\n=== CANDIDATES COUNT ===")
cur.execute("SELECT count(*) FROM crypto_candidate_ledger")
print("Total candidates in ledger:", cur.fetchone()[0])

print("\n=== LATEST CANDIDATES ===")
for r in cur.execute("SELECT candidate_id, strategy_id, decision, decision_reason, regime, rejection_codes FROM crypto_candidate_ledger ORDER BY timestamp_ms DESC LIMIT 6"):
    print(dict(r))
