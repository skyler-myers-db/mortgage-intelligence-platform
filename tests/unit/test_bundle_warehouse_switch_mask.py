"""The bundle's own SQL warehouse is a runner mask before the release uses it (B1, wave 7 part 2).

A routine release reads the warehouse from this deployer's bundle state in the
preflight, and ``bundle_state warehouse-id`` masks it there. A first install's
state binds none, so nothing masked the warehouse the step-4 apply creates,
and every later ``--warehouse-id`` echo printed it in a public Actions log.
The switch (scripts/lib/deploy_step_bundle_apply.sh, ``bundle warehouse:
after apply``) now registers the id it parses from the bundle summary
(tools/clients/redact.emit_mask) before it assigns it.

The harness runs the real block with the real ``redact`` module, stdout and
stderr merged, so the order of the mask and the first use is the log's own.
tests/unit/test_bootstrap_warehouse_release_sandbox.py proves the same on a
whole fresh-client release.
"""

from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path

import pytest

from tests.fixtures.deploy_block_harness import block, drive

REPO = Path(__file__).resolve().parents[2]
START, END = "# >>> bundle warehouse: after apply >>>", "# <<< bundle warehouse: after apply <<<"
BOOT = "b0b1b2b3b4b5b6b7"
APPLIED = "a0b1c2d3e4f5a6b7"
MASK = f"::add-mask::{APPLIED}"
NOT_THE_STATES = "the bundle's SQL warehouse after the apply is not the one its state bound before it"


def _switch(
    repo: Path, *, applied: str = APPLIED, source: str, before: str, actions: bool = True
) -> tuple[int, list[str], list[str] | None]:
    """(exit code, the merged log's lines, (warehouse, source, exported) once past the block)."""

    clients = repo / "tools" / "clients"
    clients.mkdir(parents=True)
    for init in (repo / "tools" / "__init__.py", clients / "__init__.py"):
        init.write_text("", encoding="utf-8")
    shutil.copyfile(REPO / "tools" / "clients" / "redact.py", clients / "redact.py")
    outcome = repo / "outcome.txt"
    summary = {"resources": {"sql_warehouses": {"mip_serverless_sql": {"id": applied, "name": "mip-serverless-sql"}}}}
    result = drive(
        repo,
        ("export GITHUB_ACTIONS=true\n" if actions else "")
        # One stream, as the job log is; the echo stands for the next step's ``run``.
        + f"{{\n{block(START, END)}\n"
        + 'echo "\\$ tool --warehouse-id ${_GRANTS_WAREHOUSE_ID}"\n} 2>&1\n',
        dry_run=0,
        env={"BUNDLE_SUMMARY_JSON": json.dumps(summary), "WAREHOUSE_SOURCE": source, "_GRANTS_WAREHOUSE_ID": before},
        epilogue=(
            'printf \'%s\\n\' "${_GRANTS_WAREHOUSE_ID}" "${WAREHOUSE_SOURCE}" '
            f'"$(printenv DATABRICKS_WAREHOUSE_ID || echo UNSET)" > {shlex.quote(str(outcome))}\n'
        ),
    )
    assert result.stderr == "", result.stderr
    lines = result.stdout.splitlines()
    return result.returncode, lines, outcome.read_text(encoding="utf-8").splitlines() if outcome.exists() else None


@pytest.mark.parametrize(
    ("source", "before"),
    [("first-install-bootstrap", BOOT), ("bundle-state", APPLIED)],
    ids=["first-install", "routine"],
)
def test_the_applied_warehouse_is_masked_before_its_first_use(tmp_path: Path, source: str, before: str) -> None:
    code, lines, outcome = _switch(tmp_path, source=source, before=before)

    assert code == 0, lines
    assert outcome == [APPLIED, "bundle-state", APPLIED], "the capture keeps the id alone, never the mask command"
    carrying = [index for index, line in enumerate(lines) if APPLIED in line]
    assert [lines[index] for index in carrying] == [MASK, f"$ tool --warehouse-id {APPLIED}"], lines
    assert BOOT not in "\n".join(lines)


def test_outside_actions_the_switch_is_silent(tmp_path: Path) -> None:
    code, lines, outcome = _switch(tmp_path, source="first-install-bootstrap", before=BOOT, actions=False)

    assert code == 0, lines
    assert outcome == [APPLIED, "bundle-state", APPLIED]
    assert not [line for line in lines if line.startswith("::")], lines


@pytest.mark.parametrize(
    ("applied", "source", "before"),
    [("", "first-install-bootstrap", BOOT), ("", "bundle-state", APPLIED), (BOOT, "bundle-state", APPLIED)],
    ids=["no-warehouse-first-install", "no-warehouse-routine", "another-than-the-states"],
)
def test_the_switch_still_refuses_a_missing_or_changed_warehouse(
    tmp_path: Path, applied: str, source: str, before: str
) -> None:
    code, lines, outcome = _switch(tmp_path, applied=applied, source=source, before=before)

    assert code == 4 and outcome is None, lines
    assert any(NOT_THE_STATES in line for line in lines), lines
    assert not [line for line in lines if line.startswith("$ tool")], "nothing after the block ran"
    assert [line for line in lines if line.startswith("::")] == ([f"::add-mask::{applied}"] if applied else [])


def test_the_parse_prints_the_id_only_through_the_mask() -> None:
    text = block(START, END)

    assert "from tools.clients.redact import emit_mask\n" in text
    assert 'print(emit_mask(str(warehouse.get("id") or "").strip()))\n' in text
    assert text.count("print(") == 1, "the parse has one output"
