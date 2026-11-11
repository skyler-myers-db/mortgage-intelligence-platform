"""The raw seed template reaches only render_seed_sql (M1-PR4).

lakebase/seed_campaigns.sql carries ``{{mip_seed_operator_email}}``; a raw
apply would write the literal token as an operator address. This guard scans
every module that names the seed, or could enumerate its directory, and holds
each mention to a path read straight into render_seed_sql (the operator's
rendering is pinned in test_lakebase_seed_operator.py). The analysis is in
tests/fixtures/seed_render_scan.py; the reviewed sets and the cases are here.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.fixtures.seed_render_scan import ENUMERATION, raw_seed_uses

ROOT = Path(__file__).resolve().parents[2]

# Modules that only inspect the template's text. Each executes nothing (asserted).
_TEMPLATE_INSPECTORS = frozenset(
    {
        "tests/fixtures/seed_render_scan.py",
        "tests/unit/test_campaign_treatment_contracts.py",
        "tests/unit/test_lakebase_schema_immutability.py",
        "tests/unit/test_lakebase_seed_contract.py",
        "tests/unit/test_lakebase_seed_operator.py",
        "tests/unit/test_seed_render_guard.py",
    }
)
# Every module that reads and renders the seed today: the migrate job, its plan
# inputs, and the real-PostgreSQL and live contracts. None may stop reading it
# the way the scan follows (a new reader needs no entry).
_SEED_READERS = frozenset(
    {
        "jobs/lakebase_migrate.py",
        "jobs/lakebase_migration_inputs.py",
        "tests/integration/test_approval_funnel_live.py",
        "tests/integration/test_approval_funnel_revoke_postgres.py",
        "tests/integration/test_approval_requests_postgres.py",
        "tests/integration/test_audit_exclusion_postgres.py",
        "tests/integration/test_borrower_decision_history_postgres.py",
        "tests/integration/test_genie_job_cancel_migration_postgres.py",
        "tests/integration/test_genie_job_precancel_postgres.py",
        "tests/integration/test_genie_job_sections_postgres.py",
        "tests/integration/test_growth_agent_plan_progress_postgres.py",
        "tests/integration/test_growth_agent_run_links_postgres.py",
        "tests/integration/test_growth_agent_runs_postgres.py",
        "tests/integration/test_kpi_snapshot_measures_postgres.py",
        "tests/integration/test_lakebase_schema_upgrade.py",
        "tests/integration/test_lead_snoozes_postgres.py",
        "tests/integration/test_loan_officer_assignment_live.py",
        "tests/integration/test_refusal_report_texts_migration_postgres.py",
        "tests/integration/test_rum_daily_postgres.py",
        "tests/integration/test_sales_followups_postgres.py",
        "tests/integration/test_sales_loop_postgres.py",
        "tests/integration/test_saved_views_postgres.py",
        "tests/unit/test_lakebase_migration_inputs.py",
    }
)


_SEED_LITERAL = '"lakebase/seed_campaigns.sql"'
_READ = f'Path({_SEED_LITERAL}).read_text(encoding="utf-8")'


def _in_function(body: str) -> str:
    return "def apply(client):\n" + "".join(f"    {line}\n" for line in body.splitlines())


@pytest.mark.parametrize(
    ("source", "reads"),
    [
        # A render that only a comment names.
        (_in_function(f"seed = {_READ}\n# client.execute(render_seed_sql(seed))\nclient.execute(seed)"), 1),
        # Rendered at one site, applied raw at another (also by open(), round 2).
        (f"_SEED = render_seed_sql({_READ})\ndef apply(c):\n    c.execute(_SEED)\n    c.execute({_READ})\n", 2),
        (f"_SEED = render_seed_sql({_READ})\ncontract = (pre, open({_SEED_LITERAL}).read(), post)\n", 1),
        (f"client.execute({_READ})\n", 1),
        (_in_function(f"seed = {_READ}\nsql = seed + ';'\nclient.execute(sql)"), 1),
        (_in_function(f"seed = {_READ}\n_run_transaction([pre, seed], kwargs)"), 1),
        (_in_function(f"seed = {_READ}\napply_sql(client, seed)"), 1),
        # The path through a name, and bytes.
        ('SEED = ROOT / "lakebase" / "seed_campaigns.sql"\nclient.execute(SEED.read_bytes().decode())\n', 1),
        # A render whose template is not the seed it read.
        (_in_function(f"seed = {_READ}\nclient.execute(render_seed_sql(other, seed_operator_email=seed))"), 1),
        # Reads the analyzer once dropped silently (round 2): each is red now, not uncounted.
        (f"client.execute(open({_SEED_LITERAL}, encoding='utf-8').read())\n", 0),
        (_in_function(f"with open({_SEED_LITERAL}) as fh:\n    client.execute(fh.read())"), 0),
        (f"client.execute(io.open({_SEED_LITERAL}).read())\n", 0),
        (_in_function(f"with Path({_SEED_LITERAL}).open() as fh:\n    client.execute(fh.read())"), 0),
        (f"client.execute(Path.read_text(Path({_SEED_LITERAL})))\n", 1),
        ("client.execute(importlib.resources.read_text('lakebase', 'seed_campaigns.sql'))\n", 1),
        (f"client.execute(getattr(Path({_SEED_LITERAL}), 'read_text')())\n", 0),
        (f"rt = Path.read_text\nclient.execute(rt(Path({_SEED_LITERAL})))\n", 0),
        ("def _sql(name):\n    return (Path('lakebase') / name).read_text()\nclient.execute(_sql('seed_campaigns.sql'))\n", 0),
        (_in_function(f"return {_READ}"), 1),
        (f"subprocess.run(['psql', '-f', {_SEED_LITERAL}], check=True)\n", 0),
        ("subprocess.run('psql -f lakebase/seed_campaigns.sql', shell=True, check=True)\n", 0),
        # Bound where another module can import it: text, a path, a class attribute.
        (f"SEED_SQL = {_READ}\n", 1),
        (f"SEED_PATH = Path({_SEED_LITERAL})\n", 0),
        (f"class Fixture:\n    SEED = {_READ}\n", 1),
        # An enumeration of the seed's directory.
        ("for p in Path('lakebase').glob('*.sql'):\n    client.execute(p.read_text())\n", 0),
        ("LAKEBASE = ROOT / 'lakebase'\nfor p in sorted(LAKEBASE.rglob('*')):\n    client.execute(p.read_text())\n", 0),
        ("for name in os.listdir(ROOT / 'lakebase'):\n    client.execute(name)\n", 0),
    ],
    ids=[
        "comment", "two_sites", "two_sites_open", "direct", "concatenated", "transaction", "helper",
        "path_name", "kwarg", "open_read", "with_open", "io_open", "path_open", "unbound_read_text",
        "importlib_resources", "getattr", "alias", "helper_by_name", "returned", "psql_argv", "psql_shell",
        "module_text", "module_path", "class_text", "glob", "glob_through_a_name", "listdir",
    ],
)
def test_a_raw_seed_use_is_found(source: str, reads: int) -> None:
    raw, found, _executes = raw_seed_uses(source)
    assert raw and found == reads, source


@pytest.mark.parametrize(
    ("source", "reads"),
    [
        (f"client.execute(render_seed_sql({_READ}))\n", 1),
        (_in_function(f"seed = {_READ}\nclient.execute(render_seed_sql(seed, seed_operator_email=op))"), 1),
        (f"_SEED = render_seed_sql({_READ})\ndef apply(c):\n    c.execute(_SEED)\n", 1),
        (f"seed_sql = _render_seed_sql(\n    {_READ},\n    seed_operator_email=op,\n)\n_run_transaction([seed_sql], k)\n", 1),
        # The live contracts' shape: a directory name, then the file under it, in a function.
        (_in_function("root = ROOT / 'lakebase'\nseed = (root / 'seed_campaigns.sql').read_text()\n"
                      "client.execute(render_seed_sql(seed))"), 1),
        (_in_function("path = ROOT.joinpath('lakebase', 'seed_campaigns.sql').resolve()\n"
                      "client.execute(render_seed_sql(path.read_text()))"), 1),
        ("client.execute(render_seed_sql(importlib.resources.read_text('lakebase', 'seed_campaigns.sql')))\n", 1),
        # Prose, and an enumeration that cannot reach the seed.
        ('"""Applies ``lakebase/seed_campaigns.sql``."""\nclient.execute(render_seed_sql(' + _READ + "))\n", 1),
        ("assert rows, 'lakebase/seed_campaigns.sql must seed an admin'\nclient.execute(sql)\n", 0),
        ("raise RuntimeError(f'lakebase/seed_campaigns.sql seeds {n} rows')\nclient.execute(sql)\n", 0),
        ("for probe in candidate.rglob('lakebase/schema.sql'):\n    client.execute(probe.read_text())\n", 0),
    ],
    ids=[
        "direct", "named", "module_constant", "migrate_job", "live_contract", "joinpath",
        "importlib_resources", "docstring", "assert_message", "exception_text", "schema_glob",
    ],
)
def test_a_rendered_seed_is_not_a_raw_use(source: str, reads: int) -> None:
    assert raw_seed_uses(source) == ([], reads, True)


def test_the_raw_template_reaches_only_render_seed_sql() -> None:
    """A raw apply would write the literal token as an address (CI's real-PostgreSQL contracts).

    Every module that names the seed, or could enumerate its directory, is
    scanned; every mention of it in code is a path read straight into
    render_seed_sql, and a shape the scan cannot follow is red, not skipped.
    Only a reviewed inspector, which executes nothing, may read it raw to look
    at its text. What it cannot see: a module that never spells the seed's
    name, or its directory's, in one string (a whole-repo rglob, a name built
    from pieces); _SEED_READERS turns red if a known applier moves to one.
    """

    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "tests", "tools", "jobs", "scripts", "backend"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        timeout=120,
    ).stdout.decode("utf-8")
    readers: set[str] = set()
    inspectors: set[str] = set()
    for rel in filter(None, listed.split("\0")):
        if not rel.endswith(".py"):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "seed_campaigns" not in text and not ("lakebase" in text and ENUMERATION.search(text)):
            continue
        raw, reads, executes = raw_seed_uses(text)
        if rel in _TEMPLATE_INSPECTORS:
            inspectors.add(rel)
            assert not executes, f"{rel} inspects the raw template and also executes SQL"
            continue
        assert not raw, (
            f"{rel}:{raw} names the seed where the scan cannot prove render_seed_sql gets it: read it "
            "with .read_text() straight into render_seed_sql, or bind it to a name inside a function"
        )
        if reads:
            readers.add(rel)
    assert inspectors == _TEMPLATE_INSPECTORS
    assert not _SEED_READERS - readers, f"these appliers no longer read the seed the way the scan follows: {sorted(_SEED_READERS - readers)}"
