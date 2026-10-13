"""Invocation plan signing and replay protection.

The control plane signs each pre-approved execution plan; the executor
verifies before executing (docs/02-ARCHITECTURE.md section 8: the executor
must independently validate even internal traffic).

The dev deployment authenticates the internal channel with a shared token
plus HMAC-SHA256 over the canonical plan JSON. Production replaces this
with workload identity / mTLS - the verify entry point stays the same.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import OrderedDict


def canonical_plan_bytes(plan: dict) -> bytes:
    return json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sign_plan(plan: dict, internal_token: str) -> str:
    return hmac.new(internal_token.encode("utf-8"), canonical_plan_bytes(plan),
        hashlib.sha256).hexdigest()


def verify_plan_signature(plan: dict, signature: str, internal_token: str) -> bool:
    expected = sign_plan(plan, internal_token)
    return hmac.compare_digest(expected, signature)


class NonceStore:
    """Bounded replay cache: one-time nonces with deadline-based pruning."""

    def __init__(self, max_entries: int = 100_000) -> None:
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._max_entries = max_entries

    def check_and_remember(self, nonce: str, deadline_epoch_s: float) -> bool:
        """Return True if the nonce is fresh; False on replay."""
        now = time.time()
        # prune expired entries opportunistically
        while self._seen:
            _oldest_key, oldest_deadline = next(iter(self._seen.items()))
            if oldest_deadline < now:
                self._seen.popitem(last=False)
            else:
                break
        if nonce in self._seen:
            return False
        self._seen[nonce] = max(deadline_epoch_s, now + 60)
        while len(self._seen) > self._max_entries:
            self._seen.popitem(last=False)
        return True


def fresh_deadline(timeout_s: float = 30.0) -> float:
    return time.time() + timeout_s
