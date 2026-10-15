#!/usr/bin/env bash
# ==============================================================================
# 1-Click Deployment to Oracle VM 2 (Crypto Research Daemon) from macOS
# Usage: ./deploy/deploy_vm2_mac.sh
# ==============================================================================
set -euo pipefail

SSH_KEY="${HOME}/.ssh/market-pilot-oracle"
VM2_IP="129.154.244.236"
USER="ubuntu"

echo "============================================================"
echo "Deploying Market Pilot Crypto Daemon to Oracle VM 2..."
echo "Target: ${USER}@${VM2_IP}"
echo "============================================================"

if [ ! -f "${SSH_KEY}" ]; then
    echo "ERROR: SSH key not found at ${SSH_KEY}" >&2
    exit 1
fi

ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no "${USER}@${VM2_IP}" 'bash -s' << 'EOF'
cd /home/ubuntu/market-pilot-crypto && \
git fetch origin main && \
rm -f scripts/get_live_telemetry.py && \
git checkout main -f && \
git pull origin main && \
.venv/bin/pip install -q -r requirements.txt && \
.venv/bin/python scripts/test_os_foundation.py && \
.venv/bin/python scripts/test_phase4_dispatcher.py && \
.venv/bin/python scripts/test_phase5_control.py && \
.venv/bin/python scripts/test_quant_invariants.py && \
.venv/bin/python scripts/test_crypto_copilot.py && \
sudo systemctl restart crypto-pilot.service && \
sleep 5 && \
sudo systemctl status crypto-pilot.service --no-pager -l && \
curl --retry 5 --retry-delay 1 --retry-connrefused -s http://127.0.0.1:8800/currency && echo '' && \
curl --retry 3 --retry-delay 1 --retry-connrefused -s http://127.0.0.1:8800/regime && echo '' && \
curl --retry 3 --retry-delay 1 --retry-connrefused -s http://127.0.0.1:8800/ml/status && echo ''
EOF

echo ""
echo "============================================================"
echo "[SUCCESS] Oracle VM 2 Deployment Completed Successfully!"
echo "============================================================"
