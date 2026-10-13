"""Client-side reserved-argument guard.

Mirrors the server-side policy so misuse fails fast in user code. This is
convenience only - the server re-validates everything (never trust the
client, docs/05-SECURITY.md section 2).
"""

from __future__ import annotations

from typing import Any

from ppswich_policy import RESERVED_ARGUMENT_NAMES


def assert_no_reserved_arguments(arguments: dict[str, Any]) -> None:
    def walk(mapping: dict[str, Any]) -> None:
        for key, value in mapping.items():
            if str(key).strip().lower() in RESERVED_ARGUMENT_NAMES:
                raise ValueError(
                    f"Argument '{key}' is reserved; the SDK accepts business "
                    "parameters only (no credentials, URLs or identity fields)"
                )
            if isinstance(value, dict):
                walk(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        walk(item)

    if not isinstance(arguments, dict):
        raise ValueError("arguments must be a dict of business parameters")
    walk(arguments)
