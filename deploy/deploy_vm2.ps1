# ==============================================================================
# 1-Click Deployment to Oracle VM 2 (Crypto Research Daemon)
# Usage: powershell -ExecutionPolicy Bypass -File deploy\deploy_vm2.ps1
# ==============================================================================

$SSH_KEY = "$env:USERPROFILE\.ssh\market-pilot-oracle"
$VM2_IP = "129.154.244.236"
$USER = "ubuntu"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Deploying Market Pilot Crypto Daemon to Oracle VM 2..." -ForegroundColor Cyan
Write-Host "Target: $USER@$VM2_IP" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

if (-not (Test-Path $SSH_KEY)) {
    Write-Error "SSH key not found at $SSH_KEY"
    exit 1
}

$COMMANDS = @"
cd /home/ubuntu/market-pilot-crypto && \
git fetch origin main && \
git pull origin main && \
.venv/bin/pip install -q -r requirements.txt && \
.venv/bin/python scripts/test_os_foundation.py && \
.venv/bin/python scripts/test_phase4_dispatcher.py && \
.venv/bin/python scripts/test_phase5_control.py && \
sudo systemctl restart crypto-pilot.service && \
sleep 5 && \
sudo systemctl status crypto-pilot.service --no-pager -l && \
curl --retry 5 --retry-delay 1 --retry-connrefused -s http://127.0.0.1:8800/currency && echo '' && \
curl --retry 3 --retry-delay 1 --retry-connrefused -s http://127.0.0.1:8800/regime && echo ''
"@

ssh -i $SSH_KEY -o StrictHostKeyChecking=no "$USER@$VM2_IP" $COMMANDS

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n[SUCCESS] Oracle VM 2 Deployment Completed Successfully!" -ForegroundColor Green
} else {
    Write-Host "`n[ERROR] Oracle VM 2 Deployment Encountered Errors." -ForegroundColor Red
}
