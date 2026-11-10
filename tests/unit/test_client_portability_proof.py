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
inputs that must fail them) prove each check can fail: an account-admin call,
a log and a summary leak, a client-#1 literal and an identifying value in the
tree. The ci.yml job ``client-portability-proof`` runs this module and the integrity one.
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
    account_admin_needs,
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
# The one client-#1 value acme's core-tier run may name: the dry run's
# pre-stop line prints the Supervisor default, which the deploy shell never
# reads from the profile. The core tier's probes list no agent, so the name
# is never used (tools/databricks/deploy_prestop_probes.py); the agentic
# tier's use of the default is owed (the PR body), not exempted.
SUPERVISOR_EXEMPTION = (("MIP_AGENT_SUPERVISOR_NAME", "would probe read-only: the creator of Supervisor"),)
CORE_SKIP = "core tier: skip the agentic provisioning, cutover and Agent Evaluation"
# A bundle call: the env-aware wrapper or the CLI's own bundle command.
BUNDLE_CALL = re.compile(r"-m tools\.databricks\.bundle_env\s|(?:^|[\s/])databricks\s+bundle\s")


def _planted(values: dict[str, str]) -> tuple[cp.Plant, ...]:
    """The positive controls: an account-admin call run and planned, a human bootstrap, a literal, two leaks, a tree value."""

    shell = (
        "# The portability proof's positive controls, planted in its sandbox clone only.\n"
        "databricks account service-principals list >/dev/null 2>&1 || true\n"
        "run databricks account groups list\n"
        'run "$PYTHON" -m tools.clients.bootstrap_account --apply\n'
        'run "$PYTHON" -m tools.databricks.app_start_deployment --app-name mip-app\n'
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
        assert re.findall(r"(?:^|\s)(?:-t|--target)[\s=](\S+)", command) == ["client"], command
    deployer = acme_profile.identity_ids.deployer
    for command in (c for c in planned if "--expected-target" in c):
        assert "--expected-target client" in command and f"--expected-root-leaf {cp.CLIENT}" in command, command
        assert f"--expected-principal {deployer}" in command, command
    if phase == "release":
        assert any("--expected-root-leaf" in c for c in planned)
        assert any(f"deployment bind mip_app {acme_profile.app_name} -t client" in c for c in planned)


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
    """Client #1's shape renders the default install's SQL: catalog mip, the demo feeds on, the canonical share."""

    reference = tmp_path / "default-render"
    render_sql.render(catalog="mip", demo_first_party_enabled=True, dest_root=reference)

    assert validated["shaped"].calls[0]["rendered_sha256"] == tree_sha256(reference)
    assert validated["shaped"].calls[0]["bundle_vars"]["BUNDLE_VAR_client_key"] != cp.CLIENT


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


@pytest.mark.parametrize("phase", PHASES)
def test_no_agentic_step_is_planned_or_run(dispatches: dict[str, cp.Dispatch], phase: str) -> None:
    done = dispatches[phase]
    agentic = checks.agentic_only_modules()
    assert len(agentic) > 10, "the agentic slices name their tools"
    planned = {module for command in done.planned for module in re.findall(r"-m (tools(?:\.\w+)+)", command)}

    assert planned & agentic == set()
    assert set(done.record.modules()) & agentic == set()
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

    assert account_admin_needs(cli=cli, planned=planned) == []
    # Not vacuous: the bounded account principal is planned, and classed as such.
    assert any(planned_role(command) == ACCOUNT_PRINCIPAL for command in planned)


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
    assert checks.literal_hits(lines, literals, SUPERVISOR_EXEMPTION) == []


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
    needs = account_admin_needs(cli=[call["argv"] for call in planted.record.cli_calls()], planned=planted.planned)

    assert "cli: databricks account service-principals list" in needs
    assert "planned: databricks account groups list" in needs
    assert [need for need in needs if "tools.clients.bootstrap_account --apply" in need], needs
    assert len(needs) == 3, needs


def test_planted_log_and_summary_leaks_are_found(planted: cp.Dispatch, values: dict[str, str]) -> None:
    forbidden = cp.identifying_values(values)
    lender = json.loads(values[cp.LENDER_ENV])["legal_name"]

    # The lender's name and its lower-case spelling (a derived mask) both match, in any case.
    assert {value.casefold() for value, _line in checks.leaks(planted.public_lines(), forbidden)} == {lender.casefold()}
    assert any(lender.upper() in line for line in planted.public_lines()), "a re-cased value passes the runner's masks"
    assert {value.casefold() for value, _line in checks.leaks(planted.summary.splitlines(), forbidden)} == {lender.casefold()}


def test_a_planted_client_one_literal_is_found(planted: cp.Dispatch, values: dict[str, str]) -> None:
    hits = checks.literal_hits(_corpus(planted), checks.install_literals(values), SUPERVISOR_EXEMPTION)

    assert {field for field, _line in hits} == {"MIP_APP_NAME"}
    assert all("app_start_deployment --app-name mip-app" in line for _field, line in hits)


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
