#!/usr/bin/env bash
# ==============================================================================
# Market Pilot Crypto - Oracle VM 1 Frontend Deployment Script
# Target Host: Oracle VM 1 (Gateway / NGINX / Frontend - marketpilotanuj.duckdns.org)
# ==============================================================================
set -euo pipefail

REPO_DIR="/home/ubuntu/market-pilot"
BRANCH="feature/options-shadow-broker"
WEB_ROOT="/var/www/market-pilot"

echo "============================================================"
echo "Starting Market Pilot Frontend Deployment on Oracle VM 1"
echo "============================================================"

# 1. Navigate to frontend repository
cd "${REPO_DIR}"
echo "[1/6] Current directory: $(pwd)"

# 2. Pull latest frontend code
echo "[2/6] Pulling latest code for ${BRANCH}..."
git fetch origin "${BRANCH}"
git checkout "${BRANCH}"
git pull origin "${BRANCH}"

# 3. Build production React assets
echo "[3/6] Building production frontend bundles..."
cd frontend
npm run build

# 4. Deploy built assets to NGINX web root
echo "[4/6] Copying dist/ to ${WEB_ROOT}..."
sudo cp -r dist/* "${WEB_ROOT}/"

# 5. Reload NGINX
echo "[5/6] Verifying NGINX config and reloading..."
sudo nginx -t
sudo systemctl reload nginx

# 6. Verify public crypto page status
echo "[6/6] Verifying public endpoint status..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" https://marketpilotanuj.duckdns.org/v3/crypto)
if [ "${HTTP_STATUS}" = "200" ]; then
    echo "Public Crypto Dashboard Health Check: PASS (HTTP 200)"
else
    echo "WARNING: Received HTTP status ${HTTP_STATUS}"
fi

echo "============================================================"
echo "Frontend Deployment Successful on Oracle VM 1!"
echo "============================================================"
