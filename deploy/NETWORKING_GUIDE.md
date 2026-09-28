# Networking & Reverse-Proxy Setup Guide
## Market Pilot: Dual-VM Private VCN Interconnect

```
   [Public User / Browser]
              │ HTTPS (443)
              ▼
   ┌────────────────────────────────────────────────────────┐
   │ Oracle VM 1: Public Gateway (129.x.x.x)                │
   │ Private VCN IP: 10.0.0.x                               │
   │                                                        │
   │ NGINX (SSL, Auth via /_dashboard_auth)                 │
   │   ├── /v3/*           ──> React Static Frontend        │
   │   ├── /api/v2/*       ──> FastAPI Indian Desk (8790)   │
   │   └── /api/v2/crypto/*──> Reverse-Proxy via VCN        │
   └───────────────────────────────┬────────────────────────┘
                                   │ Internal VCN
                                   │ http://10.0.0.126:8800/
                                   ▼
   ┌────────────────────────────────────────────────────────┐
   │ Oracle VM 2: Dedicated Crypto Engine (129.154.244.236)  │
   │ Private VCN IP: 10.0.0.126                             │
   │                                                        │
   │ FastAPI + Binance WebSocket Engine (Port 8800)         │
   │   ├── /health                                          │
   │   ├── /candles?symbol=BTCUSDT                          │
   │   ├── /orderbook?symbol=BTCUSDT                        │
   │   └── /feeds                                           │
   └────────────────────────────────────────────────────────┘
```

---

### Step 1: Oracle Cloud Infrastructure (OCI) VCN Security List

Allow internal traffic on TCP port **8800** between VM 1 and VM 2:

1. Log in to the [Oracle Cloud Console](https://cloud.oracle.com).
2. Go to **Networking** $\to$ **Virtual Cloud Networks**.
3. Select your VCN (e.g. `market-pilot-vcn`).
4. Under **Resources**, click **Security Lists** and select the Default Security List.
5. Click **Add Ingress Rules**:
   - **Stateless:** Unchecked (`No`)
   - **Source Type:** `CIDR`
   - **Source CIDR:** `10.0.0.0/24` (or VM 1's specific private IP e.g. `10.0.0.x/32`)
   - **IP Protocol:** `TCP`
   - **Source Port Range:** `All`
   - **Destination Port Range:** `8800`
   - **Description:** `Allow VM 1 NGINX reverse-proxy to VM 2 Crypto API`
6. Click **Add Ingress Rules**.

---

### Step 2: Configure OS Firewall on VM 2 (`129.154.244.236`)

Oracle Cloud Ubuntu images block unlisted incoming TCP ports by default via `iptables`.

Run on VM 2:
```bash
# Allow inbound traffic from the 10.0.0.0/24 private subnet to port 8800
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8800 -s 10.0.0.0/24 -j ACCEPT

# Persist iptables rules across reboots
sudo apt-get install -y iptables-persistent
sudo netfilter-persistent save
```
*(Note: This is automatically executed if you run `deploy/setup_vm2.sh` on VM 2)*

---

### Step 3: Deploy NGINX Configuration on VM 1

The NGINX configuration has been updated in `market-pilot/deploy/oracle/market-pilot.nginx.conf`:

```nginx
    # Reverse-proxy to Oracle VM 2 (Dedicated Crypto Research Engine on Port 8800)
    location /api/v2/crypto/ {
        auth_request /_dashboard_auth;
        proxy_pass http://10.0.0.126:8800/;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Connection "";

        proxy_buffering off;
        proxy_connect_timeout 5s;
        proxy_read_timeout 30s;
        proxy_next_upstream error timeout http_502 http_503;
    }
```

To apply on VM 1:
```bash
# Copy config to /etc/nginx/sites-available/
sudo cp /home/ubuntu/market-pilot/deploy/oracle/market-pilot.nginx.conf /etc/nginx/sites-available/market-pilot

# Test syntax
sudo nginx -t

# Reload NGINX without dropping connections
sudo systemctl reload nginx
```

---

### Step 4: Verification & Smoke Test

From **VM 1**:
```bash
# Test 1: Direct private VCN access to VM 2 API
curl -s http://10.0.0.126:8800/health | jq .

# Test 2: NGINX local reverse proxy (bypassing public auth for test)
curl -s http://10.0.0.126:8800/orderbook?symbol=BTCUSDT | jq .
```

From **External Browser / Terminal**:
```bash
# Test 3: Public endpoint with authentication cookie
curl -s -b "market_pilot_token=<YOUR_TOKEN>" \
  https://marketpilotanuj.duckdns.org/api/v2/crypto/health | jq .
```
