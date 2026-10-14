"""Per-test database provisioning (PR-QA-06).

On SQLite every test already gets its own file. On PostgreSQL
(``PP_SWITCH_TEST_DB_URL`` set by the CI matrix) tests must not share
one database: unique constraints (token hashes, slugs) and
revision-sensitive migration tests (QA-PG-01 upgrades from an OLD
revision) both require isolation. :func:`provision_test_database`
creates a uniquely-named database per test; CI service containers are
ephemeral, so dropping is unnecessary there.
"""

from __future__ import annotations

import uuid


def provision_test_database(base_url: str, prefix: str = "ppswitch_t") -> str:
    """Return a URL to a NEW database created for one test."""
    from sqlalchemy import create_engine, text

    admin_base, _, _ = base_url.rpartition("/")
    dbname = f"{prefix}_{uuid.uuid4().hex[:12]}"
    engine = create_engine(
        f"{admin_base}/postgres", isolation_level="AUTOCOMMIT"
    )
    try:
        with engine.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        engine.dispose()
    return f"{admin_base}/{dbname}"
