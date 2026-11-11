"""The demo seed names its operator by a token, rendered from the install's profile (M1-PR4).

lakebase/seed_campaigns.sql once committed one install's operator address on
nine rows (the sales_team admin row, the archived campaigns' owner and the
sample approvals' actor), so every install seeded that person. The rows now
carry ``{{mip_seed_operator_email}}``. The migrate job renders it from
``--seed-operator-email``, the ``seed_operator_email`` bundle variable that
tools/databricks/bundle_env.py sets from the client profile's demo-only
``MIP_SEED_OPERATOR_EMAIL``; with none it renders the synthetic
``operator@summit.example``. Plan and apply render the same text, so an
install that sets the address it seeded before renders the same seed, and the
same schema-input digest, as before the token.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from jobs import lakebase_migrate
from jobs.lakebase_migration_inputs import render_schema_inputs
from jobs.lakebase_migration_seed import (
    SEED_OPERATOR_EMAIL_ENV,
    SEED_OPERATOR_TOKEN,
    SYNTHETIC_SEED_OPERATOR,
    render_seed_sql,
)
from tests.fixtures.client_profiles import env_file_text, paychex_shaped_profile
from tools.databricks import bundle_env

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = (ROOT / "lakebase" / "seed_campaigns.sql").read_text(encoding="utf-8")
OPERATOR = "demo.operator@summit.example"
_AMBIENT_PREFIXES = ("MIP_", "DATABRICKS_", "BUNDLE_VAR_", "GENIE_", "LAKEBASE_", "APP_ENV")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [name for name in os.environ if name.startswith(_AMBIENT_PREFIXES)]:
        monkeypatch.delenv(name)


def test_the_template_names_its_operator_only_by_the_token() -> None:
    assert TEMPLATE.count(SEED_OPERATOR_TOKEN) == 9
    # Every token sits in a single-quoted literal, the only place an address may go.
    assert TEMPLATE.count(f"'{SEED_OPERATOR_TOKEN}'") == 9
    assert "{{" not in TEMPLATE.replace(SEED_OPERATOR_TOKEN, "")


def test_a_configured_operator_replaces_every_token_and_nothing_else() -> None:
    rendered = render_seed_sql(TEMPLATE, seed_operator_email=f"  {OPERATOR}\n")

    assert rendered == TEMPLATE.replace(SEED_OPERATOR_TOKEN, OPERATOR)
    assert rendered.count(f"'{OPERATOR}'") == 9 and SEED_OPERATOR_TOKEN not in rendered


@pytest.mark.parametrize("unset", ["", "   "])
def test_no_operator_seeds_the_synthetic_one(unset: str) -> None:
    rendered = render_seed_sql(TEMPLATE, seed_operator_email=unset)

    assert rendered == TEMPLATE.replace(SEED_OPERATOR_TOKEN, SYNTHETIC_SEED_OPERATOR)
    assert SYNTHETIC_SEED_OPERATOR.endswith(".example")


@pytest.mark.parametrize(
    "value",
    [
        "not-an-address",
        "o'brien@summit.example",
        "a@summit.example,b@summit.example",
        "a@summit.example b@summit.example",
        "a@summit.example'); DROP TABLE mip_app.approvals; --",
    ],
)
def test_anything_but_one_address_refuses_without_echoing_it(value: str) -> None:
    with pytest.raises(RuntimeError) as refused:
        render_seed_sql(TEMPLATE, seed_operator_email=value)

    assert SEED_OPERATOR_EMAIL_ENV in str(refused.value) and value not in str(refused.value)


def test_an_unknown_template_token_refuses() -> None:
    with pytest.raises(RuntimeError, match="unknown template token"):
        render_seed_sql(TEMPLATE + "\n-- {{mip_other}}\n", seed_operator_email=OPERATOR)


def test_plan_and_apply_render_the_same_seed_for_the_same_operator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(lakebase_migrate, "_resolve_connection", lambda: {"host": "test"})
    monkeypatch.setattr(
        lakebase_migrate, "_preflight_database_roles", lambda _kwargs, **_options: ("app-role", None)
    )
    monkeypatch.setattr(
        lakebase_migrate,
        "_run_transaction",
        lambda sql_texts, _kwargs, **_options: captured.update(sql_texts=sql_texts),
    )
    monkeypatch.setattr(lakebase_migrate, "_apply_app_role_grants", lambda _kwargs, **_options: None)

    lakebase_migrate.main(seed_operator_email=OPERATOR)
    _schema, planned = render_schema_inputs(
        ROOT,
        lender_name="Summit Mortgage",
        lender_nmls_id="",
        tenant_id="",
        seed_operator_email=OPERATOR,
    )

    assert captured["sql_texts"][1] == planned[1] == render_seed_sql(TEMPLATE, seed_operator_email=OPERATOR)


def test_the_parser_reads_the_flag_and_falls_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert lakebase_migrate.build_parser().parse_args([]).seed_operator_email == ""
    monkeypatch.setenv(SEED_OPERATOR_EMAIL_ENV, OPERATOR)
    assert lakebase_migrate.build_parser().parse_args([]).seed_operator_email == OPERATOR
    flagged = lakebase_migrate.build_parser().parse_args(["--seed-operator-email=x@summit.example"])
    assert flagged.seed_operator_email == "x@summit.example"


def test_the_bundle_passes_the_variable_to_the_migrate_job() -> None:
    bundle = yaml.safe_load((ROOT / "databricks.yml").read_text(encoding="utf-8"))
    (task,) = bundle["resources"]["jobs"]["mip_lakebase_migrate"]["tasks"]

    assert "--seed-operator-email=${var.seed_operator_email}" in task["spark_python_task"]["parameters"]
    assert bundle["variables"]["seed_operator_email"]["default"] == ""


_LIB = ROOT / "scripts" / "lib"
_LIFECYCLE = (_LIB / "deploy_quiesce_window_lifecycle.sh").read_text(encoding="utf-8")
_PLAN_OPERATOR_START = '    if ! seed_operator_email="$('
# _client_validate patches subprocess.run itself (bundle_env.subprocess is the module).
_RUN = subprocess.run


def test_the_deploys_migration_plan_passes_the_operator_it_resolved() -> None:
    plan = _LIFECYCLE[_LIFECYCLE.index('"$PYTHON" jobs/lakebase_migrate.py --mode plan') :]
    plan = plan[: plan.index("\n    plan_args+=")]

    assert '--seed-operator-email "$seed_operator_email"' in plan
    assert _LIFECYCLE.index(_PLAN_OPERATOR_START) < _LIFECYCLE.index('"$PYTHON" jobs/lakebase_migrate.py --mode plan')


def _plan_operator(cwd: Path) -> str:
    """The operator the deploy's migration plan resolves in ``cwd``, run as the lifecycle runs it.

    The identity library is sourced as deploy.sh sources it, so the shell's
    value and ``.env.local`` in ``cwd`` are read by the deploy's own helpers.
    """

    start = _LIFECYCLE.index(_PLAN_OPERATOR_START)
    block = _LIFECYCLE[start : _LIFECYCLE.index("\n    fi\n", start) + len("\n    fi\n")]
    harness = (
        "set -euo pipefail\nRED=''; RST=''\n"
        f'. "{_LIB / "deploy_identity_lifecycle.sh"}"\n'
        f'resolve() {{\n  local seed_operator_email\n{block}  printf "op=%s\\n" "$seed_operator_email"\n}}\nresolve\n'
    )
    completed = _RUN(
        ["bash", "-c", harness],
        cwd=cwd,
        env={**os.environ, "PYTHON": sys.executable, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.splitlines()[-1].removeprefix("op=")


@pytest.mark.parametrize(
    ("shell", "expected"),
    [
        (None, OPERATOR),
        ("", OPERATOR),
        ("   ", OPERATOR),
        ("\t", OPERATOR),
        ("\u00a0\u3000", OPERATOR),
        ("  shell.operator@summit.example ", "shell.operator@summit.example"),
    ],
    ids=["unset", "empty", "spaces", "tab", "unicode_space", "shell_wins"],
)
def test_the_deploys_migration_plan_renders_the_seed_the_bundle_binds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, shell: str | None, expected: str
) -> None:
    # Review of M1-PR4: the profile strips the shell value and reads
    # .env.local's when it is blank (tools/clients/environment.from_local);
    # the plan once passed a whitespace-only shell value on, so it rendered the
    # synthetic operator while apply rendered the profile's.
    if shell is not None:
        monkeypatch.setenv(SEED_OPERATOR_EMAIL_ENV, shell)
    profile = {**paychex_shaped_profile(), SEED_OPERATOR_EMAIL_ENV: OPERATOR}
    bound = _client_validate(monkeypatch, tmp_path, profile)["BUNDLE_VAR_seed_operator_email"]
    planned = _plan_operator(tmp_path)

    assert bound == expected
    assert render_seed_sql(TEMPLATE, seed_operator_email=planned) == render_seed_sql(
        TEMPLATE, seed_operator_email=bound
    )


def _client_validate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, profile: dict[str, str]) -> dict[str, str]:
    """The env bundle_env ``validate -t client`` would hand the CLI for ``profile``."""

    env_local = tmp_path / ".env.local"
    env_local.write_text(env_file_text(profile), encoding="utf-8")
    monkeypatch.setattr(bundle_env, "ENV_LOCAL", env_local)
    monkeypatch.setattr(bundle_env, "BUNDLE_ROOT", tmp_path)
    monkeypatch.setattr(bundle_env.render_sql, "render", lambda **_kwargs: (1, 0, 0))
    seen: dict[str, str] = {}

    def fake_cli(command: list[str], *, env: dict[str, str], check: bool) -> object:
        seen.update(env)
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(bundle_env.subprocess, "run", fake_cli)
    monkeypatch.setattr(bundle_env.sys, "argv", ["bundle_env.py", "validate", "-t", "client"])
    assert bundle_env.main() == 0
    return seen


def test_the_profiles_operator_alone_reaches_the_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    profile = {**paychex_shaped_profile(), SEED_OPERATOR_EMAIL_ENV: OPERATOR}
    assert _client_validate(monkeypatch, tmp_path, profile)["BUNDLE_VAR_seed_operator_email"] == OPERATOR

    # An ambient export never reaches the CLI: a profile without the field binds none.
    monkeypatch.setenv("BUNDLE_VAR_seed_operator_email", "ambient@summit.example")
    unset = {name: value for name, value in profile.items() if name != SEED_OPERATOR_EMAIL_ENV}
    assert "BUNDLE_VAR_seed_operator_email" not in _client_validate(monkeypatch, tmp_path, unset)


def test_no_tracked_seed_or_bundle_text_names_an_operator_address() -> None:
    """The ratchet (test_no_client_identifiers_committed.py) covers every file; this pins the seed."""

    addresses = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", TEMPLATE)
    assert addresses and all(address.endswith("@summit.example") for address in addresses)


# --- The raw template reaches only render_seed_sql -----------------------------------

_RENDERERS = frozenset({"render_seed_sql", "_render_seed_sql"})
_EXECUTORS = frozenset({"execute", "executemany", "executescript", "_run_transaction"})
# Modules that only inspect the template's text. Each executes nothing (asserted).
_TEMPLATE_INSPECTORS = frozenset(
    {
        "tests/unit/test_campaign_treatment_contracts.py",
        "tests/unit/test_lakebase_schema_immutability.py",
        "tests/unit/test_lakebase_seed_contract.py",
        "tests/unit/test_lakebase_seed_operator.py",
    }
)


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
    return None


def _bound_names(node: ast.AST) -> list[str] | None:
    """The names an assignment of one value binds, or None when it binds anything else."""

    targets = node.targets if isinstance(node, ast.Assign) else [node.target]  # type: ignore[attr-defined]
    if all(isinstance(target, ast.Name) for target in targets):
        return [target.id for target in targets]  # type: ignore[attr-defined]
    return None


def raw_seed_uses(source: str) -> tuple[list[int], int, bool]:
    """(lines where raw seed text goes anywhere but render_seed_sql, seed reads, whether it executes SQL).

    A seed read is ``.read_text()`` / ``.read_bytes()`` on a path naming
    ``seed_campaigns.sql``, directly or through a name bound to one. The read
    must be render_seed_sql's first argument, or be bound to a name whose every
    load is; anything else (an execute, a concatenation, a helper) is a raw use.
    Comments are not code, so a commented-out render never counts.
    """

    tree = ast.parse(source)
    parent = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    path_names = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None
        and "seed_campaigns.sql" in ast.unparse(node.value)
        and not any(_call_name(inner) in {"read_text", "read_bytes"} for inner in ast.walk(node.value))
        for name in (_bound_names(node) or [])
    }

    def names_seed(expr: ast.AST) -> bool:
        return "seed_campaigns.sql" in ast.unparse(expr) or any(
            isinstance(inner, ast.Name) and inner.id in path_names for inner in ast.walk(expr)
        )

    def rendered(node: ast.AST) -> bool:
        call = parent.get(node)
        return isinstance(call, ast.Call) and _call_name(call) in _RENDERERS and call.args[:1] == [node]

    raw: list[int] = []
    text_names: set[str] = set()
    reads = 0
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"read_text", "read_bytes"}
            and names_seed(node.func.value)
        ):
            continue
        reads += 1
        if rendered(node):
            continue
        holder = parent.get(node)
        names = _bound_names(holder) if isinstance(holder, ast.Assign | ast.AnnAssign) else None
        if names is None:
            raw.append(node.lineno)
        else:
            text_names.update(names)
    raw.extend(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in text_names and not rendered(node)
    )
    executes = any(_call_name(node) in _EXECUTORS for node in ast.walk(tree))
    return sorted(raw), reads, executes


_READ = 'Path("lakebase/seed_campaigns.sql").read_text(encoding="utf-8")'


@pytest.mark.parametrize(
    "source",
    [
        # A render that only a comment names.
        f"seed = {_READ}\n# client.execute(render_seed_sql(seed))\nclient.execute(seed)\n",
        # Rendered at one site, applied raw at another.
        f"_SEED = render_seed_sql({_READ})\nraw = {_READ}\n"
        "def apply(c):\n    c.execute(_SEED)\n    c.execute(raw)\n",
        f"client.execute({_READ})\n",
        f"seed = {_READ}\nsql = seed + ';'\nclient.execute(sql)\n",
        f"seed = {_READ}\n_run_transaction([pre, seed], kwargs)\n",
        f"seed = {_READ}\napply_sql(client, seed)\n",
        # The path through a name, and bytes.
        'SEED = ROOT / "lakebase" / "seed_campaigns.sql"\nclient.execute(SEED.read_bytes().decode())\n',
        # A render whose template is not the seed it read.
        f"seed = {_READ}\nclient.execute(render_seed_sql(other, seed_operator_email=seed))\n",
    ],
    ids=["comment", "two_sites", "direct", "concatenated", "transaction", "helper", "path_name", "kwarg"],
)
def test_a_raw_seed_use_is_found(source: str) -> None:
    raw, reads, _executes = raw_seed_uses(source)
    assert raw and reads == source.count(".read_"), source


@pytest.mark.parametrize(
    "source",
    [
        f"client.execute(render_seed_sql({_READ}))\n",
        f"seed = {_READ}\nclient.execute(render_seed_sql(seed, seed_operator_email=op))\n",
        f"_SEED = render_seed_sql({_READ})\ndef apply(c):\n    c.execute(_SEED)\n",
        f"seed_sql = _render_seed_sql(\n    {_READ},\n    seed_operator_email=op,\n)\n_run_transaction([seed_sql], k)\n",
    ],
    ids=["direct", "named", "module_constant", "migrate_job"],
)
def test_a_rendered_seed_is_not_a_raw_use(source: str) -> None:
    assert raw_seed_uses(source) == ([], 1, True)


def test_the_raw_template_reaches_only_render_seed_sql() -> None:
    """A raw apply would write the literal token as an address (CI's real-PostgreSQL contracts).

    Every module that reads the seed hands the template straight to
    render_seed_sql; only a reviewed inspector, which executes nothing, may
    read it raw to look at its text.
    """

    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "tests", "tools", "jobs", "scripts", "backend"],
        cwd=ROOT,
        capture_output=True,
        check=True,
        timeout=120,
    ).stdout.decode("utf-8")
    readers: list[str] = []
    inspectors: set[str] = set()
    for rel in filter(None, listed.split("\0")):
        if not rel.endswith(".py"):
            continue
        text = (ROOT / rel).read_text(encoding="utf-8")
        if "seed_campaigns.sql" not in text:
            continue
        raw, reads, executes = raw_seed_uses(text)
        if rel in _TEMPLATE_INSPECTORS:
            inspectors.add(rel)
            assert not executes, f"{rel} inspects the raw template and also executes SQL"
            continue
        assert not raw, f"{rel}:{raw} uses the raw seed template; pass it to render_seed_sql"
        if reads:
            readers.append(rel)
    assert inspectors == _TEMPLATE_INSPECTORS
    # The migrate job, its plan inputs and the real-PostgreSQL and live contracts.
    assert {
        "jobs/lakebase_migrate.py",
        "jobs/lakebase_migration_inputs.py",
        "tests/integration/test_sales_loop_postgres.py",
        "tests/integration/test_approval_funnel_live.py",
    } <= set(readers)
    assert len(readers) >= 20, readers
