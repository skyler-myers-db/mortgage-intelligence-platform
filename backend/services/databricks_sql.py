"""Minimal, stdlib-only Databricks SQL warehouse client.

Built for Module 0 Slice 4 -- the moment the app flips off in-process
mock repositories and onto live ``mip_demo.gold.*`` queries. Uses the
Statement Execution API (the same pattern already proven in
``tests/integration/test_sql_python_parity.py``) so we avoid pulling
another wheel dependency into the ``app.yaml`` serverless runtime.

Design notes:

- Synchronous execution via ``wait_timeout=30s`` + ``on_wait_timeout=
  CANCEL`` keeps the client one HTTP call per statement. Async with
  result polling is a resilience concern for Slice 6.
- ``parameters`` accepts both the ``:name`` style (dict input) and the
  ``?`` style (list/tuple input). Values are sent via the API's
  ``parameters`` payload, NOT string-interpolated -- the warehouse does
  the escaping. Raw string-concatenation is a SQL-injection footgun
  we explicitly refuse.
- A single process-lifetime client instance is vended through
  ``get_sql_client()`` so connection/socket reuse happens at the
  urllib level and the HTTPS keep-alive pool stays warm.
- Non-``SUCCEEDED`` responses raise ``DatabricksSqlError`` carrying the
  statement id + error. Slice 6 will translate that at the router into
  503 + degraded-state UI. For now the exception propagates.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from threading import Lock
from typing import Any


class DatabricksSqlError(RuntimeError):
    """Raised when a warehouse statement does not reach SUCCEEDED state.

    Attributes are surfaced verbatim so callers (resilience wrapper,
    router 503 translation) can inspect them without regex-parsing the
    message.
    """

    def __init__(self, message: str, *, statement_id: str | None = None, state: str | None = None) -> None:
        super().__init__(message)
        self.statement_id = statement_id
        self.state = state


class DatabricksSqlClient:
    """Stdlib-only Statement Execution API client.

    Not thread-safe for in-flight request sharing (urllib requests are
    short-lived, so per-request Request objects are fine); the single
    shared instance is safe to call from concurrent FastAPI handlers
    because each call builds its own Request.
    """

    def __init__(
        self,
        host: str,
        token: str,
        warehouse_id: str,
        timeout_s: int = 30,
    ) -> None:
        if not host.startswith("http"):
            host = "https://" + host
        self._host = host.rstrip("/")
        self._token = token
        self._warehouse_id = warehouse_id
        self._timeout_s = timeout_s

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(
        self,
        statement: str,
        parameters: dict[str, Any] | list[Any] | tuple[Any, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Run a SQL statement and return rows as ``list[dict]``.

        ``parameters`` is an opaque bag forwarded to the API under the
        ``parameters`` key. We coerce the two supported Python shapes
        (dict for named ``:name`` placeholders, list/tuple for
        positional ``?`` placeholders) into the API's expected array of
        ``{name, value, type}`` / ``{ordinal, value, type}`` objects.
        """
        body = self._build_request_body(statement, parameters)
        resp = self._post(f"{self._host}/api/2.0/sql/statements/", body)

        status = resp.get("status", {}) or {}
        state = status.get("state")
        statement_id = resp.get("statement_id")
        if state != "SUCCEEDED":
            err_msg = (status.get("error") or {}).get("message", "no error message returned")
            raise DatabricksSqlError(
                f"Databricks SQL statement did not succeed "
                f"(state={state!r} statement_id={statement_id!r}): {err_msg}",
                statement_id=statement_id,
                state=state,
            )

        result = resp.get("result", {}) or {}
        data_array = result.get("data_array") or []
        # Columns live in manifest.schema.columns per the API spec.
        manifest = resp.get("manifest", {}) or {}
        schema = manifest.get("schema", {}) or {}
        columns = schema.get("columns") or []
        column_names = [c.get("name") for c in columns]

        rows: list[dict[str, Any]] = []
        for raw_row in data_array:
            rows.append(
                {
                    column_names[i]: _coerce(raw_row[i], columns[i].get("type_name"))
                    for i in range(len(column_names))
                }
            )
        return rows

    def execute_one(
        self,
        statement: str,
        parameters: dict[str, Any] | list[Any] | tuple[Any, ...] | None = None,
    ) -> dict[str, Any] | None:
        rows = self.execute(statement, parameters)
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_request_body(
        self,
        statement: str,
        parameters: dict[str, Any] | list[Any] | tuple[Any, ...] | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "statement": statement,
            "warehouse_id": self._warehouse_id,
            "wait_timeout": f"{self._timeout_s}s",
            "on_wait_timeout": "CANCEL",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
        }
        if parameters is None:
            return body
        if isinstance(parameters, dict):
            body["parameters"] = [
                {"name": k, "value": _sql_param_value(v), "type": _sql_param_type(v)}
                for k, v in parameters.items()
            ]
        elif isinstance(parameters, list | tuple):
            body["parameters"] = [
                {"ordinal": i + 1, "value": _sql_param_value(v), "type": _sql_param_type(v)}
                for i, v in enumerate(parameters)
            ]
        else:  # pragma: no cover -- defensive
            raise TypeError(
                f"parameters must be dict, list, or tuple (got {type(parameters).__name__})"
            )
        return body

    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        payload = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            },
        )
        try:
            # Timeout budget = wait_timeout + 5s for network overhead.
            with urllib.request.urlopen(req, timeout=self._timeout_s + 5) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # pragma: no cover -- server error
            detail = exc.read().decode("utf-8", errors="replace")
            raise DatabricksSqlError(
                f"HTTP {exc.code} from Databricks SQL API: {detail[:500]}"
            ) from exc
        except urllib.error.URLError as exc:  # pragma: no cover -- network error
            raise DatabricksSqlError(
                f"Network error reaching Databricks SQL API: {exc.reason!r}"
            ) from exc


# ---------------------------------------------------------------------------
# Value coercion -- the JSON_ARRAY disposition returns every cell as a string
# or null, regardless of column type. Coerce back to native Python based on
# the column type_name so ``Borrower360(**row)`` gets int/float/bool, not str.
# ---------------------------------------------------------------------------


_INT_TYPES = {"INT", "INTEGER", "BIGINT", "LONG", "SHORT", "SMALLINT", "TINYINT"}
_FLOAT_TYPES = {"DOUBLE", "FLOAT", "DECIMAL"}
_BOOL_TYPES = {"BOOLEAN", "BOOL"}


def _coerce(value: Any, type_name: str | None) -> Any:
    if value is None:
        return None
    if type_name is None:
        return value
    t = type_name.upper()
    if t in _INT_TYPES:
        # Ints can arrive as '12' -- cast defensively.
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if t in _FLOAT_TYPES:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if t in _BOOL_TYPES:
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1", "t")
    # ARRAY / STRUCT / STRING / TIMESTAMP / DATE pass through as emitted.
    return value


def _sql_param_value(v: Any) -> str:
    if v is None:
        return None  # type: ignore[return-value]
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _sql_param_type(v: Any) -> str:
    if isinstance(v, bool):
        return "BOOLEAN"
    if isinstance(v, int):
        return "INT"
    if isinstance(v, float):
        return "DOUBLE"
    return "STRING"


# ---------------------------------------------------------------------------
# Single process-lifetime client. Lazy so tests that never touch the
# warehouse never trigger ``require_databricks_creds()``.
# ---------------------------------------------------------------------------

_CLIENT: DatabricksSqlClient | None = None
_CLIENT_LOCK = Lock()


def get_sql_client() -> DatabricksSqlClient:
    """Return the process-wide SQL client, constructing it on first use.

    Raises at call-time (not import-time) so test processes that inject
    stub repositories via FastAPI ``dependency_overrides`` never touch
    this function and never need real credentials. Production request
    paths that need the warehouse always call through the factory in
    ``backend/services/repositories/factory.py`` which in turn calls
    this helper.
    """
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    # Local import so this module's import graph stays standalone; the
    # settings module reads env once at import and we don't want that
    # cost paid at import of ``databricks_sql``.
    from backend.config.settings import settings

    with _CLIENT_LOCK:
        if _CLIENT is not None:
            return _CLIENT
        host, token, warehouse_id = settings.require_databricks_creds()
        _CLIENT = DatabricksSqlClient(
            host=host,
            token=token.get_secret_value(),
            warehouse_id=warehouse_id,
            timeout_s=settings.databricks_timeout_s,
        )
        return _CLIENT


def _reset_sql_client_for_tests() -> None:
    """Test helper -- drops the singleton so ``tests/conftest.py`` can
    reinstall a fresh stub per test. NOT for production use."""
    global _CLIENT
    with _CLIENT_LOCK:
        _CLIENT = None
