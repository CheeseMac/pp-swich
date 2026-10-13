"""Adapter interface: one approved transport execution."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ppswitch_core.entities import InvocationPlan
from ppswitch_secrets import CredentialMaterial


@runtime_checkable
class OperationAdapter(Protocol):
    def execute(self, plan: InvocationPlan, credential: CredentialMaterial) -> Any:
        """Execute one authorized plan and return the raw upstream body.

        The returned value is NOT yet safe to hand to a caller: the response
        guard must project it through the capability allowlist first.
        """
        ...  # pragma: no cover
