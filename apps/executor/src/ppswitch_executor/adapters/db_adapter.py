"""FEAT-04 database adapter: fixed parameterized read-only PostgreSQL tools.

Hard rules (docs/38 section 1, acceptance items 2/3/6):

- the plan carries a STRUCTURED query description (schema, view, columns,
  named parameters, caps) - never SQL text. This module composes the
  statement itself with ``psycopg.sql`` identifier composition, so no
  caller and no payload can smuggle arbitrary SQL, a write statement or a
  multi-statement payload: what leaves here is always exactly one
  ``SELECT cols FROM schema.view WHERE k = %(k)s ... LIMIT n``;
- the connection opens with ``default_transaction_read_only=on`` and a
  ``statement_timeout`` - on top of the read-only ROLE the account must
  already have (activation refuses superuser-class credentials), so writes
  are blocked twice;
- the schema must be inside the plan's frozen ``allowed_schemas`` (frozen
  from the APPROVED db_target; checked again here, fail-closed);
- rows fetched and bytes returned are capped before the response guard;
- the target host is admitted through the dedicated DB policy check
  (``check_db_target``) and connected by its validated literal IP - this
  adapter is the only outbound-DB code path and grants no generic TCP.

Credentials arrive as ``CredentialMaterial`` resolved from the FEAT-03
keyring path (auth_scheme=postgres, material={"user","password"}); the
password never appears in results, logs or errors raised here.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ppswitch_core.entities import InvocationPlan, InvocationPlanDbQuery
from ppswitch_core.enums import AuthScheme
from ppswitch_core.errors import ApiError, ErrorCode
from ppswitch_secrets import CredentialMaterial

from ppswitch_executor.network_policy import DbTargetCheck, check_db_target

CONNECT_TIMEOUT_S = 5.0
# acceptance item 3: caps enforced BEFORE the response guard's own budget
MAX_ROW_BYTES = 32 * 1024
MAX_DISCOVER_OBJECTS = 200
MAX_DISCOVER_COLUMNS_PER_OBJECT = 64
MAX_DISCOVER_TOTAL_COLUMNS = 4000

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def _validate_identifier(kind: str, value: str) -> str:
    if not _IDENTIFIER.match(value):
        raise ApiError(
            ErrorCode.DATABASE_QUERY_REJECTED,
            f"Database {kind} is not a permitted identifier",
        )
    return value


def _postgres_material(credential: CredentialMaterial) -> tuple[str, str]:
    if credential.auth_scheme != AuthScheme.POSTGRES:
        raise ApiError(
            ErrorCode.DATABASE_QUERY_REJECTED,
            "Database plans require a postgres credential",
        )
    user = credential.material.get("user")
    password = credential.material.get("password")
    if not isinstance(user, str) or not user or not isinstance(password, str) or not password:
        raise ApiError(
            ErrorCode.SECRET_PROVIDER_UNAVAILABLE,
            "Postgres credential material is incomplete (need user + password)",
        )
    return user, password


def _connect(
    check: DbTargetCheck,
    database: str,
    user: str,
    password: str,
    *,
    statement_timeout_ms: int,
):
    import psycopg

    # session-level read-only + statement timeout: even a mis-granted role
    # cannot write through this connection, and no query can run forever
    options = (
        f"-c default_transaction_read_only=on -c statement_timeout={statement_timeout_ms}"
    )
    try:
        return psycopg.connect(
            host=check.pinned_ip,  # validated literal: no second DNS lookup
            port=check.port,
            dbname=database,
            user=user,
            password=password,
            connect_timeout=int(CONNECT_TIMEOUT_S),
            options=options,
            row_factory=psycopg.rows.dict_row,
        )
    except psycopg.Error as exc:
        raise ApiError(ErrorCode.UPSTREAM_FAILURE, "Database connection failed") from exc


def _map_db_error(exc: Exception) -> ApiError:
    import psycopg

    if isinstance(exc, psycopg.errors.QueryCanceled):
        return ApiError(ErrorCode.UPSTREAM_TIMEOUT, "Database query timed out")
    return ApiError(ErrorCode.UPSTREAM_FAILURE, "Database rejected the query")


def compose_select(query: InvocationPlanDbQuery):
    """Compose the one and only statement shape this adapter can produce.

    Identifier-only composition via ``psycopg.sql``: values never enter the
    SQL text (they stay bound ``%(name)s`` parameters).
    """
    import psycopg.sql as sql

    columns = sql.SQL(", ").join(sql.Identifier(c) for c in query.columns)
    statement = sql.SQL("SELECT {columns} FROM {schema}.{view}").format(
        columns=columns,
        schema=sql.Identifier(query.schema_name),
        view=sql.Identifier(query.view),
    )
    where_parts = [
        sql.Composed([sql.Identifier(k), sql.SQL(" = "), sql.Placeholder(k)])
        for k in sorted(query.param_map)
    ]
    if where_parts:
        statement = statement + sql.SQL(" WHERE ") + sql.SQL(" AND ").join(where_parts)
    statement = statement + sql.SQL(" LIMIT ") + sql.Literal(query.fetch_limit)
    return statement


def execute_query(
    query: InvocationPlanDbQuery,
    credential: CredentialMaterial,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """Run one frozen read-only query; return a single projected row.

    0 rows -> ``{}`` (empty projection); more than one row -> rejected:
    a fixed parameterized tool is a get-by-key tool, and an ambiguous match
    must fail closed rather than return an arbitrary row.
    """
    # defense in depth: the control plane validated the spec against the
    # approved target, the executor re-checks the frozen allowlist
    _validate_identifier("schema", query.schema_name)
    _validate_identifier("view", query.view)
    for column in query.columns:
        _validate_identifier("column", column)
    for placeholder in query.param_map:
        _validate_identifier("parameter", placeholder)
    if query.schema_name not in query.allowed_schemas:
        raise ApiError(
            ErrorCode.DATABASE_QUERY_REJECTED,
            "Database schema is not in the approved target allowlist",
        )
    # filter columns must be projected columns of the same view
    for placeholder in query.param_map:
        if placeholder not in query.columns:
            raise ApiError(
                ErrorCode.DATABASE_QUERY_REJECTED,
                "Database filters must reference selected columns",
            )
    params = {
        placeholder: arguments[argument]
        for placeholder, argument in query.param_map.items()
        if argument in arguments
    }
    if len(params) != len(query.param_map):
        missing = sorted(
            argument for argument in query.param_map.values() if argument not in arguments
        )
        raise ApiError(
            ErrorCode.DATABASE_QUERY_REJECTED,
            "Missing parameters for the approved database query",
            {"missing": missing[:10]},
        )

    user, password = _postgres_material(credential)
    check = check_db_target(query.host, query.port)
    statement = compose_select(query)

    import psycopg

    try:
        with _connect(
            check, query.database, user, password,
            statement_timeout_ms=query.statement_timeout_ms,
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute(statement, params)
                rows = cursor.fetchmany(query.fetch_limit)
    except ApiError:
        raise
    except psycopg.Error as exc:
        raise _map_db_error(exc) from exc

    if len(rows) > 1:
        raise ApiError(
            ErrorCode.DATABASE_QUERY_REJECTED,
            "Database query matched more than one row; refusing an arbitrary pick",
        )
    if not rows:
        return {}
    row = rows[0]
    if len(json.dumps(row, ensure_ascii=False, default=str).encode("utf-8")) > MAX_ROW_BYTES:
        raise ApiError(
            ErrorCode.UPSTREAM_FAILURE,
            "Database row exceeds the allowed size budget",
        )
    return {str(key): value for key, value in row.items()}


def verify_readonly_role(
    target: dict[str, Any], credential: CredentialMaterial
) -> tuple[bool, str]:
    """Activation check for postgres connections: the credential must open
    the APPROVED target and the role must not be superuser-class (a
    superuser could flip session read-only off; fail closed instead)."""
    try:
        host = str(target["host"])
        port = int(target["port"])
        database = str(target["database"])
    except (KeyError, TypeError, ValueError):
        return False, "invalid_db_target"
    _validate_identifier("database", database)
    user, password = _postgres_material(credential)
    check = check_db_target(host, port)

    import psycopg

    try:
        with _connect(
            check, database, user, password, statement_timeout_ms=5000
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT rolsuper, rolbypassrls FROM pg_roles "
                    "WHERE rolname = current_user"
                )
                row = cursor.fetchone()
    except ApiError as exc:
        return False, exc.code.value.lower()
    except psycopg.Error:
        return False, "connection_failed"
    if row is None:
        return False, "role_lookup_failed"
    if row["rolsuper"] or row["rolbypassrls"]:
        return False, "role_not_readonly"
    return True, "ok"


def discover_objects(
    target: dict[str, Any],
    credential: CredentialMaterial,
    *,
    max_objects: int = MAX_DISCOVER_OBJECTS,
) -> dict[str, Any]:
    """List view/table metadata for the APPROVED schemas only.

    Two enforcement layers for acceptance item 1 (unapproved schemas are
    not discoverable): the query is restricted to ``allowed_schemas`` from
    the approved target, AND information_schema only ever reports objects
    the read-only role can actually see (GRANT SELECT). No data values are
    read - column names and types only.
    """
    try:
        host = str(target["host"])
        port = int(target["port"])
        database = str(target["database"])
        schemas = [str(s) for s in target["allowed_schemas"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise ApiError(ErrorCode.BAD_REQUEST, "Malformed db discovery target") from exc
    if not schemas or len(schemas) > 16:
        raise ApiError(ErrorCode.BAD_REQUEST, "allowed_schemas must be 1..16 entries")
    for schema in schemas:
        _validate_identifier("schema", schema)
    user, password = _postgres_material(credential)
    check = check_db_target(host, port)

    import psycopg

    objects: dict[tuple[str, str], dict[str, Any]] = {}
    truncated = False
    total_columns = 0
    try:
        with _connect(
            check, database, user, password, statement_timeout_ms=8000
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT c.table_schema, c.table_name, t.table_type, "
                    "c.column_name, c.data_type, c.ordinal_position "
                    "FROM information_schema.columns c "
                    "JOIN information_schema.tables t "
                    "ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
                    "WHERE c.table_schema = ANY(%(schemas)s) "
                    "ORDER BY c.table_schema, c.table_name, c.ordinal_position",
                    {"schemas": schemas},
                )
                while True:
                    row = cursor.fetchone()
                    if row is None:
                        break
                    key = (row["table_schema"], row["table_name"])
                    entry = objects.get(key)
                    if entry is None:
                        if len(objects) >= max_objects:
                            truncated = True
                            break
                        entry = {
                            "schema": row["table_schema"],
                            "name": row["table_name"],
                            "kind": (
                                "view" if row["table_type"] == "VIEW" else "table"
                            ),
                            "columns": [],
                        }
                        objects[key] = entry
                    if len(entry["columns"]) < MAX_DISCOVER_COLUMNS_PER_OBJECT:
                        entry["columns"].append(
                            {"name": row["column_name"], "data_type": row["data_type"]}
                        )
                        total_columns += 1
                        if total_columns >= MAX_DISCOVER_TOTAL_COLUMNS:
                            truncated = True
                            break
    except psycopg.Error as exc:
        raise _map_db_error(exc) from exc

    return {
        "target": {"host": host, "port": port, "database": database},
        "schemas": schemas,
        "objects": list(objects.values()),
        "truncated": truncated,
    }


class DbAdapter:
    """OperationAdapter for database plans (see adapters/base.py)."""

    def execute(self, plan: InvocationPlan, credential: CredentialMaterial) -> Any:
        if plan.db_query is None:  # pragma: no cover - guarded by the caller
            raise ApiError(ErrorCode.BAD_REQUEST, "Plan carries no database query")
        return execute_query(plan.db_query, credential, plan.arguments)
