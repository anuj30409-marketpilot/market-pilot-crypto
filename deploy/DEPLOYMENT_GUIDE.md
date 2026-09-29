# Market Pilot Crypto — Deployment Guide & Operational Runbook

> [!IMPORTANT]
> **Operational Rule `INV-OPS-001` (Strict User-Executed Deployment)**
> From this point forward, the AI agent **SHALL NEVER** execute deployment commands (`git pull`, `systemctl restart`, `npm run build`, etc.) on Oracle Cloud VM 1 or VM 2.
>
> **The Agent's Role**:
> 1. Develop, refactor, and harden the codebase locally.
> 2. Run local unit, statistical, and integration test suites.
> 3. Commit clean, atomic changes and push to GitHub.
> 4. Notify the user with exact release notes.
>
> **The User's Role**:
> The user reviews the release notes and executes the deployment using the automated 1-click scripts below.

---

## 1. Fast 1-Click Deployment from Windows

All scripts are located in `deploy\` and use your existing Oracle SSH key (`%USERPROFILE%\.ssh\market-pilot-oracle`).

### A. Deploy Crypto Research Daemon (Oracle VM 2)
When changes are pushed to `market-pilot-crypto` repository:
```powershell
powershell -ExecutionPolicy Bypass -File deploy\deploy_vm2.ps1
```
**What this script does automatically**:
1. Connects to Oracle VM 2 (`129.154.244.236`).
2. Pulls latest `main` branch.
3. Updates Python virtual environment packages (`requirements.txt`).
4. Executes the 3 automated test suites (`test_os_foundation.py`, `test_phase4_dispatcher.py`, `test_phase5_control.py`).
5. Restarts `crypto-pilot.service` via `sudo systemctl restart`.
6. Performs health check queries against `http://127.0.0.1:8800/currency` and `/regime`.

---

### B. Deploy Frontend Dashboard (Oracle VM 1)
When changes are made to the React frontend UI (`market-pilot` repository):
```powershell
powershell -ExecutionPolicy Bypass -File deploy\deploy_vm1.ps1
```
**What this script does automatically**:
1. Connects to Oracle VM 1 (`marketpilotanuj.duckdns.org`).
2. Pulls latest frontend branch.
3. Runs `npm run build` inside `frontend/`.
4. Copies build output to web root (`/var/www/market-pilot/`).
5. Tests and reloads NGINX (`sudo nginx -t && sudo systemctl reload nginx`).
6. Verifies HTTP 200 on `https://marketpilotanuj.duckdns.org/v3/crypto`.

---

## 2. Manual SSH Deployment (Direct Terminal Execution)

If you prefer logging into the Oracle VMs directly:

### On Oracle VM 2 (Crypto Daemon):
```bash
ssh -i ~/.ssh/market-pilot-oracle ubuntu@129.154.244.236
```
Then run the self-contained deployment script:
```bash
cd /home/ubuntu/market-pilot-crypto
bash deploy/deploy_vm2.sh
```

To inspect live daemon logs at any time:
```bash
sudo journalctl -u crypto-pilot.service -f
```

---

### On Oracle VM 1 (Gateway / NGINX / Frontend):
```bash
ssh -i ~/.ssh/market-pilot-oracle ubuntu@marketpilotanuj.duckdns.org
```
Then run:
```bash
cd /home/ubuntu/market-pilot
bash /home/ubuntu/market-pilot-crypto/deploy/deploy_vm1_frontend.sh
# Or manually:
cd /home/ubuntu/market-pilot && git pull
cd frontend && npm run build
sudo cp -r dist/* /var/www/market-pilot/
sudo systemctl reload nginx
```

---

## 3. Useful Health & Verification Endpoints

You can check service status from any terminal:

```bash
# Check Currency Rates (Market USDT vs Delta Settlement INR)
curl -s https://marketpilotanuj.duckdns.org/v3/crypto  # Web Dashboard

# Check from VM 1 or VM 2 terminal:
curl -s http://127.0.0.1:8800/currency
curl -s http://127.0.0.1:8800/regime
curl -s http://127.0.0.1:8800/candidates?limit=5
curl -s http://127.0.0.1:8800/paper/summary
curl -s http://127.0.0.1:8800/strategies/audit
```
