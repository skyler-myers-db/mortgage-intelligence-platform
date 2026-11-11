"""The second-client portability proof's harness (M3-PR4): acme, offline, with recording stubs.

``acme`` (tests/fixtures/clients/acme.env) is a synthetic customer install of
another lender: prod App env, the agentic tier off, the weekly refresh off and
the Cotality share mounted as ``cotality_prod.mortgage`` with unprefixed
tables. Its GitHub Environment is the file plus what only the sandbox creates
(``acme_values``): the secret-class values and the lender record's exact-digest
pin. The owner hardware-key attestation layer is deferred (M1-PR5), so the
dispatch acks are the exact digests main has (``release_inputs``): the
``profile`` ack over the profile digest, the accepted-exposure plan ack and the
protections_unverified ack. Nothing here signs or pretends to verify a
signature.

``dispatch`` replays one deploy-dev.yml dispatch (tests/fixtures/dispatch_replay.py)
in a sandbox clone whose hook (``prepare``) removes the fixture from the
clone, since a client's profile lives in its environment and never in the
tree that deploys it, applies any ``Plant`` (the positive controls), and puts
two recorders in place:

* the role-recording stub CLI (tests/fixtures/role_recording_cli.py) as
  ``databricks``;
* a deploy interpreter (``MIP_DEPLOY_PYTHON``) that runs the real one,
  records every call's arguments (and the script, when Python reads it from
  stdin: a heredoc) for tests/fixtures/role_ledger.py to class, and keeps a
  copy of every App deployment payload ``app_deploy_payload`` emits, which
  the deploy deletes on exit.

``validate`` then executes, in that same clone, the ``bundle_env validate -t
client`` a dry run only plans, against the stub CLI. The checks that read the
results live in tests/fixtures/portability_checks.py.
"""

from __future__ import annotations

import base64
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

from backend.schemas.lender_identity_record import lender_identity_digest
from tests.fixtures.client_profiles import (
    IDENTITY_IDS,
    env_file_text,
    paychex_shaped_profile,
    split_contexts,
)
from tests.fixtures.deploy_sandbox import ANSI, planned_commands
from tests.fixtures.dispatch_replay import Job, Replay, replay, run
from tests.fixtures.role_ledger import Execution
from tools.clients import dispatch_acks
from tools.clients import profile as views
from tools.clients.ci_profile import env_local_text
from tools.clients.environment import from_github, resolve
from tools.clients.schema import FIELD_CLASS, ClientProfile, FieldClass
from tools.databricks.estate.accepted_exposure import (
    EXPOSURE_ENV,
    GOVERNED_SPACE_NAME_ENV,
    load_policy,
)

REPO = Path(__file__).resolve().parents[2]
ACME_FIXTURE = "tests/fixtures/clients/acme.env"
CLIENT = "acme"
LENDER_ENV = "MIP_LENDER_IDENTITY_JSON"
PIN_ENV = "MIP_LENDER_IDENTITY_SHA256"
CLIENT_PHASE = "Run the client phase"
# The secret class never lives in the repository, synthetic or not: the
# sandbox makes these. The proof signing key is an Ed25519 seed.
SANDBOX_SECRETS = {
    "DATABRICKS_CLIENT_SECRET": "acme-normal-secret-EXAMPLE",
    "DATABRICKS_OPERATOR2_CLIENT_SECRET": "acme-operator2-secret-EXAMPLE",
    "DATABRICKS_ADMIN_CLIENT_SECRET": "acme-admin-secret-EXAMPLE",
    "DATABRICKS_RELEASE_PROBE_CLIENT_SECRET": "acme-release-probe-secret-EXAMPLE",
    "DATABRICKS_ACCOUNT_CLIENT_SECRET": "acme-account-secret-EXAMPLE",
    "MIP_AI_GATEWAY_PROOF_SIGNING_KEY": base64.urlsafe_b64encode(bytes(range(96, 128))).decode("ascii").rstrip("="),
}
# Each call is one file: its argument count and arguments, NUL-separated, and
# beside it the script when Python reads one from stdin (``-``, or no script
# after the flags). A flag that takes a value leaves the stdin unread here,
# and role_ledger classes that call unclassified.
_INTERPRETER = """#!/usr/bin/env bash
call="$(mktemp "$PROOF_PY_CALLS/call.XXXXXX")"
printf '%s\\0' "$#" "$@" > "$call"
from_stdin=1
for arg in "$@"; do
  case "$arg" in
    -) break ;;
    -c|-m) from_stdin=0; break ;;
    -*) ;;
    *) from_stdin=0; break ;;
  esac
done
if [[ "$from_stdin" -eq 1 ]]; then
  cat > "$call.stdin"
  exec < "$call.stdin"
fi
if [[ "${1:-}" == "-m" && "${2:-}" == "tools.databricks.app_deploy_payload" ]]; then
  captured="$(mktemp "$PROOF_PAYLOAD_DIR/payload.XXXXXX")"
  set +e
  PYTHON_BIN "$@" > "$captured"
  status=$?
  set -e
  cat "$captured"
  exit "$status"
fi
exec PYTHON_BIN "$@"
"""


def fixture_values() -> dict[str, str]:
    """acme.env as committed: every public and identifying field, no secret and no pin."""

    return {name: value for name, value in dotenv_values(REPO / ACME_FIXTURE).items() if value is not None}


def acme_values(**overrides: str) -> dict[str, str]:
    """acme's GitHub Environment: the fixture, the sandbox's secrets and the record's pin.

    An override replaces a field before the pin is computed, so a tampered
    record is pinned only when the caller passes the pin itself.
    """

    values = {**fixture_values(), **SANDBOX_SECRETS, **overrides}
    values.setdefault(PIN_ENV, lender_identity_digest(values[LENDER_ENV]))
    return values


def profile(values: Mapping[str, str]) -> ClientProfile:
    variables, secrets = split_contexts(dict(values))
    return resolve(from_github(variables, secrets, environment=CLIENT))


def exposure_ack(values: Mapping[str, str]) -> str:
    """The exact MIP_ACK_ACCEPTED_EXPOSURE_PLAN for the profile's accepted exposure."""

    env = {EXPOSURE_ENV: values[EXPOSURE_ENV], GOVERNED_SPACE_NAME_ENV: values[GOVERNED_SPACE_NAME_ENV]}
    return load_policy(env, require_ack=False).digest


def release_inputs(values: Mapping[str, str]) -> Callable[[str], dict[str, Any]]:
    """The dispatch inputs of an acked ``phase: release``, for the replay (it passes the head sha)."""

    resolved = profile(values)

    def inputs(sha: str) -> dict[str, Any]:
        return {
            "phase": "release",
            "owner_attestation": dispatch_acks.profile_ack(CLIENT, views.digest(resolved)),
            "operation_acks": f"accepted_exposure_plan={exposure_ack(values)}",
            "protections_unverified_ack": dispatch_acks.protections_ack(CLIENT, CLIENT, sha),
        }

    return inputs


def identifying_values(values: Mapping[str, str]) -> list[str]:
    """Every value the public log must never show: identifying leaves, their spellings, secrets."""

    resolved = profile(values)
    secrets = [value for name, value in values.items() if FIELD_CLASS.get(name) is FieldClass.SECRET and value]
    return sorted({*views.masks(resolved), *views.derived_masks(resolved), *secrets}, key=len, reverse=True)


@dataclass(frozen=True)
class Plant:
    """A positive control: ``text`` inserted after the one line ``anchor`` in ``path`` (appended when ``None``)."""

    path: str
    anchor: str | None
    text: str


def _commit(checkout: Path, message: str) -> None:
    subprocess.run(["git", "add", "--all"], cwd=checkout, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Proof", "-c", "user.email=proof@example.invalid", "commit", "-qm", message],
        cwd=checkout,
        check=True,
    )


def _plant(checkout: Path, plant: Plant) -> None:
    target = checkout / plant.path
    text = target.read_text(encoding="utf-8")
    if plant.anchor is None:
        target.write_text(text + plant.text, encoding="utf-8")
        return
    assert text.count(plant.anchor) == 1, (plant.path, plant.anchor)
    target.write_text(text.replace(plant.anchor, plant.anchor + plant.text), encoding="utf-8")


def _executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)
    return path


@dataclass(frozen=True)
class Recorders:
    """Where the stubs write: the CLI's ledger, the interpreter's calls and the payload copies."""

    cli_ledger: Path
    py_calls: Path
    payload_dir: Path
    interpreter: Path

    def env(self) -> dict[str, str]:
        return {
            "PROOF_CLI_LEDGER": str(self.cli_ledger),
            "PROOF_PY_CALLS": str(self.py_calls),
            "PROOF_PAYLOAD_DIR": str(self.payload_dir),
            "MIP_DEPLOY_PYTHON": str(self.interpreter),
        }

    def cli_calls(self) -> list[dict[str, Any]]:
        if not self.cli_ledger.exists():
            return []
        return [json.loads(line) for line in self.cli_ledger.read_text(encoding="utf-8").splitlines() if line]

    def payloads(self) -> list[dict[str, Any]]:
        return [json.loads(path.read_text(encoding="utf-8")) for path in sorted(self.payload_dir.glob("payload.*"))]

    def executions(self) -> list[Execution]:
        """Every call the deploy interpreter really ran, in any shape: ``-m``, by path, ``-c`` or a heredoc."""

        calls = []
        for path in sorted(self.py_calls.glob("call.*")):
            if path.name.endswith(".stdin"):
                continue
            count, *argv = path.read_bytes().decode("utf-8", "surrogateescape").split("\0")
            assert len(argv) == int(count) + 1 and argv[-1] == "", path
            stdin = path.with_name(path.name + ".stdin")
            calls.append(Execution(tuple(argv[:-1]), stdin.read_text(encoding="utf-8") if stdin.exists() else None))
        return calls


def recorders(tmp_path: Path, name: str = "dry-run") -> Recorders:
    payload_dir, py_calls = tmp_path / f"{name}-payloads", tmp_path / f"{name}-python-calls"
    payload_dir.mkdir(exist_ok=True)
    py_calls.mkdir(exist_ok=True)
    return Recorders(tmp_path / f"{name}-cli.jsonl", py_calls, payload_dir, tmp_path / "deploy-python")


def prepare(record: Recorders, plants: Sequence[Plant] = ()) -> Callable[[Path, dict[str, str]], None]:
    """The replay's clone hook: no fixture in the tree, the plants, the two recorders."""

    def hook(checkout: Path, base_env: dict[str, str]) -> None:
        subprocess.run(["git", "rm", "--quiet", ACME_FIXTURE], cwd=checkout, check=True)
        for plant in plants:
            _plant(checkout, plant)
        _commit(checkout, "the client's profile lives in its environment")
        python = checkout / ".venv" / "bin" / "python"
        bin_dir = Path(base_env["PATH"].split(os.pathsep)[0])
        stub = checkout / "tests" / "fixtures" / "role_recording_cli.py"
        _executable(bin_dir / "databricks", f"#!/usr/bin/env bash\nexec {shlex.quote(str(python))} {shlex.quote(str(stub))} \"$@\"\n")
        _executable(record.interpreter, _INTERPRETER.replace("PYTHON_BIN", shlex.quote(str(python))))

    return hook


@dataclass(frozen=True)
class Dispatch:
    """One replayed dispatch: the replay, its recorders and the client phase's own transcript."""

    replay: Replay
    record: Recorders

    @property
    def deploy(self) -> Job:
        return self.replay.jobs["deploy"]

    @property
    def stdout(self) -> str:
        return ANSI.sub("", self.deploy.results[CLIENT_PHASE].stdout)

    @property
    def stderr(self) -> str:
        return ANSI.sub("", self.deploy.results[CLIENT_PHASE].stderr)

    @property
    def planned(self) -> list[str]:
        return planned_commands(self.deploy.results[CLIENT_PHASE].stdout)

    @property
    def summary(self) -> str:
        return self.replay.summary.read_text(encoding="utf-8") if self.replay.summary.exists() else ""

    def public_lines(self) -> list[str]:
        """Every header and output line of every job as the public log shows it."""

        return [line for job in self.replay.jobs.values() for _step, line in job.shown()]

    def failed_steps(self) -> dict[str, int]:
        return {
            f"{job.name} | {step}": result.returncode
            for job in self.replay.jobs.values()
            for step, result in job.results.items()
            if result.returncode != 0
        }


def dispatch(tmp_path: Path, values: Mapping[str, str], phase: str, plants: Sequence[Plant] = ()) -> Dispatch:
    """One deploy-dev.yml dispatch of acme for ``phase`` (``plan`` or an acked ``release``)."""

    record = recorders(tmp_path)
    inputs = release_inputs(values) if phase == "release" else {"phase": phase}
    replayed = replay(tmp_path, values=values, client=CLIENT, inputs=inputs, prepare=prepare(record, plants), runner_env=record.env())
    return Dispatch(replayed, record)


@dataclass(frozen=True)
class Validated:
    """One ``bundle_env validate -t client``: what the stub CLI saw and the tree it rendered."""

    calls: list[dict[str, Any]]
    rendered: Path


def validate(done: Dispatch, values: Mapping[str, str] | None, name: str) -> Validated:
    """``bundle_env validate -t client`` in ``done``'s clone, against the stub CLI.

    ``values`` is written as the CI profile writer writes .env.local; ``None``
    writes the sandbox's own client-#1-shaped profile (placeholders only).
    The rendered tree is copied out, since the next validate re-renders it.
    """

    checkout, runner = done.replay.checkout, dict(done.replay.runner)
    record = recorders(done.replay.checkout.parent, name)
    if values is not None:
        text = env_local_text(profile(values))
    else:
        text = env_file_text({**paychex_shaped_profile(), "DATABRICKS_WAREHOUSE_ID": "warehouse-1", "GENIE_SPACE_ID": "space-1"})
        # An identity alias must equal its MIP_IDENTITY_IDS_JSON role (the sandbox's own setting).
        runner["DATABRICKS_AGENT_RUNTIME_CLIENT_ID"] = IDENTITY_IDS["runtime"]
    (checkout / ".env.local").write_text(text, encoding="utf-8")
    try:
        result = run([str(checkout / ".venv" / "bin" / "python"), "-m", "tools.databricks.bundle_env", "validate", "-t", "client"],
                     cwd=checkout, env={**runner, **record.env()})
    finally:
        (checkout / ".env.local").unlink()
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    rendered = done.replay.checkout.parent / f"{name}-rendered"
    shutil.copytree(checkout / "sql" / "_rendered", rendered)
    return Validated(record.cli_calls(), rendered)


def contexts_files(tmp_path: Path, values: Mapping[str, str]) -> tuple[Path, Path]:
    """The vars and secrets objects as deploy-dev.yml's collect step writes them."""

    variables, secrets = split_contexts(dict(values))
    vars_file, secrets_file = tmp_path / "vars.json", tmp_path / "secrets.json"
    vars_file.write_text(json.dumps(variables), encoding="utf-8")
    secrets_file.write_text(json.dumps(secrets), encoding="utf-8")
    return vars_file, secrets_file


def clients_check(tmp_path: Path, values: Mapping[str, str]) -> subprocess.CompletedProcess[str]:
    """``python -m tools.clients check`` over acme's environment contexts, in an empty directory."""

    vars_file, secrets_file = contexts_files(tmp_path, values)
    argv = [sys.executable, "-P", "-m", "tools.clients", "check",
            "--vars-json", str(vars_file), "--secrets-json", str(secrets_file), "--environment", CLIENT]
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(REPO), "HOME": str(tmp_path)}
    return run(argv, cwd=tmp_path, env=env)


__all__ = [
    "ACME_FIXTURE",
    "CLIENT",
    "Dispatch",
    "Plant",
    "Recorders",
    "Validated",
    "acme_values",
    "clients_check",
    "contexts_files",
    "dispatch",
    "exposure_ack",
    "fixture_values",
    "identifying_values",
    "prepare",
    "profile",
    "recorders",
    "release_inputs",
    "validate",
]
