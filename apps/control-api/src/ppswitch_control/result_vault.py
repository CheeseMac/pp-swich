"""Result vault (PR-DATA-12): at-rest protection for invocation results.

Storage policy decided at write time:

- data classification: ``restricted`` (high/critical risk capabilities) is
  metadata-only unless explicitly opted in; ``normal`` results are stored;
- retention: every stored result carries ``result_expires_at`` and reads
  past the deadline return nothing;
- encryption: when a key is configured the payload is Fernet-encrypted
  into ``result_encrypted`` and the plaintext JSON column stays NULL.

Encryption NEVER replaces read-time authorization (SEC-052): every read
path re-authorizes first and decrypts second.

review6 DATA-17: key rotation window. ``ResultVault`` accepts a primary
key (used for writes) plus previous keys (read-only window) via
MultiFernet semantics - rotating the configured key does not orphan
existing ciphertexts, and ``reencrypt_results`` migrates them to the
primary key.
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class ResultVault:
    def __init__(self, key: str = "", previous_keys: list[str] | None = None) -> None:
        ferments = [Fernet(k.encode("utf-8")) for k in [key, *(previous_keys or [])] if k]
        self._multi = MultiFernet(ferments) if ferments else None

    @property
    def encrypts(self) -> bool:
        return self._multi is not None

    def encrypt(self, payload: dict[str, Any]) -> str:
        """Encrypt under the PRIMARY key (first in the rotation window)."""
        assert self._multi is not None
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        return self._multi.encrypt(raw).decode("ascii")

    def decrypt(self, token: str) -> dict[str, Any] | None:
        if self._multi is None:
            return None
        try:
            raw = self._multi.decrypt(token.encode("ascii"))
        except (InvalidToken, ValueError):
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None


def classify_result(risk_level: str) -> str:
    """Data classification before storage (review5 PR-DATA-12).

    NOTE (review6): risk level is a proxy, not content classification -
    a low-risk tool may still return sensitive data. A dedicated result
    classification policy is tracked for the production-readiness audit.
    """
    return "restricted" if risk_level in ("high", "critical") else "normal"
