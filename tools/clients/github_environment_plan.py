"""What the GitHub-environment tool reads, observes and plans (M2-PR2).

**Inputs** come from the shell, then ``.env.local`` (or ``--env-file``), like
the bootstrap's. Every ``secret``-class value found there is dropped on read
and never kept: the M2M and account-principal secrets reach the environment
only from ``bootstrap_account --rotate-adopted`` through the GitHub sink, and
the signing keys are generated here. ``MIP_IDENTITY_IDS_JSON`` is never
written here either: the bootstrap's sink owns it.

The profile's values are written only once the whole local profile resolves
(``tools.clients.environment.resolve``, with each secret field withheld). The
profile digest ``MIP_CLIENT_PROFILE_SHA256`` is then ``profile.digest`` of
that profile: the public stand-in for the deferred owner profile
attestation. Until it resolves, the plan writes only the skeleton (the two
environments and their protections, ``MIP_CLIENT``, ``MIP_REVIEW_MODE`` and
the signing keys) and lists the refusal lines as deferred.

**The plan** is the ordered list of mutations, computed from one observation.
Its canonical form is compact key-sorted JSON and its digest the sha256 of
those bytes, as the bootstrap's (tools/clients/bootstrap_plan.py). It names
environments, protections, field names and public values only. The
identifying values a ``set_secret`` step writes and the maintainer's GitHub
id are folded into one ``binding`` sha256, so the ack pins them exactly.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tools.clients import profile as views
from tools.clients.bootstrap_plan import canonical_json
from tools.clients.environment import RawProfile, from_local, resolve
from tools.clients.errors import BootstrapRefusal, ProfileRefusal
from tools.clients.github_protections import (
    BranchPolicy,
    ReviewMode,
    branch_policies,
    drift,
    environment_names,
    environments,
    expected,
    observed,
    review_mode,
)
from tools.clients.schema import FIELD_CLASS, FieldClass, name_class

PLAN_VERSION = 1
TOOL = "tools.clients.github_environment"
PROFILE_DIGEST_VARIABLE = "MIP_CLIENT_PROFILE_SHA256"
PINS_FIELD = "MIP_IDENTITY_IDS_JSON"
PROOF_SIGNING_KEY = "MIP_AI_GATEWAY_PROOF_SIGNING_KEY"
MODEL_SIGNING_KEY = "MIP_GATEWAY_MODEL_ATTESTATION_SIGNING_KEY"
# A stand-in for each secret field while the profile validates; never written.
WITHHELD = "withheld-by-the-github-environment"
_CLIENT = re.compile(r"[a-z][a-z0-9-]{1,30}")
OPS = frozenset(
    {
        "put_environment",
        "add_branch_policy",
        "delete_branch_policy",
        "set_variable",
        "set_secret",
        "generate_signing_key",
    }
)


@dataclass(frozen=True)
class EnvironmentInputs:
    """The local profile as the environment will hold it; values never repr."""

    client: str
    mode: ReviewMode
    agentic: bool
    variables: Mapping[str, str] = field(default_factory=dict)
    identifying: Mapping[str, str] = field(default_factory=dict, repr=False)
    profile_digest: str | None = None
    deferred: tuple[str, ...] = ()

    @property
    def signing_keys(self) -> tuple[str, ...]:
        return (PROOF_SIGNING_KEY, MODEL_SIGNING_KEY) if self.agentic else (PROOF_SIGNING_KEY,)


def _skeleton(values: Mapping[str, str]) -> tuple[str, ReviewMode, bool]:
    client = values.get("MIP_CLIENT", "")
    if _CLIENT.fullmatch(client) is None:
        raise BootstrapRefusal(["MIP_CLIENT: must be 2-31 lowercase letters, digits or '-', from a letter"])
    mode = review_mode(values.get("MIP_REVIEW_MODE"))
    tier = values.get("MIP_AGENTIC_TIER", "off")
    if tier not in {"off", "on"}:
        raise BootstrapRefusal(["MIP_AGENTIC_TIER: must be off or on"])
    return client, mode, tier == "on"


def load_inputs(environ: Mapping[str, str], env_file: Path | None) -> EnvironmentInputs:
    """Read the local profile; keep no secret; resolve it whole when it can."""

    raw = from_local(environ, env_file)
    kept = {name: value for name, value in raw.values.items() if FIELD_CLASS[name] is not FieldClass.SECRET}
    client, mode, agentic = _skeleton(kept)
    withheld = {name: WITHHELD for name, kind in FIELD_CLASS.items() if kind is FieldClass.SECRET}
    try:
        profile = resolve(RawProfile(values={**kept, **withheld}, identity_aliases=raw.identity_aliases))
    except ProfileRefusal as refusal:
        skeleton = {"MIP_CLIENT": client, "MIP_REVIEW_MODE": mode.name}
        return EnvironmentInputs(client, mode, agentic, variables=skeleton, deferred=refusal.lines)
    env = views.canonical_env(profile)
    variables = {name: value for name, value in env.items() if FIELD_CLASS[name] is FieldClass.PUBLIC}
    identifying = {
        name: value
        for name, value in env.items()
        if FIELD_CLASS[name] is FieldClass.IDENTIFYING and name != PINS_FIELD
    }
    return EnvironmentInputs(
        profile.client,
        review_mode(profile.review_mode),
        profile.agentic_tier == "on",
        variables=variables,
        identifying=identifying,
        profile_digest=views.digest(profile),
    )


# --- observation -----------------------------------------------------------------


@dataclass(frozen=True)
class EnvironmentState:
    name: str
    environment: Mapping[str, Any] | None
    policies: tuple[BranchPolicy, ...] = ()
    variables: Mapping[str, str] = field(default_factory=dict)
    secret_updates: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Observation:
    maintainer: Mapping[str, Any]
    states: Mapping[str, EnvironmentState]


def named_listing(listing: Any, value_key: str, what: str) -> dict[str, str]:
    """``{name: <value_key>}`` from a ``gh ... list --json`` answer; anything odd refuses."""

    if not isinstance(listing, list):
        raise BootstrapRefusal([f"github: the {what} listing is malformed"])
    found: dict[str, str] = {}
    for item in listing:
        name = item.get("name") if isinstance(item, Mapping) else None
        value = item.get(value_key) if isinstance(item, Mapping) else None
        if not isinstance(name, str) or not isinstance(value, str) or name in found:
            raise BootstrapRefusal([f"github: the {what} listing is malformed"])
        found[name] = value
    return found


def observe(session: Any, client: str) -> Observation:
    """Read both environments' protections, and the deploy environment's names and variables."""

    maintainer = session.login()
    listing = environments(session)
    states: dict[str, EnvironmentState] = {}
    for name in environment_names(client):
        found = listing.get(name)
        if found is None:
            states[name] = EnvironmentState(name, None)
            continue
        policies = branch_policies(session, name)
        if name != client:
            states[name] = EnvironmentState(name, found, policies)
            continue
        variables = named_listing(
            session.call_json(["variable", "list", "--env", name, "--json", "name,value"]), "value", "variable"
        )
        updates = named_listing(
            session.call_json(["secret", "list", "--env", name, "--json", "name,updatedAt"]), "updatedAt", "secret"
        )
        states[name] = EnvironmentState(name, found, policies, variables, updates)
    return Observation(maintainer, states)


# --- the plan ---------------------------------------------------------------------


@dataclass(frozen=True)
class Step:
    """One mutation; ``fields`` hold public values only."""

    op: str
    fields: tuple[tuple[str, str], ...]

    def get(self, name: str) -> str:
        return dict(self.fields)[name]

    def public(self) -> dict[str, str]:
        return {"op": self.op, **dict(self.fields)}

    def line(self) -> str:
        return " ".join([self.op, *(f"{name}={value}" for name, value in self.fields)])


def step(op: str, **fields: str) -> Step:
    if op not in OPS:
        raise ValueError(f"unknown github environment step {op}")
    return Step(op, tuple(fields.items()))


@dataclass(frozen=True)
class Plan:
    client: str
    repository: str
    review_mode: str
    rotate: bool
    binding: str
    steps: tuple[Step, ...]
    notes: tuple[str, ...] = ()

    def document(self) -> dict[str, Any]:
        return {
            "tool": TOOL,
            "plan_version": PLAN_VERSION,
            "client": self.client,
            "repository": self.repository,
            "review_mode": self.review_mode,
            "rotate": self.rotate,
            "binding": self.binding,
            "steps": [item.public() for item in self.steps],
            "notes": list(self.notes),
        }

    def canonical(self) -> bytes:
        return canonical_json(self.document())

    def digest(self) -> str:
        return hashlib.sha256(self.canonical()).hexdigest()


def _misplaced(state: EnvironmentState) -> list[str]:
    """A profile field stored in the wrong place: the deploy's resolver would refuse it."""

    lines = []
    for name in sorted(state.variables):
        kind = name_class(name)
        if kind is not None and kind is not FieldClass.PUBLIC:
            lines.append(f"{name}: is {kind} but is a variable of {state.name}; delete the variable by hand")
    for name in sorted(state.secret_updates):
        if name_class(name) is FieldClass.PUBLIC:
            lines.append(f"{name}: is public but is a secret of {state.name}; delete the secret by hand")
    return lines


def _notes(inputs: EnvironmentInputs, deploy: EnvironmentState, *, rotate: bool) -> list[str]:
    """What the plan leaves alone, and why: value-free, field names only."""

    if inputs.profile_digest is None:
        return [
            "deferred: the profile's values and MIP_CLIENT_PROFILE_SHA256 wait until the whole "
            "local profile resolves",
            *(f"deferred: {line}" for line in inputs.deferred),
        ]
    notes = [
        f"stale: {name} is a {kind} of {deploy.name} the local profile does not set; the deploy "
        "reads it, so set it locally or delete it by hand"
        for kind, names in (("variable", deploy.variables), ("secret", deploy.secret_updates))
        for name in sorted(names)
        if name != PINS_FIELD
        and name_class(name) in {FieldClass.PUBLIC, FieldClass.IDENTIFYING}
        and name not in inputs.variables
        and name not in inputs.identifying
    ]
    if not rotate and any(name in deploy.secret_updates for name in inputs.identifying):
        notes.append(
            "kept: identifying secrets already set are not rewritten (GitHub cannot read them back); "
            "--rotate rewrites them from the local profile"
        )
    if rotate and any(name in deploy.secret_updates for name in inputs.signing_keys):
        notes.append(
            "kept: signing keys are generated once; --rotate never replaces one, because a new key "
            "orphans every record the old one signed (docs/deployment.md, proof-key rotation)"
        )
    return notes


def _protection_steps(state: EnvironmentState, mode: ReviewMode, maintainer_id: int) -> list[Step]:
    want = expected(mode, maintainer_id)
    steps = []
    if state.environment is None or drift(want, observed(state.environment)):
        steps.append(
            step(
                "put_environment",
                env=state.name,
                change="create" if state.environment is None else "update",
                review_mode=mode.name,
                reviewers="the signed-in maintainer",
                prevent_self_review=str(want.prevent_self_review).lower(),
                can_admins_bypass=str(want.can_admins_bypass).lower(),
                branch_policy="custom:main",
            )
        )
    if not any(policy.is_main for policy in state.policies):
        steps.append(step("add_branch_policy", env=state.name, name="main", type="branch"))
    steps += [
        step("delete_branch_policy", env=state.name, policy_id=str(policy.policy_id))
        for policy in state.policies
        if not policy.is_main
    ]
    return steps


def compute_plan(inputs: EnvironmentInputs, obs: Observation, *, repository: str, rotate: bool) -> Plan:
    """Every mutation that makes the two environments hold ``inputs``, in order."""

    deploy = obs.states[inputs.client]
    lines = _misplaced(deploy)
    if lines:
        raise BootstrapRefusal(lines)
    maintainer_id = int(obs.maintainer["id"])
    steps: list[Step] = []
    for name in environment_names(inputs.client):
        steps += _protection_steps(obs.states[name], inputs.mode, maintainer_id)
    target = inputs.client
    for name, value in sorted(inputs.variables.items()):
        if deploy.variables.get(name) != value:
            change = "update" if name in deploy.variables else "create"
            steps.append(step("set_variable", env=target, name=name, change=change, value=value))
    written: dict[str, str] = {}
    for name, value in sorted(inputs.identifying.items()):
        if rotate or name not in deploy.secret_updates:
            change = "replace" if name in deploy.secret_updates else "create"
            steps.append(step("set_secret", env=target, name=name, change=change, source="local profile"))
            written[name] = value
    steps += [
        step("generate_signing_key", env=target, name=name, kind="ed25519")
        for name in inputs.signing_keys
        if name not in deploy.secret_updates
    ]
    digest = inputs.profile_digest
    if digest is not None and deploy.variables.get(PROFILE_DIGEST_VARIABLE) != digest:
        change = "update" if PROFILE_DIGEST_VARIABLE in deploy.variables else "create"
        steps.append(step("set_variable", env=target, name=PROFILE_DIGEST_VARIABLE, change=change, value=digest))
    notes = _notes(inputs, deploy, rotate=rotate)
    binding = hashlib.sha256(
        canonical_json({"maintainer_id": maintainer_id, "secrets": written})
    ).hexdigest()
    return Plan(
        client=inputs.client,
        repository=repository,
        review_mode=inputs.mode.name,
        rotate=rotate,
        binding=binding,
        steps=tuple(steps),
        notes=tuple(notes),
    )
