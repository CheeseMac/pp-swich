"""Application factory for the control plane."""

from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import FastAPI
from ppswich_db.session import create_engine_for_url, create_session_factory

from ppswich_control.api.v1.router import router as v1_router
from ppswich_control.envelope import RequestIdMiddleware, install_error_handlers
from ppswich_control.settings import ControlSettings


def create_app(
    settings: ControlSettings | None = None,
    *,
    executor_client: httpx.Client | None = None,
    run_migrations: bool | None = None,
) -> FastAPI:
    settings = settings or ControlSettings.from_env()

    if settings.db_url.startswith("sqlite:///"):
        db_path = settings.db_url.removeprefix("sqlite:///")
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine_for_url(settings.db_url)
    session_factory = create_session_factory(engine)

    if run_migrations is None:
        run_migrations = settings.auto_migrate
    if run_migrations:
        from ppswich_db.migrate import run_upgrade

        run_upgrade(settings.db_url)

    app = FastAPI(
        title="pp swich control API",
        version="0.1.0",
        docs_url="/docs" if settings.demo_mode else None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.engine = engine
    app.state.executor_client = executor_client or httpx.Client(
        follow_redirects=False, timeout=30.0
    )

    app.add_middleware(RequestIdMiddleware)
    install_error_handlers(app)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "service": "control-api"}

    @app.get("/readyz")
    def readyz() -> dict:
        from sqlalchemy import text

        with app.state.session_factory() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ready"}

    app.include_router(v1_router)
    return app
