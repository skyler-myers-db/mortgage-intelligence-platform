"""A second client onboards with zero code edits (M3-PR4, plan section 9).

``acme`` (tests/fixtures/clients/acme.env, synthetic) is a customer install of
another lender: prod App env, the agentic tier off, the weekly refresh off,
the Cotality share mounted as ``cotality_prod.mortgage`` with unprefixed
tables. The harness (tests/fixtures/client_portability.py) gives it a GitHub
Environment, the secrets and the lender record's exact-digest pin existing
only inside the sandbox, and runs, offline, against a stub CLI and a stub
account API that record the authority each call needs:

* ``python -m tools.clients check`` and ``python -m
  tools.clients.bootstrap_account --plan`` (the human-run account bootstrap)
  against the stub account API, in tests/unit/test_client_portability_integrity.py;
* deploy-dev.yml dispatches of ``--phase plan`` and an acked ``--phase
  release``, replayed step by step in a sandbox clone with ``--dry-run``
  (tests/fixtures/dispatch_replay.py), the profile removed from the clone's
  tree; and the ``bundle_env validate -t client`` they plan, executed in that
  clone against the stub CLI.

It asserts what plan section 9 lists: the real checkout stays clean; every
bundle call is ``-t client`` with acme's variables and root leaf; the
rendered SQL holds no canonical token; the pipeline configuration and the App
payload are acme's; no agentic step; the refresh PAUSED; no CI step needs
account-admin authority; no acme identifying value in the public log or the
step summary; the analytics battery stays allowed with acme's names; a
one-character tamper of the lender or exposure JSON refuses and no other ack
substitutes. A client-#1-shaped profile (placeholders only) renders SQL byte
for byte as the default install does.

The owner hardware-key attestation layer is deferred (M1-PR5): the integrity
the tamper tests exercise is main's, the lender record's exact-digest pin and
the dispatch's exact digest acks (tools/clients/dispatch_acks.py). Nothing
here signs anything or verifies a signature.

Positive controls (a dispatch with planted defects, and the checks applied to
inputs that must fail them) prove each check can fail: account-admin calls in
the shapes the deploy really uses, planned and executed (a Python tool by
``-m``, behind an interpreter flag or by path, inline ``-c`` code and a
heredoc, a heredoc starting the human bootstrap through an aliased ``os``,
the account identity's wrapper, a CLI call behind a global flag, an
account rule-set update through ``databricks api``, the human bootstrap by
path), agentic tools called from a core file (planned, executed by path and
imported inline), bundle calls to another target (planned by path, executed
through the CLI and through the wrapper), a log and a summary leak, a
client-#1 literal and an identifying value in the tree. The ci.yml job
``client-portability-proof`` runs this module and the integrity one.

What the proof covers is what the plan and release dry runs plan or run; a
tool only a live run or another phase reaches is outside it, and the
integrity module keeps those listed (role_ledger.OUTSIDE_PROOF_MODULES).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import yaml

from backend.config import source_names
from backend.config.source_names import TABLE_STEMS, CotalitySource
from backend.schemas.lender_identity_record import lender_identity_digest
from tests.fixtures import client_portability as cp
from tests.fixtures import portability_checks as checks
from tests.fixtures.cotality_source_probe import run_fresh
from tests.fixtures.dispatch_replay import REPOSITORY, TOKEN
from tests.fixtures.lakeflow_pipeline_stub import exec_pipeline
from tests.fixtures.role_ledger import (
    ACCOUNT_PRINCIPAL,
    REVIEWED_MODULES,
    WORKSPACE,
    Execution,
    account_admin_needs,
    executed_role,
    inline_imports,
    planned_role,
)
from tests.fixtures.role_recording_cli import tree_sha256
from tests.unit.test_cotality_source_prompt_guards import BARE_MORTGAGE, REVIEWED_OBJECTIVES
from tests.unit.test_genie_guardrail_battery import _LEGITIMATE_QUESTIONS, _MUST_REFUSE
from tests.unit.test_genie_no_refusal_battery import VALID_ANALYTICAL_QUESTIONS
from tools import render_sql
from tools.clients import dispatch_acks
from tools.clients import profile as views
from tools.clients.dispatch_preflight import decide
from tools.databricks.app_deploy_payload import CORE_TIER_DROPPED_NAMES
from tools.databricks.estate.accepted_exposure import EXPOSURE_ENV, ExposureRefusal, load_policy

REPO = Path(__file__).resolve().parents[2]
PHASES = ("plan", "release")
PREFLIGHT = "scripts/lib/deploy_step_preflight_gates.sh"
PLANT_ANCHOR = 'export BUNDLE_VAR_tenant_id="$MIP_TENANT_ID"\n'
CORE_SKIP = "core tier: skip the agentic provisioning, cutover and Agent Evaluation"
# A bundle call: the env-aware wrapper in any form (``-m`` or by path), or a
# ``bundle`` token, the CLI's command group behind any global flag. Word-bound
# so the ``.bundle/`` root and ``--bundle-summary`` flags are no call.
BUNDLE_CALL = re.compile(r"(?<![\w-])bundle_env(?:\.py)?(?!\w)|(?<![\w./-])bundle(?![\w./-])")
TARGET = re.compile(r"(?:^|\s)(?:-t|--target)(?:\s+|=)(\S+)")


# The planted account-admin needs, each in a shape the deploy really uses
# (review of #330). Run: a CLI call behind a global flag, an account rule-set
# update through ``databricks api`` on the workspace proxy path, the human
# bootstrap by path, an unreviewed tool behind an interpreter flag, inline
# code and a heredoc importing what reaches an account API, a heredoc that
# runs the human bootstrap in a child process through an aliased ``os``
# (round 3: the child is the real interpreter, which no recorder sees), an
# agentic tool by path. Planned: a CLI call behind a global flag, the rule-set update, an
# unreviewed Python tool, an unreviewed tool under the account identity's
# wrapper, a reviewed workspace tool under it and the human bootstrap by path.
PLANTED_NEEDS = (
    "cli (account_admin): databricks -p account-admin account service-principals list",
    "cli (account_admin): databricks api put /api/2.0/preview/accounts/00000000-0000-0000-0000-000000000000"
    "/access-control/rule-sets",
    "executed (account_admin): tools/clients/bootstrap_account.py --help",
    "executed (unclassified): -P -m tools.databricks.account_admin_grant --help",
    "executed (account_admin): -c raise SystemExit(0); import tools.clients.bootstrap_account planted-inline",
    "executed (unclassified): - planted-heredoc (script on stdin)",
    "executed (unclassified): - planted-aliased-os (script on stdin)",
    "executed (unclassified): tools/databricks/read_agent_tool_probe_expectation.py --help",
    "planned (account_admin): databricks api put /api/2.0/preview/accounts/access-control/rule-sets",
    "planned (account_admin): databricks --profile account-admin account groups list",
    "planned (unclassified): $PY -m tools.databricks.account_admin_grant --grant account_admin --principal deployer",
    "planned (unclassified): $PY -m tools.databricks.account_sp_create --create-service-principal planted"
    " (bounded account-SCIM identity)",
    "planned (unclassified): $PY -m tools.databricks.bundle_env summary -t client (bounded account-SCIM identity)",
    "planned (account_admin): $PY tools/clients/bootstrap_account.py --apply",
    "planned (unclassified): $PY -m tools.databricks.provision_agentic_resources --plan-only",
)
PLANTED_AGENTIC = "tools.databricks.provision_agentic_resources"
EXECUTED_AGENTIC = "tools.databricks.read_agent_tool_probe_expectation"
IMPORTED_AGENTIC = "tools.databricks.agent_proxy_access"


def _planted(values: dict[str, str]) -> tuple[cp.Plant, ...]:
    """The positive controls: account-admin needs, agentic tools, stray bundle targets, a literal, two leaks, a tree value."""

    shell = (
        "# The portability proof's positive controls, planted in its sandbox clone only.\n"
        "databricks -p account-admin account service-principals list >/dev/null 2>&1 || true\n"
        "run databricks --profile account-admin account groups list\n"
        'run "$PYTHON" -m tools.databricks.account_admin_grant --grant account_admin --principal deployer\n'
        'run_with_account_identity "$PYTHON" -m tools.databricks.account_sp_create --create-service-principal planted\n'
        'run_with_account_identity "$PYTHON" -m tools.databricks.bundle_env summary -t client\n'
        'run "$PYTHON" tools/clients/bootstrap_account.py --apply\n'
        f'run "$PYTHON" -m {PLANTED_AGENTIC} --plan-only\n'
        'run "$PYTHON" tools/databricks/bundle_env.py validate -t dev\n'
        "run databricks api put /api/2.0/preview/accounts/access-control/rule-sets\n"
        # Executed, never planned: the interpreter and the CLI really run these.
        # The inline code exits before its import; the ledger reads the code.
        "databricks api put /api/2.0/preview/accounts/00000000-0000-0000-0000-000000000000/access-control/rule-sets"
        " >/dev/null 2>&1 || true\n"
        "databricks bundle validate -t dev >/dev/null 2>&1 || true\n"
        '"$PYTHON" tools/clients/bootstrap_account.py --help >/dev/null 2>&1 || true\n'
        '"$PYTHON" -P -m tools.databricks.account_admin_grant --help >/dev/null 2>&1 || true\n'
        '"$PYTHON" -m tools.databricks.bundle_env --help -t dev >/dev/null 2>&1 || true\n'
        "\"$PYTHON\" -c 'raise SystemExit(0); import tools.clients.bootstrap_account' planted-inline || true\n"
        f"\"$PYTHON\" - planted-heredoc <<'PYEOF' || true\nraise SystemExit(0)\nimport {IMPORTED_AGENTIC}\nPYEOF\n"
        # Round 3's probe: it really runs the bootstrap, in a child no recorder sees.
        "\"$PYTHON\" - planted-aliased-os <<'PYEOF' || true\nimport os as _os, sys\n_os.system(sys.executable"
        " + ' -m tools.clients.bootstrap_account --help > \"$PROOF_PAYLOAD_DIR/aliased-os.out\" 2>&1')\nPYEOF\n"
        f'MIP_PLANTED=1 "$PYTHON" {EXECUTED_AGENTIC.replace(".", "/")}.py --help >/dev/null 2>&1 || true\n'
        'run "$PYTHON" -m tools.databricks.active_deployment_pin digest --app-name mip-app\n'
        "echo \"  lender: $(printf '%s' \"$MIP_LENDER_NAME\" | tr '[:lower:]' '[:upper:]')\"\n"
        "printf 'lender %s\\n' \"$MIP_LENDER_NAME\" >> \"${GITHUB_STEP_SUMMARY:-/dev/null}\"\n"
    )
    return (
        cp.Plant(PREFLIGHT, PLANT_ANCHOR, shell),
        cp.Plant("tools/clients/__init__.py", None, f"# {values['DATABRICKS_HOST']}\n"),
    )


# --- the runs ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def baseline() -> dict[str, str | None]:
    return checks.checkout_state(REPO)


@pytest.fixture(scope="module")
def values() -> dict[str, str]:
    return cp.acme_values()


@pytest.fixture(scope="module")
def runs(
    tmp_path_factory: pytest.TempPathFactory, values: dict[str, str], baseline: dict[str, str | None]
) -> Iterator[dict[str, Future[cp.Dispatch]]]:
    """The three dispatches, side by side: plan, release and the planted plan."""

    with ThreadPoolExecutor(max_workers=3) as pool:
        yield {
            "plan": pool.submit(cp.dispatch, tmp_path_factory.mktemp("plan"), values, "plan"),
            "release": pool.submit(cp.dispatch, tmp_path_factory.mktemp("release"), values, "release"),
            "planted": pool.submit(cp.dispatch, tmp_path_factory.mktemp("planted"), values, "plan", _planted(values)),
        }


@pytest.fixture(scope="module")
def dispatches(runs: dict[str, Future[cp.Dispatch]]) -> dict[str, cp.Dispatch]:
    return {phase: runs[phase].result() for phase in PHASES}


@pytest.fixture(scope="module")
def planted(runs: dict[str, Future[cp.Dispatch]]) -> cp.Dispatch:
    return runs["planted"].result()


@pytest.fixture(scope="module")
def validated(dispatches: dict[str, cp.Dispatch], values: dict[str, str]) -> dict[str, cp.Validated]:
    """acme's validate, then the client-#1-shaped one, in the release dispatch's clone."""

    release = dispatches["release"]
    return {"acme": cp.validate(release, values, "acme"), "shaped": cp.validate(release, None, "shaped")}


@pytest.fixture(scope="module")
def acme_profile(values: dict[str, str]) -> Any:
    return cp.profile(values)


@pytest.fixture(scope="module")
def source(acme_profile: Any) -> CotalitySource:
    return source_names.from_env(views.deploy_controls(acme_profile))


@pytest.fixture(scope="module")
def payload(dispatches: dict[str, cp.Dispatch]) -> dict[str, Any]:
    payloads = dispatches["release"].record.payloads()
    assert len(payloads) == 1, len(payloads)
    return payloads[0]


@pytest.mark.parametrize("phase", PHASES)
def test_every_step_of_the_dispatch_succeeds(dispatches: dict[str, cp.Dispatch], phase: str) -> None:
    done = dispatches[phase]

    assert done.failed_steps() == {}, done.stderr[-3000:]
    assert cp.CLIENT_PHASE in done.deploy.results
    assert f"--phase {phase}" in done.summary and "- verdict: passed" in done.summary


# --- the bundle: target, variables, root, SQL and pipeline -------------------------------


@pytest.mark.parametrize("phase", PHASES)
def test_every_planned_bundle_call_addresses_the_client_target(
    dispatches: dict[str, cp.Dispatch], acme_profile: Any, phase: str
) -> None:
    planned = dispatches[phase].planned
    bundle = [command for command in planned if BUNDLE_CALL.search(command)]

    assert bundle, "the dry run plans bundle calls"
    for command in bundle:
        assert TARGET.findall(command) == ["client"], command
    deployer = acme_profile.identity_ids.deployer
    for command in (c for c in planned if "--expected-target" in c):
        assert "--expected-target client" in command and f"--expected-root-leaf {cp.CLIENT}" in command, command
        assert f"--expected-principal {deployer}" in command, command
    if phase == "release":
        assert any("--expected-root-leaf" in c for c in planned)
        assert any(f"deployment bind mip_app {acme_profile.app_name} -t client" in c for c in planned)


def _stray_targets(planned: list[str], cli: list[list[str]], executed: list[Execution]) -> list[str]:
    """Every bundle call that addresses another target than ``client``: planned, through the CLI or the wrapper."""

    stray = [command for command in planned if BUNDLE_CALL.search(command) and TARGET.findall(command) != ["client"]]
    stray += [f"cli: databricks {' '.join(argv)}" for argv in cli if "bundle" in argv and TARGET.findall(" ".join(argv)) != ["client"]]
    stray += [
        f"executed: {call.shown()}"
        for call in executed
        if call.tool() == "tools.databricks.bundle_env" and TARGET.findall(" ".join(call.argv)) != ["client"]
    ]
    return stray


def test_every_bundle_call_the_dispatches_run_addresses_the_client_target(
    dispatches: dict[str, cp.Dispatch], validated: dict[str, cp.Validated]
) -> None:
    """Review of #330: the calls a dry run really makes, not only the ones it plans."""

    cli = [call["argv"] for done in dispatches.values() for call in done.record.cli_calls()]
    cli += [call["argv"] for result in validated.values() for call in result.calls]
    executed = [call for done in dispatches.values() for call in done.record.executions()]

    assert _stray_targets([], cli, executed) == []
    # Not vacuous: the validate's call is a bundle call through the CLI.
    assert any("bundle" in argv for argv in cli)


def test_the_validate_hands_the_cli_acmes_variables_and_root_leaf(
    validated: dict[str, cp.Validated], acme_profile: Any
) -> None:
    calls = validated["acme"].calls
    assert [call["argv"] for call in calls] == [["bundle", "validate", "-t", "client"]]
    sent = calls[0]["bundle_vars"]
    # Every bundle variable the profile view names, except the refresh pause status,
    # which scripts/deploy.sh exports itself (test_the_weekly_refresh_is_paused).
    expected = {
        f"BUNDLE_VAR_{name}": value
        for name, value in views.bundle_vars(acme_profile).items()
        if name != "scheduled_refresh_pause_status"
    }
    assert {name: sent.get(name) for name in expected} == expected
    root = yaml.safe_load((REPO / "databricks.yml").read_text(encoding="utf-8"))["targets"]["client"]["workspace"]["root_path"]
    resolved = re.sub(r"\$\{var\.([a-z_]+)\}", lambda m: sent[f"BUNDLE_VAR_{m.group(1)}"], root)
    assert resolved.endswith(f"/{cp.CLIENT}") and f"/Users/{acme_profile.identity_ids.deployer.lower()}/" in resolved


def _resolved_variables(sent: dict[str, str]) -> dict[str, str]:
    """Every variable databricks.yml declares, as the CLI resolves it: the value sent, else the client target's, else the default."""

    bundle = yaml.safe_load((REPO / "databricks.yml").read_text(encoding="utf-8"))
    overrides = bundle["targets"]["client"].get("variables") or {}
    resolved = {}
    for name, declared in bundle["variables"].items():
        default = declared.get("default") if isinstance(declared, dict) else declared
        value = sent.get(f"BUNDLE_VAR_{name}", overrides.get(name, default))
        resolved[name] = value if isinstance(value, str) else json.dumps(value)
    return resolved


def test_every_declared_variable_resolves_to_acmes_value(validated: dict[str, cp.Validated], values: dict[str, str]) -> None:
    """Review of #330: a variable the view stops sending falls back to databricks.yml's default, client #1's.

    The values expected are read from acme.env itself, never from the
    profile view bundle_env sends them from.
    """

    resolved = _resolved_variables(validated["acme"].calls[0]["bundle_vars"])
    record, ids = json.loads(values[cp.LENDER_ENV]), json.loads(values["MIP_IDENTITY_IDS_JSON"])
    expected = {
        "client_key": values["MIP_CLIENT"],
        "uc_catalog": values["MIP_DEFAULT_CATALOG"],
        "app_name": values["MIP_APP_NAME"],
        "lakebase_instance_name": values["MIP_LAKEBASE_INSTANCE"],
        "lakebase_catalog_name": values["MIP_LAKEBASE_SYNC_CATALOG"],
        "lakebase_database_name": values["LAKEBASE_DATABASE"],
        "lakebase_readable_secondaries": values["MIP_LAKEBASE_READABLE_SECONDARIES"],
        "runtime_secret_scope": values["MIP_RUNTIME_SECRET_SCOPE"],
        "dashboard_parent_path": values["MIP_DASHBOARD_PARENT_PATH"],
        "cotality_source_catalog": values["MIP_COTALITY_SOURCE_CATALOG"],
        "cotality_source_schema": values["MIP_COTALITY_SOURCE_SCHEMA"],
        "cotality_source_table_prefix": values["MIP_COTALITY_SOURCE_TABLE_PREFIX"],
        "deployer_principal": ids["deployer"].lower(),
        "jobs_principal": ids["jobs"].lower(),
        "lender_name": record["legal_name"],
        "lender_nmls_id": record["nmls_id"],
        "tenant_id": record["tenant_id"],
        "lender_identity_sha256": values[cp.PIN_ENV],
    }

    assert {name: resolved[name] for name in expected} == expected
    lines = [f"{name}={value}" for name, value in resolved.items()]
    assert checks.literal_hits(lines, checks.install_literals(values)) == []
    # Not vacuous: databricks.yml's own defaults are client #1's literals.
    defaults = [f"{name}={value}" for name, value in _resolved_variables({}).items()]
    hit = {line.split("=", 1)[0] for _field, line in checks.literal_hits(defaults, checks.install_literals(values))}
    assert hit >= {"app_name", "lakebase_instance_name", "lakebase_database_name", "runtime_secret_scope",
                   "lender_name", "cotality_source_catalog"}, hit


def _sql_files(tree: Path) -> dict[str, str]:
    return {path.relative_to(tree).as_posix(): path.read_text(encoding="utf-8") for path in sorted(tree.rglob("*.sql"))}


def _canonical_tokens(tree: Path, source: CotalitySource) -> list[tuple[str, str, int]]:
    return [
        (rel, name, line)
        for rel, text in _sql_files(tree).items()
        for name, line in render_sql.canonical_source_tokens(text, source)
    ]


def test_the_rendered_sql_holds_no_canonical_token_and_names_acmes_share(
    validated: dict[str, cp.Validated], source: CotalitySource
) -> None:
    acme = validated["acme"]
    assert tree_sha256(acme.rendered) == acme.calls[0]["rendered_sha256"], "the tree the CLI saw"
    rendered = "\n".join(_sql_files(acme.rendered).values())

    assert _canonical_tokens(acme.rendered, source) == []
    assert source.table_prefix == "" and source.catalog != source_names.CANONICAL.catalog
    assert [stem for stem in TABLE_STEMS if source.table(stem) not in rendered] == []


def test_the_canonical_render_fails_the_token_scan(validated: dict[str, cp.Validated], source: CotalitySource) -> None:
    """Positive control: the client-#1-shaped tree, read as acme's, is full of canonical tokens."""

    assert _canonical_tokens(validated["shaped"].rendered, source) != []


def test_the_client_one_shaped_render_is_byte_identical(validated: dict[str, cp.Validated], tmp_path: Path) -> None:
    """Client #1's shape renders the default install's SQL: the sources, with only the demo feeds switched on.

    The reference is read from sql/ (portability_checks.default_install_render),
    not rendered by the code under test (review of #330): catalog mip, the
    canonical share and no pinned lender change no byte of a source.
    """

    reference = checks.default_install_render(tmp_path / "default-render")

    assert validated["shaped"].calls[0]["rendered_sha256"] == tree_sha256(reference)
    assert validated["shaped"].calls[0]["bundle_vars"]["BUNDLE_VAR_client_key"] != cp.CLIENT
    assert any(checks.DEMO_SWITCH in path.read_text(encoding="utf-8") for path in (REPO / "sql").rglob("*.sql"))


def test_the_pipeline_reads_acmes_share(validated: dict[str, cp.Validated], source: CotalitySource) -> None:
    """The pipeline configuration databricks.yml builds from the variables the CLI was handed."""

    sent = validated["acme"].calls[0]["bundle_vars"]
    bundle = yaml.safe_load((REPO / "databricks.yml").read_text(encoding="utf-8"))
    configured = bundle["resources"]["pipelines"]["mip_feature_pipeline"]["configuration"]
    conf = {key: re.sub(r"\$\{var\.([a-z_]+)\}", lambda m: sent[f"BUNDLE_VAR_{m.group(1)}"], value) for key, value in configured.items()}
    run = exec_pipeline(conf)

    shares = {name: value for name, value in run.namespace.items() if name.startswith("_SHARE_")}
    assert sorted(shares.values()) == sorted(source.table(stem) for stem in TABLE_STEMS)


# --- the App payload and the settings it boots with --------------------------------------


def test_the_release_builds_acmes_core_payload(payload: dict[str, Any], acme_profile: Any) -> None:
    env = checks.literal_env(payload)
    names = [str(item["name"]) for item in payload["env_vars"]]

    assert len(names) == len(set(names))
    assert payload["mode"] == "SNAPSHOT" and payload["source_code_path"].rsplit("/", 1)[-1].endswith("-dry-run")
    assert {name: env.get(name) for name in ("MIP_AGENT_ORCHESTRATOR", "MIP_AI_GATEWAY")} == {
        "MIP_AGENT_ORCHESTRATOR": "0",
        "MIP_AI_GATEWAY": "0",
    }
    assert not set(names) & CORE_TIER_DROPPED_NAMES
    owned = views.app_payload_env(acme_profile)
    for name in ("APP_ENV", "MIP_DEFAULT_CATALOG", "LAKEBASE_INSTANCE_NAME", "MIP_LAKEBASE_INSTANCE", "LAKEBASE_DATABASE",
                 "MIP_LAKEBASE_SYNC_CATALOG", "MIP_LAKEBASE_SYNC_SCHEMA", "MIP_LENDER_IDENTITY_JSON", "MIP_LENDER_IDENTITY_SHA256"):
        assert env.get(name) == owned[name], name


def test_the_app_booted_with_the_payload_reads_acmes_settings(
    payload: dict[str, Any], acme_profile: Any, source: CotalitySource, values: dict[str, str], tmp_path: Path
) -> None:
    settings = checks.app_settings(checks.literal_env(payload), tmp_path)
    record = json.loads(values[cp.LENDER_ENV])

    assert settings == {
        "app_env": "prod",
        "catalog": acme_profile.default_catalog,
        "lakebase_instance": acme_profile.lakebase_instance,
        "lakebase_database": acme_profile.lakebase_database,
        "sync_catalog": acme_profile.lakebase_sync_catalog,
        "sync_schema": acme_profile.lakebase_sync_schema,
        "source": [source.catalog, source.schema, source.table_prefix_setting],
        "lender": settings["lender"],
        "agent_orchestrator": False,
        "ai_gateway": False,
        "presenter_mode": False,
    }
    pinned = checks.literal_env(payload)
    assert settings["lender"][3] == pinned["MIP_LENDER_IDENTITY_SHA256"] == acme_profile.lender_identity_sha256
    assert settings["lender"][:3] == [pinned["MIP_LENDER_NAME"], pinned["MIP_LENDER_NMLS_ID"], pinned["MIP_TENANT_ID"]]
    assert [record["legal_name"], record["nmls_id"], record["tenant_id"]] == settings["lender"][:3]


def test_without_the_database_name_the_app_opens_the_default_database(
    payload: dict[str, Any], acme_profile: Any, tmp_path: Path
) -> None:
    """Positive control: the payload before M3-PR4 left the App on the default database."""

    env = {name: value for name, value in checks.literal_env(payload).items() if name != "LAKEBASE_DATABASE"}

    assert checks.app_settings(env, tmp_path)["lakebase_database"] != acme_profile.lakebase_database


def test_the_analytics_battery_stays_allowed_with_acmes_names(
    payload: dict[str, Any], source: CotalitySource, tmp_path: Path
) -> None:
    qualified = [f"How many borrowers are in {form}?" for form in source_names.qualified_forms(source)]
    allowed = (*_LEGITIMATE_QUESTIONS, *VALID_ANALYTICAL_QUESTIONS)
    questions = sorted({*allowed, *REVIEWED_OBJECTIVES, *BARE_MORTGAGE, *(q for q, _ in _MUST_REFUSE), *qualified})
    verdicts = run_fresh(checks.literal_env(payload), "verdicts", tmp_path, questions)

    assert [q for q in allowed if verdicts[q]["genie"]] == []
    assert [q for q in (*REVIEWED_OBJECTIVES, *BARE_MORTGAGE) if verdicts[q]["growth"] != "allowed"] == []
    assert [q for q, cls in _MUST_REFUSE if cls not in verdicts[q]["genie"]] == []
    for question in qualified:
        assert verdicts[question] == {"genie": ["scope_bypass"], "growth": "instruction_override"}, question


# --- the tier and the refresh ------------------------------------------------------------


def _planned_tools(done: cp.Dispatch) -> set[str]:
    """Every tool the dispatch plans, ``-m`` or by path."""

    planned = {module for command in done.planned for module in re.findall(r"-m (tools(?:\.\w+)+)", command)}
    return planned | {
        path.replace("/", ".") for command in done.planned for path in re.findall(r"(?:^|[\s/=])(tools(?:/\w+)+)\.py(?!\w)", command)
    }


def _modules(done: cp.Dispatch) -> set[str]:
    """Every tool the dispatch plans or its interpreter really ran, ``-m`` or by path, behind any flag."""

    return _planned_tools(done) | {tool for call in done.record.executions() if (tool := call.tool()) is not None}


def _imported(done: cp.Dispatch) -> set[str]:
    """Every repository module the inline code the interpreter ran (``-c`` or a heredoc) imports."""

    return {module for call in done.record.executions() for module in inline_imports(call.code())}


@pytest.mark.parametrize("phase", PHASES)
def test_no_agentic_step_is_planned_or_run(dispatches: dict[str, cp.Dispatch], phase: str) -> None:
    """Held to committed sets, never to one read from the tree that runs (review of #330)."""

    done = dispatches[phase]
    modules = _modules(done)

    assert (modules | _imported(done)) & checks.AGENTIC_ONLY_MODULES == set()
    # The reviewed core allowlist (tests/fixtures/role_ledger.py): a tool no core run was reviewed for fails here.
    assert modules - REVIEWED_MODULES == set()
    assert [c for c in done.planned if re.search(r"--identity-role (verifier|agent_runtime|agent_proxy)|--role-contract verifier", c)] == []
    assert "agentic tier: off (core: the normal, operator2, admin and release-probe identities only)" in done.stdout
    if phase == "release":
        assert CORE_SKIP in done.stdout


def test_the_weekly_refresh_is_paused(dispatches: dict[str, cp.Dispatch], acme_profile: Any) -> None:
    assert views.bundle_vars(acme_profile)["scheduled_refresh_pause_status"] == "PAUSED"
    for done in dispatches.values():
        assert "  scheduled refresh: PAUSED (mip_scheduled_refresh" in done.stdout
    assert any("verify_scheduled_refresh_state --expect PAUSED" in c for c in dispatches["release"].planned)


# --- authority: no CI step needs account admin -------------------------------------------


def test_no_ci_step_needs_account_admin(dispatches: dict[str, cp.Dispatch], validated: dict[str, cp.Validated]) -> None:
    cli = [call["argv"] for done in dispatches.values() for call in done.record.cli_calls()]
    cli += [call["argv"] for result in validated.values() for call in result.calls]
    planned = [command for done in dispatches.values() for command in done.planned]
    executed = [call for done in dispatches.values() for call in done.record.executions()]

    assert account_admin_needs(cli=cli, planned=planned, executed=executed) == []
    # Not vacuous: the bounded account principal is planned, and classed as
    # such; the interpreter ran tools and inline code, a heredoc among it,
    # each classed by the ledger.
    assert any(planned_role(command) == ACCOUNT_PRINCIPAL for command in planned)
    assert {executed_role(call) for call in executed} == {WORKSPACE}
    assert any(call.tool() for call in executed) and any(call.stdin for call in executed)
    assert any(call.code() and call.stdin is None for call in executed), "inline -c code"


# --- the public log and the step summary --------------------------------------------------


@pytest.mark.parametrize("phase", PHASES)
def test_no_identifying_value_reaches_the_public_log_or_the_summary(
    dispatches: dict[str, cp.Dispatch], values: dict[str, str], phase: str
) -> None:
    done = dispatches[phase]
    forbidden = cp.identifying_values(values)

    assert len(done.public_lines()) > 200
    assert checks.leaks(done.public_lines(), forbidden) == []
    assert checks.leaks(done.summary.splitlines(), forbidden) == []


# --- client literals ---------------------------------------------------------------------


def _corpus(done: cp.Dispatch) -> list[str]:
    """Everything acme's run names, unmasked: its transcript and planned commands."""

    return [*done.stdout.splitlines(), *done.stderr.splitlines(), *done.planned]


def test_no_client_one_literal_reaches_acmes_run(
    dispatches: dict[str, cp.Dispatch], validated: dict[str, cp.Validated], payload: dict[str, Any], values: dict[str, str]
) -> None:
    literals = checks.install_literals(values)
    lines = [line for done in dispatches.values() for line in _corpus(done)]
    lines += [f"{name}={value}" for name, value in validated["acme"].calls[0]["bundle_vars"].items()]
    lines += [f"{name}={value}" for name, value in checks.literal_env(payload).items()]

    assert len(literals) > 20
    # No exemption: since #333 the dry run names the profile's Supervisor, never the default.
    assert checks.literal_hits(lines, literals) == []


# --- tampering: the lender record and the accepted exposure ------------------------------


def _decide(tmp_path: Path, done: cp.Dispatch, values: dict[str, str], **acks: str) -> list[str]:
    vars_file, secrets_file = cp.contexts_files(tmp_path, values)
    environ = {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_REF": f"{REPOSITORY}/.github/workflows/deploy-dev.yml@refs/heads/main",
        "GITHUB_SHA": done.replay.sha,
        "GITHUB_TOKEN": TOKEN,
        "GITHUB_API_URL": "https://127.0.0.1:9",  # unreachable: never a network call
        "MIP_DISPATCH_BREAK_GLASS": "true",
        **acks,
    }
    verdict = decide(phase="release", environment=cp.CLIENT, vars_source=str(vars_file), secrets_source=str(secrets_file),
                     environ=environ, repo=done.replay.checkout)
    return verdict.refusals


def _tamper(text: str, old: str, new: str) -> str:
    assert text.count(old) == 1 and len(old) == len(new) and sum(a != b for a, b in zip(old, new, strict=True)) == 1
    return text.replace(old, new)


def test_a_tampered_release_dispatch_refuses_whatever_acks_it_carries(
    tmp_path: Path, dispatches: dict[str, cp.Dispatch], values: dict[str, str]
) -> None:
    release = dispatches["release"]
    sha = release.replay.sha
    every_ack = {
        "MIP_DISPATCH_PROFILE_ACK": dispatch_acks.profile_ack(cp.CLIENT, views.digest(cp.profile(values))),
        "MIP_DISPATCH_OPERATION_ACKS": f"accepted_exposure_plan={cp.exposure_ack(values)}",
        "MIP_DISPATCH_PROTECTIONS_ACK": dispatch_acks.protections_ack(cp.CLIENT, cp.CLIENT, sha),
    }
    # The control: the untampered dispatch with its exact acks passes.
    assert _decide(tmp_path, release, values, **every_ack) == []

    record = json.loads(values[cp.LENDER_ENV])
    reviewed = record["reviewed_at"]
    lender = _tamper(values[cp.LENDER_ENV], reviewed, reviewed[:-1] + checks._substitute(reviewed[-1]))
    refusals = _decide(tmp_path, release, {**values, cp.LENDER_ENV: lender}, **every_ack)
    assert refusals and all(line.startswith("profile: ") for line in refusals), refusals

    repinned = {**values, cp.LENDER_ENV: lender, cp.PIN_ENV: lender_identity_digest(lender)}
    assert [line.split(":")[0] for line in _decide(tmp_path, release, repinned, **every_ack)] == ["profile_ack"]

    # One digit of the expiry year: still a valid, unexpired entry.
    expires = json.loads(values[EXPOSURE_ENV])[0]["expires_at"]
    tampered = {**values, EXPOSURE_ENV: _tamper(values[EXPOSURE_ENV], expires, expires[:3] + checks._substitute(expires[3]) + expires[4:])}
    assert [line.split(":")[0] for line in _decide(tmp_path, release, tampered, **every_ack)] == ["profile_ack"]
    # An ack for another purpose never stands in for the profile ack.
    for other in (cp.exposure_ack(tampered), every_ack["MIP_DISPATCH_PROTECTIONS_ACK"]):
        acks = {**every_ack, "MIP_DISPATCH_PROFILE_ACK": other}
        assert [line.split(":")[0] for line in _decide(tmp_path, release, tampered, **acks)] == ["profile_ack"]
    # The owner's exact profile ack for the tampered profile does not stand in for the exposure plan's.
    with pytest.raises(ExposureRefusal):
        load_policy({EXPOSURE_ENV: tampered[EXPOSURE_ENV], "MIP_GENIE_SPACE_NAME": values["MIP_GENIE_SPACE_NAME"]},
                    ack=dispatch_acks.profile_ack(cp.CLIENT, views.digest(cp.profile(tampered))))


# --- positive controls: the planted dispatch --------------------------------------------


def test_planted_account_admin_calls_are_found(planted: cp.Dispatch) -> None:
    needs = account_admin_needs(
        cli=[call["argv"] for call in planted.record.cli_calls()],
        planned=planted.planned,
        executed=planted.record.executions(),
    )
    python = re.compile(r"\S*/deploy-python(?=\s)")

    assert sorted(python.sub("$PY", need) for need in needs) == sorted(PLANTED_NEEDS)
    # Round 3: the aliased-os heredoc really ran the human bootstrap, in a
    # child the recorders never saw, so only the ledger's reading of the heredoc finds it.
    ran = (planted.record.payload_dir / "aliased-os.out").read_text(encoding="utf-8")
    assert "usage: python -m tools.clients.bootstrap_account" in ran, ran[-2000:]
    assert ("-m", "tools.clients.bootstrap_account", "--help") not in {call.argv for call in planted.record.executions()}


def test_planted_agentic_tools_are_found_though_the_tree_stops_calling_them_agentic_only(planted: cp.Dispatch) -> None:
    """Review of #330: a core file calling an agentic tool drops it from a set read from that tree.

    Planned with ``-m``, executed by path, and imported by a heredoc: each is
    found, the first two though the planted tree stops naming them agentic-only.
    """

    executed = {call.tool() for call in planted.record.executions()}
    found = (_modules(planted) | _imported(planted)) & checks.AGENTIC_ONLY_MODULES

    assert found == {PLANTED_AGENTIC, EXECUTED_AGENTIC, IMPORTED_AGENTIC}
    assert EXECUTED_AGENTIC in executed - _planned_tools(planted)
    assert IMPORTED_AGENTIC not in _modules(planted)
    assert not {PLANTED_AGENTIC, EXECUTED_AGENTIC} & checks.agentic_only_modules(planted.replay.checkout / "scripts")


def test_planted_bundle_calls_to_another_target_are_found(planted: cp.Dispatch) -> None:
    """Planned by path, and executed through the CLI and through the wrapper (review of #330)."""

    cli = [call["argv"] for call in planted.record.cli_calls()]
    stray = _stray_targets(planted.planned, cli, planted.record.executions())

    assert len(stray) == 3, stray
    assert "tools/databricks/bundle_env.py validate -t dev" in stray[0], stray
    assert stray[1:] == ["cli: databricks bundle validate -t dev", "executed: -m tools.databricks.bundle_env --help -t dev"]


def test_planted_log_and_summary_leaks_are_found(planted: cp.Dispatch, values: dict[str, str]) -> None:
    forbidden = cp.identifying_values(values)
    lender = json.loads(values[cp.LENDER_ENV])["legal_name"]

    # The lender's name and its lower-case spelling (a derived mask) both match, in any case.
    assert {value.casefold() for value, _line in checks.leaks(planted.public_lines(), forbidden)} == {lender.casefold()}
    assert any(lender.upper() in line for line in planted.public_lines()), "a re-cased value passes the runner's masks"
    assert {value.casefold() for value, _line in checks.leaks(planted.summary.splitlines(), forbidden)} == {lender.casefold()}


def test_a_planted_client_one_literal_is_found(planted: cp.Dispatch, values: dict[str, str]) -> None:
    hits = checks.literal_hits(_corpus(planted), checks.install_literals(values))

    assert {field for field, _line in hits} == {"MIP_APP_NAME"}
    assert all("active_deployment_pin digest --app-name mip-app" in line for _field, line in hits)


def test_a_planted_identifying_value_in_the_tree_is_recorded_and_refuses_a_release(
    tmp_path: Path, planted: cp.Dispatch, values: dict[str, str]
) -> None:
    recorded = "recorded for the plan: tree_scan: 1 tracked files hold a resolved identifying value (DATABRICKS_HOST in tools/clients/__init__.py)"
    assert recorded in planted.summary
    every_ack = {
        "MIP_DISPATCH_PROFILE_ACK": dispatch_acks.profile_ack(cp.CLIENT, views.digest(cp.profile(values))),
        "MIP_DISPATCH_PROTECTIONS_ACK": dispatch_acks.protections_ack(cp.CLIENT, cp.CLIENT, planted.replay.sha),
    }
    assert [line.split(":")[0] for line in _decide(tmp_path, planted, values, **every_ack)] == ["tree_scan"]


# --- last: the real checkout -------------------------------------------------------------


def test_the_real_checkout_stays_clean(
    baseline: dict[str, str | None],
    dispatches: dict[str, cp.Dispatch],
    planted: cp.Dispatch,
    validated: dict[str, cp.Validated],
) -> None:
    assert checks.checkout_state(REPO) == baseline
    assert all(done.replay.checkout != REPO for done in (*dispatches.values(), planted))
