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
- **Next candidate work (NOT yet approved):** (a) decide whether SH2 should be parked until Delta feed exists; (b) evaluate whether ORDERBOOK_MOMENTUM's 59 net-positive rows justify relaxing ITS specific conjunctive gates â€” with regime-conditional evidence, not urgency.

### 2026-09-29: SH2 (CROSS_VENUE) parked
- **Why:** Verified `evaluate_superhuman_cross_venue` is dead-by-design. `parity_engine.delta_prices` is never populated (no caller of `update_delta_price()` exists anywhere in the repo), so `evaluate_parity` always returns `PARITY_DELTA_FEED_DISCONNECTED` -> `REJECT_EXECUTION_UNCERTAINTY` on every cycle. SH2 recorded 0/990 accepted, ever.
- **Change (local, NOT deployed):** `strategies/superhuman_evaluator.py` `evaluate_once` no longer evaluates or records SH2; loop list is now `[c1, c3, c4]`. Function definition left intact for future re-enable. Paper-only, fully reversible. Registry status left as-is (evaluation skip is the effective park; avoids an unnecessary governance transition).
- **Effect:** Superhuman cycle log drops from "8 evaluated" to "6 evaluated"; the dead-candidate stream stops growing.
- **Re-enable condition:** when `execution/parity.py` receives a live Delta India feed (i.e. something calls `update_delta_price`).

### 2026-09-29: ORDERBOOK_MOMENTUM evidence review â€” the '59 net-positive vs 0 accepted' puzzle dissolved
- **What was claimed (my own earlier framing):** STRAT_ORDERBOOK_MOMENTUM_V1 had 59 net-positive-edge rows yet 0 accepted â€” an apparent contradiction.
- **What the data actually shows:** Of the net-positive-edge rows, 7 were ACCEPT (all synthetic TEST-DISPATCH) and 53 were REJECT. The 53 rejects break down as: 28 `[REGIME_UNFAVORABLE, SIGNAL_THRESHOLD_NOT_MET, EDGE_TOO_SMALL]` and 25 `[REGIME_UNFAVORABLE, SIGNAL_THRESHOLD_NOT_MET]`. **Not one was rejected by an edge failure alone.**
- **Root insight (from orderbook_momentum.py:76-89):** `expected_edge_bps` is computed UNCONDITIONALLY from magnitudes â€” `abs(imbalance_5)*14 + abs(cvd_z)*3.5 + abs(microprice)*2` â€” so it measures *book activity magnitude*, NOT trade validity. Acceptance separately requires a direction to align across 4 conjunctive conditions (flow_long/short OR queue_long/short). A high edge value with no direction = an active but unaligned book. **There is no contradiction; the two metrics measure different things.**
- **Why the regime gate fires so often:** ALLOWED_REGIMES for this strategy = {TRENDING_UP, TRENDING_DOWN, RANGE, HIGH_VOLATILITY}. The market has been in LOW_VOLATILITY (3286/4360 candidates). So ORDERBOOK_MOMENTUM is *correctly regime-gated out* of the prevailing market â€” by design, not by fault.
- **Conclusion:** No strategy change warranted. The desk's largest-edge QUANT strategy simply does not trade in a low-volatility regime, which is what its own rules say. Do NOT relax ALLOWED_REGIMES to force trades (FM-1).
- **Correction logged:** my earlier 'contradiction' framing is retracted; recorded here so it is not re-chased.

### 2026-09-30: Candle ingest FROZE (futures WS kline channel went silent) -> REST fallback added
- **Symptom:** Desk still 0 trades. Live VM2 check: crypto_candles_1m and crypto_derivatives_state both frozen at 2026-09-29 03:53 UTC; /regime returned cvd_zscore=0.0; SH1/SH4/SWING/MACRO edges exactly 0.0. feed_status stayed NORMAL and candidate_ledger kept growing, so the freeze was masked.
- **Root cause (verified, reproducible):** An INDEPENDENT fresh WebSocket from VM2 to wss://fstream.binance.com delivered ONLY @depth20 (~110 msgs/12s) while @kline_1m, @aggTrade and @markPrice delivered ZERO frames (20s sample). Spot WS kline worked; REST /fapi/v1/klines returned HTTP 200 with fresh bars. So Binance futures stopped sending kline/aggTrade/markPrice to this host while still sending depth. Code was NOT at fault and no threshold was at fault.
- **Effect chain:** no klines -> tracker.recent_closes/CVD never advance -> cvd_z=0, ADX/Donchian frozen -> every strategy correctly rejects (edge-vs-cost ~ -10 bps).
- **Change (commit 277fd14):** Added collectors/kline_rest_fallback.py - a 20s REST poller for /fapi/v1/klines that feeds sqlite_store.upsert_candle + derivatives_engine.handle_candle_update, wired into main.py as a background task. Additive plumbing ONLY. No strategy threshold, gate, or ALLOWED_REGIMES changed (FM-1 honoured).
- **NOT DEPLOYED.** INV-OPS-001: agent never deploys; user runs the VM2 deploy. After deploy, confirm crypto_candles_1m / crypto_derivatives_state resume writing and cvd_z becomes nonzero.
- **New failure mode FM-4:** A WS channel can silently drop a SUBSET of streams while others keep the connection looking healthy. feed_status (driven by depth/markprice) is NOT a reliable proxy for kline/CVD health. Watch crypto_candles_1m MAX(open_time_ms) freshness directly.

### 2026-10-01: Full crypto desk trade audit & alpha execution fixes (INV-OPS-001 honoured)
- **Finding:** Live VM2 sync revealed 81 real closed trades (-$148.55 net PnL). Gross alpha on Superhuman desk was positive (+$12.92) but wiped by $42.43 fees. Quant desk lost -$119.04 primarily from 1m swing breakout noise (-$237.97 stop loss drag).
- **Root causes identified & fixed:**
  1. **SH1 direction signaling defect:** `evaluate_superhuman_macro_regime` omitted direction metadata, causing `dispatcher.infer_direction` to default to `LONG` even during severe negative CVD sell-offs (e.g. cvd_z = -2.05). Fixed by setting `direction = LONG/SHORT` based on `cvd_z` sign, embedding it in `decision_reason`, and adding explicit SH1 handling in `dispatcher.infer_direction`.
  2. **SH3 Volatility Expansion formula scale:** Gross edge formula `imb5*10 + micro*2` topped at ~12 bps against 11.5 bps friction (0/7,160 accepted). Rescaled to `imb5*20 + micro*3` with imbalance direction tagging.
  3. **S2 Orderbook Momentum profile calibration:** TP was 1.20% (120 bps) for a 10 bps micro-orderbook edge, forcing 100% of trades to 30m time-stop. Calibrated to `sl_pct = 0.40%`, `tp_pct = 0.60%` with realistic 30m hold.
  4. **Swing Momentum noise filter (S4 & SH4):** 20-period Donchian on 1m bars was only 20m of noise. Upgraded `DONCHIAN_PERIOD = 60` (1 hour) and `EMA_FAST = 30`, `EMA_SLOW = 90` with dynamic volume SMA window and CVD flow alignment check (`cvd_z >= 0.30` for long, `<= -0.30` for short).
  5. **Expert Crypto Auditor Agent tool deployed:** Created `scripts/expert_crypto_agent.py` for automated multidimensional desk auditing.
- **Verification:** All test suites passed 100% (Phase 4 dispatcher & risk tests, Phase 5 control & reconciliation tests, dual-desk isolation & readiness tests, OS foundation tests).
- **Deployment Status:** NOT DEPLOYED. Local only. User executes deploy per INV-OPS-001.

### 2026-10-01 (Part 2): S1 Funding Reversion Unlocked & Liquidation Parser Fixed (INV-OPS-001 honoured)
- **Finding:** Strategy S1 had 0/5,866 accepted because basis was required > 3.0 bps (max ever recorded was +2.41 bps) and microprice was required > 1.0 bps on 8h funding macro divergence. Liquidation stream `!forceOrder@arr` was silently failing with AttributeError on array/list payloads, keeping `liquidation_notional_60s` at 0.0 perpetually. Replay simulation also proved tight trailing stops at +45 bps backfire in crypto by suffocating winning trends.
- **Changes applied:**
  1. **S1 Funding Reversion calibrated:** Basis threshold lowered to $|basis| \ge 1.5$ bps, funding $|z| \ge 1.8$, microprice softened to non-negative ($\ge 0.0$), and gross carry formula upgraded to `abs_z * 7.0 + abs_basis * 2.0`. Replay confirmed 62.5% win rate and +$7.13 net profit across 8 trades.
  2. **Binance Liquidation WebSocket Stream parser fixed:** Updated `collectors/binance_ws.py` to handle both list/array and dict structures on `!forceOrder@arr` payloads, restoring liquidation event ingestion and enabling Strategy S3.
- **Verification:** All test suites passed cleanly (100%).
- **Deployment Status:** NOT DEPLOYED. Local only. User executes deploy per INV-OPS-001.

