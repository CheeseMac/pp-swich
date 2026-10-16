"""ppswitch-secret: local CLI for the OS keyring (FEAT-03).

Run by the HUMAN on the machine that runs the executor:

    uv run ppswitch-secret put crm-key-a --scheme api_key
    uv run ppswitch-secret fingerprint crm-key-a
    uv run ppswitch-secret delete crm-key-a

The secret VALUE is read from a hidden prompt or stdin - never from a
command-line argument (shell history / process listings must not see
it). The console then references this name as the connection's
secret_ref (``keyring://crm-key-a``).
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ppswitch-secret",
        description="Store pp-switch connection credentials in the OS keyring",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    put = sub.add_parser("put", help="store or update a secret")
    put.add_argument("name", help="keyring name, e.g. crm-key-a")
    put.add_argument(
        "--scheme", default="api_key", choices=["api_key", "bearer"],
        help="auth scheme (default api_key)",
    )
    put.add_argument(
        "--value-stdin", action="store_true",
        help="read the secret from stdin instead of a hidden prompt",
    )

    sub.add_parser("fingerprint", help="print sha256[:8] of the stored payload").add_argument(
        "name"
    )
    sub.add_parser("delete", help="remove the secret").add_argument("name")

    args = parser.parse_args(argv)

    from ppswitch_secrets.base import SecretProviderUnavailable
    from ppswitch_secrets.keyring import KeyringSecretProvider

    provider = KeyringSecretProvider()
    try:
        if args.command == "put":
            if args.value_stdin:
                value = sys.stdin.readline().rstrip("\r\n")
            else:
                value = getpass.getpass(f"secret value for {args.name!r}: ")
            if not value:
                print("empty secret refused", file=sys.stderr)
                return 2
            material = (
                {"token": value} if args.scheme == "bearer" else {"api_key": value}
            )
            provider.put(args.name, args.scheme, material)
            digest = provider.fingerprint(args.name)
            print(f"stored ppswitch/{args.name} (fingerprint {digest})")
            print(f"connection secret_ref: keyring://{args.name}")
            return 0
        if args.command == "fingerprint":
            digest = provider.fingerprint(args.name)
            if digest is None:
                print(f"no secret stored under {args.name!r}", file=sys.stderr)
                return 1
            print(digest)
            return 0
        if args.command == "delete":
            removed = provider.delete(args.name)
            print("deleted" if removed else "nothing stored under that name")
            return 0
    except SecretProviderUnavailable as exc:
        print(
            json.dumps({"error": "SecretProviderUnavailable", "message": str(exc)}),
            file=sys.stderr,
        )
        return 3
    return 4


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
