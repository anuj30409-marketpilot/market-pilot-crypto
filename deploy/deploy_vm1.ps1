# ==============================================================================
# 1-Click Deployment to Oracle VM 1 (Frontend & Gateway)
# Usage: powershell -ExecutionPolicy Bypass -File deploy\deploy_vm1.ps1
# ==============================================================================

$SSH_KEY = "$env:USERPROFILE\.ssh\market-pilot-oracle"
$VM1_HOST = "marketpilotanuj.duckdns.org"
$USER = "ubuntu"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Deploying Frontend Dashboard to Oracle VM 1..." -ForegroundColor Cyan
Write-Host "Target: $USER@$VM1_HOST" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan

if (-not (Test-Path $SSH_KEY)) {
    Write-Error "SSH key not found at $SSH_KEY"
    exit 1
}

$COMMANDS = @"
cd /home/ubuntu/market-pilot && \
git fetch origin feature/options-shadow-broker && \
git checkout feature/options-shadow-broker && \
git pull origin feature/options-shadow-broker && \
cd frontend && \
npm run build && \
sudo cp -r dist/* /var/www/market-pilot/ && \
sudo nginx -t && \
sudo systemctl reload nginx && \
curl -s -I https://marketpilotanuj.duckdns.org/v3/crypto | head -n 5
"@

ssh -i $SSH_KEY -o StrictHostKeyChecking=no "$USER@$VM1_HOST" $COMMANDS

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n[SUCCESS] Oracle VM 1 Frontend Deployment Completed Successfully!" -ForegroundColor Green
} else {
    Write-Host "`n[ERROR] Oracle VM 1 Deployment Encountered Errors." -ForegroundColor Red
}
