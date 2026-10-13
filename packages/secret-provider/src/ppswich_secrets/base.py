"""SecretProvider protocol.

A provider resolves an opaque ``secret_ref`` into credential material for
exactly one pre-approved request. It never logs, echoes or exports
material, and it is unreachable from any public API surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ppswich_core.enums import AuthScheme


class SecretProviderUnavailable(Exception):
    """The backing secret store is unreachable: the executor must fail closed."""


@dataclass(frozen=True)
class CredentialMaterial:
    auth_scheme: AuthScheme
    material: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class SecretProvider(Protocol):
    def resolve(self, secret_ref: str) -> CredentialMaterial:
        """Return credential material for one trusted execution.

        Raises ``SecretProviderUnavailable`` when the store cannot be
        reached - never falls back to plaintext files or environment
        variables (docs/09 stage 4: "错误场景一律不回退到 .env 明文凭证").
        """
        ...  # pragma: no cover
