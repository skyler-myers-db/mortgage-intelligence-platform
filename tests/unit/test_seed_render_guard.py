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

from tests.fixtures.seed_render_scan import (
    ENUMERATION,
    inspector_reaches,
    raw_seed_uses,
    scan_seed_uses,
)

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
# Enumerations, in modules that name the lakebase directory as a path, whose
# directory the scan cannot prove is another: (module, call). Each lists a
# tree a test built under tmp_path, or a staged release, to compare file
# names, and applies no SQL. A new one is red until it is reviewed here.
_REVIEWED_ENUMERATIONS = frozenset(
    {
        # files_under(base): the names in a fixture repo or a staged App source.
        ("tests/fixtures/app_runtime_stage_repo.py", "base.rglob('*')"),
        # nothing_written(tmp_path, out): no staging directory is left behind.
        ("tests/fixtures/app_runtime_stage_repo.py", "tmp_path.iterdir()"),
        ("tests/unit/test_app_runtime_closure_release.py", "tmp_path.iterdir()"),
        # The prebuilt release's file walk, compared with its SHA256SUMS manifest.
        ("tests/unit/test_prebuilt_release.py", "release.walk(root)"),
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
        # Bound in a function to a module name another module can import (round 3).
        (f"def _init():\n    global SEED_SQL\n    SEED_SQL = {_READ}\n_init()\n", 1),
        (f"def _init():\n    global SEED_PATH\n    SEED_PATH = Path({_SEED_LITERAL})\n_init()\n", 0),
        (_in_function(f"seed = ''\ndef load():\n    nonlocal seed\n    seed = {_READ}\nload()\n"
                      "client.execute(render_seed_sql(seed))"), 1),
        # The directory reaches the enumeration some way other than a literal (round 3).
        ("def _lakebase_dir():\n    return Path('lakebase')\nfor p in sorted(_lakebase_dir().glob('s*.sql')):\n"
         "    client.execute(p.read_text())\n", 0),
        ("def apply(client, root):\n    for p in root.glob('*.sql'):\n        client.execute(p.read_text())\n"
         "apply(client, ROOT / 'lakebase')\n", 0),
        ("class Applier:\n    def __init__(self):\n        self.root = ROOT / 'lakebase'\n"
         "    def apply(self, client):\n        for p in self.root.glob('*.sql'):\n"
         "            client.execute(p.read_text())\n", 0),
        ("from tests.fixtures.paths import LAKEBASE_DIR\nassert (ROOT / 'lakebase' / 'schema.sql').is_file()\n"
         "for p in LAKEBASE_DIR.glob('*.sql'):\n    client.execute(p.read_text())\n", 0),
        ("d = ROOT / 'sql'\nd = ROOT / 'lakebase'\nfor p in d.glob('*.sql'):\n    client.execute(p.read_text())\n", 0),
        ("for name in glob.glob(str(ROOT / 'lakebase' / '*.sql')):\n    client.execute(Path(name).read_text())\n", 0),
        ("LAKEBASE = 'lakebase'\nfor p in ROOT.rglob('seed_*.sql'):\n    client.execute(p.read_text())\n", 0),
        ("LAKEBASE = 'lakebase'\nfor top, _dirs, files in os.walk(ROOT):\n    client.execute(files)\n", 0),
        ("for p in (ROOT / 'sql').glob('../lakebase/*.sql'):\n    client.execute(p.read_text())\n", 0),
    ],
    ids=[
        "comment", "two_sites", "two_sites_open", "direct", "concatenated", "transaction", "helper",
        "path_name", "kwarg", "open_read", "with_open", "io_open", "path_open", "unbound_read_text",
        "importlib_resources", "getattr", "alias", "helper_by_name", "returned", "psql_argv", "psql_shell",
        "module_text", "module_path", "class_text", "glob", "glob_through_a_name", "listdir",
        "global_text", "global_path", "nonlocal_text",
        "glob_via_dir_helper", "glob_via_parameter", "glob_via_attribute", "glob_via_imported_dir",
        "glob_via_a_rebound_name", "glob_module_form", "rglob_from_an_unknown_root", "os_walk", "pattern_escapes",
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
        # Round 3: the directory is proven another, or the pattern cannot match, or lakebase is only a label.
        ("assert (ROOT / 'lakebase' / 'schema.sql').is_file()\n"
         "for p in sorted((ROOT / 'sql' / 'ddl').glob('*.sql')):\n    client.execute(p.read_text())\n", 0),
        ("LAKEBASE = ROOT / 'lakebase'\ndef apply(client, root):\n    for p in (root / 'wheelhouse').iterdir():\n"
         "        client.execute(p.name)\n", 0),
        ("LAKEBASE = ROOT / 'lakebase'\nfor p in LAKEBASE.glob('*.json'):\n    client.execute(p.read_text())\n", 0),
        ("DEPENDENCY = 'lakebase'\ndef evidence(root):\n    return sorted(root.glob(f'{DEPENDENCY}-*.json'))\n"
         "client.execute(sql)\n", 0),
        ("LAKEBASE = 'lakebase'\nfor node in ast.walk(tree):\n    client.execute(node)\n", 0),
        ("checks = [ClosureEntry('lakebase', 'upload')]\nfor p in root.glob('*'):\n    client.execute(p.name)\n", 0),
    ],
    ids=[
        "direct", "named", "module_constant", "migrate_job", "live_contract", "joinpath",
        "importlib_resources", "docstring", "assert_message", "exception_text", "schema_glob",
        "another_directory", "a_named_subdirectory", "a_pattern_that_cannot_match", "dependency_label",
        "ast_walk", "label_only",
    ],
)
def test_a_rendered_seed_is_not_a_raw_use(source: str, reads: int) -> None:
    assert raw_seed_uses(source) == ([], reads, True)


def test_the_raw_template_reaches_only_render_seed_sql() -> None:
    """A raw apply would write the literal token as an address (CI's real-PostgreSQL contracts).

    Every module that names the seed, or could enumerate its directory, is
    scanned; every mention of it in code is a path read straight into
    render_seed_sql, and a shape the scan cannot follow is red, not skipped.
    An enumeration that can list the seed is red unless its directory is
    proven another or it is reviewed in _REVIEWED_ENUMERATIONS. Only a
    reviewed inspector, which executes nothing, may read it raw to look at its
    text, and no other module may import that text from it (the next test).
    What it cannot see: a module that never spells the seed's name in code,
    nor the lakebase directory as a path (a whole-repo rglob, a name built
    from pieces, a helper module handed the directory by another);
    _SEED_READERS turns red if a known applier moves to one.
    """

    readers: set[str] = set()
    inspectors: set[str] = set()
    reviewed: set[tuple[str, str]] = set()
    for rel in _tracked_python("tests", "tools", "jobs", "scripts", "backend"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "seed_campaigns" not in text and not ("lakebase" in text.lower() and ENUMERATION.search(text)):
            continue
        scan = scan_seed_uses(text)
        if rel in _TEMPLATE_INSPECTORS:
            inspectors.add(rel)
            assert not scan.executes, f"{rel} inspects the raw template and also executes SQL"
            continue
        assert not scan.raw, (
            f"{rel}:{scan.raw} names the seed where the scan cannot prove render_seed_sql gets it: read it "
            "with .read_text() straight into render_seed_sql, or bind it to a name inside a function"
        )
        listed = {(rel, call) for _line, call in scan.enumerations}
        reviewed |= listed & _REVIEWED_ENUMERATIONS
        assert listed <= _REVIEWED_ENUMERATIONS, (
            f"{rel}: {sorted(call for _rel, call in listed - _REVIEWED_ENUMERATIONS)} can list "
            "lakebase/seed_campaigns.sql: list a directory the module builds from literals, or match only "
            "other names, or (if it applies no SQL) review it into _REVIEWED_ENUMERATIONS"
        )
        if scan.reads:
            readers.add(rel)
    assert inspectors == _TEMPLATE_INSPECTORS
    assert reviewed == _REVIEWED_ENUMERATIONS, f"stale reviews: {sorted(_REVIEWED_ENUMERATIONS - reviewed)}"
    assert not _SEED_READERS - readers, f"these appliers no longer read the seed the way the scan follows: {sorted(_SEED_READERS - readers)}"


def _tracked_python(*pathspecs: str) -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", *pathspecs],
        cwd=ROOT,
        capture_output=True,
        check=True,
        timeout=120,
    ).stdout.decode("utf-8")
    return [rel for rel in listed.split("\0") if rel.endswith(".py")]


# --- An inspector's raw text stays in the inspector (round 3) -------------------------


@pytest.mark.parametrize(
    ("source", "exports"),
    [
        (f"TEMPLATE = {_READ}\n", {"TEMPLATE"}),
        ("SEED = ROOT / 'lakebase' / 'seed_campaigns.sql'\n", {"SEED"}),
        (f"TEMPLATE = {_READ}\nALIAS = TEMPLATE\n", {"TEMPLATE", "ALIAS"}),
        (f"def _template():\n    return {_READ}\n", {"_template"}),
        (f"TEMPLATE = {_READ}\ndef template():\n    yield TEMPLATE\ntemplate = pytest.fixture(template)\n",
         {"TEMPLATE", "template"}),
        (f"class Seeds:\n    TEXT = {_READ}\n", {"Seeds"}),
        (f"def _init():\n    global SEED_SQL\n    SEED_SQL = {_READ}\n", {"SEED_SQL"}),
        (f"with open({_SEED_LITERAL}) as fh:\n    SEED = fh.read()\n", {"fh", "SEED"}),
        ("TEXTS = [p.read_text() for p in Path('lakebase').glob('*.sql')]\n", {"TEXTS"}),
        # What an inspector keeps to itself.
        (f"_SEED = render_seed_sql({_READ})\nOPERATOR = 'demo.operator@summit.example'\n", set()),
        (f"def test_the_seed_archives_campaigns():\n    seed = {_READ}\n    assert 'archived' in seed\n", set()),
        ("def found(node):\n    pending = [n for n in node if 'seed_campaigns' in n]\n    return len(pending)\n", set()),
    ],
    ids=[
        "module_text", "module_path", "alias", "returned", "fixture", "class_attribute", "global",
        "module_with", "enumeration", "rendered_and_unrelated", "local_inspection", "a_scanner",
    ],
)
def test_an_inspectors_exports_are_every_name_that_hands_out_the_raw_seed(source: str, exports: set[str]) -> None:
    assert scan_seed_uses(source).exports == exports


_EXPORTS = {
    "test_campaign_treatment_contracts": frozenset({"SEED"}),
    "test_lakebase_seed_contract": frozenset({"SEED"}),
    "test_lakebase_seed_operator": frozenset({"TEMPLATE"}),
}
_OPERATOR_MODULE = "tests.unit.test_lakebase_seed_operator"


@pytest.mark.parametrize(
    "source",
    [
        # Round 3's tree mutations MA1 and MA2: the raw text imported and applied.
        "from tests.unit.test_campaign_treatment_contracts import SEED as _RAW_SEED\n"
        "_run_transaction((pre_seed, _RAW_SEED, post_seed), kwargs)\n",
        f"from {_OPERATOR_MODULE} import TEMPLATE\nlakebase_migrate._run_transaction((pre, TEMPLATE, post), kwargs)\n",
        "from tests.unit.test_lakebase_seed_contract import SEED\nclient.execute(SEED.read_text())\n",
        f"from {_OPERATOR_MODULE} import *\n",
        "from .test_lakebase_seed_operator import TEMPLATE\n",
        f"import {_OPERATOR_MODULE} as m\nclient.execute(m.TEMPLATE)\n",
        f"import {_OPERATOR_MODULE}\nclient.execute({_OPERATOR_MODULE}.TEMPLATE)\n",
        "import test_lakebase_seed_operator\nclient.execute(test_lakebase_seed_operator.TEMPLATE)\n",
        "from tests.unit import test_lakebase_seed_operator as m\nclient.execute(getattr(m, 'TEMPLATE'))\n",
        f"import {_OPERATOR_MODULE} as m\nclient.execute(m.__dict__['TEMPLATE'])\n",
        f"module = importlib.import_module('{_OPERATOR_MODULE}')\n",
        "namespace = runpy.run_path(str(ROOT / 'tests/unit/test_lakebase_seed_operator.py'))\n",
        f"pytest_plugins = ['{_OPERATOR_MODULE}']\n",
    ],
    ids=[
        "ma1_campaign_seed", "ma2_operator_template", "contract_path", "star", "relative", "module_alias",
        "dotted_module", "bare_module", "getattr", "namespace", "import_module", "run_path", "pytest_plugins",
    ],
)
def test_importing_an_inspectors_raw_seed_is_found(source: str) -> None:
    assert inspector_reaches(source, _EXPORTS), source


@pytest.mark.parametrize(
    "source",
    [
        f"from {_OPERATOR_MODULE} import OPERATOR\n",
        f"import {_OPERATOR_MODULE} as m\nassert m.OPERATOR.endswith('.example')\n",
        "from tests.unit.test_campaign_treatment_contracts import DDL\n",
        "# The strip rule is pinned in tests/unit/test_lakebase_seed_operator.py.\nx = 1\n",
        '"""Pinned in test_lakebase_seed_operator.py."""\n',
        'PRELUDE = """set -euo pipefail\n# its strip rule is pinned in test_lakebase_seed_operator.py\n"""\n',
    ],
    ids=["a_constant", "a_module_attribute", "another_name", "comment", "docstring", "an_embedded_script"],
)
def test_importing_anything_else_from_an_inspector_is_not(source: str) -> None:
    assert inspector_reaches(source, _EXPORTS) == []


def test_no_module_imports_an_inspectors_raw_seed() -> None:
    """An inspector may hold the raw template at module level; no other module may take it from there.

    Each inspector's exports are computed from its source, so a new one is
    covered as soon as it is written; a module that imports one, or reaches
    the inspector's module some way the scan cannot read, is red.
    """

    exports = {
        Path(rel).stem: scan_seed_uses((ROOT / rel).read_text(encoding="utf-8")).exports
        for rel in _TEMPLATE_INSPECTORS
    }
    # The scan sees what the inspectors hold today, so the check below is not vacuous.
    assert exports["test_campaign_treatment_contracts"] >= {"SEED"}
    assert exports["test_lakebase_seed_operator"] >= {"TEMPLATE"}
    assert exports["test_lakebase_seed_contract"] >= {"SEED"}
    for rel in _tracked_python():
        if rel in _TEMPLATE_INSPECTORS:
            continue
        text = (ROOT / rel).read_text(encoding="utf-8")
        if any(stem in text for stem in exports):
            assert not inspector_reaches(text, exports), (
                f"{rel}:{inspector_reaches(text, exports)} takes the raw seed from an inspector: render it "
                "there with render_seed_sql, or read the file here the way the scan follows"
            )
