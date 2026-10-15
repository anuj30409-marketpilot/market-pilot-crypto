#!/usr/bin/env bash
# ==============================================================================
# Live Health & Telemetry Verification for Market Pilot Crypto (Oracle VM 2) from macOS
# Usage: ./deploy/verify_status_mac.sh
# ==============================================================================
set -euo pipefail

SSH_KEY="${HOME}/.ssh/market-pilot-oracle"
VM2_IP="129.154.244.236"
USER="ubuntu"

echo "============================================================"
echo "Querying Live Crypto Engine on Oracle VM 2 (${VM2_IP})..."
echo "============================================================"

if [ ! -f "${SSH_KEY}" ]; then
    echo "ERROR: SSH key not found at ${SSH_KEY}" >&2
    exit 1
fi

ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no "${USER}@${VM2_IP}" 'bash -s' << 'EOF'
echo "=== [1/5] SYSTEMD DAEMON STATUS ==="
sudo systemctl status crypto-pilot.service --no-pager -l | head -n 12

echo ""
echo "=== [2/5] RECENT DAEMON LOGS (LAST 15 LINES) ==="
sudo journalctl -u crypto-pilot.service -n 15 --no-pager

echo ""
echo "=== [3/5] LATEST CANDIDATE EVALUATIONS ==="
curl -s http://127.0.0.1:8800/candidates?limit=8 | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    cands = d.get('candidates', [])
    print(f'Total candidates in query: {len(cands)}')
    for c in cands:
        ts = c.get('created_at_iso', '')[:19]
        origin = c.get('origin', '')
        strat = c.get('strategy_id', '')
        dec = c.get('decision', '')
        sym = c.get('symbol', '')
        reason = c.get('decision_reason', '')[:65]
        print(f'  [{ts}] {origin:<10} {sym:<8} {strat:<32} {dec:<6} {reason}')
except Exception as e:
    print('Failed to parse candidates:', e)
"

echo ""
echo "=== [4/5] DUAL-DESK PAPER ACCOUNT SUMMARY ==="
curl -s http://127.0.0.1:8800/paper/summary | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    q = d.get('quant', {})
    sh = d.get('superhuman', {})
    q_cap = q.get('capital_usdt')
    q_pos = q.get('open_positions')
    q_tr = q.get('total_trades')
    q_wr = q.get('win_rate_pct')
    q_pnl = q.get('net_pnl')
    sh_cap = sh.get('capital_usdt')
    sh_pos = sh.get('open_positions')
    sh_tr = sh.get('total_trades')
    sh_wr = sh.get('win_rate_pct')
    sh_pnl = sh.get('net_pnl')
    print(f'  QUANT DESK:      Capital: \${q_cap} | Open Pos: {q_pos} | Trades: {q_tr} | WinRate: {q_wr}% | Net PnL: \${q_pnl}')
    print(f'  SUPERHUMAN DESK: Capital: \${sh_cap} | Open Pos: {sh_pos} | Trades: {sh_tr} | WinRate: {sh_wr}% | Net PnL: \${sh_pnl}')
except Exception as e:
    print('Failed to parse paper summary:', e)
"

echo ""
echo "=== [5/5] ML RETRAINING CADENCE & DRIFT STATUS ==="
curl -s http://127.0.0.1:8800/ml/status | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    status = d.get('status', 'UNKNOWN')
    cadence = d.get('cadence', 'UNKNOWN')
    last_tr = d.get('last_trained_at_utc', 'NEVER')
    next_tr = d.get('next_retrain_at_utc', 'UNKNOWN')
    vitals = d.get('dataset_vitals', {})
    metrics = d.get('validation_metrics', {})
    drift_mon = d.get('drift_monitor', {})
    drift = drift_mon.get('drift_status', 'UNKNOWN')
    psi = drift_mon.get('feature_psi_24h', 0.0)
    samples = vitals.get('effective_sample_size_neff', 0)
    bars = vitals.get('total_bars_evaluated', 0)
    cands = vitals.get('candidate_records', 0)
    auc = metrics.get('oos_roc_auc', 0.0)
    brier = metrics.get('brier_score', 0.0)
    net_exp = metrics.get('net_expectancy_bps', 0.0)
    print(f'  Engine Status:   {status} ({d.get(\"engine_version\", \"\")}) | Active Model: {d.get(\"active_model_id\", \"\")}')
    print(f'  Cadence:         {cadence}')
    print(f'  Schedule:        Last Trained: {last_tr} | Next Retrain: {next_tr}')
    print(f'  Dataset Vitals:  N_eff={samples} | Bars={bars} | Candidates={cands}')
    print(f'  Validation:      OOS ROC-AUC={auc:.3f} | Brier={brier:.3f} | Net Expectancy=+{net_exp}bps')
    print(f'  Drift Monitor:   24h PSI={psi:.4f} | Status={drift}')
except Exception as e:
    print('Failed to parse ML status:', e)
"
EOF
