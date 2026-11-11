"""The raw seed template reaches only render_seed_sql (M1-PR4).

lakebase/seed_campaigns.sql carries ``{{mip_seed_operator_email}}``; a raw
apply would write the literal token as an operator address. This guard scans
every module that names the seed, or could enumerate its directory, and holds
each mention to a path read straight into render_seed_sql (the operator's
rendering is pinned in test_lakebase_seed_operator.py).
"""

from __future__ import annotations

import ast
import fnmatch
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

_RENDERERS = frozenset({"render_seed_sql", "_render_seed_sql"})
_EXECUTORS = frozenset({"execute", "executemany", "executescript", "_run_transaction"})
_READS = frozenset({"read_text", "read_bytes"})
# What a path may pass through between the file's name and its read or binding.
_PATH_STEPS = frozenset(
    {"Path", "PurePath", "PosixPath", "PurePosixPath", "joinpath", "resolve", "absolute", "expanduser", "str", "fspath"}
)
_ENUMERATORS = frozenset({"glob", "rglob", "iglob", "iterdir", "listdir", "scandir", "walk"})
_LAKEBASE_DIR = re.compile(r"(?:^|/)lakebase(?:/|$)")
_ENUMERATION = re.compile(r"\b(?:" + "|".join(sorted(_ENUMERATORS)) + r")\b")
# Modules that only inspect the template's text. Each executes nothing (asserted).
_TEMPLATE_INSPECTORS = frozenset(
    {
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


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
    return None


def _bound_names(node: ast.AST | None) -> list[str] | None:
    """The names an assignment of one value binds, or None when it binds anything else."""

    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, ast.AnnAssign):
        targets = [node.target]
    else:
        return None
    if all(isinstance(target, ast.Name) for target in targets):
        return [target.id for target in targets]
    return None


def raw_seed_uses(source: str) -> tuple[list[int], int, bool]:
    """(lines where the seed goes anywhere but render_seed_sql, seed reads, whether it executes SQL).

    Fail-closed: every string in code that names ``seed_campaigns`` must be a
    path, built only by ``/``, ``+``, f-strings and Path steps, that is either
    bound to a name inside a function or read by ``.read_text()`` /
    ``.read_bytes()`` (``Path.read_text(p)`` and
    ``importlib.resources.read_text(pkg, name)`` count too). A read must be
    render_seed_sql's first argument, or be bound inside a function to a name
    whose every load is; a bound path's every load must be such a read.
    Anything else is a raw use: open(), an alias, a helper taking the name, a
    subprocess argument, a return, or a module- or class-level binding another
    module could import. Prose (a docstring, an assert message, an exception's
    text) may name the file. An enumeration of the lakebase directory that can
    match the seed is a raw use too. Comments are not code.
    """

    tree = ast.parse(source)
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    loads: dict[str, list[ast.Name]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loads.setdefault(node.id, []).append(node)

    def rendered(node: ast.AST) -> bool:
        call = parent.get(node)
        return isinstance(call, ast.Call) and _call_name(call) in _RENDERERS and call.args[:1] == [node]

    def in_function(node: ast.AST) -> bool:
        up = parent.get(node)
        while up is not None:
            if isinstance(up, ast.ClassDef):
                return False
            if isinstance(up, ast.FunctionDef | ast.AsyncFunctionDef):
                return True
            up = parent.get(up)
        return False

    def prose(node: ast.Constant) -> bool:
        if not any(char.isspace() for char in node.value):
            return False
        child, up = node, parent.get(node)
        while isinstance(up, ast.JoinedStr | ast.BinOp):
            child, up = up, parent.get(up)
        if isinstance(up, ast.Expr):
            return True
        if isinstance(up, ast.Assert):
            return child is up.msg
        return isinstance(up, ast.Call) and child in up.args and isinstance(parent.get(up), ast.Raise)

    def method(up: ast.AST | None, names: frozenset[str]) -> ast.Call | None:
        """The call ``up`` makes when it is ``<receiver>.<one of names>``, else None."""

        call = parent.get(up) if isinstance(up, ast.Attribute) and up.attr in names else None
        return call if isinstance(call, ast.Call) and call.func is up else None

    def climb(node: ast.AST) -> tuple[ast.AST, ast.AST | None]:
        """The whole path expression ``node`` is part of, and what holds it."""

        child, up = node, parent.get(node)
        while True:
            if (step := method(up, _PATH_STEPS)) is not None:
                child = step  # p.resolve(), p.joinpath(...)
            elif isinstance(up, ast.BinOp | ast.JoinedStr | ast.FormattedValue) or (
                isinstance(up, ast.Call) and _call_name(up) in _PATH_STEPS and child in up.args
            ):
                child = up
            else:
                return child, up
            up = parent.get(child)

    raw: list[int] = []
    reads = 0
    path_names: set[str] = set()
    text_names: set[str] = set()
    pending: list[ast.AST] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "seed_campaigns" in node.value
        and not prose(node)
    ]
    while pending:
        anchor = pending.pop()
        path, up = climb(anchor)
        read = method(up, _READS)
        if read is None and isinstance(up, ast.Call) and _call_name(up) in _READS and path in up.args:
            read = up  # Path.read_text(p), importlib.resources.read_text(pkg, name)
        if read is None:
            names = _bound_names(up)
            if names is None or not in_function(path):
                raw.append(path.lineno)
            for name in set(names or []) - path_names:
                path_names.add(name)
                pending.extend(loads.get(name, []))
            continue
        reads += 1
        if rendered(read):
            continue
        names = _bound_names(parent.get(read))
        if names is None or not in_function(read):
            raw.append(read.lineno)
        text_names.update(names or [])
    raw.extend(node.lineno for name in text_names for node in loads.get(name, []) if not rendered(node))

    lakebase_names: set[str] = set()

    def names_lakebase(expr: ast.AST) -> bool:
        return any(
            (isinstance(inner, ast.Constant) and isinstance(inner.value, str) and _LAKEBASE_DIR.search(inner.value))
            or (isinstance(inner, ast.Name) and inner.id in lakebase_names)
            for inner in ast.walk(expr)
        )

    bindings = [node for node in ast.walk(tree) if isinstance(node, ast.Assign | ast.AnnAssign) and node.value]
    while new := {
        name
        for node in bindings
        if node.value is not None and names_lakebase(node.value)
        for name in _bound_names(node) or []
    } - lakebase_names:
        lakebase_names |= new
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _call_name(node) in _ENUMERATORS and names_lakebase(node)):
            continue
        first = node.args[0] if node.args else None
        pattern = first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None
        if pattern is None or any(
            fnmatch.fnmatchcase(seed, pattern) for seed in ("seed_campaigns.sql", "lakebase/seed_campaigns.sql")
        ):
            raw.append(node.lineno)
    executes = any(_call_name(node) in _EXECUTORS for node in ast.walk(tree))
    return sorted(raw), reads, executes


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
        if "seed_campaigns" not in text and not ("lakebase" in text and _ENUMERATION.search(text)):
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
