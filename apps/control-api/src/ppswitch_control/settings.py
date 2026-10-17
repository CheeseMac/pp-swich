"""Control plane settings.

All values are non-secret configuration. The internal token default is a
synthetic dev value shared with the executor; production deployments set a
real secret via deployment-platform injection (never committed, never in
``.env`` of a repo an AI agent can read - docs/18-SECRETLESS-DEVELOPMENT.md).
"""

from __future__ import annotations

import os

from ppswitch_db.session import default_db_url
from pydantic import BaseModel


class ControlSettings(BaseModel):
    db_url: str
    executor_url: str = "http://127.0.0.1:8912"
    internal_token: str = os.environ.get(
        "PP_SWITCH_INTERNAL_TOKEN", "dev-internal-token-synthetic"
    )
    # review PR-SEC-05: demo mode (mock endpoints, dev login directory,
    # deterministic mock credentials) is explicitly opt-in only
    demo_mode: bool = os.environ.get("PP_SWITCH_DEMO_MODE", "false").lower() in (
        "1",
        "true",
        "yes",
    )
    auto_migrate: bool = os.environ.get("PP_SWITCH_AUTO_MIGRATE", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    max_import_bytes: int = 5 * 1024 * 1024
    # review3 / PR-DEP-14 (application side): endpoints carrying real
    # credentials must be HTTPS by default; plain HTTP needs this explicit
    # operator opt-in (demo mode implies it for localhost mocks)
    allow_plain_http: bool = os.environ.get(
        "PP_SWITCH_ALLOW_PLAIN_HTTP", "false"
    ).lower() in ("1", "true", "yes")
    # local runtime directory (gitignored) where seed-demo writes synthetic
    # dev tokens so the demo login can list them; never committed
    dev_tokens_path: str = "data/dev-tokens.json"
    # review4: production signs delegation context tickets with a DEDICATED
    # key, never the executor-internal shared secret; empty falls back to
    # internal_token for local/demo use and fails production validation
    delegation_signing_key: str = os.environ.get("PP_SWITCH_DELEGATION_SIGNING_KEY", "")

    # PR-DATA-12 (review5): result retention and at-rest encryption
    result_retention_days: int = int(
        os.environ.get("PP_SWITCH_RESULT_RETENTION_DAYS", "7")
    )
    # high/critical risk results are metadata-only unless opted in
    store_high_risk_results: bool = os.environ.get(
        "PP_SWITCH_STORE_HIGH_RISK_RESULTS", "false"
    ).lower() in ("1", "true", "yes")
    # Fernet key (urlsafe base64 of 32 bytes); empty = plaintext column
    result_encryption_key: str = os.environ.get("PP_SWITCH_RESULT_ENCRYPTION_KEY", "")
    # DATA-17: comma-separated PREVIOUS keys kept as a read-only window
    # during rotation; writes always use result_encryption_key
    result_previous_keys: str = os.environ.get(
        "PP_SWITCH_RESULT_ENCRYPTION_PREVIOUS_KEYS", ""
    )

    # FEAT-01 browser collector: one-time pairing code lifetime, issued
    # collector token lifetime and per-batch observation cap
    collector_pairing_ttl_seconds: int = int(
        os.environ.get("PP_SWITCH_COLLECTOR_PAIRING_TTL", "600")
    )
    collector_token_ttl_seconds: int = int(
        os.environ.get("PP_SWITCH_COLLECTOR_TOKEN_TTL", "28800")
    )
    collector_max_observations: int = int(
        os.environ.get("PP_SWITCH_COLLECTOR_MAX_OBSERVATIONS", "200")
    )

    # SECRET-06 (docs/41): shared-keyring isolation. Personal mode (the
    # default) keeps the FEAT-03 first-activation experience; shared mode
    # requires an explicit namespace id (claims are machine-global) and
    # the executor side demands its own keys (fail-closed there).
    shared_keyring: bool = os.environ.get(
        "PP_SWITCH_SHARED_KEYRING", "false"
    ).lower() in ("1", "true", "yes")
    namespace_id: str = os.environ.get("PP_SWITCH_NAMESPACE_ID", "local")
    # FIX-02: authorization signing keys, separate trust channels from the
    # transport token. Shared deployments MUST set them explicitly
    # (validated at startup); personal mode falls back to the internal
    # token so the single-user Alpha needs no extra secrets.
    claim_ticket_key: str = os.environ.get("PP_SWITCH_CLAIM_TICKET_KEY", "")
    plan_signing_key: str = os.environ.get("PP_SWITCH_PLAN_SIGNING_KEY", "")

    @property
    def effective_delegation_key(self) -> str:
        return self.delegation_signing_key or self.internal_token

    @property
    def effective_claim_ticket_key(self) -> str:
        return self.claim_ticket_key or self.internal_token

    @property
    def effective_plan_signing_key(self) -> str:
        return self.plan_signing_key or self.internal_token

    @classmethod
    def from_env(cls) -> ControlSettings:
        return cls(
            db_url=default_db_url(),
            executor_url=os.environ.get("PP_SWITCH_EXECUTOR_URL", "http://127.0.0.1:8912"),
            dev_tokens_path=os.environ.get("PP_SWITCH_DEV_TOKENS", "data/dev-tokens.json"),
        )
