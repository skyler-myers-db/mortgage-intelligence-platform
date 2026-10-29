"""The deploy helper that publishes the App's observed-lineage view.

``tools/databricks/ensure_asset_lineage_view.py`` probes
``system.access.table_lineage`` as the deploying identity (the view's future
owner), publishes the rendered view, and proves its four-column projection.
Only a probe the system table denies is a skip (exit 3, nothing created);
every other failure is fatal (exit 4). No workspace is contacted here: the
statement runner is a fake.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.databricks import ensure_asset_lineage_view as tool

DESCRIBED = [
    ["source_table_full_name", "string", "Upstream"],
    ["target_table_full_name", "string", "Downstream"],
    ["event_time", "timestamp", "When"],
    ["event_date", "date", "Date"],
]


class _Runner:
    def __init__(
        self,
        *,
        fail_on: str | None = None,
        error: str = "",
        described: list[list[str]] | None = None,
    ) -> None:
        self.statements: list[str] = []
        self._fail_on = fail_on
        self._error = error
        self._described = DESCRIBED if described is None else described

    def __call__(self, statement: str) -> object:
        self.statements.append(statement)
        if self._fail_on is not None and statement.startswith(self._fail_on):
            raise RuntimeError(
                f"SQL statement did not succeed (FAILED): {statement}\n{self._error}"
            )
        if statement.startswith("DESCRIBE TABLE "):
            return SimpleNamespace(result=SimpleNamespace(data_array=self._described))
        return SimpleNamespace(result=None)


@pytest.mark.parametrize("catalog", ["mip", "summit_mortgage"])
def test_publishes_the_rendered_view_after_the_probe_and_verifies_it(
    catalog: str, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _Runner()

    assert tool.ensure_asset_lineage_view(runner, catalog=catalog) == "published"

    probe, create, describe = runner.statements
    assert probe == (
        "SELECT source_table_full_name, target_table_full_name, event_time, event_date, "
        "source_table_catalog, target_table_catalog FROM system.access.table_lineage LIMIT 0"
    )
    assert create == tool.view_statement(catalog)
    assert create.startswith(f"CREATE OR REPLACE VIEW {catalog}.audit.asset_lineage (")
    assert f"source_table_catalog = '{catalog}'" in create and not create.endswith(";")
    assert describe == f"DESCRIBE TABLE {catalog}.audit.asset_lineage"
    assert f"published: {catalog}.audit.asset_lineage (4 columns" in capsys.readouterr().out


@pytest.mark.parametrize("marker", tool.MISSING_ACCESS_MARKERS)
def test_a_denied_probe_skips_without_creating_anything(
    marker: str, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = _Runner(
        fail_on="SELECT ",
        error=f"[{marker}] User does not have USE SCHEMA on Schema 'system.access'.",
    )

    assert tool.ensure_asset_lineage_view(runner, catalog="mip") == "skipped"

    assert len(runner.statements) == 1
    assert not any("CREATE" in statement for statement in runner.statements)
    err = capsys.readouterr().err
    assert f"cannot read system.access.table_lineage ({marker})" in err
    assert tool.REQUIRED_PRIVILEGES in err
    assert "User does not have" not in err  # the marker, never the raw server text


def test_an_unexplained_probe_failure_is_fatal() -> None:
    runner = _Runner(fail_on="SELECT ", error="[INTERNAL_ERROR] warehouse lost")

    with pytest.raises(tool.LineageViewError, match="probe failed"):
        tool.ensure_asset_lineage_view(runner, catalog="mip")
    assert len(runner.statements) == 1


def test_a_publish_failure_after_the_probe_is_fatal_even_when_it_is_a_privilege() -> None:
    runner = _Runner(
        fail_on="CREATE OR REPLACE VIEW",
        error="[INSUFFICIENT_PERMISSIONS] User is not an owner of Table 'mip.audit.asset_lineage'.",
    )

    with pytest.raises(tool.LineageViewError, match="could not be published"):
        tool.ensure_asset_lineage_view(runner, catalog="mip")
    assert len(runner.statements) == 2


@pytest.mark.parametrize(
    "described",
    [
        [*DESCRIBED, ["created_by", "string", "Acting identity"]],
        DESCRIBED[:3],
        [DESCRIBED[1], DESCRIBED[0], *DESCRIBED[2:]],
    ],
    ids=["extra-column", "missing-column", "reordered"],
)
def test_a_published_view_with_any_other_projection_is_fatal(described: list[list[str]]) -> None:
    runner = _Runner(described=described)

    with pytest.raises(tool.LineageViewError, match="expected exactly"):
        tool.ensure_asset_lineage_view(runner, catalog="mip")


def test_described_columns_stop_at_the_first_section_break() -> None:
    response = SimpleNamespace(
        result=SimpleNamespace(data_array=[*DESCRIBED, ["", "", ""], ["# Detailed", "", ""]])
    )

    assert tool.described_columns(response) == tool.VIEW_COLUMNS


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, tool.EXIT_PUBLISHED),
        ("[INSUFFICIENT_PERMISSIONS] no USE CATALOG on system", tool.EXIT_SKIPPED),
        ("[INTERNAL_ERROR] warehouse lost", tool.EXIT_FAILED),
    ],
    ids=["published", "skipped", "failed"],
)
def test_main_maps_each_outcome_to_its_exit_status(
    monkeypatch: pytest.MonkeyPatch, error: str | None, expected: int
) -> None:
    from tools.databricks import ensure_campaign_treatment_table, workspace_auth

    runner = _Runner(fail_on=None if error is None else "SELECT ", error=error or "")
    workspace = object()
    monkeypatch.setattr(workspace_auth, "deployment_workspace_client", lambda: workspace)

    def execute_sql(client: object, *, warehouse_id: str, statement: str) -> object:
        assert client is workspace and warehouse_id == "wh-1"
        return runner(statement)

    monkeypatch.setattr(ensure_campaign_treatment_table, "execute_sql", execute_sql)

    assert tool.main(["--warehouse-id", "wh-1", "--catalog", "mip"]) == expected


@pytest.mark.parametrize(
    "argv",
    [
        ["--warehouse-id", "wh-1", "--catalog", "Mip"],
        ["--warehouse-id", "wh-1", "--catalog", "mip.audit"],
        ["--warehouse-id", " ", "--catalog", "mip"],
    ],
)
def test_main_refuses_invalid_arguments_before_any_workspace_call(
    monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    from tools.databricks import workspace_auth

    def refuse() -> object:
        raise AssertionError("no workspace call on invalid arguments")

    monkeypatch.setattr(workspace_auth, "deployment_workspace_client", refuse)

    assert tool.main(argv) == 2
