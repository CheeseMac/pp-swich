"""Cross-language golden contract (FEAT-01).

The committed fixture ``tests/fixtures/collector-golden/observations.json``
is produced BY THE EXTENSION'S TYPESCRIPT ENGINE (apps/browser-extension
tests/golden.test.ts pins the TS side against the same bytes). Feeding it
through the Python collector parser here proves the two halves of the
wire contract stay in sync: what the extension uploads is exactly what
``build_candidates_from_collector`` understands.

Regenerate the fixture with ``REGEN_GOLDEN=1 pnpm test`` (then update the
expectations below if the scenario changed on purpose).
"""

from __future__ import annotations

import json
from pathlib import Path

from ppswitch_discovery.parsers.collector import build_candidates_from_collector

REPO_ROOT = Path(__file__).resolve().parents[3]
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "collector-golden" / "observations.json"


def _load() -> dict:
    assert GOLDEN.exists(), f"missing golden fixture: {GOLDEN}"
    return {"observations": json.loads(GOLDEN.read_text(encoding="utf-8"))}


def test_extension_output_parses_into_expected_candidates():
    result = build_candidates_from_collector(_load())
    assert result.unsanitized_fields == []
    by_template = {c.template: c for c in result.candidates}

    # numeric and uuid identifiers are DIFFERENT route templates by
    # design (same behavior as the HAR pipeline): /api/customers/1024
    # and /api/customers/<uuid> do not merge into one candidate
    detail = by_template["/api/customers/{id}"]
    assert detail.method == "GET"
    assert detail.sample_count == 2
    assert detail.query_keys == ["verbose"]
    assert detail.status_codes == [200]
    props = detail.output_schema["properties"]
    assert props["id"]["type"] == "string"
    assert props["display_name"]["type"] == "string"
    assert props["vip"]["type"] == "boolean"
    assert props["score"]["type"] == "number"

    uuid_detail = by_template["/api/customers/{uuid}"]
    assert uuid_detail.sample_count == 1
    assert uuid_detail.query_keys == []

    search = by_template["/api/customers/search"]
    assert search.method == "POST"
    assert search.query_keys == ["limit", "page"]
    assert search.input_schema["body"]["properties"]["filters"]["properties"][
        "region"
    ]["type"] == "string"
    assert search.output_schema["properties"]["results"]["items"]["properties"][
        "total"
    ]["type"] == "number"

    gql = by_template["/graphql#getCustomer"]
    assert gql.sample_count == 1

    delete = by_template["/api/orders/{id}"]
    assert delete.method == "DELETE"
    assert delete.status_codes == [404]


def test_golden_fixture_is_value_free():
    """Privacy invariant across the wire: no leaf may carry data."""
    document = _load()
    for observation in document["observations"]:
        for value in observation["query"].values():
            assert value == ""
        for key in ("request_body", "response_body"):
            body = observation[key]
            if body is None:
                continue

            def walk(node: object, where: str) -> None:
                if isinstance(node, str):
                    assert node == "", f"data value leaked into {where}"
                elif isinstance(node, bool):
                    assert node is False
                elif isinstance(node, (int, float)):
                    assert node in (0, 0.5), f"data number leaked into {where}: {node}"
                elif isinstance(node, list):
                    for item in node:
                        walk(item, where)
                elif isinstance(node, dict):
                    for item in node.values():
                        walk(item, where)

            walk(body, key)
