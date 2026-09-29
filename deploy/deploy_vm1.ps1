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
sudo DEPLOY_BRANCH='feature/options-shadow-broker' bash /home/ubuntu/market-pilot-git/deploy/oracle/deploy-git.sh
"@

ssh -i $SSH_KEY -o StrictHostKeyChecking=no "$USER@$VM1_HOST" $COMMANDS

if ($LASTEXITCODE -eq 0) {
    Write-Host "`n[SUCCESS] Oracle VM 1 Frontend Deployment Completed Successfully!" -ForegroundColor Green
} else {
    Write-Host "`n[ERROR] Oracle VM 1 Deployment Encountered Errors." -ForegroundColor Red
}
