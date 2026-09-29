#!/usr/bin/env bash
# ==============================================================================
# Market Pilot Crypto - Oracle VM 2 Deployment Script
# Target Host: Oracle VM 2 (Crypto Daemon - 129.154.244.236 / 10.0.0.126)
# ==============================================================================
set -euo pipefail

APP_DIR="/home/ubuntu/market-pilot-crypto"
SERVICE_NAME="crypto-pilot.service"

echo "============================================================"
echo "Starting Market Pilot Crypto Deployment on Oracle VM 2"
echo "============================================================"

# 1. Navigate to repository
cd "${APP_DIR}"
echo "[1/6] Current directory: $(pwd)"

# 2. Pull latest code from GitHub
echo "[2/6] Pulling latest code from origin/main..."
git fetch origin main
CURRENT_COMMIT=$(git rev-parse HEAD)
TARGET_COMMIT=$(git rev-parse origin/main)

if [ "${CURRENT_COMMIT}" != "${TARGET_COMMIT}" ]; then
    echo "Updating from ${CURRENT_COMMIT:0:7} to ${TARGET_COMMIT:0:7}..."
    git pull origin main
else
    echo "Already at latest commit (${CURRENT_COMMIT:0:7})."
fi

# 3. Update Python dependencies if requirements changed
echo "[3/6] Checking Python virtual environment..."
source .venv/bin/activate
pip install -q -r requirements.txt

# 4. Run automated test suites before restarting service
echo "[4/6] Running pre-deployment verification test suites..."
python scripts/test_os_foundation.py
python scripts/test_phase4_dispatcher.py
python scripts/test_phase5_control.py
echo "All test suites passed cleanly."

# 5. Restart systemd daemon service
echo "[5/6] Restarting ${SERVICE_NAME}..."
sudo systemctl restart "${SERVICE_NAME}"
sleep 2

# 6. Verify service health and API endpoints
echo "[6/6] Verifying service status and health..."
sudo systemctl status "${SERVICE_NAME}" --no-pager -l

HEALTH_CHECK=$(curl -s http://127.0.0.1:8800/currency || echo "FAIL")
if [ "${HEALTH_CHECK}" != "FAIL" ]; then
    echo "API Health Check: PASS -> ${HEALTH_CHECK}"
else
    echo "API Health Check: FAILED! Check 'sudo journalctl -u ${SERVICE_NAME} -n 50 --no-pager'"
    exit 1
fi

echo "============================================================"
echo "Deployment Successful on Oracle VM 2!"
echo "============================================================"
