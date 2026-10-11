# ==============================================================================
# Live Health & Telemetry Verification for Market Pilot Crypto (Oracle VM 2)
# Usage: powershell -ExecutionPolicy Bypass -File deploy\verify_status.ps1
# ==============================================================================

$SSH_KEY = "$env:USERPROFILE\.ssh\market-pilot-oracle"
$VM2_IP = "129.154.244.236"
$USER = "ubuntu"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Querying Live Crypto Engine on Oracle VM 2 ($VM2_IP)..." -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

if (-not (Test-Path $SSH_KEY)) {
    Write-Error "SSH key not found at $SSH_KEY"
    exit 1
}

$COMMANDS = @'
echo "=== [1/4] SYSTEMD DAEMON STATUS ==="
sudo systemctl status crypto-pilot.service --no-pager -l | head -n 12

echo ""
echo "=== [2/4] RECENT DAEMON LOGS (LAST 15 LINES) ==="
sudo journalctl -u crypto-pilot.service -n 15 --no-pager

echo ""
echo "=== [3/4] LATEST CANDIDATE EVALUATIONS ==="
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
echo "=== [4/4] DUAL-DESK PAPER ACCOUNT SUMMARY ==="
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
    meta = d.get('metadata', {})
    status = d.get('status', 'UNKNOWN')
    cadence = meta.get('cadence', 'UNKNOWN')
    last_tr = meta.get('last_retrained_utc', 'NEVER')
    next_tr = meta.get('next_scheduled_retrain_utc', 'UNKNOWN')
    drift = meta.get('drift_status', 'UNKNOWN')
    psi = meta.get('psi_score', 0.0)
    samples = meta.get('training_samples', 0)
    bars = meta.get('dataset_1m_bars', 0)
    cands = meta.get('dataset_candidates', 0)
    auc = meta.get('oos_roc_auc', 0.0)
    print(f'  Status: {status} | Cadence: {cadence}')
    print(f'  Last Retrained: {last_tr} | Next Scheduled: {next_tr}')
    print(f'  Dataset Vitals: {samples} eff samples | {bars} 1m bars | {cands} candidates')
    print(f'  Validation: OOS ROC-AUC={auc:.3f} | PSI={psi:.4f} ({drift})')
except Exception as e:
    print('Failed to parse ML status:', e)
"
'@

$COMMANDS | ssh -i $SSH_KEY -o StrictHostKeyChecking=no "$USER@$VM2_IP" "bash -s"

