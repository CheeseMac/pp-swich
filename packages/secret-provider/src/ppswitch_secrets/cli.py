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


def _claim_pair(args) -> int:
    """SECRET-06 (docs/41 section 8.1 / errata 1 + FIX-03): run on the
    EXECUTOR machine. The one-time pairing code is read from a HIDDEN
    prompt (never argv); the request travels over the OS-protected local
    channel only, and the single-use nonce from the protected directory
    is consumed by a successful attestation.
    """
    import json
    import os
    from pathlib import Path

    from ppswitch_secrets.local_channel import local_attest_request

    attest_dir = Path(
        os.environ.get("PP_SWITCH_LOCAL_ATTEST_DIR", "")
        or (Path.home() / ".ppswitch" / "attest")
    )
    nonce_path = attest_dir / "local.nonce"
    if not nonce_path.exists():
        print(
            f"local proof file not found: {nonce_path}\n"
            "claim-pair must run on the machine (and as the user) that runs "
            "the executor - it reads the protected local nonce",
            file=sys.stderr,
        )
        return 2
    local_nonce = nonce_path.read_text(encoding="utf-8").strip()
    code = getpass.getpass(f"one-time pairing code for {args.name!r}: ")
    if not code:
        print("empty pairing code refused", file=sys.stderr)
        return 2
    # FIX-03: the request travels over the OS-protected LOCAL channel
    # (AF_UNIX socket / loopback port file inside the executor user's
    # 0700 attest directory) - never over HTTP, never with the generic
    # internal token
    reply = local_attest_request(
        str(attest_dir),
        {
            "name": args.name,
            "pairing_code": code,
            "ticket": args.ticket,
            "local_nonce": local_nonce,
        },
    )
    if "attestation" not in reply:
        print(f"attestation refused: {reply.get('error', 'unknown')}",
              file=sys.stderr)
        return 3
    attestation = reply["attestation"]
    print("claim attestation (paste into the console activate dialog):")
    print(json.dumps({"attestation": attestation}))
    print("valid for 5 minutes, single use; never logged by pp-switch.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ppswitch-secret",
        description="Store pp-switch connection credentials in the OS keyring",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    put = sub.add_parser("put", help="store or update a secret")
    put.add_argument("name", help="keyring name, e.g. crm-key-a")
    put.add_argument(
        "--scheme", default="api_key", choices=["api_key", "bearer", "postgres"],
        help="auth scheme (default api_key)",
    )
    # FEAT-04: postgres material = {"user", "password"}; the username is
    # not secret material but travels with the payload so the executor can
    # open the connection without any DB coordinate entering the keyring
    put.add_argument(
        "--user", default=None,
        help="database role name (scheme=postgres only)",
    )
    put.add_argument(
        "--value-stdin", action="store_true",
        help="read the secret from stdin instead of a hidden prompt",
    )

    sub.add_parser("fingerprint", help="print sha256[:8] of the stored payload").add_argument(
        "name"
    )
    sub.add_parser("delete", help="remove the secret").add_argument("name")

    claim_pair = sub.add_parser(
        "claim-pair",
        help="SECRET-06 shared mode: obtain the executor-local claim attestation",
    )
    claim_pair.add_argument("name", help="keyring name being claimed")
    claim_pair.add_argument(
        "--ticket", required=True,
        help="binding ticket shown with the console pairing (non-secret)",
    )

    args = parser.parse_args(argv)

    from ppswitch_secrets.base import SecretProviderUnavailable
    from ppswitch_secrets.keyring import KeyringSecretProvider

    provider = KeyringSecretProvider()
    try:
        if args.command == "claim-pair":
            return _claim_pair(args)
        if args.command == "put":
            if args.scheme == "postgres":
                user = args.user or input(f"database role for {args.name!r}: ").strip()
                if not user:
                    print("empty role name refused", file=sys.stderr)
                    return 2
                if args.value_stdin:
                    password = sys.stdin.readline().rstrip("\r\n")
                else:
                    password = getpass.getpass(f"password for role {user!r}: ")
                if not password:
                    print("empty secret refused", file=sys.stderr)
                    return 2
                material = {"user": user, "password": password}
            else:
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
