"""Console entry point for the control API."""

from __future__ import annotations

import os


def main() -> None:  # pragma: no cover - console entry point
    import uvicorn

    from ppswich_control.app import create_app
    from ppswich_control.settings import ControlSettings

    app = create_app(ControlSettings.from_env())
    port = int(os.environ.get("PP_SWICH_CONTROL_PORT", "8911"))
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
