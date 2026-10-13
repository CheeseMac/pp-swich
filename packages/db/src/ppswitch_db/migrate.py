"""Programmatic Alembic upgrade helper (used at dev startup and in scripts)."""

from __future__ import annotations

from pathlib import Path


def run_upgrade(db_url: str) -> None:
    from alembic import command
    from alembic.config import Config

    # src/ppswitch_db/migrate.py -> package root (where alembic.ini + migrations live)
    package_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(package_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(package_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    command.upgrade(cfg, "head")
