"""Control plane settings.

All values are non-secret configuration. The internal token default is a
synthetic dev value shared with the executor; production deployments set a
real secret via deployment-platform injection (never committed, never in
``.env`` of a repo an AI agent can read - docs/18-SECRETLESS-DEVELOPMENT.md).
"""

from __future__ import annotations

import os

from ppswich_db.session import default_db_url
from pydantic import BaseModel


class ControlSettings(BaseModel):
    db_url: str
    executor_url: str = "http://127.0.0.1:8912"
    internal_token: str = os.environ.get(
        "PP_SWICH_INTERNAL_TOKEN", "dev-internal-token-synthetic"
    )
    demo_mode: bool = os.environ.get("PP_SWICH_DEMO_MODE", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    auto_migrate: bool = os.environ.get("PP_SWICH_AUTO_MIGRATE", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    max_import_bytes: int = 5 * 1024 * 1024
    # local runtime directory (gitignored) where seed-demo writes synthetic
    # dev tokens so the demo login can list them; never committed
    dev_tokens_path: str = "data/dev-tokens.json"

    @classmethod
    def from_env(cls) -> ControlSettings:
        return cls(
            db_url=default_db_url(),
            executor_url=os.environ.get("PP_SWICH_EXECUTOR_URL", "http://127.0.0.1:8912"),
            dev_tokens_path=os.environ.get("PP_SWICH_DEV_TOKENS", "data/dev-tokens.json"),
        )
