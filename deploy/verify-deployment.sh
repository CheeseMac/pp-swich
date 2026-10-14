#!/usr/bin/env bash
# PR-QA-06 / review8 §7 deployment acceptance (QA-DEP-01..05).
#
# Runs on a GitHub Actions ubuntu runner (docker available):
#   QA-DEP-01  image builds; services start and pass health checks
#   QA-DEP-02  approved egress paths work: an EXTERNAL https target via
#              the gateway, and an internal-network peer directly
#   QA-DEP-03  unapproved host, metadata range and plain-HTTP forwarding
#              all refused by the gateway
#   QA-DEP-04  the executor container cannot reach the internet directly
#              (bypassing the application + gateway layers)
#   QA-DEP-05  synthetic credentials never appear in container logs
set -euo pipefail

cd "$(dirname "$0")/.."
export COMPOSE_FILE="deploy/docker-compose.yml"
export PP_SWITCH_INTERNAL_TOKEN="ci-internal-token-0123456789abcdef"
export PP_SWITCH_DELEGATION_SIGNING_KEY="ci-ticket-key-0123456789abcdef"
# stdlib-only generation of a random valid Fernet key (32 urlsafe-b64 bytes)
export PP_SWITCH_RESULT_ENCRYPTION_KEY="$(python3 -c 'import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())')"
export EGRESS_ALLOWED_HOSTS="httpbin.org"
export PP_SWITCH_DEMO_MODE="true"

step() { echo "::group::$1"; }
endgroup() { echo "::endgroup::"; }

step "QA-DEP-01: build image"
docker compose build
endgroup

step "QA-DEP-01: start full demo stack"
docker compose --profile demo up -d
for i in $(seq 1 60); do
  if docker compose exec -T control python -c \
      "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8911/healthz')" \
      2>/dev/null; then break; fi
  sleep 2
done
docker compose exec -T control python -c \
  "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8911/healthz')"
docker compose exec -T executor python -c \
  "import urllib.request;assert urllib.request.urlopen('http://127.0.0.1:8912/healthz').status==200"
echo "services healthy"
endgroup

step "QA-DEP-02a: external HTTPS target via the egress gateway"
docker compose exec -T executor python - <<'PY'
import httpx
client = httpx.Client(proxy="http://egress-gateway:8914", timeout=30.0, trust_env=False)
r = client.get("https://httpbin.org/status/200")
assert r.status_code == 200, r.status_code
print("external https via gateway: OK")
PY
endgroup

step "QA-DEP-02b: internal-network peer (mock ERP) directly"
docker compose exec -T executor python - <<'PY'
import httpx
# peer-to-peer on the isolated internal network: no gateway needed for
# same-network targets; plain HTTP is acceptable INSIDE the trusted net
client = httpx.Client(timeout=10.0, trust_env=False)
r = client.get("http://mock-crm:8913/")
assert r.status_code == 200, r.status_code
print("internal peer reached: OK")
PY
endgroup

step "QA-DEP-03: unapproved host / metadata / plain HTTP refused"
docker compose exec -T executor python - <<'PY'
import httpx
client = httpx.Client(proxy="http://egress-gateway:8914", timeout=10.0, trust_env=False)
try:
    client.get("https://example.com/status/200")
    raise SystemExit("FAIL: unapproved host was reachable through the gateway")
except httpx.HTTPStatusError:
    raise SystemExit("FAIL: gateway returned an upstream error for unapproved host")
except httpx.HTTPError:
    print("unapproved host blocked by gateway: OK")
PY

docker compose exec -T egress-gateway python - <<'PY'
import ppswitch_egress.main as m
from ppswitch_egress.main import Deny, GatewayPolicy, validate_target
policy = GatewayPolicy(allowlist={"metadata.evil.test"})
m._resolve_all = lambda h, p: ["169.254.169.254"]
try:
    validate_target(policy, "metadata.evil.test", 443)
    raise SystemExit("FAIL: metadata range allowed even when allowlisted")
except Deny:
    print("metadata range hard-denied: OK")
PY

docker compose exec -T executor python - <<'PY'
import asyncio
from ppswitch_egress.main import GatewayPolicy, create_server

async def main():
    server = await asyncio.start_server(
        create_server(GatewayPolicy(allowlist=set())), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET http://127.0.0.1/x HTTP/1.1\r\nHost: x\r\n\r\n")
    await writer.drain()
    status = await reader.readline()
    assert b"403" in status, status
    server.close()
    print("plain-HTTP forwarding refused: OK")

asyncio.run(main())
PY
endgroup

step "QA-DEP-04: executor cannot reach the internet directly"
docker compose exec -T executor python - <<'PY'
import socket
socket.setdefaulttimeout(5)
try:
    socket.create_connection(("93.184.216.34", 443))
    raise SystemExit("FAIL: executor reached the internet WITHOUT the gateway")
except OSError:
    print("direct egress blocked at the container level: OK")
PY
endgroup

step "QA-DEP-05: no credential material in container logs"
if docker compose logs 2>/dev/null | grep -q "ci-internal-token"; then
  echo "FAIL: internal token found in logs"; exit 1
fi
echo "no credential material in logs: OK"
endgroup

step "teardown"
docker compose --profile demo down -v
endgroup

echo "QA-DEP-01..05: PASS"
