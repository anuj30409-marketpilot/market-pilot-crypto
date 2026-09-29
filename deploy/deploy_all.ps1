# ==============================================================================
# 1-Click Combined Deployment to Oracle Cloud (VM 2 Daemon + VM 1 Frontend)
# Usage: powershell -ExecutionPolicy Bypass -File deploy\deploy_all.ps1
# ==============================================================================

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Starting Full Market Pilot Crypto Deployment..." -ForegroundColor Cyan
Write-Host "Step 1: Deploy VM 2 (Crypto Background Engine)" -ForegroundColor Yellow
Write-Host "Step 2: Deploy VM 1 (Frontend Dashboard & Gateway)" -ForegroundColor Yellow
Write-Host "============================================================" -ForegroundColor Cyan

$DEPLOY_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path

# 1. Deploy VM 2
Write-Host "`n>>> [1/2] Deploying VM 2 (129.154.244.236)..." -ForegroundColor Cyan
& "$DEPLOY_DIR\deploy_vm2.ps1"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Deployment stopped: VM 2 deployment failed with exit code $LASTEXITCODE."
    exit $LASTEXITCODE
}

# 2. Deploy VM 1
Write-Host "`n>>> [2/2] Deploying VM 1 (marketpilotanuj.duckdns.org)..." -ForegroundColor Cyan
& "$DEPLOY_DIR\deploy_vm1.ps1"
if ($LASTEXITCODE -ne 0) {
    Write-Error "Deployment stopped: VM 1 deployment failed with exit code $LASTEXITCODE."
    exit $LASTEXITCODE
}

Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "[SUCCESS] Full Market Pilot Crypto Stack Deployed Successfully!" -ForegroundColor Green
Write-Host "Crypto Dashboard URL: https://marketpilotanuj.duckdns.org/v3/crypto" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Green
