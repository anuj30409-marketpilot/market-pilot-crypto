#!/usr/bin/env bash
# ==============================================================================
# 1-Click Deployment to Oracle VM 1 (Frontend & Gateway) from macOS
# Usage: ./deploy/deploy_vm1_mac.sh
# ==============================================================================
set -euo pipefail

SSH_KEY="${HOME}/.ssh/market-pilot-oracle"
VM1_HOST="marketpilotanuj.duckdns.org"
USER="ubuntu"

echo "============================================================"
echo "Deploying Frontend Dashboard to Oracle VM 1..."
echo "Target: ${USER}@${VM1_HOST}"
echo "============================================================"

if [ ! -f "${SSH_KEY}" ]; then
    echo "ERROR: SSH key not found at ${SSH_KEY}" >&2
    exit 1
fi

ssh -i "${SSH_KEY}" -o StrictHostKeyChecking=no "${USER}@${VM1_HOST}" 'bash -s' << 'EOF'
sudo DEPLOY_BRANCH='feature/options-shadow-broker' bash /home/ubuntu/market-pilot-git/deploy/oracle/deploy-git.sh
EOF

echo ""
echo "============================================================"
echo "[SUCCESS] Oracle VM 1 Frontend Deployment Completed Successfully!"
echo "============================================================"
