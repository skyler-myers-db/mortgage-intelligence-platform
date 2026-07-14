"""Deployment contract for Lakebase runtime-role grants."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from jobs import lakebase_migrate


class _Cursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, Any]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: object, params: Any = None) -> None:
        self.executed.append((str(statement), params))

    def fetchall(self) -> list[tuple[str]]:
        return []


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _Cursor:
        return self._cursor

    def close(self) -> None:
        return None


def test_missing_app_role_is_a_deployment_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    cursor = _Cursor()
    monkeypatch.setattr(lakebase_migrate, "_candidate_app_roles", lambda: ["app-role"])
    monkeypatch.setattr(lakebase_migrate.time, "monotonic", lambda: 10.0)

    import psycopg

    monkeypatch.setattr(psycopg, "connect", lambda **kwargs: _Connection(cursor))

    with pytest.raises(RuntimeError, match="no app role found"):
        lakebase_migrate._apply_app_role_grants(
            {}, role_wait_timeout_s=0, role_wait_interval_s=1
        )

    assert cursor.executed == [
        (
            "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
            (["app-role"],),
        )
    ]


def test_lakebase_grant_docs_match_automated_sequence_contract() -> None:
    templates = "\n".join(lakebase_migrate._APP_ROLE_GRANT_TEMPLATES)
    grants_doc = Path("docs/security/GRANTS.md").read_text(encoding="utf-8")

    assert "GRANT USAGE ON ALL SEQUENCES IN SCHEMA mip_app TO {role}" in templates
    assert (
        "ALTER DEFAULT PRIVILEGES IN SCHEMA mip_app GRANT USAGE ON SEQUENCES TO {role}"
        in templates
    )
    assert 'GRANT USAGE ON ALL SEQUENCES IN SCHEMA mip_app TO "mip-app"' in grants_doc
    assert "No separate `GRANT` SQL is issued" not in grants_doc
