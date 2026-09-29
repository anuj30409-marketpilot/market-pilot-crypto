# Crypto Desk Logbook (`market-pilot-crypto`)

> MANDATORY REFERENCE. Per project rule (parity with Indian-desk logbooks): read before ANY recommendation or code change to the crypto desk; cross-check fixes against the failure-mode list; append a change-log entry after any shipped fix. This desk is SEPARATE from the Indian `market-pilot` desk (INV-CRYPTO-010).

---

## 1. SETTLED & LOCKED FOUNDATION (IMMUTABLE)

| Item | Locked value |
| :--- | :--- |
| Mode | Paper-only. Zero live exchange execution (INV-CRYPTO-001). |
| Isolation | Complete isolation from the Indian desk (INV-CRYPTO-010). |
| Universe V1 | BTCUSDT, ETHUSDT (Binance Futures & Spot). |
| Timestamps | Canonical UTC epoch ms (INV-CRYPTO-004); event vs receive time strictly separated (INV-CRYPTO-005). |
| Data quality | NORMAL -> DEGRADED -> QUARANTINED; no ML training on corrupted books (INV-CRYPTO-006). |
| Storage | Parquet lake for raw high-volume; SQLite for operational candles/metrics (INV-CRYPTO-017). |
| Deploy | INV-OPS-001: agent NEVER runs deploy commands on VM1/VM2; user executes deploys. |
| Capital (paper) | QUANT 10,000 USDT + SUPERHUMAN 10,000 USDT = 20,000 USDT total. |

---

## 2. HISTORICAL FAILURE MODES & ROOT CAUSES (WHY WE NEVER DO "X")

### FM-1: Do NOT lower strategy thresholds just to force trades
- **Context:** The desk rejected 4290 of 4296 candidates; only 6 (synthetic TEST-DISPATCH) ever accepted.
- **Temptation:** Lower SH CVD/ADX/dislocation thresholds so signals fire.
- **Why we never do it:** On 2026-09-29 live readings showed CVD z = -0.05 to -1.82 (weak impulse) with regime = LOW_VOLATILITY. The gates were firing CORRECTLY. Forcing trades by loosening thresholds manufactures negative-expectancy entries. Threshold relaxation requires regime-conditional justification AND evidence, never urgency.

### FM-2: Conjunctive gates produce 0.00 edge on rejected rows by design
- **What happens:** SH/QUANT edge is computed `if direction else 0.0`. Rejected candidates therefore store `expected_edge_bps = 0.0` and `signal_score = 0.0`.
- **Trap:** Mistaking these stored zeros for a "dead feed" or "broken formula" bug. Verified 2026-09-29: the CVD z-score feed IS alive (`cvd_notional_usd_zscore` moved -0.056 / -0.981 / -1.817 / -1.357 on live rows). Do not chase a phantom.

### FM-3: Superhuman strategies depend on feeds that may not exist yet
- **What happens:** SH2 (CROSS_VENUE) requires `dislocation_bps >= 10`, sourced from Delta-India execution-venue parity telemetry. With no live Delta feed, dislocation ~0 -> SH2 can never fire (0/990 accepted ever).
- **Rule:** Before treating a strategy as "miscalibrated", verify its input feed is actually wired. A strategy reading a non-existent feed should be PARKED, not tuned.

---

## 3. VERIFIED STATE (2026-09-29, read-only, Oracle VM2 129.154.244.236)

- Service `crypto-pilot.service`: active, NRestarts=0, up since 2026-09-29 09:27 UTC. Feeds NORMAL (BTC+ETH).
- `crypto_candidate_ledger` = 4360 rows: REJECT 4290 / ACCEPT 6 (all 6 synthetic TEST-DISPATCH). SUPERHUMAN origin 3802 REJECT / 0 ACCEPT; QUANT 488 REJECT / 6 ACCEPT.
- Logs, every 60s: `Superhuman cycle: 8 evaluated, 0 accepted (negative proofs recorded).`
- Avg gross edge 1.97 bps vs avg cost 10.61 bps -> avg net -8.63 bps; 71/4360 net-positive; 26 clear the 3 bps funding threshold.
- Best real-edge strategy: ORDERBOOK_MOMENTUM (avg gross 8.85 bps, 59 net-positive rows). FUNDING_REVERSION 7.11/12. SH_VOL 5.04/0.
- Regime skew: LOW_VOLATILITY 3286, RANGE 884, FUNDING_EXTREME 190.
- Anomaly (test artifact): 6 closed positions show exit_reason=TAKE_PROFIT with NEGATIVE pnl (-8.89/-6.45); entry 83000 -> exit 82910, opened/closed 20 ms apart. Not real trades.
- UNRESOLVED: SH_MACRO ledger rows average exactly 0.00 edge while live cvd_z is nonzero. Likely written during tracker warm-up; NOT proven. Do not act on it without evidence.

---

## 4. CHANGE LOG (append-only)

### 2026-09-29: Deep 'why no trades' review + logbook created
- **Finding:** Desk is healthy and evaluating every 60s; it simply never clears its own edge gates. Dominant cause is edge-vs-cost and regime/feed mismatch (SH2 un-wired), NOT a dead CVD feed (earlier claim retracted).
- **Change:** NONE to strategy logic. No threshold changed. Created this logbook (rule parity with Indian desk).
- **Next candidate work (NOT yet approved):** (a) decide whether SH2 should be parked until Delta feed exists; (b) evaluate whether ORDERBOOK_MOMENTUM's 59 net-positive rows justify relaxing ITS specific conjunctive gates — with regime-conditional evidence, not urgency.

### 2026-09-29: SH2 (CROSS_VENUE) parked
- **Why:** Verified `evaluate_superhuman_cross_venue` is dead-by-design. `parity_engine.delta_prices` is never populated (no caller of `update_delta_price()` exists anywhere in the repo), so `evaluate_parity` always returns `PARITY_DELTA_FEED_DISCONNECTED` -> `REJECT_EXECUTION_UNCERTAINTY` on every cycle. SH2 recorded 0/990 accepted, ever.
- **Change (local, NOT deployed):** `strategies/superhuman_evaluator.py` `evaluate_once` no longer evaluates or records SH2; loop list is now `[c1, c3, c4]`. Function definition left intact for future re-enable. Paper-only, fully reversible. Registry status left as-is (evaluation skip is the effective park; avoids an unnecessary governance transition).
- **Effect:** Superhuman cycle log drops from "8 evaluated" to "6 evaluated"; the dead-candidate stream stops growing.
- **Re-enable condition:** when `execution/parity.py` receives a live Delta India feed (i.e. something calls `update_delta_price`).
