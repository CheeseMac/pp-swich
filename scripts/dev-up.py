#!/usr/bin/env python
"""Start the full local pp swich demo stack.

Launches (all bound to 127.0.0.1):
- mock CRM            :8913
- executor            :8912
- control-api         :8911
- web console (vite)  :5173   (with --ui)

Then run ``uv run python scripts/seed-demo.py`` once and open
http://127.0.0.1:5173.

Usage:
    uv run python scripts/dev-up.py [--ui] [--no-migrate]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "control-api" / "src"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "executor" / "src"))
sys.path.insert(0, str(REPO_ROOT / "apps" / "demo-mock-crm" / "src"))

PORTS = {"mockcrm": 8913, "executor": 8912, "control": 8911, "ui": 5173}


def start(app, name: str, port: int) -> uvicorn.Server:
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    )
    threading.Thread(target=server.run, name=name, daemon=True).start()
    return server


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ui", action="store_true", help="also start the vite dev server")
    parser.add_argument("--no-migrate", action="store_true")
    args = parser.parse_args(argv)

    import os

    if not args.no_migrate:
        os.environ.setdefault("PP_SWICH_AUTO_MIGRATE", "true")

    from ppswich_control.app import create_app
    from ppswich_control.settings import ControlSettings
    from ppswich_executor.main import ExecutorSettings
    from ppswich_executor.main import create_app as create_executor_app
    from ppswich_mockcrm import app as mockcrm_app

    servers = {
        "mock CRM": start(mockcrm_app, "mockcrm", PORTS["mockcrm"]),
        "executor": start(
            create_executor_app(ExecutorSettings()), "executor", PORTS["executor"]
        ),
    }

    control_settings = ControlSettings.from_env()
    control_app = create_app(control_settings)
    servers["control-api"] = start(control_app, "control", PORTS["control"])

    ui_process = None
    if args.ui:
        ui_process = subprocess.Popen(
            ["pnpm", "--filter", "web-console", "dev"],
            cwd=REPO_ROOT,
            shell=(sys.platform == "win32"),
        )

    deadline = time.time() + 30
    for name, server in servers.items():
        while time.time() < deadline and not server.started:
            time.sleep(0.05)
        if not server.started:
            print(f"warning: {name} did not report started", file=sys.stderr)

    print()
    print("pp swich demo stack is up:")
    print(f"  control-api : http://127.0.0.1:{PORTS['control']}   (docs at /docs)")
    print(f"  executor    : http://127.0.0.1:{PORTS['executor']}")
    print(f"  mock CRM    : http://127.0.0.1:{PORTS['mockcrm']}")
    if ui_process:
        print(f"  web console : http://127.0.0.1:{PORTS['ui']}")
    else:
        print("  web console : run `pnpm dev` separately, or pass --ui")
    print()
    print("First run? In another terminal: uv run python scripts/seed-demo.py")
    print("Press Ctrl+C to stop.")
    print()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nshutting down...")
        for server in servers.values():
            server.should_exit = True
        if ui_process:
            ui_process.terminate()
        time.sleep(1)
        print("bye")


if __name__ == "__main__":
    main()
