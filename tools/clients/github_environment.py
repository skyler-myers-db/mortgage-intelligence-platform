"""``python -m tools.clients.github_environment``: one client's GitHub Environments.

A human-run tool for the repository's maintainer (plan §4.10, M2-PR2). It
creates or converges two environments with the review mode's protections
(tools/clients/github_protections.py):

* ``<client>``, which the deploy job runs in, and
* ``<client>-validate``, the validator identity's federation subject.

Then it fills ``<client>`` from the local profile
(tools/clients/github_environment_plan.py):

* public fields as environment **variables**;
* identifying fields (hosts, ids, emails, principals) as environment
  **secrets**, never variables, set only where missing unless ``--rotate``;
* the Ed25519 signing keys, ``MIP_AI_GATEWAY_PROOF_SIGNING_KEY`` and, on the
  agentic tier, ``MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY``: generated in
  memory and piped straight into secrets on stdin, never on disk or in argv.
  Each is generated once; ``--rotate`` never replaces one;
* ``MIP_CLIENT_PROFILE_SHA256``, the profile digest, as a public variable.

**Never:** a runtime secret (the App's runtime secret scope keeps the mask
and action keys, presence-only, and none is read or imported here; attack
G10), an M2M or account-principal secret (``bootstrap_account
--rotate-adopted --sink github-environment`` hands those over), or
``MIP_IDENTITY_IDS_JSON`` (that sink's).

**Actions.** ``--plan`` (the default) is read-only: it prints the canonical,
public-safe plan and its sha256. ``--apply --ack-plan <sha256>`` recomputes
the plan read-only, refuses on any difference with nothing changed, then
executes it, re-plans and reports ``converged``. A second ``--apply`` makes
zero mutations.

**Deferred owner attestation.** The plan's owner hardware-key ``mint``
attestation (and the ``MIP_OWNER_PROFILE_ATTESTATION`` it would store) is
deferred; the review mode is ``single_maintainer_audited``. Its stand-in is
the exact plan-digest ack, the public profile digest variable, and human
presence: every action refuses under ``GITHUB_ACTIONS`` or ``CI`` and without
a terminal on stdin, before any input is read, and every run logs in to gh
fresh in a throwaway directory (tools/clients/github_session.py).

Output names environments, fields, steps and public values, never an
identifying value or a secret. Exit 0 on success, 1 when an apply did not
converge, 2 on a refusal.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tools.clients.bootstrap_session import assert_human_present
from tools.clients.errors import BootstrapRefusal, ProfileRefusal
from tools.clients.github_environment_plan import (
    EnvironmentInputs,
    Plan,
    Step,
    compute_plan,
    load_inputs,
    observe,
)
from tools.clients.github_protections import expected
from tools.clients.github_session import REPOSITORY, GhSession, fresh_gh_session

REPO = Path(__file__).resolve().parents[2]
_DIGEST = re.compile(r"[0-9a-f]{64}")


class StepFailure(RuntimeError):
    """A mutation failed; the message is value-free."""


def ed25519_signing_key() -> bytes:
    """A new raw Ed25519 private key, base64url without padding (the deploy's encoding)."""

    raw = Ed25519PrivateKey.generate().private_bytes_raw()
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _infer_repository() -> str | None:
    from tools.databricks.m2m_oauth_config import infer_gh_repo

    return infer_gh_repo(REPO)


@dataclass
class Dependencies:
    """Everything the tool touches outside itself; tests replace each one."""

    open_session: Callable[[str], AbstractContextManager[GhSession]] = fresh_gh_session
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)
    infer_repository: Callable[[], str | None] = _infer_repository
    generate_key: Callable[[], bytes] = ed25519_signing_key


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.clients.github_environment", description=(__doc__ or "").splitlines()[0]
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--plan", dest="action", action="store_const", const="plan", help="read-only (default)")
    actions.add_argument("--apply", dest="action", action="store_const", const="apply")
    parser.set_defaults(action="plan")
    parser.add_argument("--ack-plan", help="the sha256 that --plan printed")
    parser.add_argument("--rotate", action="store_true", help="rewrite identifying secrets already set")
    parser.add_argument("--github-repository", help="owner/repo whose environments to manage (default: git origin)")
    parser.add_argument("--env-file", help="local env file (default: .env.local)")
    parser.add_argument("--env-file-only", action="store_true", help="ignore the shell environment")
    return parser


# --- execution ------------------------------------------------------------------------


class _Executor:
    """Runs one acked plan's steps in order; the only code here that mutates."""

    def __init__(self, session: GhSession, inputs: EnvironmentInputs, generate_key: Callable[[], bytes]) -> None:
        self.session = session
        self.inputs = inputs
        self.generate_key = generate_key
        self.mutations = 0
        self._generated: set[str] = set()

    def run(self, plan: Plan) -> int:
        handlers: dict[str, Callable[[Step], None]] = {
            "put_environment": self._put_environment,
            "add_branch_policy": self._add_branch_policy,
            "delete_branch_policy": self._delete_branch_policy,
            "set_variable": self._set_variable,
            "set_secret": self._set_secret,
            "generate_signing_key": self._generate_signing_key,
        }
        for index, item in enumerate(plan.steps, start=1):
            try:
                handlers[item.op](item)
            except BootstrapRefusal:
                raise
            except Exception as exc:  # the class only: the message could carry a value
                raise StepFailure(
                    f"step {index} ({item.op}) failed: {type(exc).__name__}; {self.mutations} mutation(s) "
                    "applied before it. Re-run --plan: what was applied drops out."
                ) from None
            self.mutations += 1
        return self.mutations

    def _environment_path(self, item: Step, *parts: str) -> str:
        return self.session.repo_path("environments", item.get("env"), *parts)

    def _put_environment(self, item: Step) -> None:
        maintainer_id = int(self.session.login()["id"])
        self.session.api("PUT", self._environment_path(item), body=expected(self.inputs.mode, maintainer_id).body())

    def _add_branch_policy(self, item: Step) -> None:
        body = {"name": item.get("name"), "type": item.get("type")}
        self.session.api("POST", self._environment_path(item, "deployment-branch-policies"), body=body)

    def _delete_branch_policy(self, item: Step) -> None:
        self.session.api("DELETE", self._environment_path(item, "deployment-branch-policies", item.get("policy_id")))

    def _set_variable(self, item: Step) -> None:
        self.session.call(["variable", "set", item.get("name"), "--env", item.get("env")], stdin=item.get("value").encode())

    def _set_secret(self, item: Step) -> None:
        value = self.inputs.identifying[item.get("name")]
        self.session.call(["secret", "set", item.get("name"), "--env", item.get("env")], stdin=value.encode("utf-8"))

    def _generate_signing_key(self, item: Step) -> None:
        key = self.generate_key()
        fingerprint = hashlib.sha256(key).hexdigest()
        if fingerprint in self._generated:
            raise BootstrapRefusal(["signing keys: two generated keys are equal; nothing more was written"])
        self._generated.add(fingerprint)
        self.session.call(["secret", "set", item.get("name"), "--env", item.get("env")], stdin=key)
        del key


# --- output and the loop -------------------------------------------------------------------


def _print_plan(plan: Plan, out: TextIO) -> None:
    print(
        f"github environment plan: client={plan.client} review_mode={plan.review_mode} "
        f"rotate={str(plan.rotate).lower()} steps={len(plan.steps)}",
        file=out,
    )
    for index, item in enumerate(plan.steps, start=1):
        print(f"  {index:>2}. {item.line()}", file=out)
    for note in plan.notes:
        print(f"  note: {note}", file=out)
    print(f"canonical plan: {plan.canonical().decode('ascii')}", file=out)
    print(f"plan sha256: {plan.digest()}", file=out)


def _run(args: argparse.Namespace, deps: Dependencies) -> int:
    # Every action opens an interactive gh login, so every action needs a human.
    assert_human_present(deps.environ, deps.stdin)
    if args.action == "apply":
        if not args.ack_plan or not _DIGEST.fullmatch(args.ack_plan):
            raise BootstrapRefusal(["--ack-plan: --apply needs the 64-hex sha256 that --plan printed"])
    elif args.ack_plan:
        raise BootstrapRefusal(["--ack-plan: applies to --apply only"])
    env_file = Path(args.env_file) if args.env_file else REPO / ".env.local"
    inputs = load_inputs({} if args.env_file_only else deps.environ, env_file)
    repository = args.github_repository or deps.infer_repository()
    if not repository or REPOSITORY.fullmatch(repository) is None:
        raise BootstrapRefusal(["--github-repository: name the owner/repository whose environments to manage"])
    with deps.open_session(repository) as session:
        plan = compute_plan(inputs, observe(session, inputs.client), repository=repository, rotate=args.rotate)
        if args.action == "plan":
            _print_plan(plan, deps.stdout)
            return 0
        if not hmac.compare_digest(plan.digest(), args.ack_plan):
            raise BootstrapRefusal(
                ["--ack-plan: does not match the plan computed now; nothing was changed. Run --plan again and review it"]
            )
        count = _Executor(session, inputs, deps.generate_key).run(plan)
        print(f"github environment apply: {len(plan.steps)} step(s), {count} mutation(s)", file=deps.stdout)
        after = compute_plan(inputs, observe(session, inputs.client), repository=repository, rotate=False)
    if after.steps:
        print(f"github environment apply: not converged; {len(after.steps)} step(s) remain", file=deps.stdout)
        return 1
    print("github environment apply: converged; a second run makes 0 mutations", file=deps.stdout)
    return 0


def main(argv: Sequence[str] | None = None, deps: Dependencies | None = None) -> int:
    deps = deps or Dependencies()
    args = _parser().parse_args(argv)
    try:
        return _run(args, deps)
    except ProfileRefusal as refusal:
        print("github environment refused:", file=deps.stderr)
        for line in refusal.lines:
            print(f"  - {line}", file=deps.stderr)
        return 2
    except StepFailure as failure:
        print(f"github environment failed: {failure}", file=deps.stderr)
        return 2
    except Exception as exc:  # the class only: a message could carry a value
        print(f"github environment failed: {type(exc).__name__}", file=deps.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
