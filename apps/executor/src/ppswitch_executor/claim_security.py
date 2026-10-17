"""SECRET-06 claim security primitives (docs/41 section 8 + errata).

Held ONLY by the trusted executor:

- ``material_tag``: full HMAC-SHA-256 over the canonical credential
  material (all authenticated fields, length-prefixed encoding - no
  ambiguity). The truncated sha256[:8] "fingerprint" stays a CLI display
  artifact and never participates in security decisions (errata 2).
- claim tickets: purpose-bound, short-lived HMAC authorizations the
  control plane issues AFTER its claim checks; every executor entry
  point that resolves keyring material demands one (errata 3).
- local attestation: the shared-mode first-claim ownership proof. The
  nonce file lives in an OS-permission-protected directory owned by the
  executor user; possession of the current (or previous) nonce proves
  LOCAL read access to that file - the generic internal token alone can
  never obtain an attestation (errata 1).
- attestations: HMAC(K_attest) over the full binding (namespace, name,
  principal, workspace, proof id, pairing-code hash, purpose, expiry)
  redeemed over the internal channel; constant-time verified.

Key handling: in shared mode ``PP_SWITCH_PIN_KEY`` and
``PP_SWITCH_ATTEST_KEY`` are mandatory and never derived; in personal
mode the pin key is deterministically derived from the internal token
(dev-grade, single-user trust domain) so the Alpha UX needs no extra
secrets, while the MAC check itself still always runs.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from ppswitch_core.errors import ApiError, ErrorCode

TICKET_PURPOSES = ("verify", "discover")
CLAIM_PURPOSE = "keyring.claim"
ATTEST_TTL_S = 300.0


# ---------------------------------------------------------------------------
# canonical encoding + HMAC helpers
# ---------------------------------------------------------------------------


def canonical_encode(*parts: str) -> bytes:
    """Length-prefixed concatenation - no delimiter ambiguity."""
    out = bytearray()
    for part in parts:
        encoded = part.encode("utf-8")
        out += len(encoded).to_bytes(8, "big")
        out += encoded
    return bytes(out)


def _hmac_hex(key: str, message: bytes) -> str:
    return hmac.new(key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _b64e(obj: dict) -> str:
    return base64.urlsafe_b64encode(
        json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")


def _b64d(blob: str) -> dict:
    try:
        payload = json.loads(base64.urlsafe_b64decode(blob.encode("ascii")))
    except (ValueError, TypeError) as exc:
        raise ApiError(ErrorCode.KEYRING_CLAIM_PROOF_INVALID, "Malformed token") from exc
    if not isinstance(payload, dict) or "sig" not in payload:
        raise ApiError(ErrorCode.KEYRING_CLAIM_PROOF_INVALID, "Malformed token")
    return payload


def _signed(obj: dict, key: str) -> str:
    body = {k: v for k, v in obj.items() if k != "sig"}
    body["sig"] = _hmac_hex(key, _b64e(body).encode("ascii"))
    return _b64e(body)


def _verify_signed(blob: str, key: str) -> dict:
    payload = _b64d(blob)
    expected = payload.pop("sig")
    recomputed = _hmac_hex(key, _b64e(payload).encode("ascii"))
    if not hmac.compare_digest(expected, recomputed):
        raise ApiError(ErrorCode.KEYRING_CLAIM_PROOF_INVALID, "Token signature invalid")
    return payload


# ---------------------------------------------------------------------------
# claim tickets (control -> executor, purpose-bound authorizations)
# ---------------------------------------------------------------------------


TICKET_AUDIENCE = "executor"


def sign_claim_ticket(
    *,
    key: str,
    purpose: str,
    namespace_id: str,
    name: str,
    generation: int,
    pin_version: int,
    ttl_s: float = 60.0,
    extra: dict[str, Any] | None = None,
) -> str:
    if purpose not in TICKET_PURPOSES:
        raise ValueError(f"unsupported ticket purpose {purpose!r}")
    body: dict[str, Any] = {
        "purpose": purpose,
        "aud": TICKET_AUDIENCE,
        "namespace_id": namespace_id,
        "name": name,
        "generation": generation,
        "pin_version": pin_version,
        "exp": time.time() + ttl_s,
    }
    if extra:
        body.update(extra)
    return _signed(body, key)


def verify_claim_ticket(blob: str, *, key: str, purpose: str) -> dict:
    payload = _verify_signed(blob, key)
    if payload.get("purpose") != purpose:
        raise ApiError(ErrorCode.KEYRING_CLAIM_PROOF_INVALID, "Token purpose mismatch")
    if payload.get("aud") != TICKET_AUDIENCE:
        raise ApiError(ErrorCode.KEYRING_CLAIM_PROOF_INVALID, "Token audience mismatch")
    if float(payload.get("exp", 0)) < time.time():
        raise ApiError(ErrorCode.KEYRING_CLAIM_PROOF_INVALID, "Token expired")
    for field in ("namespace_id", "name", "generation", "pin_version"):
        if field not in payload:
            raise ApiError(ErrorCode.KEYRING_CLAIM_PROOF_INVALID, "Token incomplete")
    return payload


# ---------------------------------------------------------------------------
# material tag (K_pin) + comparison
# ---------------------------------------------------------------------------


def material_tag(pin_key: str, *, namespace_id: str, name: str, auth_scheme: str,
                 material: dict[str, Any]) -> str:
    """Full HMAC over EVERY authenticated material field (errata 2)."""
    canonical_material = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return _hmac_hex(
        pin_key,
        canonical_encode(namespace_id, name, auth_scheme, canonical_material),
    )


def tags_match(expected: str, actual: str) -> bool:
    """Constant-time full-length comparison (never a truncated prefix)."""
    return hmac.compare_digest(expected, actual)


def derive_pin_key(internal_token: str) -> str:
    """Personal-mode dev-grade derivation (single-user trust domain).

    Shared mode must set PP_SWITCH_PIN_KEY explicitly; the derived key
    exists only in executor memory and never leaves the process.
    """
    return _hmac_hex(internal_token, b"ppswitch/pin-key/derivation/v1")


# ---------------------------------------------------------------------------
# pairing binding ticket (control signs; executor includes it in the
# attestation so the attestation is bound to namespace/name/principal/
# workspace/purpose/expiry - errata 1)
# ---------------------------------------------------------------------------


def sign_pairing_ticket(
    *,
    key: str,
    namespace_id: str,
    name: str,
    principal_id: str,
    workspace_id: str,
    proof_id: str,
    ttl_s: float = 600.0,
) -> str:
    return _signed(
        {
            "purpose": CLAIM_PURPOSE,
            "namespace_id": namespace_id,
            "name": name,
            "principal_id": principal_id,
            "workspace_id": workspace_id,
            "proof_id": proof_id,
            "exp": time.time() + ttl_s,
        },
        key,
    )


def verify_pairing_ticket(blob: str, *, key: str) -> dict:
    payload = _verify_signed(blob, key)
    if payload.get("purpose") != CLAIM_PURPOSE:
        raise ApiError(ErrorCode.KEYRING_CLAIM_PROOF_INVALID, "Ticket purpose mismatch")
    if float(payload.get("exp", 0)) < time.time():
        raise ApiError(ErrorCode.KEYRING_CLAIM_PROOF_INVALID, "Ticket expired")
    return payload


# ---------------------------------------------------------------------------
# executor-local attestation nonce (OS-permission-protected file)
# ---------------------------------------------------------------------------


class LocalNonceStore:
    """Rotating nonce in a 0700 directory owned by the executor user.

    Reading the current or previous nonce requires local filesystem
    access with the executor user's OS permissions - that read access is
    exactly what the CLI must prove to obtain an attestation. The nonce
    never appears in the environment, so possession of the generic
    internal token alone cannot fabricate it (errata 1).
    """

    def __init__(self, directory: str) -> None:
        self._dir = Path(directory)
        self._current: str = ""
        self._previous: str = ""
        self._rotated_at = 0.0

    def _path(self) -> Path:
        return self._dir / "local.nonce"

    def ensure(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._dir, 0o700)
        except OSError:
            pass  # Windows: ACLs govern; POSIX gets the restrictive mode
        if not self._current:
            self._rotate()
        elif time.time() - self._rotated_at > 600.0:
            self.rotate()

    def rotate(self) -> None:
        self.ensure_dir_only()
        self._rotate()

    def _rotate(self) -> None:
        self._previous = self._current
        self._current = secrets.token_hex(32)
        self._rotated_at = time.time()
        path = self._path()
        tmp = self._dir / "local.nonce.tmp"
        tmp.write_text(self._current, encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)

    def ensure_dir_only(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self._dir, 0o700)
        except OSError:
            pass

    def validate(self, presented: str) -> bool:
        """Strict check against the CURRENT nonce only (the previous value
        died at the last rotation); no consumption here."""
        self.ensure()
        if not presented:
            return False
        return hmac.compare_digest(presented, self._current)

    def validate_and_consume(self, presented: str) -> bool:
        """FIX-03: single-use local proof - a successful attestation
        rotates the nonce, so a leaked value dies with its first use."""
        if not self.validate(presented):
            return False
        self._rotate()
        return True


# ---------------------------------------------------------------------------
# shared-mode ownership attestations (K_attest)
# ---------------------------------------------------------------------------


def sign_attestation(
    attest_key: str,
    *,
    namespace_id: str,
    name: str,
    principal_id: str,
    workspace_id: str,
    proof_id: str,
    pairing_code_hash: str,
    ttl_s: float = ATTEST_TTL_S,
) -> str:
    return _signed(
        {
            "purpose": CLAIM_PURPOSE,
            "namespace_id": namespace_id,
            "name": name,
            "principal_id": principal_id,
            "workspace_id": workspace_id,
            "proof_id": proof_id,
            "code_hash": pairing_code_hash,
            "exp": time.time() + ttl_s,
        },
        attest_key,
    )


def verify_attestation(
    attest_key: str,
    blob: str,
    *,
    namespace_id: str,
    name: str,
    principal_id: str,
    workspace_id: str,
    proof_id: str,
    pairing_code_hash: str,
) -> None:
    payload = _verify_signed(blob, attest_key)
    expected = {
        "purpose": CLAIM_PURPOSE,
        "namespace_id": namespace_id,
        "name": name,
        "principal_id": principal_id,
        "workspace_id": workspace_id,
        "proof_id": proof_id,
        "code_hash": pairing_code_hash,
    }
    if any(payload.get(k) != v for k, v in expected.items()):
        raise ApiError(
            ErrorCode.KEYRING_CLAIM_PROOF_INVALID, "Attestation binding mismatch"
        )
    if float(payload.get("exp", 0)) < time.time():
        raise ApiError(ErrorCode.KEYRING_CLAIM_PROOF_INVALID, "Attestation expired")


class ClaimSecurity:
    """Resolved SECRET-06 key material for one executor instance.

    Shared mode (PP_SWITCH_SHARED_KEYRING=true) fails closed at startup
    unless the pin key, attest key and control URL (revocation freshness)
    are all explicitly configured. Personal mode derives the pin key from
    the internal token (dev-grade, single-user trust domain) so the Alpha
    UX needs no extra secrets while the MAC check still always runs.
    """

    def __init__(self, *, internal_token: str, shared: bool, pin_key: str,
                 attest_key: str, claim_ticket_key: str, plan_signing_key: str,
                 local_attest_dir: str, control_url: str,
                 start_listener: bool = True) -> None:
        self.shared = shared
        if pin_key:
            self.pin_key = pin_key
        elif shared:
            raise RuntimeError("PP_SWITCH_PIN_KEY is mandatory in shared-keyring mode")
        else:
            self.pin_key = derive_pin_key(internal_token)
        # FIX-02: authorization signing keys are SEPARATE trust channels
        # from the transport token. Shared mode refuses derived values;
        # personal mode (single-user trust domain) falls back to the
        # internal token so the Alpha UX needs no extra secrets.
        if claim_ticket_key:
            self.claim_ticket_key = claim_ticket_key
        elif shared:
            raise RuntimeError(
                "PP_SWITCH_CLAIM_TICKET_KEY is mandatory in shared-keyring mode"
            )
        else:
            self.claim_ticket_key = internal_token
        if plan_signing_key:
            self.plan_signing_key = plan_signing_key
        elif shared:
            raise RuntimeError(
                "PP_SWITCH_PLAN_SIGNING_KEY is mandatory in shared-keyring mode"
            )
        else:
            self.plan_signing_key = internal_token
        self.attest_key = attest_key or None
        self.nonces = LocalNonceStore(local_attest_dir)
        self.control_url = control_url.rstrip("/")
        self.listener: LocalAttestListener | None = None
        if shared:
            if not self.attest_key:
                raise RuntimeError(
                    "PP_SWITCH_ATTEST_KEY is mandatory in shared-keyring mode"
                )
            if not self.control_url:
                raise RuntimeError(
                    "PP_SWITCH_CONTROL_URL is mandatory in shared-keyring mode "
                    "(claim revocation freshness)"
                )
            # eager: the CLI on this machine must find the nonce file the
            # moment the executor is up (OS-permission-protected directory)
            self.nonces.ensure()
            # FIX-03: ownership attestations are minted ONLY over the
            # OS-protected local channel - never over HTTP
            self.listener = LocalAttestListener(self)
            if start_listener:
                self.listener.start()


class LocalAttestListener:
    """FIX-03: OS-protected local IPC for minting ownership attestations.

    The signing surface is deliberately NOT an HTTP endpoint - the
    generic internal token can never mint an attestation, regardless of
    what nonce material it may hold.

    - POSIX: an AF_UNIX socket inside the 0700 attest directory with the
      socket file chmod 0600 - only the executor's OS user (or root) can
      even connect; remote hosts cannot reach it at all.
    - Windows (no AF_UNIX in WinSock): a loopback-only TCP socket whose
      RANDOM port is written to ``listener.port`` inside the same
      protected directory - reading the port requires local filesystem
      access to that directory, and the single-use nonce still gates
      every request.

    Protocol: one newline-delimited JSON request per connection
    ``{name, pairing_code, ticket, local_nonce}``; a successful issuance
    CONSUMES the nonce (rotation), so a leaked value dies with its
    first use. Replies ``{"attestation": ...}`` or ``{"error": ...}``.
    """

    def __init__(self, claims: ClaimSecurity) -> None:
        self._claims = claims
        self._socket: Any = None
        self._thread: threading.Thread | None = None
        self.unix_path = claims.nonces._dir / "attest.sock"
        self.port_file = claims.nonces._dir / "listener.port"

    def start(self) -> None:
        import socket as socket_module

        claims = self._claims
        claims.nonces.ensure_dir_only()
        if hasattr(socket_module, "AF_UNIX"):
            try:
                self.unix_path.unlink()
            except FileNotFoundError:
                pass
            self._socket = socket_module.socket(
                socket_module.AF_UNIX, socket_module.SOCK_STREAM
            )
            self._socket.bind(str(self.unix_path))
            try:
                os.chmod(self.unix_path, 0o600)
            except OSError:
                pass
        else:
            self._socket = socket_module.socket(
                socket_module.AF_INET, socket_module.SOCK_STREAM
            )
            self._socket.bind(("127.0.0.1", 0))
            self.port_file.write_text(str(self._socket.getsockname()[1]),
                                      encoding="utf-8")
        self._socket.listen(4)
        try:
            self._socket.settimeout(1.0)
        except OSError:
            pass
        self._thread = threading.Thread(
            target=self._serve, name="ppswitch-local-attest", daemon=True
        )
        self._thread.start()

    def _serve(self) -> None:  # pragma: no cover - socket accept loop
        while True:
            try:
                conn, _ = self._socket.accept()
            except (OSError, TimeoutError):
                continue
            try:
                with conn:
                    conn.settimeout(15.0)
                    data = b""
                    while not data.endswith(b"\n"):
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                        if len(data) > 65536:
                            break
                    reply = self._handle(data)
                    conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
            except OSError:
                continue

    def _handle(self, data: bytes) -> dict:
        try:
            request = json.loads(data.decode("utf-8"))
            name = str(request["name"])
            pairing_code = str(request["pairing_code"])
            ticket = str(request["ticket"])
            local_nonce = str(request["local_nonce"])
        except (ValueError, KeyError, TypeError, UnicodeDecodeError):
            return {"error": "malformed_request"}
        claims = self._claims
        if not claims.nonces.validate_and_consume(local_nonce):
            return {"error": "local_proof_invalid"}
        try:
            binding = verify_pairing_ticket(ticket, key=claims.claim_ticket_key)
        except ApiError:
            return {"error": "ticket_invalid"}
        if binding["name"] != name.lower():
            return {"error": "ticket_name_mismatch"}
        code_hash = hashlib.sha256(pairing_code.encode("utf-8")).hexdigest()
        attestation = sign_attestation(
            claims.attest_key or "",
            namespace_id=binding["namespace_id"],
            name=binding["name"],
            principal_id=binding["principal_id"],
            workspace_id=binding["workspace_id"],
            proof_id=binding["proof_id"],
            pairing_code_hash=code_hash,
        )
        return {"attestation": attestation}

    def connect_address(self):
        """Client-side helper: the address a LOCAL CLI should dial."""
        import socket as socket_module

        if hasattr(socket_module, "AF_UNIX"):
            return ("unix", str(self.unix_path))
        return ("tcp", "127.0.0.1", int(self.port_file.read_text(encoding="utf-8")))
