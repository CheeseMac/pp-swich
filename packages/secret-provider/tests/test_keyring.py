"""FEAT-03 keyring provider tests (in-process fake backend).

The OS store is never touched: keyring.set_keyring swaps the process to
an in-memory backend, mirroring what e2e tests do.
"""

from __future__ import annotations

import json

import keyring
import pytest
from ppswitch_secrets.base import SecretProviderUnavailable
from ppswitch_secrets.keyring import (
    DispatchingSecretProvider,
    KeyringSecretProvider,
    _ref_to_name,
)
from ppswitch_secrets.mock import MockSecretProvider


class FakeKeyring:
    """Minimal in-memory keyring backend."""

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.store.pop((service, username), None)


@pytest.fixture()
def fake_keyring(monkeypatch):
    backend = FakeKeyring()
    monkeypatch.setattr(keyring, "set_keyring", lambda kr: None)  # keep real store untouched
    # route the provider's keyring calls to the fake regardless of platform
    import ppswitch_secrets.keyring as kr_module

    monkeypatch.setattr(kr_module, "_keyring_module", lambda: _FakeModule(backend))
    return backend


class _FakeModule:
    """Quacks like the keyring module for the provider's usage."""

    def __init__(self, backend: FakeKeyring) -> None:
        self._backend = backend

        class errors:
            class KeyringError(Exception):
                pass

            class PasswordDeleteError(KeyringError):
                pass

        self.errors = errors()

    def get_password(self, service, username):
        return self._backend.get_password(service, username)

    def set_password(self, service, username, password):
        self._backend.set_password(service, username, password)

    def delete_password(self, service, username):
        if self._backend.get_password(service, username) is None:
            raise self.errors.PasswordDeleteError("not found")
        self._backend.delete_password(service, username)


def test_put_resolve_roundtrip(fake_keyring):
    provider = KeyringSecretProvider()
    provider.put("crm-key-a", "api_key", {"api_key": "MOCKKEY-DEMO-A-feat03"})
    material = provider.resolve("keyring://crm-key-a")
    assert material.material == {"api_key": "MOCKKEY-DEMO-A-feat03"}
    assert material.auth_scheme.value == "api_key"


def test_missing_secret_fails_closed(fake_keyring):
    provider = KeyringSecretProvider()
    with pytest.raises(SecretProviderUnavailable, match="ppswitch-secret put"):
        provider.resolve("keyring://never-stored")


def test_malformed_payload_fails_closed(fake_keyring):
    fake_keyring.store[("ppswitch/bad", "pp-switch-executor")] = "not json"
    with pytest.raises(SecretProviderUnavailable, match="not valid JSON"):
        KeyringSecretProvider().resolve("keyring://bad")


def test_invalid_ref_scheme_rejected(fake_keyring):
    with pytest.raises(SecretProviderUnavailable):
        KeyringSecretProvider().resolve("mock://connections/xyz")
    with pytest.raises(SecretProviderUnavailable):
        _ref_to_name("keyring://Bad Name!")


def test_fingerprint_never_exposes_value(fake_keyring):
    provider = KeyringSecretProvider()
    provider.put("crm-key-a", "api_key", {"api_key": "MOCKKEY-DEMO-A-feat03"})
    digest = provider.fingerprint("crm-key-a")
    assert len(digest) == 8
    assert int(digest, 16) >= 0  # hex
    assert "MOCKKEY" not in digest


def test_delete(fake_keyring):
    provider = KeyringSecretProvider()
    provider.put("temp", "bearer", {"token": "MOCK-t"})
    assert provider.delete("temp") is True
    assert provider.delete("temp") is False
    with pytest.raises(SecretProviderUnavailable):
        provider.resolve("keyring://temp")


def test_dispatcher_routes_by_scheme(fake_keyring):
    dispatcher = DispatchingSecretProvider()
    # mock refs keep working (demo connections)
    mock_material = dispatcher.resolve("mock://connections/x?dataset=A")
    assert mock_material.material["api_key"].startswith("MOCKKEY-DEMO-A-")
    # keyring refs reach the OS-store path
    fake_keyring.store[("ppswitch/k", "pp-switch-executor")] = json.dumps(
        {"auth_scheme": "bearer", "material": {"token": "MOCK-b"}}
    )
    assert dispatcher.resolve("keyring://k").material == {"token": "MOCK-b"}


def test_mock_provider_still_directly_usable():
    # injection path for pure-mock deployments stays intact
    assert MockSecretProvider() is not None
