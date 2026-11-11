"""Three smaller findings of PR #336's review, closed against the handover fake.

* ``--preconditions`` prints a script the owner runs in psql: a newline in the
  legacy role's or the database's name ended the header comment and made the
  rest of that name an executable line. Names in comments are JSON strings
  now, and a statement whose quoted name holds a control character refuses.
* ``--retire-check`` read zero residue for an identity no Postgres role has
  (a typo, or a case variant: the jobs half matches case-insensitively, a
  role name does not), so it passed. It refuses ``legacy_role_not_found``.
* A rehearsal whose failure's take-back failed too left its CREATE grant on
  the throwaway with no report. It warns on stderr with the REVOKE to run,
  and so does a rehearsal whose measurement finished and whose final
  take-back raised (round 2).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.fixtures.fake_lakebase import DEPLOYER, LEGACY, FakeServer, VirtualConcurrent, throwaway
from tests.fixtures.handover_jobs_fake import jobs_workspace
from tools.clients import lakebase_handover as handover
from tools.clients import lakebase_handover_rehearse as rehearsal
from tools.clients import lakebase_handover_roles as roles
from tools.clients.lakebase_handover_sql import PgObject

INJECTED = "appdb\nDROP SCHEMA mip_app CASCADE; --"


def _script(server: FakeServer) -> tuple[list[str], list[str]]:
    """The printed script as psql reads it (one line per newline), and the refusals."""

    with server.read_session() as cursor:
        lines, refusals = roles.preconditions(cursor, target=DEPLOYER)
    return "\n".join(lines).splitlines(), refusals


def _named(server: FakeServer, database: str) -> FakeServer:
    item = PgObject("DATABASE", "", database, "", LEGACY)
    server.objects[0] = item
    server.owners[item.key] = LEGACY
    return server


def test_a_newline_in_the_database_name_stays_inside_the_header_comment() -> None:
    script, refusals = _script(_named(FakeServer(), INJECTED))
    assert refusals == []
    assert [line for line in script if not line.startswith("--")] == [], "no line psql would execute"
    assert json.dumps(INJECTED) in script[0] and json.dumps(LEGACY) in script[0]


def test_the_header_names_were_raw_before_the_fix() -> None:
    """Positive control: the unescaped header splits into an executable DROP line."""

    header = f"-- run in this order as {LEGACY}, connected to {INJECTED}."
    assert [line for line in header.splitlines() if not line.startswith("--")] == ["DROP SCHEMA mip_app CASCADE; --."]


def test_a_statement_naming_a_control_character_refuses_instead_of_printing() -> None:
    server = FakeServer(schemas={"public": "pg_database_owner", "odd\nname": LEGACY, "plain": LEGACY})
    script, refusals = _script(server)
    statements = [line for line in script if not line.startswith("--")]
    assert statements == [f'GRANT USAGE ON SCHEMA "plain" TO "{DEPLOYER}";']
    assert refusals == ["name_has_control_character"]
    assert any(line.startswith("-- REFUSED name_has_control_character") for line in script)


def _retire(server: FakeServer, identity: str, tmp_path: Path) -> int:
    workspace = jobs_workspace({}, [], me=LEGACY)
    session = SimpleNamespace(workspace=lambda: workspace, read_session=server.read_session)
    argv = ["--retire-check", "--legacy-identity", identity, "--report-out", str(tmp_path / "r.json")]
    return handover.main(argv, session=lambda *a: session)


@pytest.mark.parametrize(("identity", "variants"), [("absent-role@example.com", 0), (LEGACY.upper(), 1)])
def test_retire_check_refuses_an_identity_no_role_has(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], identity: str, variants: int
) -> None:
    server = FakeServer()
    server.extensions.clear()
    server.default_acl_roles.clear()
    capsys.readouterr()
    assert _retire(server, identity, tmp_path) == 2
    err = capsys.readouterr().err
    assert "REFUSED legacy_role_not_found" in err
    assert ("differ from it only in case" in err) is bool(variants)
    assert LEGACY not in err and identity not in err, "the message names no role"
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["legacy_role_found"] is False and report["lakebase_residue"] is None


def test_retire_check_still_passes_the_exact_name_with_nothing_left(tmp_path: Path) -> None:
    server = FakeServer()
    server.extensions.clear()
    server.default_acl_roles.clear()
    for key in list(server.owners):
        server.owners[key] = DEPLOYER
    server.acl.clear()
    assert _retire(server, LEGACY, tmp_path) == 0


def _rehearse(server: FakeServer) -> rehearsal.RehearsalReport:
    return rehearsal.rehearse(
        server.connect,
        legacy=LEGACY,
        target=DEPLOYER,
        hold_s=8.0,
        concurrent=lambda _connect, hold: VirtualConcurrent(server, hold),
        clock=server.clock,
        sleep=server.sleep,
    )


def test_a_failed_rehearsal_whose_take_back_fails_says_what_remains(capsys: pytest.CaptureFixture[str]) -> None:
    server = throwaway()
    schema = f"SCHEMA:{rehearsal.REHEARSAL_SCHEMA}"
    server.fail_on |= {f"TABLE:{rehearsal.REHEARSAL_SCHEMA}.rehearsal_target", schema}
    with pytest.raises(Exception, match="injected") as failed:
        _rehearse(server)
    assert server.has_privilege(DEPLOYER, schema, "CREATE"), "the grant really remains"
    revoke = f'REVOKE CREATE ON SCHEMA "{rehearsal.REHEARSAL_SCHEMA}" FROM'
    err = capsys.readouterr().err
    assert "WARNING" in err and revoke in err and DEPLOYER not in err
    assert any(revoke in note for note in getattr(failed.value, "__notes__", []))


def test_a_measured_rehearsal_whose_take_back_fails_says_what_remains(capsys: pytest.CaptureFixture[str]) -> None:
    """Round 2: the measurement completes and only the final REVOKE raises; the leftover is reported the same way."""

    server = throwaway()
    schema = f"SCHEMA:{rehearsal.REHEARSAL_SCHEMA}"
    server.fail_on.add(schema)  # only the take-back's REVOKE fails
    with pytest.raises(Exception, match="injected") as failed:
        _rehearse(server)
    assert server.has_privilege(DEPLOYER, schema, "CREATE"), "the grant really remains"
    assert server.owners[f"TABLE:{rehearsal.REHEARSAL_SCHEMA}.rehearsal_target"] == LEGACY, "the measurement ran and reverted"
    revoke = f'REVOKE CREATE ON SCHEMA "{rehearsal.REHEARSAL_SCHEMA}" FROM'
    err = capsys.readouterr().err
    assert "WARNING" in err and revoke in err and DEPLOYER not in err
    assert any(revoke in note for note in getattr(failed.value, "__notes__", []))


def test_a_failed_rehearsal_whose_take_back_lands_says_nothing_more(capsys: pytest.CaptureFixture[str]) -> None:
    server = throwaway()
    server.fail_on.add(f"TABLE:{rehearsal.REHEARSAL_SCHEMA}.rehearsal_target")
    with pytest.raises(Exception, match="injected") as failed:
        _rehearse(server)
    assert "WARNING" not in capsys.readouterr().err and not getattr(failed.value, "__notes__", [])
