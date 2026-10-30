"""``python -m tools.clients.bootstrap_account``: one client's account bootstrap.

A human-run tool for the client's account admin (plan §4.3 and §4.10). It
creates or adopts the install's identities, pinned by application id, in
this order:

1. ``<prefix>-deployer``: no secret, no token, one federation policy with
   subject ``repo:<owner>/<repo>:environment:<client>`` and the workspace
   OIDC audience, set before its workspace ADMIN assignment;
2. ``<prefix>-jobs``: no secret, a USER assignment; the deployer gets
   Service Principal User on it;
3. the account group ``<prefix>-data-owners`` = {deployer, jobs}, plus an
   optional ``--interim-member``;
4. ``<prefix>-validator``: no secret, federated to
   ``environment:<client>-validate``, a USER assignment;
5. the reserved M2M identities (the agentic three only with ``--tier
   agentic``), each with a USER assignment;
6. the account principal, with Service Principal Manager on the reserved
   M2M identities only, never on the three above and never account admin;
7. USE_CATALOG, USE_SCHEMA and SELECT on the Cotality source for the group.

**Actions.** ``--plan`` (the default) is read-only: it prints the canonical
plan and its sha256. ``--for`` picks the action it plans (``apply``,
``rotate-adopted`` or ``post-first-install``). Each mutating action needs
``--ack-plan <sha256>``: it recomputes the plan read-only, refuses on any
difference, then executes. ``--verify`` re-checks read-only and exits 1 on
drift. A second ``--apply`` makes zero mutations.

* ``--rotate-adopted`` mints a new secret for every pinned M2M identity,
  hands it to the credential sink, confirms and probes it, then revokes
  every older secret of that identity.
* ``--post-first-install`` grants the account principal Service Principal
  Manager on the App's own identity, once the first install created it.

**Deferred owner attestation.** The plan's owner hardware-key ``mint``
attestation is deferred (review mode ``single_maintainer_audited``). Its
stand-in is the exact plan-digest ack, plus human presence: the mutating
actions refuse under ``GITHUB_ACTIONS`` or ``CI`` and without a terminal on
stdin, and every run logs in fresh in a throwaway directory
(tools/clients/bootstrap_session.py).

**Never:** a metastore ownership transfer, a revoke on another team's
object, or a delete of anything this tool did not create. Output names
roles and steps, never an identifier or a credential. Exit 0 on success,
1 on drift (``--verify``) or an unconverged apply, 2 on a refusal.
"""

from __future__ import annotations

import argparse
import hmac
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TextIO

from databricks.sdk.errors import NotFound

from tools.clients.bootstrap_execute import Executor, StepFailure
from tools.clients.bootstrap_inputs import (
    BootstrapInputs,
    BootstrapRequest,
    build_request,
    load_inputs,
    parse_adoptions,
)
from tools.clients.bootstrap_observe import (
    SP_MANAGER,
    Observation,
    observe,
    rule_set_name,
    text,
)
from tools.clients.bootstrap_plan import (
    Action,
    Plan,
    plan_apply,
    plan_post_first_install,
    plan_rotation,
)
from tools.clients.bootstrap_session import SessionFactory, assert_human_present, fresh_session
from tools.clients.credential_sink import (
    CredentialProbe,
    CredentialSink,
    UnconfiguredSink,
    account_token_probe,
)
from tools.clients.errors import BootstrapRefusal, ProfileRefusal

REPO = Path(__file__).resolve().parents[2]
MUTATING = ("apply", "rotate-adopted", "post-first-install")
_DIGEST = re.compile(r"[0-9a-f]{64}")


def _infer_repository() -> str | None:
    from tools.databricks.m2m_oauth_config import infer_gh_repo

    return infer_gh_repo(REPO)


def _default_probe(inputs: BootstrapInputs) -> CredentialProbe:
    return account_token_probe(inputs.resolved_account_host(), inputs.account_id)


@dataclass
class Dependencies:
    """Everything the tool touches outside itself; tests replace each one."""

    open_session: SessionFactory = fresh_session
    sink: CredentialSink = field(default_factory=UnconfiguredSink)
    probe_for: Callable[[BootstrapInputs], CredentialProbe] = _default_probe
    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)
    stdin: TextIO = field(default_factory=lambda: sys.stdin)
    stdout: TextIO = field(default_factory=lambda: sys.stdout)
    stderr: TextIO = field(default_factory=lambda: sys.stderr)
    infer_repository: Callable[[], str | None] = _infer_repository
    sleep: Callable[[float], None] | None = None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.clients.bootstrap_account", description=(__doc__ or "").splitlines()[0]
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--plan", dest="action", action="store_const", const="plan", help="read-only (default)")
    actions.add_argument("--apply", dest="action", action="store_const", const="apply")
    actions.add_argument("--rotate-adopted", dest="action", action="store_const", const="rotate-adopted")
    actions.add_argument("--post-first-install", dest="action", action="store_const", const="post-first-install")
    actions.add_argument("--verify", dest="action", action="store_const", const="verify", help="read-only re-check")
    parser.set_defaults(action="plan")
    parser.add_argument("--for", dest="plan_for", choices=MUTATING, help="--plan: the action to plan (default apply)")
    parser.add_argument("--ack-plan", help="the sha256 that --plan printed for this action")
    parser.add_argument("--tier", choices=("core", "agentic"), default="core")
    parser.add_argument("--adopt", action="append", default=[], metavar="ROLE=ID", help="pin an existing identity")
    parser.add_argument("--interim-member", help="an account user's email, added to the data-owners group")
    parser.add_argument("--github-repository", help="owner/repo the federation subjects name (default: git origin)")
    parser.add_argument("--env-file", help="local env file (default: .env.local)")
    parser.add_argument("--env-file-only", action="store_true", help="ignore the shell environment")
    return parser


def _request(args: argparse.Namespace, deps: Dependencies) -> BootstrapRequest:
    env_file = Path(args.env_file) if args.env_file else REPO / ".env.local"
    inputs, aliases = load_inputs({} if args.env_file_only else deps.environ, env_file)
    return build_request(
        inputs,
        aliases,
        tier=args.tier,
        adoptions=parse_adoptions(args.adopt),
        interim_member=args.interim_member,
        repository=args.github_repository or deps.infer_repository(),
    )


# --- post-first-install: the App's own identity ------------------------------


def _plan_post_first_install(clients: Any, obs: Observation) -> Plan:
    inputs = obs.request.inputs
    if obs.principal("account_principal") is None or obs.roles["account_principal"].provenance != "pinned":
        raise BootstrapRefusal(["account_principal: is not pinned yet; run --apply first"])
    if inputs.app_name is None:
        raise BootstrapRefusal(["MIP_APP_NAME: is required for --post-first-install"])
    try:
        app = clients.workspace.apps.get(inputs.app_name)
    except NotFound:
        raise BootstrapRefusal(["MIP_APP_NAME: the App does not exist yet; the first install creates it"]) from None
    application_id = text(getattr(app, "service_principal_client_id", None)).lower()
    if not application_id:
        raise BootstrapRefusal(["MIP_APP_NAME: the App has no service principal yet"])
    if application_id in set(obs.request.pins.values()):
        raise BootstrapRefusal(["MIP_APP_NAME: the App's identity is one of the bootstrap identities"])
    listed = clients.account.service_principals.list(filter=f"applicationId eq '{application_id}'")
    matches = [record for record in listed if text(record.application_id).lower() == application_id]
    if len(matches) != 1:
        raise BootstrapRefusal(["MIP_APP_NAME: the App's identity is not exactly one account service principal"])
    rules = clients.account.access_control.get_rule_set(rule_set_name(inputs.account_id, application_id), etag="")
    holders = frozenset(
        text(principal)
        for rule in getattr(rules, "grant_rules", None) or ()
        if text(rule.role) == SP_MANAGER
        for principal in rule.principals or ()
    )
    return plan_post_first_install(
        obs, app_scim_id=text(matches[0].id), app_application_id=application_id, holders=holders
    )


def _compute(clients: Any, request: BootstrapRequest, action: Action) -> tuple[Observation, Plan]:
    obs = observe(clients, request)
    if action == "apply":
        return obs, plan_apply(obs)
    if action == "rotate-adopted":
        return obs, plan_rotation(obs)
    return obs, _plan_post_first_install(clients, obs)


# --- output -------------------------------------------------------------------


def _print_plan(plan: Plan, out: TextIO) -> None:
    print(
        f"bootstrap plan: action={plan.action} client={plan.client} prefix={plan.identity_prefix} "
        f"tier={plan.tier} steps={len(plan.steps)}",
        file=out,
    )
    for index, item in enumerate(plan.steps, start=1):
        print(f"  {index:>2}. {item.line()}", file=out)
    for note in plan.deferred:
        print(f"  deferred: {note}", file=out)
    print(f"canonical plan: {plan.canonical().decode('ascii')}", file=out)
    print(f"plan sha256: {plan.digest()}", file=out)


def _verify(clients: Any, request: BootstrapRequest, out: TextIO) -> int:
    obs, plan = _compute(clients, request, "apply")
    drift = [item.line() for item in plan.steps]
    for role, state in obs.roles.items():
        if state.spec.secret_field is not None and state.principal is not None and not state.secret_ids:
            drift.append(f"{role}: holds no OAuth secret; run --rotate-adopted")
    drift += list(plan.deferred)
    for role, state in obs.roles.items():
        status = "absent" if state.principal is None else state.provenance
        print(f"  {role}: {status}; secrets={len(state.secret_ids)} policies={len(state.policies)}", file=out)
    if drift:
        print(f"bootstrap verify: drift ({len(drift)})", file=out)
        for line in drift:
            print(f"  - {line}", file=out)
        return 1
    print("bootstrap verify: ok; the deployer, jobs and validator hold no credential", file=out)
    return 0


def _mutate(clients: Any, request: BootstrapRequest, action: Action, ack: str, deps: Dependencies) -> int:
    obs, plan = _compute(clients, request, action)
    if not hmac.compare_digest(plan.digest(), ack):
        raise BootstrapRefusal(
            [
                f"--ack-plan: does not match the {action} plan computed now; nothing was changed. "
                f"Run --plan --for {action} again and review it"
            ]
        )
    probe = deps.probe_for(request.inputs) if action == "rotate-adopted" else None
    kwargs: dict[str, Any] = {"probe": probe}
    if deps.sleep is not None:
        kwargs["sleep"] = deps.sleep
    executor = Executor(clients, obs, deps.sink, **kwargs)
    count = executor.run(plan)
    print(f"bootstrap {action}: {len(plan.steps)} step(s), {count} mutation(s)", file=deps.stdout)
    if action != "apply":
        return 0
    # What was created or adopted is pinned now: re-plan from the sink's pin map.
    settled = replace(request, pins=dict(executor.pins), adopted_roles=frozenset())
    _, after = _compute(clients, settled, "apply")
    if after.steps:
        print(f"bootstrap apply: not converged; {len(after.steps)} step(s) remain; run --verify", file=deps.stdout)
        return 1
    print("bootstrap apply: converged; a second run makes 0 mutations", file=deps.stdout)
    return 0


def _run(args: argparse.Namespace, deps: Dependencies) -> int:
    action = args.action
    if action in MUTATING:
        assert_human_present(deps.environ, deps.stdin)
        if not args.ack_plan or not _DIGEST.fullmatch(args.ack_plan):
            raise BootstrapRefusal(
                [f"--ack-plan: {action} needs the 64-hex sha256 that --plan --for {action} printed"]
            )
    elif args.ack_plan:
        raise BootstrapRefusal(["--ack-plan: applies to --apply, --rotate-adopted and --post-first-install only"])
    if args.plan_for is not None and action != "plan":
        raise BootstrapRefusal(["--for: applies to --plan only"])
    request = _request(args, deps)
    with deps.open_session(request.inputs) as clients:
        if action == "plan":
            _print_plan(_compute(clients, request, args.plan_for or "apply")[1], deps.stdout)
            return 0
        if action == "verify":
            return _verify(clients, request, deps.stdout)
        return _mutate(clients, request, action, args.ack_plan, deps)


def main(argv: Sequence[str] | None = None, deps: Dependencies | None = None) -> int:
    deps = deps or Dependencies()
    args = _parser().parse_args(argv)
    try:
        return _run(args, deps)
    except ProfileRefusal as refusal:
        print("bootstrap refused:", file=deps.stderr)
        for line in refusal.lines:
            print(f"  - {line}", file=deps.stderr)
        return 2
    except StepFailure as failure:
        print(f"bootstrap failed: {failure}", file=deps.stderr)
        return 2
    except Exception as exc:  # the class only: a message could carry a value
        print(f"bootstrap failed: {type(exc).__name__}", file=deps.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
