# Market Pilot Crypto Research Desk (`market-pilot-crypto`)

Institutional-grade, paper-only cryptocurrency market microstructure and derivatives research engine.

## Core Principles & Invariants
- **INV-CRYPTO-001:** Crypto V1 is research and paper-trading only. Zero live exchange order execution.
- **INV-CRYPTO-004:** All market data uses canonical UTC epoch millisecond timestamps.
- **INV-CRYPTO-005:** Event time (exchange) is strictly distinguished from receive and process time.
- **INV-CRYPTO-006:** Strict data quarantine (`NORMAL` -> `DEGRADED` -> `QUARANTINED`). No ML training on corrupted books.
- **INV-CRYPTO-010:** Complete isolation from the Indian market trading desk.
- **INV-CRYPTO-017:** High-volume raw trade data written to compressed Parquet lake; operational candles and metrics stored in SQLite.

## Universe V1
- **BTCUSDT** (Binance Futures & Spot)
- **ETHUSDT** (Binance Futures & Spot)

## Architecture Overview
```
Binance Multiplex WS (@aggTrade, @depth20@100ms, @kline_1m, @markPrice@1s)
         │
         ├──> [In-Memory Bounded Orderbook] ──> Microprice, Spread Bps, Imbalance
         ├──> [Aggregated Trade Buffer]    ──> Hourly Partitioned Parquet Lake
         └──> [Candle & State Builder]     ──> SQLite (crypto_candles_1m, feed_status)
         │
         └──> [FastAPI Server (Port 8800)]  ──> Proxied to Market Pilot /v3/crypto
```

## Quick Start on Oracle VM 2
```bash
# Clone repository
cd /home/ubuntu
git clone https://github.com/anuj30409-marketpilot/market-pilot-crypto.git
cd market-pilot-crypto

# Run bootstrap setup
chmod +x deploy/setup_vm2.sh
./deploy/setup_vm2.sh
```

## Health Check
```bash
curl http://127.0.0.1:8800/health
curl "http://127.0.0.1:8800/candles?symbol=BTCUSDT&limit=10"
curl "http://127.0.0.1:8800/orderbook?symbol=BTCUSDT"
```
