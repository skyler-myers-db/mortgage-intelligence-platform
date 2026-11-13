"""The owner's read-only adopt preflight computes the bind plan from the declared names (cutover step 6c).

``tools/databricks/adopt_preflight.py`` feeds the unchanged
``adopt_legacy_map.compute`` with names read from the real databricks.yml and
a client profile's bundle variables, in place of a bundle CLI plan. Driven
here over the synthetic 16-key legacy install (tests/fixtures/adopt_estate.py)
whose live names are the names the real bundle declares, under the CLI's
development-mode prefix: the shape an install deployed by the retired
development-mode target has. The experiment starts in the shape the first
client's snapshot found (another folder, another base name), which refuses
``renamed`` until the owner's rename.

Pinned: the verdict before and after that rename; that the tool reads only
(GETs by id and two state downloads) on every path, the refused and the not
measured ones included, starts no child process of its own and calls no
bundle CLI, and calls nothing but its reviewed callees; that it prints no
identifier and no acknowledgement; that its
digest is the one a CLI-sourced plan gives for the same estate; that it prints
the profile ack of the profile it judged; and that every bundle or profile
shape it cannot resolve refuses, never guesses a name.
"""

from __future__ import annotations

import ast
import copy
import functools
import json
import os
import re
import subprocess
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from databricks.sdk.errors import PermissionDenied

from tests.fixtures import adopt_estate as es
from tests.fixtures import bundle_plan_fixtures as fx
from tests.fixtures.client_profiles import core_tier_profile
from tests.unit import test_adopt_phase_deploy_contract as adopt_contract
from tests.unit.test_bundle_experiment_path import _cli_presets_name
from tools.clients import dispatch_acks
from tools.clients import profile as views
from tools.clients.environment import RawProfile, resolve
from tools.clients.errors import ProfileRefusal
from tools.databricks import adopt_legacy_map as legacy_map
from tools.databricks import adopt_preflight as preflight
from tools.databricks import install_state_adopt as adopt
from tools.databricks.bundle_plan_lookups import KIND_LOOKUPS

REPO = Path(__file__).resolve().parents[2]
BUNDLE = REPO / "databricks.yml"
MODULE = REPO / "tools" / "databricks" / "adopt_preflight.py"
KEY = "experiments.mip_lead_scoring"
# The shape the snapshot found: the first declaration's folder and base name, development-mode prefixed.
LEGACY_EXPERIMENT = "/Shared/mip/[dev example] lead-scoring"
# Names no bundle key contains, so a printed name cannot hide inside a symbolic key.
NAMES = {
    "MIP_APP_NAME": "lender-one-app",
    "MIP_LAKEBASE_INSTANCE": "lender-one-instance",
    "MIP_LAKEBASE_SYNC_CATALOG": "lender_one_sync",
}
GETS = frozenset(
    {
        ("apps", "get"),
        ("database", "get_database_instance"),
        ("database", "get_database_catalog"),
        ("warehouses", "get"),
        ("jobs", "get"),
        ("pipelines", "get"),
        ("lakeview", "get"),
        ("experiments", "get_experiment"),
    }
)
# Every way a Python process starts another one: patched in every test, so a call is recorded even
# when the module swallows the error it raises.
_CHILD_PROCESS = (
    (subprocess, ("run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput")),
    (os, ("system", "popen", "fork", "forkpty", "posix_spawn", "posix_spawnp", "execl", "execle", "execlp",
          "execlpe", "execv", "execve", "execvp", "execvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe", "spawnv",
          "spawnve", "spawnvp", "spawnvpe")),
)  # fmt: skip


@pytest.fixture(autouse=True)
def _no_child_process(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    started: list[str] = []

    def forbidden(qualified: str) -> Any:
        def never(*_args: Any, **_kwargs: Any) -> Any:
            started.append(qualified)
            raise AssertionError(f"the preflight starts no child process ({qualified})")

        return never

    for module, names in _CHILD_PROCESS:
        for name in names:
            if hasattr(module, name):
                monkeypatch.setattr(module, name, forbidden(f"{module.__name__}.{name}"))
    yield started
    assert started == [], f"a child process was started, its error swallowed or not: {started}"


def _profile(**overrides: str | None) -> Any:
    values = core_tier_profile(
        MIP_LEGACY_BUNDLE_ROOT=es.LEGACY_ROOT, MIP_DASHBOARD_PARENT_PATH=es.DASHBOARD_PARENT, **{**NAMES, **overrides}
    )
    return resolve(RawProfile(values=values))


@functools.lru_cache(maxsize=1)
def _parsed_bundle() -> dict[str, Any]:
    return yaml.safe_load(BUNDLE.read_text(encoding="utf-8"))


def _bundle() -> dict[str, Any]:
    """A private copy of the real databricks.yml, parsed once."""

    return copy.deepcopy(_parsed_bundle())


def _declared(profile: Any, bundle: dict[str, Any] | None = None) -> dict[str, tuple[str, ...]]:
    plan = preflight.declared_plan(bundle or _bundle(), views.bundle_vars(profile))
    return {
        entry.symbolic: tuple(entry.new_value[name] for name in KIND_LOOKUPS[entry.kind].name_fields)
        for entry in plan.entries
    }


def _estate(profile: Any, *, experiment: str | None = LEGACY_EXPERIMENT) -> es.Estate:
    """The legacy install with the real declared names, development-mode prefixed; ``None``: the experiment too."""

    names = _declared(profile)
    estate = es.Estate()
    for resource in estate.legacy_ids:
        kind = resource.split(".", 1)[0]
        declared = names[resource]
        if kind in es.EXACT_KINDS:
            live = {"name": declared[0]}
        elif kind == "dashboards":
            live = {"parent_path": es.live_folder(declared[0]), "display_name": es.DEV + declared[1]}
        elif kind == "experiments":
            live = {"name": experiment or _cli_presets_name(declared[0], es.DEV)}
        else:
            live = {"name": es.DEV + declared[0]}
        estate.object_of(resource).names = live
    estate.config = SimpleNamespace(host=profile.databricks_host)
    return estate


def _tree(estate: es.Estate) -> dict[str, Any]:
    return {path: (node.kind, node.data) for path, node in estate.tree.nodes.items()}


def _run(
    estate: Any, profile: Any, capsys: pytest.CaptureFixture[str], *argv: str, bundle_path: Path = BUNDLE
) -> tuple[int, str, str]:
    before = _tree(estate)
    code = preflight.main(list(argv), profile=lambda: profile, workspace_factory=lambda: estate, bundle_path=bundle_path)
    printed = capsys.readouterr()
    # Read-only on every path, refused and not measured too. The double records a call before it
    # answers or raises, so a write whose error the module swallowed is still seen here.
    assert set(estate.reads()) <= GETS, f"a workspace call that is no reviewed GET: {set(estate.reads()) - GETS}"
    assert {op for op, _path in estate.tree.calls} <= {"download"}, estate.tree.calls
    assert _tree(estate) == before, "the workspace tree changed"
    return code, printed.out, printed.err


def _refusals(err: str) -> list[str]:
    return [line for line in err.splitlines() if " REFUSED" in line]


def _verdict(out: str) -> str:
    return out.rstrip().splitlines()[-1].removeprefix("[mip-adopt-preflight] ")


def _digest(out: str, label: str) -> str:
    line = next(line for line in out.splitlines() if label in line)
    found = re.findall(r"\b[0-9a-f]{64}\b", line)
    assert len(found) == 1, line
    return found[0]


# --- the verdict, before and after the owner's rename ---------------------------------------


def test_the_declared_names_refuse_the_legacy_experiment_and_clear_once_it_carries_the_bundles_name(
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile = _profile()
    estate = _estate(profile)
    declared = _declared(profile)[KEY][0]
    assert declared == _bundle()["resources"]["experiments"]["mip_lead_scoring"]["name"]

    code, out, err = _run(estate, profile, capsys)

    refused = _refusals(err)
    assert code == 2 and _verdict(out) == "verdict: refused"
    assert len(refused) == 1, "the other 15 keys bind under the real declared names"
    assert f"REFUSED renamed {KEY} (name): " in refused[0] and "first-client-cutover.md, step 3b" in refused[0]

    # The owner's rename: the same object and id, now under the declared name.
    estate.object_of(KEY).names = {"name": declared}
    code, out, err = _run(estate, profile, capsys)

    assert code == 0 and _refusals(err) == [] and _verdict(out) == "verdict: clear"
    assert "16 legacy keys would bind, 0 already bound, 2 expected creates" in out
    assert f"expected creates after the binds: {', '.join(es.NEW_KEYS)}" in out

    # A development-mode name in the same folder binds too: only another folder or base name refuses.
    assert _run(_estate(profile, experiment=None), profile, capsys)[0] == 0
    # An App whose live name is not the profile's is sent to the profile, never to a rename.
    other = _estate(profile, experiment=None)
    other.object_of("apps.mip_app").names = {"name": "another-app"}
    code, out, err = _run(other, profile, capsys)
    assert code == 2 and [line.split(": ", 1)[0].split("REFUSED ")[1] for line in _refusals(err)] == ["renamed apps.mip_app (name)"]
    assert legacy_map.RENAMED_EXACT_REMEDY in err and "step 3b" not in err


# --- read-only, no child process, no bundle CLI ----------------------------------------------


def test_the_preflight_only_reads_and_runs_no_child_process(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("the preflight runs no child process")

    monkeypatch.setattr(subprocess, "run", never)
    monkeypatch.setattr(subprocess, "Popen", never)
    profile = _profile()
    estate = _estate(profile, experiment=None)
    before = {path: (node.kind, node.data) for path, node in estate.tree.nodes.items()}

    code, _out, _err = _run(estate, profile, capsys)

    assert code == 0
    assert set(estate.reads()) == GETS, "one reviewed GET by id per kind, and nothing else"
    assert len(estate.calls) == len(estate.legacy_ids) == 16, "one GET per legacy object"
    assert {op for op, _path in estate.tree.calls} == {"download"}, "the two state files, read"
    assert {path: (node.kind, node.data) for path, node in estate.tree.nodes.items()} == before


_CLI_CALLS = frozenset({"bind", "unbind", "plan"})
_WRITES = frozenset(
    {"upload", "mkdirs", "import_", "delete", "create", "update", "patch", "start", "stop", "deploy", "trash",
     "put_secret", "set_permissions", "update_permissions", "write_text", "write_bytes", "unlink", "rename"}
)  # fmt: skip


def _static_findings(source: str) -> set[str]:
    """What the module imports or calls that a read-only, CLI-free tool must not."""

    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
            names: list[str] = []
        elif isinstance(node, ast.ImportFrom):
            modules, names = [node.module or ""], [alias.name for alias in node.names]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _CLI_CALLS | _WRITES:
                found.add(f"call .{node.func.attr}(")
            continue
        else:
            continue
        for module in modules:
            if module.split(".")[0] == "subprocess" or module.endswith(".bundle_env") or module == "bundle_env":
                found.add(f"import {module}")
        found.update(f"import {name}" for name in names if name in {"BundleEnvCli", "bundle_env", "subprocess"})
    return found


def test_the_module_imports_no_child_process_or_bundle_cli_and_calls_no_write() -> None:
    source = MODULE.read_text(encoding="utf-8")

    assert _static_findings(source) == set()
    assert MODULE in adopt_contract.ADOPT_PATH, "the adopt path's stop, grant and delete contract covers it"
    assert adopt_contract.violations(source) == []
    # Positive control: each shape is found.
    planted = (
        "import subprocess\nfrom tools.databricks import bundle_env\n"
        "from tools.databricks.install_state_adopt import BundleEnvCli\n"
        "cli.plan()\ncli.bind(key, live_id, authority)\nworkspace.workspace.upload(path, data)\n"
    )
    assert _static_findings(planted) == {
        "import subprocess", "import bundle_env", "import BundleEnvCli", "call .plan(", "call .bind(", "call .upload(",
    }  # fmt: skip


# The module's reviewed callees and imports, exactly. A forbidden-name list misses the SDK's real
# mutators (update_experiment, set_experiment_tag, api_client.do, jobs.reset) and os.system, so every
# call and import is matched against what was reviewed: a new one is a finding until it is reviewed.
_REVIEWED_CALLEES = frozenset(
    {"<boolop>.splitlines", "<constant>.join", "DeclaredNameError", "Path", "Path().resolve", "SystemExit",
     "_VAR.fullmatch", "_literal", "_mapping", "_mapping().get", "_mapping().items", "_not_measured", "_print",
     "_resolved", "_write_private", "argparse.ArgumentParser", "body.get", "bundle_path.read_text", "declared_plan",
     "deployment_workspace_client", "dispatch_acks.profile_ack", "from_local", "getattr", "handle.write",
     "host_refusals", "inputs_from_profile", "isinstance", "json.dump", "json.dumps", "legacy_map.compute", "len",
     "main", "match.group", "merged.get", "merged.items", "os.chmod", "os.fdopen", "os.open", "parse_plan",
     "parser.add_argument", "parser.parse_args", "plan.full", "print", "profile", "re.compile", "read_state",
     "resolve", "sorted", "spec.get", "str", "super", "super().__init__", "sys.path.insert", "target.get", "top.get",
     "type", "views.bundle_vars", "views.digest", "workspace_factory", "yaml.safe_load"}
)  # fmt: skip
_REVIEWED_IMPORTS = frozenset(
    {"__future__:annotations", "argparse", "collections.abc:Callable", "collections.abc:Mapping", "json", "os",
     "pathlib:Path", "re", "sys", "tools.clients.environment:from_local", "tools.clients.environment:resolve",
     "tools.clients.errors:ProfileRefusal", "tools.clients.live_identity:host_refusals", "tools.clients:dispatch_acks",
     "tools.clients:profile", "tools.databricks.bundle_plan_lookups:KIND_LOOKUPS",
     "tools.databricks.bundle_plan_shape:BundlePlan", "tools.databricks.bundle_plan_shape:PlanShapeError",
     "tools.databricks.bundle_plan_shape:parse_plan", "tools.databricks.bundle_state:BundleStateError",
     "tools.databricks.bundle_state:STATE_FILE", "tools.databricks.bundle_state:read_state",
     "tools.databricks.install_state_adopt:inputs_from_profile",
     "tools.databricks.workspace_auth:deployment_workspace_client", "tools.databricks:adopt_legacy_map",
     "typing:Any", "yaml"}
)  # fmt: skip


def _dotted(node: ast.expr) -> str:
    """A callee as written: names and attributes kept, a call in the chain as ``()``, anything else by its kind."""

    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return f"{_dotted(node.func)}()"
    return f"<{type(node).__name__.lower()}>"


def _uses(source: str) -> tuple[set[str], set[str]]:
    callees: set[str] = set()
    imports: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            callees.add(_dotted(node.func))
        elif isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.update(f"{node.module}:{alias.name}" for alias in node.names)
    return callees, imports


def _unreviewed(source: str) -> set[str]:
    callees, imports = _uses(source)
    return {f"call {name}(" for name in callees - _REVIEWED_CALLEES} | {
        f"import {name}" for name in imports - _REVIEWED_IMPORTS
    }


def test_the_module_calls_and_imports_only_its_reviewed_callees() -> None:
    source = MODULE.read_text(encoding="utf-8")

    assert _unreviewed(source) == set()
    assert _uses(source) == (set(_REVIEWED_CALLEES), set(_REVIEWED_IMPORTS)), "no stale entry admits a later call"
    # Positive controls: the SDK's mutators, its raw client, and every way to start a child process.
    for planted, finding in (
        ("workspace.experiments.update_experiment(experiment_id=i, new_name=n)", "workspace.experiments.update_experiment"),
        ("workspace.experiments.set_experiment_tag(experiment_id=i, key=k, value=v)", "workspace.experiments.set_experiment_tag"),
        ('workspace.api_client.do("POST", "/api/2.0/mlflow/experiments/update", body={})', "workspace.api_client.do"),
        ("workspace.jobs.reset(job_id=1, new_settings=s)", "workspace.jobs.reset"),
        ("workspace.workspace.upload(path, data)", "workspace.workspace.upload"),
        ('os.system("true")', "os.system"),
        ('os.popen("true")', "os.popen"),
        ("os.posix_spawn(path, argv, env)", "os.posix_spawn"),
        ("os.execv(path, argv)", "os.execv"),
        ("os.spawnv(mode, path, argv)", "os.spawnv"),
        ('getattr(workspace.experiments, "update_experiment")(experiment_id=i)', "getattr()"),
    ):
        assert _unreviewed(planted) == {f"call {finding}("}, planted
    assert _unreviewed('from os import system\nsystem("true")\n') == {"import os:system", "call system("}
    assert _unreviewed("import pty\nimport subprocess\n") == {"import pty", "import subprocess"}


# --- no identifier, no acknowledgement --------------------------------------------------------


def _identifiers(estate: es.Estate, profile: Any) -> set[str]:
    inputs = adopt.inputs_from_profile(profile)
    values = {"legacy-owner@example.invalid", es.LEGACY_ROOT, inputs.deployer_root, es.DASHBOARD_PARENT, es.DEV.strip()}
    values |= {profile.databricks_host, profile.identity_ids.deployer, *NAMES.values()}
    values |= {value for value in estate.legacy_ids.values() if not value.startswith("synthetic-")}
    values |= {name for obj in estate.objects.values() for name in obj.names.values() if "/" in name or name.startswith("[")}
    return values


def test_the_preflight_prints_no_identifier_and_no_acknowledgement_and_keeps_the_document_private(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    profile = _profile()
    report = tmp_path / "adopt-preflight.json"
    texts = []
    for experiment in (LEGACY_EXPERIMENT, None):
        estate = _estate(profile, experiment=experiment)
        _code, out, err = _run(estate, profile, capsys, "--report-out", str(report))
        texts.append(out + err)
        shown = re.sub(r"\b[0-9a-f]{64}\b", "<sha256>", out + err)
        assert [value for value in _identifiers(estate, profile) if value in shown] == []
        assert "MIP_ADOPT_PLAN_ACK" not in shown and "::add-mask::" not in shown
        assert "this preflight is no acknowledgement" in out
        assert oct(report.stat().st_mode & 0o777) == "0o600"
        document = json.loads(report.read_text(encoding="utf-8"))
        assert document["plan_sha256"] == _digest(out, "bind plan document sha256")
        assert {edit["resource"]: edit["id"] for edit in document["document"]["bind"]} == estate.legacy_ids
        assert document["preflight"]["profile_ack"] == _digest(out, "profile ack")
        assert ("renamed" in [refusal["code"] for refusal in document["refusals"]]) is (experiment is not None)
    assert texts[0] != texts[1]

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    estate = _estate(profile)
    _code, _out, err = _run(estate, profile, capsys)
    masks = {line.removeprefix("::add-mask::") for line in err.splitlines() if line.startswith("::add-mask::")}
    assert set(estate.legacy_ids.values()) <= masks


def test_the_profile_ack_printed_is_the_local_profiles_and_moves_with_a_name(capsys: pytest.CaptureFixture[str]) -> None:
    """A ``clear`` from a local profile that is not CI's proves nothing about CI's names: the ack tells them apart."""

    profile = _profile()
    renamed = _profile(MIP_APP_NAME="lender-two-app")
    acks = []
    for candidate in (profile, renamed):
        code, out, _err = _run(_estate(candidate, experiment=None), candidate, capsys)
        assert code == 0
        acks.append(_digest(out, "profile ack of the local profile judged"))
        assert acks[-1] == dispatch_acks.profile_ack(candidate.client, views.digest(candidate))
        assert "owner_attestation=" in out
    assert acks[0] != acks[1], "another App name is another profile, so another ack"


# --- the digest is the one a CLI-sourced plan gives -------------------------------------------


def test_the_digest_is_the_one_the_adopt_phases_cli_sourced_plan_gives_for_the_same_estate(
    capsys: pytest.CaptureFixture[str],
) -> None:
    profile = _profile()
    inputs = adopt.inputs_from_profile(profile)
    for experiment in (LEGACY_EXPERIMENT, None):
        estate = _estate(profile, experiment=experiment)
        from_cli = adopt.compute_plan("adopt", inputs, es.FakeBundleCli(estate), estate)
        _code, out, _err = _run(estate, profile, capsys)
        assert _digest(out, "bind plan document sha256") == from_cli.sha256
    # The document covers the key set, so a reader that drops a declared key gives another digest.
    declared = preflight.declared_plan(_bundle(), views.bundle_vars(profile))
    assert sorted(entry.symbolic for entry in declared.entries) == sorted(es.declared())
    bundle = _bundle()
    del bundle["resources"]["jobs"][es.NEW_KEYS[0].split(".", 1)[1]]
    estate = _estate(profile, experiment=None)
    fewer = preflight.declared_plan(bundle, views.bundle_vars(profile))
    full = legacy_map.compute("adopt", inputs, declared, None, estate.state(es.LEGACY_ROOT), estate)
    partial = legacy_map.compute("adopt", inputs, fewer, None, estate.state(es.LEGACY_ROOT), estate)
    assert full.sha256 != partial.sha256 and len(partial.expected_creates) == 1


# --- fail closed --------------------------------------------------------------------------------


def _copy(tmp_path: Path, change: Any) -> Path:
    bundle = _bundle()
    change(bundle)
    path = tmp_path / "databricks.yml"
    path.write_text(yaml.safe_dump(bundle, sort_keys=False), encoding="utf-8")
    return path


def test_no_legacy_root_refuses_without_building_a_workspace_client(capsys: pytest.CaptureFixture[str]) -> None:
    fresh = resolve(RawProfile(values=core_tier_profile()))

    def never() -> Any:
        raise AssertionError("no workspace client for an install with no legacy state")

    code = preflight.main([], profile=lambda: fresh, workspace_factory=never)

    printed = capsys.readouterr()
    assert code == 2 and "REFUSED not_an_adopted_install" in printed.err and _verdict(printed.out) == "verdict: refused"


def test_a_cli_profile_on_another_host_is_refused_before_any_read(capsys: pytest.CaptureFixture[str]) -> None:
    profile = _profile()
    estate = _estate(profile, experiment=None)
    estate.config = SimpleNamespace(host="https://dbc-another.cloud.databricks.com")

    code, out, err = _run(estate, profile, capsys)

    assert code == 2 and _verdict(out) == "verdict: not measured"
    assert "the CLI profile's workspace host is not the client profile's DATABRICKS_HOST; nothing was read" in err
    assert estate.calls == [] and estate.tree.calls == []
    assert "dbc-another" not in out + err and profile.databricks_host not in out + err


def _development(bundle: dict[str, Any]) -> None:
    bundle["targets"]["client"]["mode"] = "development"


def _no_mode(bundle: dict[str, Any]) -> None:
    del bundle["targets"]["client"]["mode"]


def _prefixed(bundle: dict[str, Any]) -> None:
    bundle["targets"]["client"]["presets"]["name_prefix"] = "[lender] "


def _prefixed_everywhere(bundle: dict[str, Any]) -> None:
    bundle["presets"] = {"name_prefix": "[lender] "}


def _included(bundle: dict[str, Any]) -> None:
    bundle["include"] = ["resources/*.yml"]


def _target_variables(bundle: dict[str, Any]) -> None:
    bundle["targets"]["client"]["variables"] = {"app_name": "another-app"}


def _unknown_kind(bundle: dict[str, Any]) -> None:
    bundle["resources"]["schemas"] = {"mip_extra": {"name": "extra", "catalog_name": "mip"}}


@pytest.mark.parametrize(
    ("change", "code"),
    (
        (_development, "target_not_production"),
        (_no_mode, "target_not_production"),
        (_prefixed, "target_name_prefix"),
        (_prefixed_everywhere, "target_name_prefix"),
        (_included, "bundle_include"),
        (_target_variables, "target_variables"),
        (_unknown_kind, "unknown_kind"),
    ),
    ids=("development", "no-mode", "name-prefix", "top-level-name-prefix", "include", "target-variables", "unknown-kind"),
)
def test_a_bundle_shape_the_reader_cannot_vouch_for_is_refused_before_any_workspace_call(
    change: Any, code: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each would let the CLI plan other keys or names than this reader sees (binding change 9)."""

    profile = _profile()

    def never() -> Any:
        raise AssertionError("no workspace client before the bundle's names are resolved")

    rc = preflight.main([], profile=lambda: profile, workspace_factory=never, bundle_path=_copy(tmp_path, change))

    printed = capsys.readouterr()
    assert rc == 2 and _verdict(printed.out) == "verdict: not measured"
    assert f"(DeclaredNameError: {code})" in printed.err and "lender" not in printed.err and "another-app" not in printed.err
    with pytest.raises(preflight.DeclaredNameError) as refused:
        bundle = _bundle()
        change(bundle)
        preflight.declared_plan(bundle, views.bundle_vars(profile))
    assert refused.value.code == code and str(refused.value) == code


@pytest.mark.parametrize(
    "reference",
    ("${var.unknown}", "${workspace.file_path}", "${bundle.target}-x", "${var.app_name}-x", "${var.app_name}${var.client_key}"),
)
def test_a_name_the_profile_does_not_resolve_is_unresolved_and_never_guessed(
    reference: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = _profile()

    def unresolved(bundle: dict[str, Any]) -> None:
        bundle["resources"]["experiments"]["mip_lead_scoring"]["name"] = reference

    path = _copy(tmp_path, unresolved)
    plan = preflight.declared_plan(yaml.safe_load(path.read_text(encoding="utf-8")), views.bundle_vars(profile))
    entry = next(entry for entry in plan.entries if entry.symbolic == KEY)
    assert dict(entry.new_vars) == {"name": reference}, "the reference is carried, not resolved"

    code, out, err = _run(_estate(profile, experiment=None), profile, capsys, bundle_path=path)

    assert code == 2 and _verdict(out) == "verdict: refused"
    assert [line for line in _refusals(err) if "unresolved_name" in line and KEY in line] and "renamed" not in err


def test_an_unpinned_dashboard_folder_is_unresolved(capsys: pytest.CaptureFixture[str]) -> None:
    """The variable's default is a workspace reference: only the profile's pin resolves it."""

    profile = _profile()
    variables = {name: value for name, value in views.bundle_vars(profile).items() if name != "dashboard_parent_path"}
    plan = preflight.declared_plan(_bundle(), variables)
    dashboards = [entry for entry in plan.entries if entry.kind == "dashboards"]
    assert len(dashboards) == 2 and all(set(entry.new_vars) == {"parent_path"} for entry in dashboards)
    estate = _estate(profile, experiment=None)
    refused = legacy_map.compute("adopt", adopt.inputs_from_profile(profile), plan, None, estate.state(es.LEGACY_ROOT), estate)
    assert sorted((r.code, r.resource) for r in refused.refusals) == [
        ("unresolved_name", "dashboards.mip_executive_dashboard"),
        ("unresolved_name", "dashboards.mip_segment_dashboard"),
    ]


def test_a_read_that_fails_is_never_read_as_absent_or_clear(capsys: pytest.CaptureFixture[str]) -> None:
    profile = _profile()
    estate = _estate(profile, experiment=None)
    answer = estate._answer

    def denied(service: str, method: str, args: Any, kwargs: Any) -> Any:
        if (service, method) == ("experiments", "get_experiment"):
            raise PermissionDenied("experiment 1014 is not readable by owner@lender.example")
        return answer(service, method, args, kwargs)

    estate._answer = denied  # type: ignore[method-assign]
    code, out, err = _run(estate, profile, capsys)
    assert code == 2 and _verdict(out) == "verdict: refused"
    assert f"REFUSED read_failed {KEY} (PermissionDenied)" in err and "owner@lender.example" not in out + err

    # A state that cannot be read at all: the class only, and not measured.
    broken = _estate(profile, experiment=None)

    def unreadable(path: str, **_kwargs: Any) -> Any:
        raise PermissionDenied(f"{path} is not readable")

    broken.tree.download = unreadable  # type: ignore[method-assign]
    code, out, err = _run(broken, profile, capsys)
    assert code == 2 and _verdict(out) == "verdict: not measured"
    assert "the read did not complete (PermissionDenied)" in err and es.LEGACY_ROOT not in out + err


def test_a_profile_that_refuses_is_not_measured(capsys: pytest.CaptureFixture[str]) -> None:
    def refuses() -> Any:
        raise ProfileRefusal(["MIP_APP_NAME: is required"])

    def never() -> Any:
        raise AssertionError("no workspace client without a resolved profile")

    code = preflight.main([], profile=refuses, workspace_factory=never)

    printed = capsys.readouterr()
    assert code == 2 and "client profile refused:" in printed.err and "  - MIP_APP_NAME: is required" in printed.err
    assert _verdict(printed.out) == "verdict: not measured"


# --- the reader against the real bundle ---------------------------------------------------------


def test_the_reader_resolves_every_declared_key_of_the_real_bundle_for_an_adopted_profile() -> None:
    profile = _profile()
    bundle = _bundle()
    plan = preflight.declared_plan(bundle, views.bundle_vars(profile))

    assert sorted((entry.kind, entry.name) for entry in plan.entries) == fx.declared_resources()
    assert {entry.kind for entry in plan.entries} <= set(KIND_LOOKUPS)
    assert all(entry.action == "create" and not entry.new_vars for entry in plan.entries), "no unresolved field"
    names = _declared(profile)
    assert names["apps.mip_app"] == (NAMES["MIP_APP_NAME"],)
    assert names["database_instances.mip_app_state"] == (NAMES["MIP_LAKEBASE_INSTANCE"],)
    assert names["database_catalogs.mip_app_state_catalog"] == (NAMES["MIP_LAKEBASE_SYNC_CATALOG"],)
    assert {names[f"dashboards.{key}"][0] for key in bundle["resources"]["dashboards"]} == {es.DASHBOARD_PARENT}
    # The experiment's name is what the CLI renders for a production target: nothing is prefixed.
    experiment = bundle["resources"]["experiments"]["mip_lead_scoring"]["name"]
    assert names[KEY] == (experiment,) == (_cli_presets_name(experiment, ""),)
    target = bundle["targets"]["client"]
    assert target["mode"] == "production" and "name_prefix" not in target["presets"]
    assert "include" not in bundle and "variables" not in target, "the two shapes the reader refuses are absent today"
    # Every other name field is a literal the CLI passes through unchanged.
    for kind, entries in bundle["resources"].items():
        for key, body in entries.items():
            for name_field in KIND_LOOKUPS[kind].name_fields:
                raw = body.get(name_field, target.get("resources", {}).get(kind, {}).get(key, {}).get(name_field))
                assert isinstance(raw, str) and (
                    "${" not in raw or re.fullmatch(r"\$\{var\.[a-z_]+\}", raw)
                ), (kind, key, name_field)


# --- the runbook's step 6c says what the tool prints ------------------------------------------

RUNBOOK = "docs/clients/first-client-cutover.md"
COMMAND = (
    "DATABRICKS_CONFIG_PROFILE=<owner-profile> python -m tools.databricks.adopt_preflight "
    "--report-out <private-dir>/adopt-preflight.json"
)


def _flat(relative: str) -> str:
    return " ".join((REPO / relative).read_text(encoding="utf-8").split())


def test_the_runbook_makes_the_preflight_a_step_7_precondition_and_quotes_what_the_tool_prints(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runbook = _flat(RUNBOOK)
    step = runbook[runbook.index("## Step 6c.") : runbook.index("## Step 7.")]
    assert COMMAND in (REPO / RUNBOOK).read_text(encoding="utf-8").splitlines()
    assert (REPO / "tools" / "databricks" / "adopt_preflight.py").read_text(encoding="utf-8").count(COMMAND) == 1

    profile = _profile()
    _code, out, _err = _run(_estate(profile, experiment=None), profile, capsys)
    for quoted in ("verdict: clear", "bind plan document sha256", "16 legacy keys would bind", "2 expected creates"):
        assert quoted in out and quoted in step, quoted
    assert "owner_attestation=" in out and "(`owner_attestation=<profile-ack>`, step 6)" in step
    for words in (
        "**the profile ack it prints equals the one plan #1 printed**",
        "a `clear` says nothing about the names CI will use",
        "`verdict: not measured`",
        "Not measured is not clear.",
        "never a name or an id",
        # The names are the checkout's own databricks.yml: the one the dispatches will run.
        "Run it in a checkout of the `main` commit the adopt dispatches will run: the names are that checkout's.",
        "The `adopt_plan` ack comes from 10B-1's own output only, never from this preflight",
        "It does not measure the CI deployer's own reads",
    ):
        assert words in step, words
    # Every refusal code the step names is one the bind plan prints, the name and existence ones included.
    listed = step[step.index("A refusal is one of the bind plan's own codes") : step.index("It does not measure")]
    codes = set(re.findall(r"`([a-z_]+)`", listed))
    assert codes <= set(legacy_map.REMEDIES), codes - set(legacy_map.REMEDIES)
    assert {"renamed", "missing", "dashboard_parent_moved", "unresolved_name", "read_failed", "foreign_bound"} <= codes

    blockers = runbook[runbook.index("## Blockers on main") : runbook.index("## Decisions taken")]
    assert "**The owner runs a read-only preflight of the bind plan (step 6c).**" in blockers
    assert "step 3b is done, step 6c's preflight is clear" in blockers
    glance = runbook[runbook.index("## Steps at a glance") : runbook.index("## Step 1. Freeze and snapshot")]
    assert "| 6c | Owner preflight" in glance and "step 6's two answers, step 3b and step 6c (it starts the 14-day sequence)" in glance
    step_7 = runbook[runbook.index("## Step 7. Unity Catalog ownership handover") : runbook.index("## Step 8.")]
    preconditions = step_7[: step_7.index("**The rollback-scope check (read-only).**")]
    assert "- Step 6c's preflight ends `verdict: clear`, and prints the profile ack plan #1 printed." in preconditions
    adopt_step = runbook[runbook.index("**10B-1, read-only: the adopt plans.**") : runbook.index("**10B-2, the adoption.**")]
    assert "The adopt plan's sha256 is expected to equal step 6c's preflight digest" in adopt_step

    deployment = _flat("docs/deployment.md")
    paragraph = deployment[deployment.index("**The owner's preflight (read-only, before the handovers).**") :]
    paragraph = paragraph[: paragraph.index("**Owed live confirmations.**")]
    for words in (
        "a client target that is not production mode or that sets a name prefix, a top-level `include`, target-level `variables`",
        "exits 0 only with `verdict: clear`",
        "prints as `owner_attestation=`",
        "is no acknowledgement",
    ):
        assert words in paragraph, words
    onboarding = _flat("docs/clients/onboarding.md")
    assert "the read-only adopt preflight (`tools.databricks.adopt_preflight`; first-client-cutover.md, step 6c)" in onboarding
