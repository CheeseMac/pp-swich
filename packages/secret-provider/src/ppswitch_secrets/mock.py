"""Mock SecretProvider for development and tests.

Two modes:
1. explicitly seeded in-memory values (``seed``) for unit tests;
2. deterministic derivation for ``mock://connections/<id>?dataset=A|B``
   refs: the executor derives a synthetic ``MOCKKEY-DEMO-<dataset>-<hash>``
   from the opaque ref itself, so a restart needs no re-provisioning and no
   secret value ever crosses the control plane.

Values follow the ``MOCKKEY-*`` convention so no real credential can ever
be confused with demo material. Production deployments plug in OS-keyring
or OpenBao implementations instead (docs/05-SECURITY.md section 4).
"""

from __future__ import annotations

import hashlib
from urllib.parse import urlsplit

from ppswitch_secrets.base import (
    CredentialMaterial,
    SecretProviderUnavailable,
)


class MockSecretProvider:
    """In-memory + deterministic mock provider."""

    def __init__(self) -> None:
        self._store: dict[str, dict] = {}

    def seed(self, secret_ref: str, material: dict) -> None:
        self._store[secret_ref] = dict(material)

    def resolve(self, secret_ref: str) -> CredentialMaterial:
        from ppswitch_core.enums import AuthScheme

        entry = self._store.get(secret_ref)
        if entry is not None:
            return CredentialMaterial(
                auth_scheme=AuthScheme(entry.get("auth_scheme", "api_key")),
                material=entry.get("material", {}),
            )
        return self._derive_demo_key(secret_ref)

    @staticmethod
    def _derive_demo_key(secret_ref: str) -> CredentialMaterial:
        from ppswitch_core.enums import AuthScheme

        split = urlsplit(secret_ref)
        if split.scheme != "mock":
            raise SecretProviderUnavailable(f"unsupported ref scheme in {secret_ref!r}")
        query = dict(part.split("=", 1) for part in split.query.split("&") if "=" in part)
        dataset = query.get("dataset")
        if dataset not in ("A", "B"):
            raise SecretProviderUnavailable(f"no mock secret for ref {secret_ref!r}")
        digest = hashlib.sha256(secret_ref.encode("utf-8")).hexdigest()[:8]
        return CredentialMaterial(
            auth_scheme=AuthScheme.API_KEY,
            material={"api_key": f"MOCKKEY-DEMO-{dataset}-{digest}"},
        )

    def __contains__(self, secret_ref: str) -> bool:
        return secret_ref in self._store


class UnavailableSecretProvider:
    """Always unavailable: models the vault outage path (SEC-013, fail closed)."""

    def resolve(self, secret_ref: str) -> CredentialMaterial:  # pragma: no cover
        raise SecretProviderUnavailable("secret provider intentionally unavailable")
