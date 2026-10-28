"""The funnel snapshot SQL resolves against the source root, from any cwd.

backend/services/lifecycle_sync.py reads the rendered funnel SQL first and
falls back to sql/transformations/, both under its source root rather than
the process's working directory: the App starts in its source root, but deploy
step 9 and local runs need not. An explicit ``funnel_sql_path`` still wins.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.services import lifecycle_sync

REPO = Path(__file__).resolve().parents[2]
RELATIVE = "transformations/gold_funnel_snapshot_daily.sql"


class _Client:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str) -> list[dict[str, Any]]:
        self.statements.append(statement)
        return []

    def execute_one(self, statement: str) -> dict[str, Any] | None:
        self.statements.append(statement)
        return {"n": 1}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Client:
    # Everything but the funnel read is stubbed; the cwd is a folder with no sql/.
    monkeypatch.chdir(tmp_path)
    for name, stub in {
        "_resolve_connection": lambda: {},
        "_fetch_lakebase_rows": lambda _kwargs: [],
        "_ensure_lifecycle_schema": lambda *_args, **_kwargs: None,
        "_build_lifecycle_merge": lambda *_args, **_kwargs: "-- lifecycle merge",
        "_funnel_snapshot_schema_ready": lambda *_args, **_kwargs: True,
        "_wake_snoozes": lambda *_args, **_kwargs: None,
    }.items():
        monkeypatch.setattr(lifecycle_sync, name, stub)
    return _Client()


def _sync(client: _Client, funnel_sql_path: Path | None = None) -> None:
    lifecycle_sync.sync_lifecycle_state_via_warehouse(
        catalog="mip", sql_client=client,  # type: ignore[arg-type]
        funnel_sql_path=funnel_sql_path,
    )


def test_both_funnel_sql_paths_sit_under_the_source_root() -> None:
    assert lifecycle_sync.RENDERED_FUNNEL_SQL == REPO / "sql" / "_rendered" / RELATIVE
    assert lifecycle_sync.FUNNEL_SQL == REPO / "sql" / RELATIVE
    assert lifecycle_sync.FUNNEL_SQL.is_file()


def test_the_rendered_sql_is_read_first_from_any_cwd(
    client: _Client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    rendered = tmp_path / "rendered.sql"
    rendered.write_text("-- rendered funnel\n", encoding="utf-8")
    monkeypatch.setattr(lifecycle_sync, "RENDERED_FUNNEL_SQL", rendered)

    _sync(client)

    assert "-- rendered funnel\n" in client.statements


def test_without_a_rendered_sql_the_source_root_fallback_is_read_from_any_cwd(
    client: _Client, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(lifecycle_sync, "RENDERED_FUNNEL_SQL", tmp_path / "absent.sql")

    _sync(client)

    assert lifecycle_sync.FUNNEL_SQL.read_text(encoding="utf-8") in client.statements


def test_an_explicit_funnel_sql_path_still_wins(client: _Client, tmp_path: Path) -> None:
    override = tmp_path / "override.sql"
    override.write_text("-- override funnel\n", encoding="utf-8")

    _sync(client, override)

    assert "-- override funnel\n" in client.statements


def test_the_fallback_swaps_the_catalog_from_any_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)

    sql = lifecycle_sync._read_funnel_sql(tmp_path / "absent.sql", catalog="acme")

    assert sql == lifecycle_sync.FUNNEL_SQL.read_text(encoding="utf-8").replace("mip.", "acme.")
