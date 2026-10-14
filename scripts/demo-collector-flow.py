#!/usr/bin/env python
"""FEAT-01 demo: pairing + observation submission against a running stack.

Simulates exactly what the Chrome extension's service worker does
(pair -> auto/manual submit with the SAME wire format the TypeScript
engine produces - tests/fixtures/collector-golden/observations.json),
so the console ends up showing real collector-sourced drafts.

Usage (stack must be up: scripts/dev-up.py --ui + seed-demo.py):
    uv run python scripts/demo-collector-flow.py [--control URL]

Credentials are used transiently from data/dev-tokens.json; nothing is
printed or persisted beyond the process lifetime.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "collector-golden" / "observations.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--control", default="http://127.0.0.1:8911")
    args = parser.parse_args(argv)

    tokens_path = REPO_ROOT / "data" / "dev-tokens.json"
    tokens: dict[str, str] = json.loads(tokens_path.read_text(encoding="utf-8"))
    alice_token = next(iter(tokens.values()))  # seeded admin (Alice)

    # trust_env=False: the OS proxy must never intercept localhost (known pitfall)
    with httpx.Client(base_url=args.control, trust_env=False, timeout=15) as client:
        services = client.get(
            "/api/v1/services", headers={"Authorization": f"Bearer {alice_token}"}
        ).raise_for_status().json()["data"]
        service = next((s for s in services if s["slug"] == "crm"), services[0])
        print(f"service: {service['slug']} ({service['id']})")

        pairing = client.post(
            f"/api/v1/services/{service['id']}/collector/pairings",
            headers={"Authorization": f"Bearer {alice_token}"},
        ).raise_for_status().json()["data"]
        code = pairing["pairing_code"]
        print(f"pairing created: {pairing['id']} code {code} (valid 10 min)")

        paired = client.post(
            "/api/v1/collector/pair", json={"pairing_code": code}
        ).raise_for_status().json()["data"]
        collector_token = paired["collector_token"]
        print(f"collector token issued, expires {paired['token_expires_at']}")

        observations = json.loads(GOLDEN.read_text(encoding="utf-8"))
        summary = client.post(
            "/api/v1/collector/observations",
            json={"observations": observations},
            headers={"Authorization": f"Bearer {collector_token}"},
        ).raise_for_status().json()["data"]
        print(
            "submitted "
            f"{len(observations)} observations -> {summary['candidates']} candidates, "
            f"{summary['operations_created']} created, {summary['operations_updated']} updated"
        )

        ops = client.get(
            f"/api/v1/operations?service_id={service['id']}",
            headers={"Authorization": f"Bearer {alice_token}"},
        ).raise_for_status().json()["data"]
        for op in ops:
            print(f"  {op['status']:<10} {op['method']:<7} {op['normalized_path']}")

    print("done - open http://localhost:5173 and check the Discovery page")
    return 0


if __name__ == "__main__":
    sys.exit(main())
