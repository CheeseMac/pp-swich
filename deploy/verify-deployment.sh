#!/usr/bin/env bash
# Gate R1 deployment acceptance (upgraded from review8 QA-DEP-01..05).
#
# Legs:
#   QA-DEP-01  non-demo production config boots (3 mandatory keys) and
#              stays healthy; dev login directory stays off
#   AUD-E2E-05 FULL agent chain in the real deployment:
#              dev login -> discovery import -> capability -> review ->
#              publish -> connection/profile -> invoke -> executor ->
#              credential broker -> egress gateway -> external HTTPS
#              backend -> response guard -> result
#   QA-DEP-02  approved egress (external HTTPS via gateway; internal peer)
#   QA-DEP-03  unapproved host refused WITH audit-log assertion (not just
#              any network error); metadata range hard-denied
#   QA-DEP-04  reachability CONTROL first (gateway can reach the target),
#              then the executor cannot reach it directly
#   QA-DEP-05  full-chain synthetic-credential canary: the MOCKKEY the
#              executor sends to the backend appears in NO log and NO
#              response payload
set -euo pipefail

cd "$(dirname "$0")/.."
export COMPOSE_FILE="deploy/docker-compose.yml"
export PP_SWITCH_INTERNAL_TOKEN="ci-internal-token-0123456789abcdef"
export PP_SWITCH_DELEGATION_SIGNING_KEY="ci-ticket-key-0123456789abcdef"
export PP_SWITCH_RESULT_ENCRYPTION_KEY="$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
export EGRESS_PROXY_TOKEN="ci-egress-token-0123456789abcdef"
export EGRESS_ALLOWED_HOSTS="httpbin.org"
export PP_SWITCH_DEMO_MODE="false"

step() { echo "::group::$1"; }
endgroup() { echo "::endgroup::"; }

step "QA-DEP-01: build image"
docker compose build
endgroup

step "QA-DEP-01: production (non-demo) boot + health"
docker compose up -d control executor egress-gateway
for i in $(seq 1 60); do
  if docker compose exec -T control python -c \
      "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8911/healthz')" 2>/dev/null; then break; fi
  sleep 2
done
docker compose exec -T control python -c \
  "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8911/healthz')"
docker compose exec -T executor python -c \
  "import urllib.request;assert urllib.request.urlopen('http://127.0.0.1:8912/healthz').status==200"
# dev login directory stays disabled outside demo mode
code=$(docker compose exec -T control python -c \
  "import urllib.request
try:
    urllib.request.urlopen('http://127.0.0.1:8911/api/v1/dev/principals')
    print('200')
except urllib.error.HTTPError as e:
    print(e.code)")
[ "$code" = "404" ] || { echo "FAIL: dev directory returned $code"; exit 1; }
echo "production boot healthy, dev surfaces off"
docker compose down
endgroup

# ---- demo leg for the execution-chain verifications ----
export PP_SWITCH_DEMO_MODE="true"
step "demo stack up"
docker compose --profile demo up -d
for i in $(seq 1 60); do
  if docker compose exec -T control python -c \
      "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8911/healthz')" 2>/dev/null; then break; fi
  sleep 2
done
endgroup

step "AUD-E2E-05: full agent chain against an external HTTPS backend"
# seed demo principals directly in the container (mirrors seed-demo.py:
# workspace + role-carrying users with KNOWN synthetic tokens)
docker compose exec -T control python - <<'PY'
import os
import uuid

from ppswitch_control.auth import hash_token
from ppswitch_db.models import PrincipalRow, WorkspaceRow
from ppswitch_db.session import create_engine_for_url, create_session_factory

engine = create_engine_for_url(os.environ["PP_SWITCH_DB_URL"])
factory = create_session_factory(engine)
with factory() as session:
    ws = session.query(WorkspaceRow).filter_by(slug="demo").one_or_none()
    if ws is None:
        ws = WorkspaceRow(id=uuid.uuid4(), slug="demo", name="Demo")
        session.add(ws)
        session.flush()
    for kind, name, token, role in (
        ("user", "Alice (ci)", "dev-token-alice-ci-001", "admin"),
        ("user", "Carol (ci)", "dev-token-carol-ci-002", "reviewer"),
    ):
        if not session.query(PrincipalRow).filter_by(
            token_hash=hash_token(token)
        ).one_or_none():
            session.add(PrincipalRow(
                id=uuid.uuid4(), workspace_id=ws.id, kind=kind,
                display_name=name, token_hash=hash_token(token),
                token_hint=token[:14] + "...", role=role,
            ))
    session.commit()
print("principals seeded")
PY

docker compose exec -T control python - <<'PY'
# everything runs INSIDE the control container against localhost:8911
import json
import urllib.request

BASE = "http://127.0.0.1:8911/api/v1"
alice_token = "dev-token-alice-ci-001"
carol_token = "dev-token-carol-ci-002"


def api(method, path, token, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.load(resp)["data"]
    except urllib.error.HTTPError as e:
        return e.code, json.load(e).get("error", {})


# 1. service + approved endpoint pointing at the external backend
_, svc = api("POST", "/services", alice_token, {"slug": "httpbin", "name": "httpbin"})
_, ep = api("POST", f"/services/{svc['id']}/endpoints", alice_token, {
    "environment": "demo",
    "base_url": "https://httpbin.org",
    "approved_hosts": ["httpbin.org"],
    "status": "approved",
})

# 2. discovery import: a minimal HAR with one GET to /uuid
har = {
    "log": {
        "version": "1.2",
        "creator": {"name": "ci", "version": "1"},
        "entries": [{
            "request": {
                "method": "GET",
                "url": "https://httpbin.org/uuid",
                "headers": [],
                "queryString": [],
            },
            "response": {
                "status": 200,
                "content": {"mimeType": "application/json",
                             "text": "{\"uuid\": \"demo\"}"},
            },
        }],
    }
}
boundary = "----ppswitchci"
body = (
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="file"; filename="httpbin.har.json"\r\n'
    f"Content-Type: application/json\r\n\r\n"
    + json.dumps(har) + "\r\n"
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="source_type"\r\n\r\nhar\r\n'
    f"--{boundary}\r\n"
    f'Content-Disposition: form-data; name="service_id"\r\n\r\n{svc["id"]}\r\n'
    f"--{boundary}--\r\n"
).encode()
req = urllib.request.Request(BASE + "/discoveries/imports", data=body, method="POST")
req.add_header("Authorization", f"Bearer {alice_token}")
req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
with urllib.request.urlopen(req) as resp:
    assert resp.status == 201, resp.read()

_, ops = api("GET", f"/operations?service_id={svc['id']}", alice_token)
op = next(o for o in ops if o["normalized_path"] == "/uuid")

# 3. capability -> review -> verify -> publish (two-human rule)
_, cap = api("POST", "/capabilities", alice_token, {
    "name": "httpbin.get_uuid", "display_name": "Get UUID",
    "operation_id": op["id"], "risk_level": "low",
    "output_schema": {"type": "object",
                      "properties": {"uuid": {"type": "string"}}},
    "allow_fields": ["uuid"],
})
for action in ("review", "verify"):
    status, _ = api("POST", f"/capabilities/{cap['id']}/{action}", carol_token,
                    {"evidence": f"ci {action}"})
    assert status == 200
status, _ = api("POST", f"/capabilities/{cap['id']}/publish", carol_token,
                {"version": "1.0.0"})
assert status == 200

# 4. connection (synthetic demo credential) + profile + binding
_, conn = api("POST", "/connections", alice_token, {
    "service_id": svc["id"], "auth_scheme": "api_key", "demo_dataset": "A"})
_, profile = api("POST", "/profiles", alice_token, {
    "service_id": svc["id"], "endpoint_id": ep["id"],
    "connection_id": conn["id"], "name": "httpbin A"})
status, _ = api("PUT", f"/active-bindings/{svc['id']}/personal", alice_token,
                {"profile_id": profile["id"], "expected_version": 0})
assert status == 200

# 5. INVOKE: agent chain through executor -> gateway -> httpbin
status, result = api("POST", "/invocations", alice_token,
                     {"capability": "httpbin.get_uuid", "arguments": {}})
assert status == 200, result
assert result["result"] and "uuid" in result["result"], result
assert "MOCKKEY" not in json.dumps(result)  # canary: no credential echoes
print("FULL CHAIN OK: invocation uuid =", result["result"]["uuid"])
PY
endgroup

step "QA-DEP-02: approved egress paths"
# external HTTPS via the gateway (executor's configured path)
docker compose exec -T executor python - <<'PY'
import os

import httpx

proxy = os.environ["PP_SWITCH_EGRESS_PROXY"]
client = httpx.Client(proxy=proxy, timeout=30.0, trust_env=False)
r = client.get("https://httpbin.org/status/200")
assert r.status_code == 200, r.status_code
print("external https via authenticated gateway: OK")
PY

# internal peer (direct, same isolated network)
docker compose exec -T executor python -c \
  "import httpx; r=httpx.Client(trust_env=False,timeout=10).get('http://mock-crm:8913/'); assert r.status_code==200; print('internal peer direct: OK')"
endgroup

step "QA-DEP-03: refusals verified against audit logs (not just errors)"
docker compose exec -T executor python - <<'PY' || true
import httpx

client = httpx.Client(proxy="http://egress-gateway:8914", timeout=8.0, trust_env=False)
try:
    client.get("https://example.com/status/200")
    raise SystemExit("FAIL: unapproved host reachable")
except SystemExit:
    raise
except httpx.HTTPError:
    print("unapproved blocked at connection level")
PY
# the gateway LOGGED the policy denial (not a random network failure)
docker compose logs egress-gateway 2>&1 | grep -q \
  "classification=host_not_in_egress_allowlist" \
  || { echo "FAIL: no policy denial in gateway audit log"; exit 1; }
echo "gateway audit log confirms policy denial"
docker compose exec -T egress-gateway python - <<'PY'
import ppswitch_egress.main as m
from ppswitch_egress.main import Deny, GatewayPolicy, validate_target

policy = GatewayPolicy(allowlist={"metadata.evil.test"})
m._resolve_all = lambda h, p: ["100.100.100.200"]  # aliyun IMDS (non-LL)
try:
    validate_target(policy, "metadata.evil.test", 443)
    raise SystemExit("FAIL: aliyun metadata allowed")
except Deny:
    print("non-link-local cloud metadata hard-denied: OK")
PY
endgroup

step "QA-DEP-04: reachability control, then direct-block"
# control: the GATEWAY container CAN reach the target IP...
docker compose exec -T egress-gateway python -c \
  "import socket; socket.setdefaulttimeout(5); socket.create_connection(('93.184.216.34', 443)); print('gateway reaches target: control OK')"
# ...while the EXECUTOR cannot reach it directly
docker compose exec -T executor python - <<'PY'
import socket

socket.setdefaulttimeout(5)
try:
    socket.create_connection(("93.184.216.34", 443))
    raise SystemExit("FAIL: executor reached the internet WITHOUT the gateway")
except OSError:
    print("executor direct egress blocked: OK")
PY
endgroup

step "QA-DEP-05: full-chain synthetic-credential canary"
if docker compose logs 2>&1 | grep -q "MOCKKEY"; then
  echo "FAIL: synthetic credential material found in container logs"; exit 1
fi
echo "no credential material in any container log: OK"
endgroup

step "teardown"
docker compose --profile demo down -v
endgroup

echo "Gate R1 deployment legs: PASS"
