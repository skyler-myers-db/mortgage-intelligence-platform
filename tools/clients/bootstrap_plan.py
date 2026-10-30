"""The bootstrap's read-only plans and their exact digests.

A plan is the ordered list of mutations one action would make, computed
from an ``Observation`` and nothing else. Its canonical form is compact,
key-sorted JSON; its digest is the sha256 of those bytes. ``--apply``,
``--rotate-adopted`` and ``--post-first-install`` recompute the plan
read-only right before mutating and refuse unless its digest equals
``--ack-plan`` exactly: the deferred owner ``mint`` attestation's stand-in.

**Public-safe.** A plan names roles, actions, public display names, the
federation subject (repository and client key), privileges and the source
catalog, never an identifier. The identifying facts the mutations act on
(account, workspace and metastore ids, hosts, the OIDC audience, every
application and SCIM id, policy and secret ids, the group id, the interim
member) are folded into one ``binding`` sha256, so the ack still pins them
exactly while the printed plan stays safe for a public log.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from tools.clients.bootstrap_observe import (
    SOURCE_CATALOG_PRIVILEGES,
    SOURCE_SCHEMA_PRIVILEGES,
    SP_MANAGER,
    SP_USER,
    Observation,
    RoleState,
    sp_reference,
)
from tools.clients.bootstrap_roster import ROSTER, federation_subject
from tools.clients.errors import BootstrapRefusal

PLAN_VERSION = 1
TOOL = "tools.clients.bootstrap_account"
Action = Literal["apply", "rotate-adopted", "post-first-install"]
# Steps that write to the credential sink: the sink must be ready first.
SINK_OPS = frozenset({"create_service_principal", "pin_service_principal", "rotate_secret"})
OPS = frozenset(
    {
        "create_service_principal",
        "pin_service_principal",
        "set_federation_policy",
        "assign_workspace",
        "grant_sp_role",
        "create_group",
        "add_group_member",
        "grant_uc",
        "rotate_secret",
    }
)
_SP_ROLE = {"user": SP_USER, "manager": SP_MANAGER}


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
        raise ValueError(f"unknown bootstrap step {op}")
    return Step(op, tuple(fields.items()))


@dataclass(frozen=True)
class Plan:
    action: Action
    client: str
    identity_prefix: str
    tier: str
    binding: str
    steps: tuple[Step, ...]
    deferred: tuple[str, ...] = ()
    # Identifying execution facts: never printed, never in the document.
    facts: Mapping[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def document(self) -> dict[str, Any]:
        return {
            "tool": TOOL,
            "plan_version": PLAN_VERSION,
            "action": self.action,
            "client": self.client,
            "identity_prefix": self.identity_prefix,
            "tier": self.tier,
            "binding": self.binding,
            "steps": [item.public() for item in self.steps],
            "deferred": list(self.deferred),
        }

    def canonical(self) -> bytes:
        return canonical_json(self.document())

    def digest(self) -> str:
        return hashlib.sha256(self.canonical()).hexdigest()

    @property
    def needs_sink(self) -> bool:
        return any(item.op in SINK_OPS for item in self.steps)


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def binding_digest(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(material)).hexdigest()


def _base_material(obs: Observation) -> dict[str, Any]:
    inputs = obs.request.inputs
    return {
        "account_id": inputs.account_id,
        "account_host": inputs.resolved_account_host(),
        "workspace_host": inputs.workspace_host,
        "workspace_id": inputs.workspace_id,
        "metastore_id": inputs.metastore_id,
        "audience": obs.audience,
        "repository": obs.request.repository,
        "pins": dict(sorted(obs.request.pins.items())),
        "adopted_roles": sorted(obs.request.adopted_roles),
        "principals": {
            role: [state.principal.scim_id, state.principal.application_id]
            for role, state in obs.roles.items()
            if state.principal is not None
        },
    }


def _plan(
    obs: Observation,
    action: Action,
    steps: list[Step],
    material: dict[str, Any],
    *,
    deferred: tuple[str, ...] = (),
    facts: Mapping[str, Any] | None = None,
) -> Plan:
    request = obs.request
    return Plan(
        action=action,
        client=request.inputs.client,
        identity_prefix=request.inputs.identity_prefix,
        tier=request.tier,
        binding=binding_digest(material),
        steps=tuple(steps),
        deferred=deferred,
        facts=dict(facts or {}),
    )


# --- apply ------------------------------------------------------------------


def _has_sp_role(obs: Observation, target: str, grantee: str, sp_role: str) -> bool:
    grantee_principal = obs.principal(grantee)
    if obs.principal(target) is None or grantee_principal is None:
        return False
    holders = obs.rule_sets.get(target, {}).get(_SP_ROLE[sp_role], frozenset())
    return sp_reference(grantee_principal.application_id) in holders


def _identity_steps(obs: Observation, state: RoleState) -> list[Step]:
    spec, request = state.spec, obs.request
    steps = []
    if state.principal is None:
        name = spec.display_name(request.inputs.identity_prefix)
        steps.append(step("create_service_principal", role=spec.role, name=name))
    elif state.provenance == "adopted":
        steps.append(step("pin_service_principal", role=spec.role))
    if spec.environment_suffix is not None:
        subject = federation_subject(request.repository, request.inputs.client, spec)
        if not state.policies:
            steps.append(step("set_federation_policy", role=spec.role, subject=subject, change="create"))
        elif (state.policies[0].subject, state.policies[0].audiences) != (subject, (obs.audience,)):
            steps.append(step("set_federation_policy", role=spec.role, subject=subject, change="update"))
    held = state.permissions
    # ADMIN includes USER; the bootstrap never lowers a permission.
    satisfied = spec.assignment in held or (spec.assignment == "USER" and "ADMIN" in held)
    if spec.assignment is not None and not satisfied:
        steps.append(step("assign_workspace", role=spec.role, permission=spec.assignment))
    return steps


def _group_steps(obs: Observation) -> list[Step]:
    steps = []
    if obs.group.group_id is None:
        steps.append(step("create_group", group=obs.group.name))
    for role in ("deployer", "jobs"):
        principal = obs.principal(role)
        if principal is None or principal.scim_id not in obs.group.member_ids:
            steps.append(step("add_group_member", group=obs.group.name, member=role))
    if obs.interim_user_id is not None and obs.interim_user_id not in obs.group.member_ids:
        steps.append(step("add_group_member", group=obs.group.name, member="interim"))
    return steps


def _share_steps(obs: Observation) -> list[Step]:
    share = obs.share
    if share is None or share.deferred is not None:
        return []
    steps = []
    targets = (
        ("catalog", share.catalog, SOURCE_CATALOG_PRIVILEGES, share.catalog_privileges),
        ("schema", f"{share.catalog}.{share.schema}", SOURCE_SCHEMA_PRIVILEGES, share.schema_privileges),
    )
    for securable_type, full_name, wanted, held in targets:
        missing = [privilege for privilege in wanted if privilege not in held]
        if missing:
            steps.append(
                step(
                    "grant_uc",
                    securable_type=securable_type,
                    securable=full_name,
                    principal=obs.group.name,
                    privileges=",".join(missing),
                )
            )
    return steps


def plan_apply(obs: Observation) -> Plan:
    """Create or adopt every identity, in the spec's order (M2-PR1, steps 1-7)."""

    roles = obs.roles
    steps: list[Step] = []
    steps += _identity_steps(obs, roles["deployer"])  # 1: subject before ADMIN
    steps += _identity_steps(obs, roles["jobs"])  # 2
    if not _has_sp_role(obs, "jobs", "deployer", "user"):
        steps.append(step("grant_sp_role", target="jobs", grantee="deployer", sp_role="user"))
    steps += _group_steps(obs)  # 3
    steps += _identity_steps(obs, roles["validator"])  # 4
    managed = [spec.role for spec in ROSTER if spec.role in roles and spec.managed_by_account_principal]
    for role in managed:  # 5
        steps += _identity_steps(obs, roles[role])
    steps += _identity_steps(obs, roles["account_principal"])  # 6
    for role in managed:
        if not _has_sp_role(obs, role, "account_principal", "manager"):
            steps.append(step("grant_sp_role", target=role, grantee="account_principal", sp_role="manager"))
    steps += _share_steps(obs)  # 7
    material = _base_material(obs)
    material.update(
        {
            "policies": {
                role: [policy.policy_id for policy in state.policies]
                for role, state in roles.items()
                if state.policies
            },
            "group_id": obs.group.group_id,
            "interim": [obs.request.interim_member, obs.interim_user_id],
        }
    )
    deferred = (obs.share.deferred,) if obs.share is not None and obs.share.deferred else ()
    return _plan(obs, "apply", steps, material, deferred=deferred)


# --- rotate-adopted ---------------------------------------------------------


def plan_rotation(obs: Observation) -> Plan:
    """Mint one new secret per pinned M2M identity and revoke every older one.

    Every secret-bearing identity of the tier must already be pinned: a
    secret is handed to the sink only for an identity the sink's pin map
    already names.
    """

    unpinned = [
        role
        for role, state in obs.roles.items()
        if state.spec.secret_field is not None and state.provenance != "pinned"
    ]
    if unpinned:
        raise BootstrapRefusal(
            [f"{role}: is not pinned in MIP_IDENTITY_IDS_JSON; run --apply first" for role in unpinned]
        )
    steps = []
    secrets: dict[str, list[str]] = {}
    for role, state in obs.roles.items():
        field_name = state.spec.secret_field
        if field_name is None:
            continue
        secrets[role] = list(state.secret_ids)
        steps.append(step("rotate_secret", role=role, field=field_name, revoke=str(len(state.secret_ids))))
    material = _base_material(obs)
    material["secrets"] = secrets
    return _plan(obs, "rotate-adopted", steps, material)


# --- post-first-install -----------------------------------------------------


def plan_post_first_install(
    obs: Observation, *, app_scim_id: str, app_application_id: str, holders: frozenset[str]
) -> Plan:
    """Service Principal Manager on the new App identity, for the account principal."""

    manager = obs.principal("account_principal")
    assert manager is not None
    steps = []
    if sp_reference(manager.application_id) not in holders:
        steps.append(step("grant_sp_role", target="app", grantee="account_principal", sp_role="manager"))
    material = _base_material(obs)
    material["app"] = [app_scim_id, app_application_id]
    facts = {"app_application_id": app_application_id}
    return _plan(obs, "post-first-install", steps, material, facts=facts)
