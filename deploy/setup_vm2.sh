#!/usr/bin/env bash
# One-time bootstrap script for Market Pilot Crypto on Oracle VM 2
set -euo pipefail

echo "=== 1. Updating System Packages ==="
sudo apt update && sudo apt install -y python3-pip python3-venv git curl htop

echo "=== 2. Setting up 2GB Swap ==="
if [ ! -f /swapfile ]; then
    sudo fallocate -l 2G /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile
    sudo swapon /swapfile
    echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
    echo "Swap created successfully."
else
    echo "Swapfile already exists."
fi

echo "=== 3. Configuring Local Firewall for Private VCN (Port 8800) ==="
# Allow inbound TCP 8800 from private VCN subnet (10.0.0.0/24) where VM 1 resides
sudo iptables -C INPUT -p tcp --dport 8800 -s 10.0.0.0/24 -j ACCEPT 2>/dev/null || \
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8800 -s 10.0.0.0/24 -j ACCEPT

if command -v netfilter-persistent &> /dev/null; then
    sudo netfilter-persistent save || true
fi

echo "=== 4. Setting up Python Virtual Environment ==="
cd /home/ubuntu/market-pilot-crypto
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "=== 5. Installing systemd Service ==="
sudo cp deploy/crypto-pilot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable crypto-pilot.service
sudo systemctl restart crypto-pilot.service

echo "=== 6. Verifying Service Status ==="
sudo systemctl status crypto-pilot.service --no-pager

echo "=== Done! Crypto Research Daemon is now running on port 8800 ==="
