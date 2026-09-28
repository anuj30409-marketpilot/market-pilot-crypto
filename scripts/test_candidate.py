import urllib.request
import json
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

data = {
    "candidate_id": "CAND-BTC-001",
    "timestamp_ms": 1790597880000,
    "symbol": "BTCUSDT",
    "origin": "QUANT",
    "engine_version": "v1.0",
    "model_version": "v1.0",
    "signal_version": "v1.0",
    "decision": "REJECT",
    "decision_reason": "Negative basis spread (-4.1 bps) violates entry filter",
    "hypothetical_entry": 83312.0,
    "created_at_iso": "2026-09-28T12:18:03Z"
}

req = urllib.request.Request(
    "https://127.0.0.1/api/v2/crypto/candidates",
    data=json.dumps(data).encode("utf-8"),
    headers={"Content-Type": "application/json"}
)

resp = urllib.request.urlopen(req, context=ctx)
print("POST response:", resp.read().decode())

get_req = urllib.request.Request("https://127.0.0.1/api/v2/crypto/candidates")
get_resp = urllib.request.urlopen(get_req, context=ctx)
print("GET candidates:", get_resp.read().decode())
