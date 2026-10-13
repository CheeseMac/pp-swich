import pytest
from ppswitch_core.enums import AuthScheme
from ppswitch_secrets import (
    MockSecretProvider,
    SecretProvider,
    SecretProviderUnavailable,
    UnavailableSecretProvider,
)


def test_mock_provider_roundtrip() -> None:
    provider = MockSecretProvider()
    provider.seed(
        "mock://connections/1",
        {"auth_scheme": "api_key", "material": {"api_key": "MOCKKEY-A"}},
    )
    assert isinstance(provider, SecretProvider)
    resolved = provider.resolve("mock://connections/1")
    assert resolved.auth_scheme == AuthScheme.API_KEY
    assert resolved.material["api_key"] == "MOCKKEY-A"


def test_mock_provider_missing_ref_fails_closed() -> None:
    provider = MockSecretProvider()
    with pytest.raises(SecretProviderUnavailable):
        provider.resolve("mock://missing")


def test_unavailable_provider_always_fails() -> None:
    provider = UnavailableSecretProvider()
    with pytest.raises(SecretProviderUnavailable):
        provider.resolve("mock://anything")


def test_deterministic_demo_derivation() -> None:
    """Refs carrying a dataset derive stable synthetic keys across restarts."""
    provider = MockSecretProvider()
    ref = "mock://connections/abc?dataset=A"
    first = provider.resolve(ref)
    second = MockSecretProvider().resolve(ref)
    assert first.material["api_key"].startswith("MOCKKEY-DEMO-A-")
    assert first.material["api_key"] == second.material["api_key"]

    b_ref = "mock://connections/abc?dataset=B"
    b_key = provider.resolve(b_ref).material["api_key"]
    assert b_key.startswith("MOCKKEY-DEMO-B-")
    assert b_key != first.material["api_key"]


def test_derivation_rejects_refs_without_dataset() -> None:
    provider = MockSecretProvider()
    with pytest.raises(SecretProviderUnavailable):
        provider.resolve("mock://connections/abc")
    with pytest.raises(SecretProviderUnavailable):
        provider.resolve("openbao://workspaces/w1/connections/c1")
