"""In-process mock adapter.

Used for unit tests and offline development: executes against a synthetic
in-memory backend instead of the network. Never used for production
endpoints (mock:// base URLs never pass endpoint review).
"""

from __future__ import annotations

from typing import Any

from ppswitch_core.entities import InvocationPlan
from ppswitch_secrets import CredentialMaterial


class MockAdapter:
    def execute(self, plan: InvocationPlan, credential: CredentialMaterial) -> Any:
        customer_id = plan.arguments.get("customer_id", "C-UNKNOWN")
        # deliberately leaky payload: the response guard must strip it
        return {
            "id": str(customer_id),
            "display_name": "Mock Adapter Customer",
            "access_token": "CANARY_TOKEN_NOT_REAL",
            "internal_note": "should never escape the guard",
        }
