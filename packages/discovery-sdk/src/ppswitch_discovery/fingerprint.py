"""Operation fingerprints.

A fingerprint identifies a candidate operation within one service:
``method + normalized path template + sorted query parameter names``.
GraphQL operations already carry the operation name inside the template.
"""

from __future__ import annotations

import hashlib


def fingerprint_operation(method: str, template: str, query_keys: list[str] | None = None) -> str:
    canonical_query = ",".join(sorted(query_keys or []))
    material = f"{method.upper()}|{template}|{canonical_query}".encode()
    return hashlib.sha256(material).hexdigest()[:16]
