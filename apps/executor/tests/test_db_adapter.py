"""FEAT-04 database adapter tests.

Two layers:

- FAKE-psycopg tests (always run): admission order, schema allowlist,
  identifier validation, filter/param wiring, row/size caps, error
  mapping - the control flow of the adapter without a real server;
- REAL-PostgreSQL tests (``PP_SWITCH_TEST_DB_URL``, CI postgres job):
  the read-only guarantees against an actual server - role privileges,
  statement timeout, non-unique rejection, metadata discovery scope and
  the superuser-refusing activation check.

Acceptance mapping (docs/38 section 1): item 2 (no arbitrary SQL / no
writes / no out-of-scope fields), item 3 (timeout / row / size caps),
item 6 (hard-denied networks, dedicated admission path).
"""

from __future__ import annotations

import os
import uuid

import pytest
from ppswitch_core.entities import InvocationPlanDbQuery
from ppswitch_core.enums import AuthScheme
from ppswitch_core.errors import ApiError, ErrorCode
from ppswitch_executor.adapters.db_adapter import (
    compose_select,
    discover_objects,
    execute_query,
    verify_readonly_role,
)
from ppswitch_secrets import CredentialMaterial

PG_URL = os.environ.get("PP_SWITCH_TEST_DB_URL", "")
RO_PASSWORD = "MOCKKEY-pg-ro-feat04"


def _query(**overrides) -> InvocationPlanDbQuery:
    base = dict(
        host="127.0.0.1",
        port=5432,
        database="crm",
        allowed_schemas=["crm"],
        schema_name="crm",
        view="customers_v",
        columns=["id", "display_name"],
        param_map={"id": "customer_id"},
        fetch_limit=2,
        statement_timeout_ms=5000,
    )
    base.update(overrides)
    return InvocationPlanDbQuery(**base)


def _credential(user: str = "ro_user", password: str = RO_PASSWORD) -> CredentialMaterial:
    return CredentialMaterial(
        auth_scheme=AuthScheme.POSTGRES, material={"user": user, "password": password}
    )


class _FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self.executed: list[tuple[object, dict | None]] = []

    def execute(self, statement, params=None) -> None:
        self.executed.append((statement, params))

    def fetchmany(self, limit: int) -> list[dict]:
        return self._rows[:limit]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.connect_kwargs: dict | None = None

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class TestAdapterControlFlow:
    """Always-run tests against a faked psycopg connection layer."""

    def test_non_postgres_credential_rejected(self, monkeypatch):
        credential = CredentialMaterial(
            auth_scheme=AuthScheme.API_KEY, material={"api_key": "MOCKKEY-x"}
        )
        with pytest.raises(ApiError) as exc:
            execute_query(_query(), credential, {"customer_id": "C-1"})
        assert exc.value.code == ErrorCode.DATABASE_QUERY_REJECTED

    def test_incomplete_material_rejected(self, monkeypatch):
        credential = CredentialMaterial(
            auth_scheme=AuthScheme.POSTGRES, material={"user": "ro_user"}
        )
        with pytest.raises(ApiError) as exc:
            execute_query(_query(), credential, {"customer_id": "C-1"})
        assert exc.value.code == ErrorCode.SECRET_PROVIDER_UNAVAILABLE

    def test_schema_outside_allowlist_rejected_before_any_connection(self, monkeypatch):
        def _explode(*args, **kwargs):  # pragma: no cover - must not be reached
            raise AssertionError("connection attempted for unapproved schema")

        import psycopg

        monkeypatch.setattr(psycopg, "connect", _explode)
        with pytest.raises(ApiError) as exc:
            execute_query(
                _query(schema_name="hr", allowed_schemas=["crm"]),
                _credential(),
                {"customer_id": "C-1"},
            )
        assert exc.value.code == ErrorCode.DATABASE_QUERY_REJECTED

    @pytest.mark.parametrize(
        "field,value",
        [
            ("schema", "pg-catalog"),
            ("view", 'customers"; DROP TABLE x'),
            ("columns", ["id", "display name"]),
            ("param_map", {"id;--": "customer_id"}),
        ],
    )
    def test_exotic_identifiers_rejected_at_the_entity_layer(self, field, value):
        with pytest.raises(ValueError):
            _query(**{field: value})

    def test_filter_must_reference_selected_columns(self):
        with pytest.raises(ApiError) as exc:
            execute_query(
                _query(columns=["id"], param_map={"display_name": "customer_id"}),
                _credential(),
                {"customer_id": "C-1"},
            )
        assert exc.value.code == ErrorCode.DATABASE_QUERY_REJECTED

    def test_missing_parameter_rejected(self, monkeypatch):
        import psycopg

        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: pytest.fail("no connect"))
        with pytest.raises(ApiError) as exc:
            execute_query(_query(), _credential(), {})
        assert exc.value.code == ErrorCode.DATABASE_QUERY_REJECTED

    def test_zero_rows_project_to_empty_dict(self, monkeypatch):
        cursor = _FakeCursor([])
        conn = _FakeConn(cursor)
        import psycopg

        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: conn)
        result = execute_query(_query(), _credential(), {"customer_id": "C-1"})
        assert result == {}
        # bound parameters only: the value never enters the SQL text
        _statement, params = cursor.executed[0]
        assert params == {"id": "C-1"}

    def test_single_row_returned_and_parameters_bound(self, monkeypatch):
        cursor = _FakeCursor([{"id": "C-1", "display_name": "Acme"}])
        conn = _FakeConn(cursor)
        import psycopg

        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: conn)
        result = execute_query(_query(), _credential(), {"customer_id": "C-1"})
        assert result == {"id": "C-1", "display_name": "Acme"}
        _statement, params = cursor.executed[0]
        assert params == {"id": "C-1"}

    def test_non_unique_match_rejected(self, monkeypatch):
        cursor = _FakeCursor([{"id": "C-1"}, {"id": "C-1"}])
        conn = _FakeConn(cursor)
        import psycopg

        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: conn)
        with pytest.raises(ApiError) as exc:
            execute_query(_query(), _credential(), {"customer_id": "C-1"})
        assert exc.value.code == ErrorCode.DATABASE_QUERY_REJECTED

    def test_oversize_row_rejected_before_returning(self, monkeypatch):
        cursor = _FakeCursor([{"id": "C-1", "display_name": "x" * 64_000}])
        conn = _FakeConn(cursor)
        import psycopg

        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: conn)
        with pytest.raises(ApiError) as exc:
            execute_query(_query(), _credential(), {"customer_id": "C-1"})
        assert exc.value.code == ErrorCode.UPSTREAM_FAILURE

    def test_session_forced_read_only_with_statement_timeout(self, monkeypatch):
        captured: dict = {}

        def fake_connect(**kwargs):
            captured.update(kwargs)
            return _FakeConn(_FakeCursor([]))

        import psycopg

        monkeypatch.setattr(psycopg, "connect", fake_connect)
        execute_query(
            _query(statement_timeout_ms=1500), _credential(), {"customer_id": "C-1"}
        )
        assert "default_transaction_read_only=on" in captured["options"]
        assert "statement_timeout=1500" in captured["options"]
        # connect to the validated literal IP, not the hostname
        assert captured["host"] == "127.0.0.1"

    def test_hard_denied_network_never_reaches_connect(self, monkeypatch):
        import psycopg

        monkeypatch.setattr(psycopg, "connect", lambda *a, **k: pytest.fail("no connect"))
        with pytest.raises(ApiError) as exc:
            execute_query(
                _query(host="169.254.169.254"), _credential(), {"customer_id": "C-1"}
            )
        assert exc.value.code == ErrorCode.UPSTREAM_FAILURE  # link-local/metadata denied

    def test_composed_statement_is_one_parameterized_select(self):
        statement = compose_select(_query())

        rendered = statement.as_string(None)
        assert rendered.count(";") == 0
        assert rendered.upper().startswith("SELECT")
        assert rendered.upper().endswith("LIMIT 2")
        assert "%(id)s" in rendered
        assert "'C-1'" not in rendered  # values never inlined


# ---------------------------------------------------------------------------
# real PostgreSQL layer (CI postgres job provides PP_SWITCH_TEST_DB_URL)
# ---------------------------------------------------------------------------

pytestmark_real = pytest.mark.skipif(not PG_URL, reason="PP_SWITCH_TEST_DB_URL not set")


def _admin_engine():
    from sqlalchemy import create_engine

    return create_engine(PG_URL, isolation_level="AUTOCOMMIT")


@pytest.fixture(scope="module")
def pg_stack():
    from ppswitch_db.testdb import provision_test_database

    db_url = provision_test_database(PG_URL, prefix="ppswitch_feat04")
    role = f"ro_feat04_{uuid.uuid4().hex[:8]}"
    engine = _admin_engine()
    from sqlalchemy import text

    with engine.connect() as conn:
        conn.execute(text(
            f'CREATE ROLE "{role}" LOGIN PASSWORD \'{RO_PASSWORD}\''
        ))
    engine.dispose()
    target_engine = __import__("sqlalchemy").create_engine(db_url, isolation_level="AUTOCOMMIT")
    with target_engine.connect() as conn:
        conn.execute(text("CREATE SCHEMA crm"))
        conn.execute(text("CREATE SCHEMA hr"))
        conn.execute(text(
            "CREATE TABLE crm.customers (id text PRIMARY KEY, display_name text, note text)"
        ))
        canary = "CANARY-INTERNAL-NOTE"
        conn.execute(text(
            "INSERT INTO crm.customers VALUES ('C-1024', 'Acme Industries', :c), "
            "('C-2048', 'Globex', :c)"
        ), {"c": canary})
        conn.execute(text(
            "CREATE VIEW crm.customers_v AS "
            "SELECT id, display_name, note FROM crm.customers"
        ))
        conn.execute(text("CREATE VIEW crm.slow_v AS SELECT pg_sleep(3) AS slept, 'x' AS id"))
        conn.execute(text(
            "CREATE VIEW crm.big_v AS SELECT 'C-1' AS id, repeat('x', 100000) AS blob"
        ))
        conn.execute(text(
            "CREATE VIEW crm.dup_v AS SELECT 'C-1' AS id UNION ALL SELECT 'C-1' AS id"
        ))
        conn.execute(text("CREATE TABLE hr.salaries (emp text, amount integer)"))
        conn.execute(text("INSERT INTO hr.salaries VALUES ('alice', 999999)"))
        conn.execute(text(f'GRANT USAGE ON SCHEMA crm TO "{role}"'))
        conn.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA crm TO "{role}"'))
        # hr stays UNgranted: the ro role cannot even see it
    target_engine.dispose()

    from sqlalchemy.engine import make_url

    url = make_url(db_url)
    yield {
        "host": url.host or "127.0.0.1",
        "port": url.port or 5432,
        "database": url.database,
        "role": role,
    }
    admin2 = _admin_engine()
    with admin2.connect() as conn:
        conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
    admin2.dispose()


@pytestmark_real
class TestRealPostgres:
    def test_readonly_role_cannot_write_even_with_raw_sql(self, pg_stack):
        """Acceptance 2's role-layer guarantee: the account the executor
        uses cannot write at all, independent of any pp-switch code."""
        import psycopg

        with psycopg.connect(
            host=pg_stack["host"], port=pg_stack["port"], dbname=pg_stack["database"],
            user=pg_stack["role"], password=RO_PASSWORD,
        ) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with conn.cursor() as cur:
                    cur.execute(
                        'INSERT INTO crm.customers VALUES (\'X-1\', \'x\', \'x\')'
                    )

    def test_execute_query_returns_the_matching_row(self, pg_stack):
        result = execute_query(
            _query(
                host=pg_stack["host"], port=pg_stack["port"], database=pg_stack["database"],
                columns=["id", "display_name"], param_map={"id": "customer_id"},
            ),
            _credential(user=pg_stack["role"]),
            {"customer_id": "C-1024"},
        )
        assert result == {"id": "C-1024", "display_name": "Acme Industries"}

    def test_out_of_scope_column_never_leaves_the_database(self, pg_stack):
        # only the two reviewed columns are selected: the leaky `note`
        # stays server-side no matter what the response guard would do
        result = execute_query(
            _query(
                host=pg_stack["host"], port=pg_stack["port"], database=pg_stack["database"],
            ),
            _credential(user=pg_stack["role"]),
            {"customer_id": "C-1024"},
        )
        assert set(result) == {"id", "display_name"}

    def test_statement_timeout_enforced(self, pg_stack):
        with pytest.raises(ApiError) as exc:
            execute_query(
                _query(
                    host=pg_stack["host"], port=pg_stack["port"], database=pg_stack["database"],
                    view="slow_v", columns=["id"], param_map={}, statement_timeout_ms=400,
                ),
                _credential(user=pg_stack["role"]),
                {},
            )
        assert exc.value.code == ErrorCode.UPSTREAM_TIMEOUT

    def test_non_unique_match_fails_closed(self, pg_stack):
        with pytest.raises(ApiError) as exc:
            execute_query(
                _query(
                    host=pg_stack["host"], port=pg_stack["port"], database=pg_stack["database"],
                    view="dup_v", columns=["id"], param_map={},
                ),
                _credential(user=pg_stack["role"]),
                {},
            )
        assert exc.value.code == ErrorCode.DATABASE_QUERY_REJECTED

    def test_oversize_row_rejected(self, pg_stack):
        with pytest.raises(ApiError) as exc:
            execute_query(
                _query(
                    host=pg_stack["host"], port=pg_stack["port"], database=pg_stack["database"],
                    view="big_v", columns=["id", "blob"], param_map={},
                ),
                _credential(user=pg_stack["role"]),
                {},
            )
        assert exc.value.code == ErrorCode.UPSTREAM_FAILURE

    def test_discovery_limited_to_approved_and_granted_schemas(self, pg_stack):
        """Acceptance 1: hr is NOT in allowed_schemas AND not granted to
        the role - it must not appear in the metadata at all."""
        document = discover_objects(
            {
                "host": pg_stack["host"], "port": pg_stack["port"],
                "database": pg_stack["database"], "allowed_schemas": ["crm"],
            },
            _credential(user=pg_stack["role"]),
        )
        objects = {(o["schema"], o["name"]) for o in document["objects"]}
        assert ("crm", "customers_v") in objects
        assert ("crm", "customers") in objects
        assert not any(schema == "hr" for schema, _ in objects)
        # column metadata present, data values never read
        customers_v = next(o for o in document["objects"] if o["name"] == "customers_v")
        assert {"id", "display_name", "note"} <= {c["name"] for c in customers_v["columns"]}

    def test_verify_accepts_readonly_role_and_refuses_superuser(self, pg_stack):
        ok, reason = verify_readonly_role(
            {
                "host": pg_stack["host"], "port": pg_stack["port"],
                "database": pg_stack["database"],
            },
            _credential(user=pg_stack["role"]),
        )
        assert (ok, reason) == (True, "ok")
        # the CI admin account is superuser-class: activation must refuse
        from sqlalchemy.engine import make_url

        admin = make_url(PG_URL)
        bad, why = verify_readonly_role(
            {
                "host": pg_stack["host"], "port": pg_stack["port"],
                "database": pg_stack["database"],
            },
            CredentialMaterial(
                auth_scheme=AuthScheme.POSTGRES,
                material={"user": admin.username, "password": admin.password or ""},
            ),
        )
        assert bad is False
        assert why == "role_not_readonly"
