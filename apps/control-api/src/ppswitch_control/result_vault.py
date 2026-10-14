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
"""

from __future__ import annotations

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class ResultVault:
    def __init__(self, key: str = "") -> None:
        self._fernet = Fernet(key.encode("utf-8")) if key else None

    @property
    def encrypts(self) -> bool:
        return self._fernet is not None

    def encrypt(self, payload: dict[str, Any]) -> str:
        assert self._fernet is not None
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        return self._fernet.encrypt(raw).decode("ascii")

    def decrypt(self, token: str) -> dict[str, Any] | None:
        if self._fernet is None:
            return None
        try:
            raw = self._fernet.decrypt(token.encode("ascii"))
        except (InvalidToken, ValueError):
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return None


def classify_result(risk_level: str) -> str:
    """Data classification before storage (review5 PR-DATA-12)."""
    return "restricted" if risk_level in ("high", "critical") else "normal"
