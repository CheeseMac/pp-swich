"""FIX-03 client half: the OS-protected local attest channel.

Lives in the secret-provider package (no dependency on the executor)
so both the `ppswitch-secret` CLI and tests can dial the listener the
shared-mode executor opens inside its 0700 attest directory:

- POSIX: an AF_UNIX socket ``attest.sock`` (chmod 0600) - connecting
  requires the executor user's filesystem permissions;
- Windows: a loopback-only TCP socket whose random port is written to
  ``listener.port`` in the same protected directory.

Protocol: one newline-delimited JSON request per connection. The
single-use nonce is validated and CONSUMED by a successful attestation.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any


def local_attest_request(attest_dir: str, request: dict[str, Any]) -> dict:
    directory = Path(attest_dir)
    try:
        if hasattr(socket, "AF_UNIX"):
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(str(directory / "attest.sock"))
        else:
            port_text = (directory / "listener.port").read_text(encoding="utf-8")
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect(("127.0.0.1", int(port_text)))
        with client:
            client.settimeout(15.0)
            payload = json.dumps(request) + "\n"
            client.sendall(payload.encode("utf-8"))
            data = b""
            while not data.endswith(b"\n"):
                chunk = client.recv(4096)
                if not chunk:
                    break
                data += chunk
        return json.loads(data.decode("utf-8"))
    except (OSError, ValueError) as exc:
        return {"error": f"local_channel_unreachable: {exc}"}
