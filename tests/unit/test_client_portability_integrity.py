"""The second client's profile, check and account bootstrap, in process (M3-PR4, plan section 9).

The half of the portability proof that needs no dispatch replay
(tests/unit/test_client_portability_proof.py has the other half; the ci.yml
job ``client-portability-proof`` runs both):

* ``python -m tools.clients check`` accepts acme's environment and prints no
  value; the committed fixture alone never resolves (no secret, no pin);
* ``python -m tools.clients.bootstrap_account --plan`` against the stub
  account API (tests/fixtures/fake_databricks_account.py, which records every
  call) only reads, plans the account principal the Service Principal
  Manager role on the CI identities alone, and its mutations refuse under CI:
  the account admin's work is human-run (attack G15);
* every one-character substitution of the lender record or the accepted
  exposure is refused under the old pin, or changes the profile digest the
  owner's exact profile ack covers, and the exposure plan's own ack;
* the proof's real-checkout check sees an untracked file and an ignored
  build artifact (its positive control);
* the reviewed tables the proof classes by (tests/fixtures/role_ledger.py
  and the committed agentic-only set) agree with the code: no tool reviewed
  as workspace reaches an account API through its imports, the agentic
  slices name exactly the committed tools, every tool a core deploy file
  names is classed (the ones no dry run reaches listed as outside the proof,
  with those that reach an account API pinned), and the ledger fails closed
  for every call shape, planned or executed (review of #330), inline code
  included: it is held to a reviewed allowlist of what it may name, and
  every way rounds 3 and 4 found to start a process or load code by name is
  refused while the deploy's own inline code, written other ways, is not.

The owner hardware-key attestation layer is deferred (M1-PR5): the integrity
here is main's exact-digest pin and acks. Nothing signs or verifies a
signature.
"""

from __future__ import annotations

import importlib
import inspect
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from backend.schemas.lender_identity_record import LenderIdentityRefused, lender_identity_digest
from tests.fixtures import client_portability as cp
from tests.fixtures import portability_checks as checks
from tests.fixtures import role_ledger as roles
from tests.fixtures.bootstrap_harness import FakeSink, plan_and_apply, run_tool
from tests.fixtures.fake_databricks_account import FakeEstate
from tests.fixtures.inline_code_review import INLINE_MODULES, INLINE_REPO_MODULES
from tests.fixtures.role_ledger import READ, Execution, account_admin_needs, sdk_role
from tools.clients import dispatch_acks
from tools.clients import profile as views
from tools.clients.errors import ProfileRefusal
from tools.databricks.estate.accepted_exposure import EXPOSURE_ENV, ExposureRefusal, load_policy


@pytest.fixture(scope="module")
def values() -> dict[str, str]:
    return cp.acme_values()


# --- check, and the committed fixture ----------------------------------------------------


def test_the_fixture_alone_never_resolves() -> None:
    """No secret and no pin is committed: only the sandbox completes acme's environment."""

    values = cp.fixture_values()
    assert cp.PIN_ENV not in values and not set(values) & set(cp.SANDBOX_SECRETS)
    with pytest.raises(ProfileRefusal) as refusal:
        cp.profile(values)
    assert not checks.leaks(refusal.value.lines, cp.identifying_values(cp.acme_values()))


def test_tools_clients_check_accepts_acme_and_prints_no_value(tmp_path: Path, values: dict[str, str]) -> None:
    result = cp.clients_check(tmp_path, values)

    assert result.returncode == 0, result.stderr[-2000:]
    digest = views.digest(cp.profile(values))
    assert result.stdout.strip() == f"client profile ok: client=acme class=customer tier=off digest={digest}"
    assert not checks.leaks([result.stdout, result.stderr], cp.identifying_values(values))


# --- the human-run account bootstrap against the stub account API ------------------------


def _bootstrap_env(tmp_path: Path) -> Path:
    """acme's bootstrap inputs before its first install: no identity ids yet."""

    pre = {name: value for name, value in cp.fixture_values().items() if name != "MIP_IDENTITY_IDS_JSON"}
    path = tmp_path / "pre-install.env"
    path.write_text("".join(f"{name}='{value}'\n" for name, value in pre.items()), encoding="utf-8")
    return path


def _stub_account(values: dict[str, str]) -> FakeEstate:
    estate = FakeEstate()
    catalog, schema = values["MIP_COTALITY_SOURCE_CATALOG"], values["MIP_COTALITY_SOURCE_SCHEMA"]
    estate.catalogs, estate.schemas = {catalog}, {f"{catalog}.{schema}"}
    estate.workspace_id, estate.metastore_id = int(values["MIP_EXPECT_WORKSPACE_ID"]), values["MIP_EXPECT_METASTORE_ID"]
    return estate


def test_the_bootstrap_plan_only_reads_the_stub_account(tmp_path: Path, values: dict[str, str]) -> None:
    estate = _stub_account(values)
    result = run_tool(estate, "--plan", env_file=_bootstrap_env(tmp_path))

    assert result.code == 0, result.err
    calls = [(call.plane, call.api, call.method) for call in estate.calls]
    assert calls and {sdk_role(*call) for call in calls} == {READ}
    assert account_admin_needs(sdk=calls) == []
    # The account principal is planned the Service Principal Manager role on
    # the four CI identities only, never account admin (attack G15).
    steps = result.document()["steps"]
    grants = [step for step in steps if step.get("grantee") == "account_principal"]
    assert sorted(step["target"] for step in grants) == ["admin", "normal", "operator2", "release_probe"]
    assert {(step["op"], step["sp_role"]) for step in grants} == {("grant_sp_role", "manager")}
    others = [step for step in steps if "account_principal" in json.dumps(step) and step not in grants]
    assert [step["op"] for step in others] == ["create_service_principal"]


def test_the_bootstraps_mutations_refuse_in_ci(tmp_path: Path, values: dict[str, str]) -> None:
    """The account admin's work is human-run: under CI it refuses before it changes anything."""

    env_file = _bootstrap_env(tmp_path)
    estate = _stub_account(values)
    digest = run_tool(estate, "--plan", env_file=env_file).digest
    for ci in ({"GITHUB_ACTIONS": "true"}, {"CI": "true"}):
        refused = run_tool(estate, "--apply", "--ack-plan", digest, env_file=env_file, environ=ci)
        assert refused.code != 0, ci
    assert {sdk_role(call.plane, call.api, call.method) for call in estate.calls} == {READ}


def test_an_account_admin_call_on_the_stub_account_is_found(tmp_path: Path, values: dict[str, str]) -> None:
    """Positive control: an applied bootstrap's calls are account-admin needs, as a CI step's would be."""

    estate = _stub_account(values)
    _planned, applied = plan_and_apply(estate, _bootstrap_env(tmp_path), FakeSink())
    needs = account_admin_needs(sdk=[(call.plane, call.api, call.method) for call in estate.calls])

    assert applied.code == 0, applied.err
    assert "sdk (account_admin): account.service_principals.create" in needs
    # An SDK call the map does not know fails closed, like an account-admin need.
    assert account_admin_needs(sdk=[("account", "metastores", "delete")]) == ["sdk (unclassified): account.metastores.delete"]


# --- one-character tampers of the lender record and the accepted exposure ----------------


def _refuses(values: dict[str, str]) -> bool:
    try:
        cp.profile(values)
    except ProfileRefusal:
        return True
    return False


def test_every_one_character_lender_tamper_refuses_under_the_old_pin(values: dict[str, str]) -> None:
    record = values[cp.LENDER_ENV]
    survived = [i for i, tampered in checks.one_char_tampers(record) if not _refuses({**values, cp.LENDER_ENV: tampered})]

    assert len(record) > 100 and survived == []


def test_every_repinned_lender_tamper_needs_a_new_profile_ack(values: dict[str, str]) -> None:
    """Re-pinning a tampered record resolves, but the owner's profile ack no longer matches it."""

    original = dispatch_acks.profile_ack(cp.CLIENT, views.digest(cp.profile(values)))
    repinned = 0
    for _index, tampered in checks.one_char_tampers(values[cp.LENDER_ENV]):
        try:
            pin = lender_identity_digest(tampered)
            resolved = cp.profile({**values, cp.LENDER_ENV: tampered, cp.PIN_ENV: pin})
        except (ProfileRefusal, LenderIdentityRefused):
            continue
        repinned += 1
        assert dispatch_acks.profile_ack(cp.CLIENT, views.digest(resolved)) != original
    assert repinned > 20


def test_every_one_character_exposure_tamper_changes_both_acks(values: dict[str, str]) -> None:
    original_profile = dispatch_acks.profile_ack(cp.CLIENT, views.digest(cp.profile(values)))
    original_exposure = cp.exposure_ack(values)
    resolved_count = 0
    for _index, tampered in checks.one_char_tampers(values[EXPOSURE_ENV]):
        try:
            resolved = cp.profile({**values, EXPOSURE_ENV: tampered})
        except ProfileRefusal:
            continue
        resolved_count += 1
        assert dispatch_acks.profile_ack(cp.CLIENT, views.digest(resolved)) != original_profile
        with pytest.raises(ExposureRefusal):
            load_policy({EXPOSURE_ENV: tampered, "MIP_GENIE_SPACE_NAME": values["MIP_GENIE_SPACE_NAME"]}, ack=original_exposure)
    assert resolved_count > 20


# --- the real-checkout check ---------------------------------------------------------------


def _git(repo: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=repo, check=True, capture_output=True)


def test_the_checkout_state_sees_an_untracked_file_and_an_ignored_build_artifact(tmp_path: Path) -> None:
    """Positive control for the proof's last test: what a run would leave in a checkout changes its state."""

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text("sql/_rendered/\n.env.local\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "-c", "user.name=Proof", "-c", "user.email=proof@example.invalid", "commit", "-qm", "base")
    clean = checks.checkout_state(repo)

    (repo / "sql" / "_rendered").mkdir(parents=True)
    (repo / "sql" / "_rendered" / "a.sql").write_text("SELECT 1;\n", encoding="utf-8")
    rendered = checks.checkout_state(repo)
    (repo / ".env.local").write_text("MIP_CLIENT='client-two'\n", encoding="utf-8")
    profiled = checks.checkout_state(repo)
    (repo / "stray.txt").write_text("x\n", encoding="utf-8")

    assert clean["git status"] == rendered["git status"] == profiled["git status"] == ""
    assert clean != rendered != profiled
    assert checks.checkout_state(repo)["git status"] == "?? stray.txt\n"


# --- the reviewed tables agree with the code ----------------------------------------------


def test_no_tool_reviewed_as_workspace_reaches_an_account_api() -> None:
    """A tool that builds an account client must be reviewed for the account identity, or is a need (review of #330)."""

    tables = (
        roles.WORKSPACE_MODULES,
        roles.ACCOUNT_IDENTITY_MODULES,
        roles.ACCOUNT_PRINCIPAL_MODULES,
        roles.HUMAN_ONLY_MODULES,
        roles.OUTSIDE_PROOF_MODULES,
        checks.AGENTIC_ONLY_MODULES,
    )
    assert sum(map(len, tables)) == len(frozenset().union(*tables)), "each tool has one class"
    assert all(roles.module_files(module) for table in tables for module in table), "no stale entry"

    assert {module: roles.account_api_reach(module) for module in sorted(roles.WORKSPACE_MODULES) if roles.account_api_reach(module)} == {}
    # Not vacuous: the tools reviewed for the account principal do reach one.
    assert [m for m in sorted(roles.ACCOUNT_IDENTITY_MODULES | roles.ACCOUNT_PRINCIPAL_MODULES) if not roles.account_api_reach(m)] == []


def _scripts_copy(tmp_path: Path, plant: tuple[str, str] | None = None) -> Path:
    """deploy.sh and scripts/lib/*.sh copied, with ``(file, line)`` appended to one of them."""

    scripts = tmp_path / "scripts"
    (scripts / "lib").mkdir(parents=True)
    shutil.copy2(checks.REPO / "scripts" / "deploy.sh", scripts / "deploy.sh")
    for path in (checks.REPO / "scripts" / "lib").glob("*.sh"):
        shutil.copy2(path, scripts / "lib" / path.name)
    if plant is not None:
        target = scripts / plant[0]
        target.write_text(target.read_text(encoding="utf-8") + plant[1] + "\n", encoding="utf-8")
    return scripts


def _shapes(tool: str) -> tuple[str, ...]:
    """One call of ``tool`` in each shape a deploy file writes one (review of #330, round 3).

    ``-m`` and a path; a quoted ``-m``; a path behind ``$ROOT_DIR`` or ``./``;
    a ``-m`` continued on the next line; and an array whose body holds ``-m``
    and the name on lines of their own, as deploy_step_agentic_cutover.sh's
    ``AI_GATEWAY_PROOF_ARGS`` does.
    """

    path = tool.replace(".", "/") + ".py"
    return (
        f'"$PYTHON" -m {tool} --x',
        f'"$PYTHON" {path}',
        f'"$PYTHON" -m "{tool}"',
        f'"$PYTHON" "$ROOT_DIR/{path}" --x',
        f'"$PYTHON" ./{path}',
        f'"$PYTHON" -m \\\n  {tool} \\\n  --x',
        f'ARGS=(\n  -m\n  {tool}\n  --x\n)\n"$PYTHON" "${{ARGS[@]}}"',
    )


def test_every_tool_a_core_deploy_file_names_is_classed(tmp_path: Path) -> None:
    """Review of #330: a tool only a live run reaches is never planned or run by the proof's dry runs.

    So every tool a core deploy file names, ``-m`` or by path, must be in a
    reviewed table or listed as outside the proof, and the listed ones that
    reach an account API are pinned: a new one fails here until reviewed.
    """

    named = checks.core_deploy_tools()
    outside = named - roles.REVIEWED_MODULES - roles.HUMAN_ONLY_MODULES

    assert outside == roles.OUTSIDE_PROOF_MODULES, sorted(outside ^ roles.OUTSIDE_PROOF_MODULES)
    assert {m for m in outside if roles.account_api_reach(m)} == roles.OUTSIDE_PROOF_ACCOUNT_REACH
    # Positive controls: a tool a live-only branch runs is named in every
    # shape a deploy file writes a call (review of #330, round 3).
    for index, line in enumerate(_shapes("tools.databricks.account_admin_grant")):
        scripts = _scripts_copy(tmp_path / str(index), ("lib/deploy_step_preflight_gates.sh", f'if [[ "$DRY_RUN" -eq 0 ]]; then\n{line}\nfi'))
        assert checks.core_deploy_tools(scripts) - named == {"tools.databricks.account_admin_grant"}, line


def test_the_role_table_fails_closed_for_every_shape_the_deploy_uses() -> None:
    """Positive controls, in process: each shape a planted dispatch carries, and the ones it cannot."""

    zero = "00000000-0000-0000-0000-000000000000"
    # ``databricks api``: the reviewed workspace path only; any accounts segment is an account call (review of #330).
    assert roles.cli_role(["api", "post", "/api/2.0/sql/statements/", "--json", "{}"]) == roles.WORKSPACE
    assert roles.cli_role(["api", "get", "/api/2.0/sql/statements/01ef-ab"]) == roles.WORKSPACE
    for argv in (
        ["api", "put", f"/api/2.0/preview/accounts/{zero}/access-control/rule-sets", "--json", "@rules.json"],
        ["api", "put", "/api/2.0/preview/accounts/access-control/rule-sets", "--json", "@rules.json"],
        ["api", "get", f"/api/2.0/accounts/{zero}/scim/v2/Groups"],
    ):
        assert roles.cli_role(argv) == roles.ACCOUNT_ADMIN, argv
    for argv in (["api", "get", "/api/2.0/permissions/apps/x"], ["api", "post", "--json", "{}", "/api/2.0/sql/statements/"]):
        assert roles.cli_role(argv) == roles.UNCLASSIFIED, argv
    assert roles.planned_role("databricks api put /api/2.0/preview/accounts/access-control/rule-sets") == roles.ACCOUNT_ADMIN

    py = "/venv/bin/python"
    label = roles.ACCOUNT_IDENTITY_LABEL
    assert roles.planned_role(f"{py} -m tools.databricks.bundle_env validate -t client") == roles.WORKSPACE
    assert roles.planned_role(f"run_with_proof_signing_authority {py} -m tools.databricks.ensure_pipeline_namespace {label}") == roles.ACCOUNT_PRINCIPAL
    for command in (
        f"{py} -m tools.databricks.unreviewed_tool",  # an unreviewed tool
        f"{py} -m tools.databricks.ensure_pipeline_namespace",  # reviewed for the wrapper, run without it
        f"{py} -m tools.databricks.bundle_env summary -t client {label}",  # a workspace tool under the wrapper
        f"{py} -c import_tools",  # inline code
        "bash scripts/unreviewed.sh",  # an unknown executable
        f"databricks apps list {label}",  # the CLI under the wrapper
        "databricks --host https://example.invalid apps list",  # an unknown global flag
        "databricks auth token",  # an unknown command group
    ):
        assert roles.planned_role(command) == roles.UNCLASSIFIED, command
    for command in (
        f"{py} tools/clients/bootstrap_account.py --plan",
        f"{py} -m tools.clients.bootstrap_account --apply",
        "databricks -p account-admin account groups list",
        "databricks --profile=x api get /api/2.0/accounts/1/scim/v2/Groups",
    ):
        assert roles.planned_role(command) == roles.ACCOUNT_ADMIN, command
    assert roles.cli_role(["--debug", "-o", "json", "bundle", "validate"]) == roles.WORKSPACE
    assert roles.module_role("tools.databricks.unreviewed_tool") == roles.UNCLASSIFIED
    # A tool outside the proof is unclassified should a dry run ever reach it.
    assert roles.module_role("tools.databricks.app_core_rollback") == roles.UNCLASSIFIED


def _executed(*argv: str, stdin: str | None = None) -> str:
    return roles.executed_role(Execution(argv, stdin))


def test_every_executed_shape_is_classed_and_fails_closed() -> None:
    """Review of #330: the interpreter's calls in every shape, not only a leading ``-m``."""

    # Tools: by the table, run with -m or by path, behind a reviewed flag.
    for argv in (("-m", "tools.render_sql"), ("-P", "-m", "tools.render_sql"), ("tools/render_sql.py", "--x"), ("-P", "tools/render_sql.py")):
        assert _executed(*argv) == roles.WORKSPACE, argv
    for argv in (
        ("tools/clients/bootstrap_account.py", "--apply"),
        ("-P", "-m", "tools.clients.bootstrap_account"),
        ("-c", "import tools.clients.bootstrap_account"),
        ("-c", "from tools.clients import bootstrap_account"),
    ):
        assert _executed(*argv) == roles.ACCOUNT_ADMIN, argv
    for argv in (
        ("-m", "tools.databricks.account_admin_grant"),  # an unreviewed tool
        ("-P", "-m", "tools.databricks.app_core_rollback"),  # outside the proof
        ("tools/databricks/provision_agentic_resources.py", "--help"),  # agentic, by path
        ("scripts/other.py",),  # a script that is no tool
        ("-W", "ignore", "-m", "tools.render_sql"),  # a flag the map does not know
        ("-Im", "tools.render_sql"),
        ("-c",),
        ("-",),  # a script on stdin that was not kept
    ):
        assert _executed(*argv) == roles.UNCLASSIFIED, argv
    # Inline code, -c or a heredoc: workspace only when nothing it reaches names an account API.
    clean = "import json, sys\nfrom backend.schemas.lender_identity_record import LEGACY_ENV_NAMES\nprint(LEGACY_ENV_NAMES)\n"
    assert _executed("-c", clean) == _executed("-", "x", stdin=clean) == _executed(stdin=clean) == roles.WORKSPACE
    for code in (
        "from databricks.sdk import AccountClient",  # an account API by name
        "w.api_client.do('PUT', '/api/2.0/preview/accounts/access-control/rule-sets')",
        "import tools.databricks.uc_owner_policy",  # a repository module that reaches one
        "from tools.databricks import agent_proxy_access",
        "import tools.databricks.account_admin_grant",  # a repository module that does not exist
        "import importlib; importlib.import_module('tools.x')",  # loads code by name
        "__import__('tools.x')",
        "import subprocess; subprocess.run(['python', '-m', 'tools.x'])",  # starts a process
        "import os; os.system('python -m tools.x')",
        "from os import execv",
        "from .x import y",
        "def broken(:",
    ):
        assert _executed("-c", code) == _executed("-", stdin=code) == roles.UNCLASSIFIED, code
    assert roles.inline_imports("from tools.databricks import agent_proxy_access") >= {"tools.databricks.agent_proxy_access"}


# Review of #330, round 3: inline code that starts a process or loads code by
# name, which round 2's literal-name denylist classed workspace (the first
# six), and its relatives: each refused by tests/fixtures/inline_code_review.py.
INLINE_BYPASSES = (
    "import os as _os, sys\n_os.system(sys.executable + ' -m tools.clients.bootstrap_account --help')",
    "import builtins; builtins.__import__('tools.clients.bootstrap_account')",
    "import pkgutil; pkgutil.resolve_name('tools.clients.bootstrap_account:main')",
    "import sys; sys.path.insert(0, 'tools/clients'); import bootstrap_account",
    "import asyncio\nasyncio.run(asyncio.create_subprocess_exec('python', '-m', 'tools.x'))",
    "import os\ngetattr(os, 'system')('python -m tools.x')",
    # Every other import form and alias of os, and a module reached through another.
    "from os import system as run; run('python -m tools.x')",
    "from os import *\nsystem('python -m tools.x')",
    "import os\nalias = os\nalias.system('python -m tools.x')",
    "import os.path as p\np.os.system('python -m tools.x')",
    "import pathlib\npathlib.os.system('python -m tools.x')",
    "from backend.schemas.lender_identity_record import hashlib",  # a name its imports bind: a module re-exported
    "import sys\nsys.modules['os'].system('python -m tools.x')",
    # The routes from an object back to a namespace or the builtins.
    "import json\njson.loads.__globals__['os'].system('x')",
    "frames = (x for x in [1])\nframes.gi_frame.f_globals['__builtins__']['__import__']('os')",
    "import os\nvars(os)['system']('x')",
    "eval('1')",
    "asyncio.create_subprocess_exec('x')",  # a name the code never binds
    # Reviewed modules' unreviewed surfaces: yaml's object constructor, a submodule.
    "import yaml\nyaml.unsafe_load('x: 1')",
    "import json\njson.decoder.scanstring('x', 0)",
    "import concurrent.futures\nconcurrent.futures.ProcessPoolExecutor()",
    # Round 4. A reviewed module's public name that is a getattr by string:
    # pathlib binds operator.attrgetter, which "any public attribute" let through.
    "import pathlib, json\npathlib.attrgetter('__globals__')(json.loads)['__builtins__']['__import__']('os').system('x')",
    "from pathlib import attrgetter\nimport json\nattrgetter('__globals__')(json.loads)['__builtins__']['__import__']('os')",
    # A class pattern reads its keywords as attributes, with no attribute node.
    "import json\nmatch json.loads:\n    case object(__globals__={'__builtins__': {'__import__': imp}}):\n        imp('os').system('x')",
    "g = (x for x in [1])\nmatch g:\n    case object(gi_frame=object(f_builtins={'__import__': imp})):\n        imp('os').system('x')",
    # A private repository helper that runs a caller's argv, from a module no
    # inline code is reviewed to import; and a private name of a reviewed one.
    "from tools.deps_report.advisories import _json_command\n_json_command(['sh', '-c', 'x'], 'x')",
    "from pathlib import Path\nfrom tools.databricks.app_runtime_closure import _git\n_git(Path('.'), '-c', 'alias.zz=!sh', 'zz')",
    "from backend.schemas.lender_identity_record import _unique_object\nprint(_unique_object([]))",
    # A tool the table does not review, run in process: by ``-m`` it is unclassified.
    "from tools.databricks.provision_agentic_resources import main\nmain(['--plan-only'])",
)
# Not rigid: what the deploy's own inline code does, written the other ways a
# heredoc would write it (round 3 asked that legitimate code stay workspace).
INLINE_ACCEPTED = (
    "import os.path\nprint(os.path.join('a', 'b'), os.environ.get('X'), os.getenv('Y'))",
    "from os import environ, path\nprint(environ.get('X'), path.exists('.'))",
    "import json, re\nprint(json.dumps({'a': 1}, indent=2), re.IGNORECASE, re.compile('x').pattern)",
    "class Row:\n    width = 1\n\n\ndef f(*args, **kwargs):\n    return [a for a in args if a]\n\n\nprint(f(Row.width), {k: v for k, v in {}.items()})",
    "try:\n    import yaml\nexcept ModuleNotFoundError as error:\n    raise SystemExit(str(error)) from error\nprint(yaml.safe_load('a: 1'))",
    "import sys\nsys.stdout.write(sys.argv[0])\nsys.exit(0)",
    "def main():\n    return 0\n\n\nif __name__ == '__main__':\n    raise SystemExit(main())",
    "from pathlib import Path\nprint(Path('.env.local').exists() and Path('.env.local').read_text(encoding='utf-8'))",
    "from backend.schemas.lender_identity_record import as_configured\nprint(as_configured({}))",
    # Round 4's explicit sets keep the reviewed siblings of what the dry runs use.
    "import json, re, sys\nbody = json.loads(sys.stdin.read())\nprint(re.sub(r'\\s+', ' ', body.get('x', '')), re.fullmatch('a', 'a'))",
    "import os.path\nfrom pathlib import PurePath\nprint(os.path.basename(os.path.abspath('.')), PurePath('a').suffix)",
    "from backend.schemas import lender_identity_record\nimport backend.services.ai_gateway_proof_attestation as gateway\n"
    "print(lender_identity_record.LEGACY_ENV_NAMES, gateway.derive_gateway_proof_verify_key)",
)


def _exposed_modules(value: object) -> list[str]:
    """How a reviewed value exposes a module: it is one, or a class with a public attribute that is one (3.13's ``Path.parser``)."""

    if inspect.ismodule(value):
        return ["itself"]
    if not isinstance(value, type):
        return []
    return [name for name in dir(value) if not name.startswith("_") and inspect.ismodule(getattr(value, name, None))]


def test_inline_code_is_held_to_the_reviewed_allowlist() -> None:
    """Review of #330, rounds 3 and 4: an allowlist of what inline code may name, not a list of literal names it may not."""

    for code in INLINE_BYPASSES:
        assert _executed("-c", code) == _executed("-", stdin=code) == roles.UNCLASSIFIED, code
    for code in INLINE_ACCEPTED:
        assert _executed("-c", code) == _executed("-", stdin=code) == roles.WORKSPACE, code
    # No stale entry: each reviewed module exists, and each reviewed attribute
    # is one it has. A value reached through an attribute is held only to the
    # private and frame rule, so none may be a module or a class exposing one.
    for module, attributes in INLINE_MODULES.items():
        imported = importlib.import_module(module)
        for name in attributes:
            assert hasattr(imported, name), f"{module}.{name}"
            assert not _exposed_modules(getattr(imported, name)), f"{module}.{name}"
    repo = roles.repository()
    for module in INLINE_REPO_MODULES:
        assert roles.module_files(module) and not roles.account_api_reach(module), module
        imported = importlib.import_module(module)
        public = [name for name in dir(imported) if not name.startswith("_") and repo.defines(module, name)]
        assert public and not {name: found for name in public if (found := _exposed_modules(getattr(imported, name)))}, module
    # The check's positive control: a class whose public attribute is a module, as Python 3.13's Path.parser is.
    assert _exposed_modules(type("Shaped", (), {"parser": importlib})) == ["parser"]


def test_the_agentic_slices_name_exactly_the_committed_tools(tmp_path: Path) -> None:
    """The set the proof refuses is committed; the tree is held to it, so a core call into one fails both ways."""

    assert checks.agentic_only_modules() == checks.AGENTIC_ONLY_MODULES
    assert not roles.REVIEWED_MODULES & checks.AGENTIC_ONLY_MODULES
    # Positive controls (review of #330, rounds 2 and 3): a new agentic tool,
    # in any shape a deploy file writes a call, changes the set read from the tree.
    for index, line in enumerate(_shapes("tools.databricks.new_agentic_tool")):
        scripts = _scripts_copy(tmp_path / str(index), ("lib/deploy_step_agentic_provisioning.sh", line))
        assert checks.agentic_only_modules(scripts) - checks.AGENTIC_ONLY_MODULES == {"tools.databricks.new_agentic_tool"}, line
