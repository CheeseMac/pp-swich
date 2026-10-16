"""OS Keyring SecretProvider (FEAT-03, local trusted environment).

Resolve credentials from the operating system's secure store (Windows
Credential Manager / macOS Keychain / Linux Secret Service) via the
``keyring`` package.

Fail-closed contract (docs/09 stage 4):
- when no secure backend is available the provider raises
  ``SecretProviderUnavailable``; it NEVER falls back to plaintext files
  or environment variables;
- values are written by the human, for the human, through the
  ``ppswitch-secret`` CLI on the same machine as the executor - secret
  material never transits the control plane, the console or any agent;
- ``keyring://`` refs map to ``<service>`` entries under the
  ``ppswitch/`` namespace with the JSON payload
  ``{"auth_scheme": ..., "material": {...}}``.

Only the trusted Executor (and its local CLI) may import this module.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from ppswitch_secrets.base import (
    CredentialMaterial,
    SecretProviderUnavailable,
)

NAMESPACE = "ppswitch"
ENTRY_USER = "pp-switch-executor"


def _keyring_module():
    try:
        import keyring as keyring_module
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise SecretProviderUnavailable(
            "the 'keyring' package is not installed in this environment"
        ) from exc
    return keyring_module


class KeyringSecretProvider:
    """Resolve ``keyring://<name>`` refs from the OS secure store."""

    def __init__(self, namespace: str = NAMESPACE) -> None:
        self.namespace = namespace

    def service_for(self, name: str) -> str:
        return f"{self.namespace}/{name}"

    # -- SecretProvider protocol ------------------------------------------
    def resolve(self, secret_ref: str) -> CredentialMaterial:
        name = _ref_to_name(secret_ref)
        payload = self._get_json(name)
        if not isinstance(payload, dict):
            raise SecretProviderUnavailable(f"missing keyring entry for {secret_ref!r}")
        try:
            auth_scheme = payload["auth_scheme"]
            material = payload["material"]
            if not isinstance(material, dict):
                raise ValueError("material must be an object")
        except (KeyError, ValueError, TypeError) as exc:
            raise SecretProviderUnavailable(
                f"malformed keyring payload for {secret_ref!r}"
            ) from exc
        from ppswitch_core.enums import AuthScheme

        return CredentialMaterial(
            auth_scheme=AuthScheme(auth_scheme), material=dict(material)
        )

    # -- local CLI surface (never called from request paths) --------------
    def put(self, name: str, auth_scheme: str, material: dict[str, Any]) -> None:
        payload = json.dumps({"auth_scheme": auth_scheme, "material": material})
        keyring = _keyring_module()
        try:
            keyring.set_password(self.service_for(name), ENTRY_USER, payload)
        except keyring.errors.KeyringError as exc:
            raise SecretProviderUnavailable(
                f"OS keyring rejected the write for {name!r}: {exc}"
            ) from exc

    def delete(self, name: str) -> bool:
        keyring = _keyring_module()
        try:
            keyring.delete_password(self.service_for(name), ENTRY_USER)
            return True
        except keyring.errors.PasswordDeleteError:
            return False
        except keyring.errors.KeyringError as exc:
            raise SecretProviderUnavailable(
                f"OS keyring rejected the delete for {name!r}: {exc}"
            ) from exc

    def fingerprint(self, name: str) -> str | None:
        """sha256[:8] of the stored payload - evidence, never the value."""
        payload = self._get_json(name, missing_ok=True)
        if payload is None:
            return None
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:8]

    def _get_json(self, name: str, *, missing_ok: bool = False) -> dict | None:
        keyring = _keyring_module()
        try:
            value = keyring.get_password(self.service_for(name), ENTRY_USER)
        except keyring.errors.KeyringError as exc:
            raise SecretProviderUnavailable(
                f"OS keyring backend unavailable for {name!r}: {exc}"
            ) from exc
        if value is None:
            if missing_ok:
                return None
            raise SecretProviderUnavailable(
                f"no secret stored under keyring name {name!r} "
                "(store it with: ppswitch-secret put)"
            )
        try:
            return json.loads(value)
        except ValueError as exc:
            raise SecretProviderUnavailable(
                f"keyring entry {name!r} is not valid JSON"
            ) from exc


def _ref_to_name(secret_ref: str) -> str:
    prefix = "keyring://"
    if not secret_ref.startswith(prefix):
        raise SecretProviderUnavailable(
            f"unsupported ref scheme in {secret_ref!r} (expected keyring://)"
        )
    name = secret_ref[len(prefix) :]
    if not name or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789/_-" for c in name):
        raise SecretProviderUnavailable(f"invalid keyring name in {secret_ref!r}")
    return name


class DispatchingSecretProvider:
    """Route by ref scheme: keyring:// -> OS keyring, else -> mock.

    Used as the executor default so a single deployment can serve demo
    (mock://) and real local (keyring://) connections simultaneously.
    The keyring provider is constructed lazily; mock refs never touch
    the OS store.
    """

    def __init__(self) -> None:
        from ppswitch_secrets.mock import MockSecretProvider

        self._mock = MockSecretProvider()
        self._keyring: KeyringSecretProvider | None = None

    @property
    def mock(self):
        return self._mock

    @property
    def keyring(self) -> KeyringSecretProvider:
        if self._keyring is None:
            self._keyring = KeyringSecretProvider()
        return self._keyring

    def resolve(self, secret_ref: str) -> CredentialMaterial:
        if secret_ref.startswith("keyring://"):
            return self.keyring.resolve(secret_ref)
        return self._mock.resolve(secret_ref)
