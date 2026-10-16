"""pp switch SecretProvider abstraction.

Only the trusted Executor may import this package (docs/08 dependency
rules). The control plane and every public SDK must stay independent of it.
"""

from ppswitch_secrets.base import (
    CredentialMaterial,
    SecretProvider,
    SecretProviderUnavailable,
)
from ppswitch_secrets.keyring import DispatchingSecretProvider, KeyringSecretProvider
from ppswitch_secrets.mock import MockSecretProvider, UnavailableSecretProvider

__all__ = [
    "CredentialMaterial",
    "DispatchingSecretProvider",
    "KeyringSecretProvider",
    "MockSecretProvider",
    "SecretProvider",
    "SecretProviderUnavailable",
    "UnavailableSecretProvider",
]
