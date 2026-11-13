"""Every command the client runbooks document parses against the tool it names (M2-PR9).

The runbooks under ``docs/clients/`` (onboarding, the first-client cutover and
the review modes) and the owner break-glass block in docs/deployment.md are
read, their ``<placeholders>`` filled with synthetic values, and each command
is checked against the real thing it names, offline:

* ``python -m <module> ...``: the module's own argparse, through its own
  ``main``. A tool whose ``main`` takes its dependencies as arguments is
  handed factories that stop the run at the first one, so its post-parse
  rules run too: the ``parser.error`` rules, and for the two human-run
  bootstrap tools (``bootstrap_account``, ``github_environment``) the
  action-flag refusals they apply before reading any input, run with a
  terminal on stdin and no CI marker. Any other tool stops right after
  ``parse_args`` returns. Either way no Databricks, GitHub or file call is made.
* ``./scripts/deploy.sh ...``: the script's own argument loop (the arguments,
  then ``--help``, which exits inside the loop before any call), plus the
  rules after the loop: target ``client``, a known phase, and, in a block a
  reader runs, ``--dry-run`` with every ``--phase``, because a live phase is
  CI-only.
* ``gh workflow run <workflow> ...``: the workflow's ``workflow_dispatch``
  inputs, choices and booleans, and for ``deploy-dev.yml`` the dispatch
  preflight's own rules (the break-glass, the profile ack on a mutating
  phase and on the two marker phases, ``freeze`` and ``thaw``,
  ``operation_acks`` through ``dispatch_acks.operation_exports``, the
  release-only expected deployment).
* ``gh variable set`` and ``gh secret set``: an environment-scoped write of a
  profile field of the right class, a secret never on the command line.
* ``databricks ...``: one of the reviewed owner commands (start the App, a
  read, or the owner's own short-lived Lakebase token), always naming the
  owner's CLI profile; never a deploy or a stop.
* ``PGPASSWORD="$(databricks ...)" ... psql ...``: the legacy owner's Lakebase
  connection, with its values local to ``psql`` and the token from the owner's
  CLI (a reviewed ``databricks`` command whose body fields the SDK's call
  names), never one of the ``LAKEBASE_*`` overrides ``lakebase_handover``
  refuses and never a token on the command line.
* ``make <target>``: a Makefile target.

Anything else in a ``bash`` block fails: a command the docs show must be one
this file knows how to check. The same docs hold no client identifier: the
ratchet's shapes (tests/unit/test_no_client_identifiers_committed.py) find
none in them, with a positive control.
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import io
import json
import random
import re
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from tests.unit.test_no_client_identifiers_committed import shape_hits
from tools.clients import dispatch_acks
from tools.clients.errors import ProfileRefusal
from tools.clients.schema import FIELD_CLASS

ROOT = Path(__file__).resolve().parents[2]
CLIENT_DOCS = (
    "docs/clients/onboarding.md",
    "docs/clients/first-client-cutover.md",
    "docs/clients/review-modes.md",
)
DEPLOYMENT_DOC = "docs/deployment.md"
BREAK_GLASS_HEADING = "### Owner break-glass: a raw `apps start`"
COMMAND_OF_RECORD_DOCS = ("README.md", "CLAUDE.md", "AGENTS.md", DEPLOYMENT_DOC, *CLIENT_DOCS)

_DIGEST = "c0ffee" + "a" * 58
_SHA = "1234567" + "b" * 33
PLACEHOLDERS: dict[str, str] = {
    "client": "acme",
    "owner-profile": "owner-profile",
    "app-name": "acme-app",
    "app-rollback-scope": "acme-app-rollback",
    "sha256": _DIGEST,
    "profile-ack": _DIGEST,
    "sha": _SHA,
    "epoch": "4102444800",
    "phase": "plan",
    "legacy-owner-email": "owner@lender.example",
    "owner-user-name": "owner@lender.example",
    "genie-space-title": "Lender Mortgage Lead Intelligence",
    "lifecycle-job-id": "4242",
    "deployment-id": "deployment-placeholder",
    "child-a": "artifacts",
    "child-b": "files",
    "lakebase-instance": "acme-app-state",
    "lakebase-database": "acme_app",
    "read-write-dns": "instance.database.example",
}
PRIVATE_DIR = "private-dir"

_PLACEHOLDER = re.compile(r"<([a-z0-9-]+)>")
_ENV_PREFIX = re.compile(r"[A-Z_][A-Z0-9_]*=\S*")
# The only environment a documented command may set: the owner's CLI profile.
ALLOWED_ENV = frozenset({"DATABRICKS_CONFIG_PROFILE"})
DEPLOY_PHASES = frozenset({"plan", "release", "data", "adopt", "unadopt"})
# The reviewed owner CLI commands: the raw start (break-glass, cutover step 10A), reads,
# and the owner's own short-lived Lakebase token (cutover step 8's psql connection).
DATABRICKS_COMMANDS = frozenset(
    {
        ("apps", "start"),
        ("apps", "get"),
        ("apps", "list-deployments"),
        ("apps", "get-deployment"),
        ("database", "get-database-instance"),
        ("database", "generate-database-credential"),
    }
)
# The legacy owner's psql connection: values local to psql, never a LAKEBASE_* override.
PSQL_ENV = frozenset({"PGSSLMODE"})
PSQL_FLAGS = frozenset({"-h", "-U", "-d"})
_PSQL = re.compile(r'PGPASSWORD="\$\((?P<token>databricks .+?)\)" (?P<rest>.+)')


# --------------------------------------------------------------- reading the docs


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _bash_blocks(text: str) -> list[str]:
    return re.findall(r"```bash\n(.*?)\n```", text, flags=re.DOTALL)


def _block_commands(block: str) -> list[str]:
    joined = block.replace("\\\n", " ")
    return [line.strip() for line in joined.splitlines() if line.strip() and not line.strip().startswith("#")]


def break_glass_block() -> str:
    text = _read(DEPLOYMENT_DOC)
    section = text[text.index(BREAK_GLASS_HEADING) :]
    return _bash_blocks(section)[0]


def documented_commands() -> list[tuple[str, str]]:
    """(source, command) for every command line of every bash block the runbooks show."""

    found = [(doc, line) for doc in CLIENT_DOCS for block in _bash_blocks(_read(doc)) for line in _block_commands(block)]
    found += [(f"{DEPLOYMENT_DOC} (break-glass)", line) for line in _block_commands(break_glass_block())]
    return found


_RUNNABLE_PREFIX = re.compile(r"^(?:python -m |\./scripts/deploy\.sh |make |gh workflow run |databricks )")


def inline_commands() -> list[tuple[str, str]]:
    """(source, command) for every inline code span in the runbooks that is a whole command."""

    found: list[tuple[str, str]] = []
    for doc in CLIENT_DOCS:
        prose = re.sub(r"```.*?```", "", _read(doc), flags=re.DOTALL)
        for span in re.findall(r"`([^`]+)`", prose):
            span = " ".join(span.split())
            if _RUNNABLE_PREFIX.match(span) and len(span.split()) > 2:
                found.append((doc, span))
    return found


def _fill(command: str, private_dir: Path) -> str:
    def value(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == PRIVATE_DIR:
            return str(private_dir)
        assert name in PLACEHOLDERS, f"add a synthetic value for <{name}> to PLACEHOLDERS"
        return PLACEHOLDERS[name]

    return _PLACEHOLDER.sub(value, command)


def _split(command: str, private_dir: Path) -> tuple[dict[str, str], list[str], str]:
    """(env prefix, argv, piped tail) of one documented command, placeholders filled."""

    head, _, tail = command.partition(" | ")
    tokens = shlex.split(_fill(head, private_dir), comments=True)
    env: dict[str, str] = {}
    while tokens and _ENV_PREFIX.fullmatch(tokens[0]):
        name, value = tokens.pop(0).split("=", 1)
        env[name] = value
    return env, tokens, tail


# --------------------------------------------------------------- the python tools


class _Stopped(BaseException):
    """The run reached its first dependency, or parse_args returned: the command parsed."""


def _stop(*_args: Any, **_kwargs: Any) -> Any:
    raise _Stopped


def _parse_only(module: str) -> Callable[[list[str], pytest.MonkeyPatch], None]:
    def run(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
        original = argparse.ArgumentParser.parse_args

        def parse(self: argparse.ArgumentParser, args: Any = None, namespace: Any = None) -> Any:
            original(self, args, namespace)
            raise _Stopped

        monkeypatch.setattr(argparse.ArgumentParser, "parse_args", parse)
        importlib.import_module(module).main(argv)

    return run


def _injected(module: str, **factories: Callable[..., Any]) -> Callable[[list[str], pytest.MonkeyPatch], None]:
    def run(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
        importlib.import_module(module).main(argv, **factories)

    return run


class _Terminal(io.StringIO):
    """A stdin that is a terminal, so the human-presence check passes."""

    def isatty(self) -> bool:
        return True


def _human_run(module: str, first_input: str) -> Callable[[list[str], pytest.MonkeyPatch], None]:
    """Run a human-run tool's ``main`` with a terminal and no CI marker, stopped at its first input read.

    Its action-flag refusals (``--sink`` or ``--for`` outside their actions, an
    ``--ack-plan`` the action never takes) all run before ``first_input``, the
    call that would read the shell and ``.env.local``; every later dependency
    (a login, the account, GitHub) is a stop as well.
    """

    def run(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
        tool = importlib.import_module(module)
        monkeypatch.setattr(tool, first_input, _stop)
        stderr = io.StringIO()
        factories: dict[str, Any] = {"open_session": _stop}
        if hasattr(tool.Dependencies, "open_github_sink"):
            factories["open_github_sink"] = _stop
        deps = tool.Dependencies(
            environ={},
            stdin=_Terminal(),
            stdout=io.StringIO(),
            stderr=stderr,
            infer_repository=lambda: "owner/repo",
            **factories,
        )
        code = tool.main(argv, deps=deps)
        raise AssertionError(f"{module} {' '.join(argv)}: refused after parsing (exit {code}): {stderr.getvalue()}")

    return run


def _estate(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    import databricks.sdk

    cli = importlib.import_module("tools.databricks.estate.cli")
    monkeypatch.setattr(cli, "load_policy", _stop)
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", _stop)
    cli.main(argv)


def _rollback_scope_check(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """The owner's rollback-scope check (cutover step 7): ``assert`` reads; ``ensure`` creates and marks a scope."""

    assert argv[:1] == ["assert"], f"a runbook shows the rollback scope's read-only assert mode only: {argv}"
    _parse_only("tools.databricks.app_rollback_secret_scope")(argv, monkeypatch)


# Each tool a runbook names, and how its run is stopped before any side effect.
RUNNERS: dict[str, Callable[[list[str], pytest.MonkeyPatch], None]] = {
    "tools.clients": _parse_only("tools.clients.__main__"),
    "tools.clients.bootstrap_account": _human_run("tools.clients.bootstrap_account", "_request"),
    "tools.clients.github_environment": _human_run("tools.clients.github_environment", "load_inputs"),
    "tools.clients.handover_uc_ownership": _injected(
        "tools.clients.handover_uc_ownership", install=_stop, workspace_factory=_stop
    ),
    "tools.clients.lakebase_handover": _injected("tools.clients.lakebase_handover", session=_stop),
    "tools.clients.handover_verify": _injected("tools.clients.handover_verify", workspace_factory=_stop, catalog=_stop),
    "tools.clients.archive_legacy_root": _injected(
        "tools.clients.archive_legacy_root", roots=_stop, workspace_factory=_stop
    ),
    "tools.clients.estate_inventory": _parse_only("tools.clients.estate_inventory"),
    "tools.clients.genie_pin_review": _injected("tools.clients.genie_pin_review", workspace_factory=_stop),
    "tools.databricks.estate.cli": _estate,
    "tools.databricks.app_deployment_lease": _parse_only("tools.databricks.app_deployment_lease"),
    "tools.databricks.app_rollback_secret_scope": _rollback_scope_check,
    "tools.databricks.quiesce_window": _injected("tools.databricks.quiesce_window", workspace_factory=_stop),
    "tools.databricks.metastore_app_grants": _injected(
        "tools.databricks.metastore_app_grants", workspace_factory=_stop
    ),
    "tools.live_validation_gate": _parse_only("tools.live_validation_gate"),
}


def _check_python(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    assert argv[:2] == ["python", "-m"], argv
    module, args = argv[2], argv[3:]
    assert module in RUNNERS, f"{module}: add a runner that stops it before any side effect"
    try:
        RUNNERS[module](args, monkeypatch)
    except _Stopped:
        return
    except SystemExit as exc:
        raise AssertionError(f"{module} {' '.join(args)}: refused by its argparse (exit {exc.code})") from exc
    raise AssertionError(f"{module} {' '.join(args)}: returned before reaching a dependency")


# --------------------------------------------------------------- scripts/deploy.sh


def _check_deploy_sh(argv: list[str], *, runnable: bool) -> None:
    assert argv[0] == "./scripts/deploy.sh", argv
    args = argv[1:]
    result = subprocess.run(
        ["bash", "scripts/deploy.sh", *args, "--help"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0 and "Usage" in result.stdout, (args, result.stderr[-2000:])
    target = args[args.index("-t") + 1] if "-t" in args else "client"
    assert target == "client", args
    if "--phase" in args:
        assert args[args.index("--phase") + 1] in DEPLOY_PHASES, args
        if runnable:
            assert "--dry-run" in args, f"a live --phase is CI-only; a runbook block shows it with --dry-run: {args}"


def test_a_live_phase_outside_ci_is_refused_by_the_script() -> None:
    """The dry-run rule above is the script's own: it refuses a live phase outside GitHub Actions."""

    script = _read("scripts/deploy.sh")
    assert 'is CI-only; use --dry-run or make bundle-validate' in script
    assert '"$PHASE_SEEN" -eq 1 && "$DRY_RUN" -eq 0 && "${GITHUB_ACTIONS:-}" != "true"' in script


# --------------------------------------------------------------- gh workflow run


def _workflow_inputs(workflow: str) -> dict[str, dict[str, Any]]:
    path = ROOT / ".github" / "workflows" / workflow
    assert path.is_file(), workflow
    document = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    return document["on"]["workflow_dispatch"]["inputs"]


def _check_gh_workflow_run(argv: list[str]) -> None:
    assert argv[:3] == ["gh", "workflow", "run"], argv
    workflow, rest = argv[3], argv[4:]
    inputs = _workflow_inputs(workflow)
    assert rest[:2] == ["--ref", "main"], f"{workflow}: dispatch on main only: {rest}"
    fields: dict[str, str] = {}
    pairs = rest[2:]
    assert len(pairs) % 2 == 0, pairs
    for flag, pair in zip(pairs[::2], pairs[1::2], strict=True):
        assert flag == "-f", pairs
        name, _, value = pair.partition("=")
        assert name in inputs, f"{workflow} has no input {name}: gh refuses the dispatch"
        assert name not in fields, f"{name} given twice"
        fields[name] = value
    for name, spec in inputs.items():
        if spec.get("required") == "true":
            assert name in fields, f"{workflow}: required input {name} missing"
        if name in fields and spec.get("type") == "choice":
            assert fields[name] in spec["options"], (name, fields[name])
        if name in fields and spec.get("type") == "boolean":
            assert fields[name] in {"true", "false"}, (name, fields[name])
    if workflow == "deploy-dev.yml":
        assert fields.get("acknowledge_single_maintainer_break_glass") == "true", "or the run ends in refuse-unacknowledged"
        phase = fields.get("phase", inputs["phase"]["default"])
        # The preflight's own rule (Verdict.mutating): the deploy.sh phases that mutate, and the
        # two marker phases, whose step writes the freeze marker (wave 7c).
        if phase in dispatch_acks.MUTATING_PHASES | dispatch_acks.MARKER_PHASES:
            assert fields.get("owner_attestation"), f"phase {phase} needs the profile ack (owner_attestation)"
        dispatch_acks.operation_exports(fields.get("operation_acks", ""), phase)
        if fields.get("expected_active_deployment_sha256"):
            assert phase == "release", "expected_active_deployment_sha256 is read by --phase release only"


# --------------------------------------------------------------- gh variable / secret set


def _check_gh_environment_write(argv: list[str]) -> None:
    assert argv[:2] in (["gh", "variable"], ["gh", "secret"]) and argv[2] == "set", argv
    kind, name, rest = argv[1], argv[3], argv[4:]
    assert "--env" in rest, f"{name}: an environment-scoped write only, never repository level"
    assert "--repo" not in rest, argv
    assert name in FIELD_CLASS, f"{name} is no client profile field"
    field_class = getattr(FIELD_CLASS[name], "value", FIELD_CLASS[name])
    if kind == "variable":
        assert field_class == "public", f"{name} is {field_class}: a secret, never a variable"
    else:
        assert field_class in {"identifying", "secret"}, f"{name} is public: a variable"
        assert "--body" not in rest and "-b" not in rest, f"{name}: a secret's value comes from the terminal"


# --------------------------------------------------------------- databricks, make


def _check_databricks(argv: list[str], tail: str) -> None:
    assert argv[0] == "databricks", argv
    assert tuple(argv[1:3]) in DATABRICKS_COMMANDS, f"not a reviewed owner command: {argv[:3]}"
    assert "-p" in argv and argv[argv.index("-p") + 1] == PLACEHOLDERS["owner-profile"], argv
    assert not tail or tail.startswith("jq "), tail


def _check_psql(command: str, private_dir: Path) -> None:
    """The legacy owner's connection: the token from the owner's CLI, every other value local to psql."""

    assert "LAKEBASE_" not in command, f"a LAKEBASE_* override makes lakebase_handover refuse: {command}"
    match = _PSQL.fullmatch(command)
    assert match is not None, f"psql takes its token from the owner's CLI, never the command line: {command}"
    env, argv, tail = _split(match["token"], private_dir)
    assert not env, env
    _check_databricks(argv, tail)
    assert tuple(argv[1:3]) == ("database", "generate-database-credential") and tail == "jq -r .token", argv
    from databricks.sdk.service.database import DatabaseAPI

    body = json.loads(argv[argv.index("--json") + 1])
    accepted = set(inspect.signature(DatabaseAPI.generate_database_credential).parameters) - {"self"}
    assert set(body) <= accepted and len(body.get("instance_names", [])) == 1, body
    env, argv, tail = _split(match["rest"], private_dir)
    assert set(env) <= PSQL_ENV and not tail, (env, tail)
    assert argv[0] == "psql", argv
    flags = argv[1:]
    assert len(flags) % 2 == 0 and set(flags[::2]) <= PSQL_FLAGS, flags


def _check_make(argv: list[str]) -> None:
    assert argv[0] == "make" and len(argv) == 2, argv
    assert re.search(rf"(?m)^{re.escape(argv[1])}:", _read("Makefile")), f"no Makefile target {argv[1]}"


def _check(command: str, private_dir: Path, monkeypatch: pytest.MonkeyPatch, *, runnable: bool) -> None:
    if command.startswith("PG") or re.search(r"\bpsql\b", command):
        _check_psql(command, private_dir)
        return
    env, argv, tail = _split(command, private_dir)
    assert set(env) <= ALLOWED_ENV, env
    assert argv, command
    if argv[0] != "databricks":
        assert not tail, f"only a databricks read pipes into jq: {command}"
    if argv[0] == "python":
        _check_python(argv, monkeypatch)
    elif argv[0] == "./scripts/deploy.sh":
        _check_deploy_sh(argv, runnable=runnable)
    elif argv[:3] == ["gh", "workflow", "run"]:
        _check_gh_workflow_run(argv)
    elif argv[0] == "gh":
        _check_gh_environment_write(argv)
    elif argv[0] == "databricks":
        _check_databricks(argv, tail)
    elif argv[0] == "make":
        _check_make(argv)
    else:
        raise AssertionError(f"a runbook command this test cannot check: {command}")


@pytest.fixture
def private_dir(tmp_path: Path) -> Path:
    path = tmp_path / "private"
    path.mkdir(mode=0o700)
    return path


@pytest.mark.parametrize(("source", "command"), documented_commands(), ids=lambda value: value[:60])
def test_every_runbook_command_parses(
    source: str, command: str, private_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _check(command, private_dir, monkeypatch, runnable=True)


@pytest.mark.parametrize(("source", "command"), inline_commands(), ids=lambda value: value[:60])
def test_every_inline_command_parses(
    source: str, command: str, private_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _check(command, private_dir, monkeypatch, runnable=False)


def test_the_runbooks_show_each_kind_of_command() -> None:
    """Non-vacuity: the parsers above all run against something."""

    commands = [command for _, command in documented_commands()]
    for prefix in ("python -m tools.clients", "python -m tools.databricks", "./scripts/deploy.sh", "gh workflow run deploy-dev.yml", "gh workflow run nightly.yml", "gh secret set", "databricks apps start", " psql ", "make "):
        assert any(prefix in command for command in commands), prefix


def test_the_command_of_record_parses_wherever_it_is_named() -> None:
    """Every ``gh workflow run deploy-dev.yml`` the operator docs name is a dispatch the workflow accepts."""

    seen = 0
    for doc in COMMAND_OF_RECORD_DOCS:
        for match in re.finditer(r"gh workflow run deploy-dev\.yml[^`\n]*", _read(doc)):
            command = match.group(0).split("  #", 1)[0].strip()
            if " -f " not in command:
                continue  # a mention of the workflow, not a dispatch
            _env, argv, _tail = _split(command, ROOT)
            _check_gh_workflow_run(argv)
            seen += 1
    assert seen >= 10


def test_the_parse_check_refuses_a_flag_the_tool_lacks(private_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Positive controls: an unknown flag, a missing required pair and an unreviewed ack all fail."""

    bad = (
        "python -m tools.clients.handover_uc_ownership --plan --dry-run",
        "python -m tools.clients.handover_uc_ownership --apply --ack-plan <sha256>",
        "python -m tools.clients.bootstrap_account --plan --sink nowhere",
        "python -m tools.clients.bootstrap_account --plan --sink github-environment --interim-member <legacy-owner-email>",
        "python -m tools.clients.bootstrap_account --apply --for rotate-adopted --ack-plan <sha256>",
        "python -m tools.clients.github_environment --plan --ack-plan <sha256>",
        "python -m tools.clients.archive_legacy_root --only <child-a> --apply",
        # The rollback-scope check is the read-only mode, with both names.
        "python -m tools.databricks.app_rollback_secret_scope ensure --app-name <app-name> --scope <app-rollback-scope>",
        "python -m tools.databricks.app_rollback_secret_scope assert --app-name <app-name>",
        "PGHOST=<read-write-dns> PGPASSWORD=token psql -U <legacy-owner-email>",
        "LAKEBASE_HOST=<read-write-dns> psql -U <legacy-owner-email>",
        'PGPASSWORD="$(databricks database generate-database-credential --json \'{"instance_names":["<lakebase-instance>"],"no_such_field":"x"}\' -p <owner-profile> -o json | jq -r .token)" psql -h <read-write-dns>',
        "./scripts/deploy.sh -t client --phase release",
        "./scripts/deploy.sh -t client --phase plan --dry-run --no-such-flag",
        # operation_acks the carrier refuses (B2/B4/B6 admitted adopt_live in adopt, so these replace that control).
        "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=unadopt -f owner_attestation=<profile-ack> -f operation_acks=adopt_live=<sha256> -f acknowledge_single_maintainer_break_glass=true",
        "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=adopt -f owner_attestation=<profile-ack> -f operation_acks=quiesce_window=<sha256> -f acknowledge_single_maintainer_break_glass=true",
        "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=adopt -f owner_attestation=<profile-ack> -f operation_acks=supersedes_deployment=yes -f acknowledge_single_maintainer_break_glass=true",
        "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=data -f owner_attestation=<profile-ack> -f operation_acks=genie_pin=<sha256> -f acknowledge_single_maintainer_break_glass=true",
        "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=adopt -f owner_attestation=<profile-ack> -f operation_acks=zyrplax=<sha256> -f acknowledge_single_maintainer_break_glass=true",
        "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=adopt -f owner_attestation=<profile-ack> -f expected_active_deployment_sha256=<sha256> -f acknowledge_single_maintainer_break_glass=true",
        "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=release -f acknowledge_single_maintainer_break_glass=true",
        # The marker phases (wave 7c): each needs the profile ack; freeze_thaw rides a thaw only,
        # and no deploy.sh ack rides either.
        "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=freeze -f acknowledge_single_maintainer_break_glass=true",
        "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=thaw -f operation_acks=freeze_thaw=<sha256> -f acknowledge_single_maintainer_break_glass=true",
        "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=freeze -f owner_attestation=<profile-ack> -f operation_acks=freeze_thaw=<sha256> -f acknowledge_single_maintainer_break_glass=true",
        "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=release -f owner_attestation=<profile-ack> -f operation_acks=freeze_thaw=<sha256> -f acknowledge_single_maintainer_break_glass=true",
        "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=plan -f operation_acks=freeze_thaw=<sha256> -f acknowledge_single_maintainer_break_glass=true",
        "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=thaw -f owner_attestation=<profile-ack> -f operation_acks=metastore_app_grants=<sha256> -f acknowledge_single_maintainer_break_glass=true",
        "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=thaw -f owner_attestation=<profile-ack> -f operation_acks=freeze_thaw=yes -f acknowledge_single_maintainer_break_glass=true",
        "gh workflow run deploy-dev.yml --ref main -f client=<client> -f phase=unfreeze -f owner_attestation=<profile-ack> -f acknowledge_single_maintainer_break_glass=true",
        "./scripts/deploy.sh -t client --phase freeze --dry-run",
        "gh secret set MIP_CLIENT --env <client>-validate",
        "databricks apps deploy <app-name> -p <owner-profile>",
    )
    for command in bad:
        with pytest.raises((AssertionError, ProfileRefusal)):
            _check(command, private_dir, monkeypatch, runnable=True)


# --------------------------------------------------------------- what the docs say


def test_the_runbooks_hold_no_client_identifier() -> None:
    """The ratchet's eight shapes find nothing in the runbooks; a planted id is found (positive control)."""

    rng = random.Random(9)
    planted = "".join(rng.choice("0123456789abcdef") for _ in range(32))
    control = f"{planted[:8]}-{planted[8:12]}-4{planted[13:16]}-9{planted[17:20]}-{planted[20:]}"
    for doc in (*CLIENT_DOCS, DEPLOYMENT_DOC):
        data = _read(doc).encode("utf-8")
        assert shape_hits(data, doc) == [], doc
        assert shape_hits(data + b"\n" + control.encode(), doc), "the shapes must find a planted id"


def test_no_runbook_shows_a_deploy_or_a_stop() -> None:
    for doc in CLIENT_DOCS:
        text = _read(doc)
        assert not re.search(r"databricks\s+(?:apps\s+(?:deploy|stop|update)|bundle\s+(?:deploy|destroy))\b", text), doc


def test_the_runbooks_say_hardware_key_attestation_is_deferred_and_claim_no_signature_check() -> None:
    for doc in CLIENT_DOCS:
        flat = " ".join(_read(doc).split())
        assert "M1-PR5" in flat and "deferred" in flat.lower(), doc
        for claim in ("signature is verified", "verifies the owner's signature", "verifies the signature", "signed by the owner's hardware key"):
            assert claim not in flat, (doc, claim)
    review = " ".join(_read("docs/clients/review-modes.md").split())
    assert "Single-maintainer approval is not human-only review." in review
    assert "Nothing verifies a signature anywhere in a governed run." in review


def test_the_cutover_runbook_keeps_the_decisions_of_record() -> None:
    flat = " ".join(_read("docs/clients/first-client-cutover.md").split())
    for anchor in (
        "never transfer metastore ownership",
        "MIP_SEED_OPERATOR_EMAIL",
        "--verify --released",
        "MIP_UC_INTERIM_OWNER_PAIRS_JSON",
        "Never hand-write `GRANT USAGE ON SCHEMA public`",
        "target_will_not_own_database",
        "Dashboards stay pinned",
        "**Step 3 is done** (the owner, 2026-11-06)",
        "adopt-live signs only a **running** App",
        "MIP_SALESFORCE_BINDINGS",
    ):
        assert anchor in flat, anchor
