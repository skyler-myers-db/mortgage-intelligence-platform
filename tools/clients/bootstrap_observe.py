"""Read-only observation of one client's account and workspace for the bootstrap.

``observe`` reads, and never writes, everything the plan depends on, and
refuses (value-free) whatever no plan may proceed from:

* the workspace session is not ``MIP_EXPECT_WORKSPACE_ID`` (or the metastore
  is not ``MIP_EXPECT_METASTORE_ID``);
* a pinned or adopted application id names no service principal, or more
  than one;
* an unpinned role's display name is already taken: a name match the
  bootstrap did not create refuses unless ``--adopt <role>=<id>`` names it;
* an identity is inactive or holds account admin;
* the deployer, jobs or validator identity holds an OAuth secret or a
  PAT/OBO token, or more than one federation policy (jobs: any), or a policy
  from another issuer;
* jobs or validator is a workspace admin;
* any principal but an account admin holds a role on the deployer, jobs or
  validator identity (a Manager could mint it a secret, a User act as it);
  the deployer's Service Principal User on jobs is the one reviewed grant,
  and the account principal is refused by name;
* ``<prefix>-data-owners`` exists but is not provably the bootstrap's, or
  holds members other than deployer, jobs and the interim member.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from databricks.sdk.errors import NotFound, PermissionDenied

from tools.clients.bootstrap_inputs import BootstrapRequest
from tools.clients.bootstrap_roster import GITHUB_OIDC_ISSUER, RoleSpec, data_owners_group, roster
from tools.clients.errors import BootstrapRefusal

ACCOUNT_ADMIN_ROLE = "account_admin"
SP_USER = "roles/servicePrincipal.user"
SP_MANAGER = "roles/servicePrincipal.manager"
SOURCE_CATALOG_PRIVILEGES = ("USE_CATALOG",)
SOURCE_SCHEMA_PRIVILEGES = ("SELECT", "USE_SCHEMA")
Provenance = Literal["pinned", "adopted", "absent"]
SECRETLESS_ROLES = ("deployer", "jobs", "validator")
# (target, SP role, grantee): the only non-admin grant a secretless identity may carry.
REVIEWED_GRANTS = frozenset({("jobs", SP_USER, "deployer")})
_PRINCIPAL_KINDS = {"servicePrincipals/": "a service principal", "groups/": "a group", "users/": "a user"}


def text(value: object) -> str:
    """An SDK field (enum or plain) as a stripped string."""

    return str(getattr(value, "value", value) or "").strip()


@dataclass(frozen=True)
class Principal:
    scim_id: str
    application_id: str
    display_name: str


@dataclass(frozen=True)
class Policy:
    policy_id: str
    issuer: str
    subject: str
    audiences: tuple[str, ...]


@dataclass(frozen=True)
class RoleState:
    spec: RoleSpec
    principal: Principal | None
    provenance: Provenance
    secret_ids: tuple[str, ...] = ()
    policies: tuple[Policy, ...] = ()
    permissions: frozenset[str] = frozenset()


@dataclass(frozen=True)
class GroupState:
    name: str
    group_id: str | None
    member_ids: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ShareState:
    catalog: str
    schema: str
    catalog_privileges: frozenset[str] = frozenset()
    schema_privileges: frozenset[str] = frozenset()
    deferred: str | None = None


@dataclass(frozen=True)
class Observation:
    request: BootstrapRequest
    audience: str
    roles: Mapping[str, RoleState]
    group: GroupState
    interim_user_id: str | None
    # Service-principal rule sets by target role: SP role -> principals.
    rule_sets: Mapping[str, Mapping[str, frozenset[str]]] = field(default_factory=dict)
    share: ShareState | None = None

    def principal(self, role: str) -> Principal | None:
        state = self.roles.get(role)
        return state.principal if state else None


def rule_set_name(account_id: str, application_id: str) -> str:
    return f"accounts/{account_id}/servicePrincipals/{application_id}/ruleSets/default"


def sp_reference(application_id: str) -> str:
    return f"servicePrincipals/{application_id}"


def workspace_audience(workspace: Any) -> str:
    """The audience the deployer's GitHub OIDC exchange requests (github_oidc_auth.py).

    The workspace host metadata's ``token_federation_default_oidc_audiences``
    (the SDK's ``token_audience``), else the workspace OIDC token endpoint.
    """

    config = workspace.config
    audience = text(getattr(config, "token_audience", None))
    if audience:
        return audience
    endpoints = getattr(config, "databricks_oidc_endpoints", None)
    return text(getattr(endpoints, "token_endpoint", None))


def _principal(record: Any) -> Principal:
    return Principal(
        scim_id=text(getattr(record, "id", None)),
        application_id=text(getattr(record, "application_id", None)).lower(),
        display_name=text(getattr(record, "display_name", None)),
    )


def _exact(records: Iterable[Any], attribute: str, wanted: str) -> list[Any]:
    folded = wanted.casefold()
    return [record for record in records if text(getattr(record, attribute, None)).casefold() == folded]


class _Reader:
    def __init__(self, clients: Any, request: BootstrapRequest) -> None:
        self.account = clients.account
        self.workspace = clients.workspace
        self.request = request
        self.inputs = request.inputs
        self.lines: list[str] = []
        self.admins: dict[str, bool] = {}

    def refuse(self, line: str) -> None:
        self.lines.append(line)

    # --- workspace binding ----------------------------------------------------

    def bind_workspace(self) -> None:
        if str(self.workspace.get_workspace_id()) != self.inputs.workspace_id:
            raise BootstrapRefusal(["DATABRICKS_HOST: the workspace session is not MIP_EXPECT_WORKSPACE_ID"])
        if self.inputs.metastore_id is not None:
            current = text(getattr(self.workspace.metastores.current(), "metastore_id", None)).lower()
            if current != self.inputs.metastore_id:
                raise BootstrapRefusal(["MIP_EXPECT_METASTORE_ID: the workspace is not on that metastore"])

    # --- identities -------------------------------------------------------------

    def find(self, spec: RoleSpec) -> tuple[Principal | None, Provenance]:
        pinned = self.request.pins.get(spec.role)
        adopted = spec.role in self.request.adopted_roles
        source = f"--adopt {spec.role}" if adopted else f"MIP_IDENTITY_IDS_JSON.{spec.role}"
        if pinned is not None:
            listed = self.account.service_principals.list(filter=f"applicationId eq '{pinned}'")
            matches = _exact(listed, "application_id", pinned)
            if len(matches) != 1:
                count = "no" if not matches else "more than one"
                self.refuse(f"{source}: {count} account service principal has that application id")
                return None, "absent"
            record = self.account.service_principals.get(text(matches[0].id))
            self.check_record(spec, record)
            return _principal(record), "adopted" if adopted else "pinned"
        name = spec.display_name(self.inputs.identity_prefix)
        listed = self.account.service_principals.list(filter=f"displayName eq '{name}'")
        if _exact(listed, "display_name", name):
            self.refuse(
                f"{spec.role}: a service principal named {name} exists that this bootstrap did not pin; "
                f"check where it came from, then name it with --adopt {spec.role}=<application id>"
            )
        return None, "absent"

    def check_record(self, spec: RoleSpec, record: Any) -> None:
        if getattr(record, "active", True) is False:
            self.refuse(f"{spec.role}: the service principal is inactive")
        roles = {text(getattr(item, "value", None)) for item in getattr(record, "roles", None) or ()}
        if ACCOUNT_ADMIN_ROLE in roles:
            self.refuse(
                f"{spec.role}: holds account admin; no bootstrap identity may "
                "(remove it in the account console)"
            )

    def secret_ids(self, principal: Principal) -> tuple[str, ...]:
        listed = self.account.service_principal_secrets.list(principal.scim_id)
        return tuple(sorted(text(item.id) for item in listed))

    def policies(self, principal: Principal) -> tuple[Policy, ...]:
        found = []
        for item in self.account.service_principal_federation_policy.list(int(principal.scim_id)):
            oidc = getattr(item, "oidc_policy", None)
            found.append(
                Policy(
                    policy_id=text(getattr(item, "policy_id", None) or getattr(item, "uid", None)),
                    issuer=text(getattr(oidc, "issuer", None)),
                    subject=text(getattr(oidc, "subject", None)),
                    audiences=tuple(sorted(text(a) for a in getattr(oidc, "audiences", None) or ())),
                )
            )
        return tuple(sorted(found, key=lambda policy: policy.policy_id))

    def check_secretless(self, spec: RoleSpec, state: RoleState, token_owners: frozenset[str]) -> None:
        if state.secret_ids:
            self.refuse(
                f"{spec.role}: holds {len(state.secret_ids)} OAuth secret(s) and must hold none; "
                "revoke them in the account console (the bootstrap never revokes)"
            )
        assert state.principal is not None
        if state.principal.scim_id in token_owners:
            self.refuse(f"{spec.role}: owns a personal access or on-behalf-of token and must own none")
        allowed = 0 if spec.environment_suffix is None else 1
        if len(state.policies) > allowed:
            self.refuse(
                f"{spec.role}: holds {len(state.policies)} federation policies; at most {allowed} allowed"
            )
        elif any(policy.issuer != GITHUB_OIDC_ISSUER for policy in state.policies):
            self.refuse(f"{spec.role}: its federation policy trusts an issuer other than GitHub Actions")
        if spec.kind in {"jobs", "validator"} and "ADMIN" in state.permissions:
            self.refuse(f"{spec.role}: is a workspace admin and must not be")

    def assignments(self) -> dict[str, frozenset[str]]:
        found: dict[str, frozenset[str]] = {}
        for item in self.account.workspace_assignment.list(int(self.inputs.workspace_id)):
            principal = getattr(item, "principal", None)
            principal_id = text(getattr(principal, "principal_id", None))
            if principal_id:
                found[principal_id] = frozenset(text(p) for p in getattr(item, "permissions", None) or ())
        return found

    def token_owners(self) -> frozenset[str]:
        """Owners of every PAT and on-behalf-of token in the workspace (admin-only read)."""

        try:
            listed = list(self.workspace.token_management.list())
        except PermissionDenied:
            # Unreadable is not "none": the secretless check fails closed.
            raise BootstrapRefusal(
                ["token inventory: not readable by this session; run as a workspace admin"]
            ) from None
        return frozenset(text(item.owner_id) for item in listed if item.owner_id)

    def identities(self) -> dict[str, RoleState]:
        found = {spec.role: (spec, *self.find(spec)) for spec in roster(self.request.tier)}
        assignments = self.assignments()
        secretless_present = any(spec.secretless and p for spec, p, _ in found.values())
        owners = self.token_owners() if secretless_present else frozenset()
        states: dict[str, RoleState] = {}
        for role, (spec, principal, provenance) in found.items():
            if principal is None:
                states[role] = RoleState(spec, None, provenance)
                continue
            state = RoleState(
                spec,
                principal,
                provenance,
                secret_ids=self.secret_ids(principal),
                policies=self.policies(principal) if spec.secretless else (),
                permissions=assignments.get(principal.scim_id, frozenset()),
            )
            if spec.secretless:
                self.check_secretless(spec, state, owners)
            states[role] = state
        return states

    # --- rule sets, group, interim member, share -----------------------------

    def rule_sets(self, states: Mapping[str, RoleState]) -> dict[str, dict[str, frozenset[str]]]:
        found = {}
        for role, state in states.items():
            if state.principal is None or role == "account_principal":
                continue
            response = self.account.access_control.get_rule_set(
                rule_set_name(self.inputs.account_id, state.principal.application_id), etag=""
            )
            found[role] = {
                text(rule.role): frozenset(text(p) for p in rule.principals or ())
                for rule in getattr(response, "grant_rules", None) or ()
            }
        self.check_secretless_grants(states, found)
        return found

    def account_admin(self, reference: str) -> bool:
        """``users/<name>`` names exactly one account user who holds account admin."""

        name = reference.removeprefix("users/")
        if name not in self.admins:
            matches = []
            if name and "'" not in name:  # an unfilterable name is no admin: fail closed
                listed = self.account.users.list(filter=f"userName eq '{name}'", attributes="id,userName,roles")
                matches = _exact(listed, "user_name", name)
            roles = (getattr(matches[0], "roles", None) or ()) if len(matches) == 1 else ()
            self.admins[name] = ACCOUNT_ADMIN_ROLE in {text(getattr(item, "value", None)) for item in roles}
        return self.admins[name]

    def check_secretless_grants(
        self, states: Mapping[str, RoleState], found: Mapping[str, Mapping[str, frozenset[str]]]
    ) -> None:
        """Only account admins, and the reviewed grants, hold a role on a secretless identity."""

        def reference(role: str) -> str | None:
            state = states.get(role)
            return sp_reference(state.principal.application_id) if state and state.principal else None

        reviewed = {(target, sp_role, reference(grantee)) for target, sp_role, grantee in REVIEWED_GRANTS}
        manager = reference("account_principal")
        lines: list[str] = []
        for target in SECRETLESS_ROLES:
            for sp_role, principals in sorted(found.get(target, {}).items()):
                for principal in sorted(principals):
                    if (target, sp_role, principal) in reviewed:
                        continue
                    if principal == manager:
                        line = (
                            f"account_principal: holds a role on the {target} identity; "
                            "it may manage the reserved M2M identities only"
                        )
                    elif principal.startswith("users/") and self.account_admin(principal):
                        continue
                    else:
                        kind = next((k for p, k in _PRINCIPAL_KINDS.items() if principal.startswith(p)), "a principal")
                        kind += " who is not an account admin" if kind == "a user" else ""
                        line = (
                            f"{target}: {kind} holds {sp_role} on it and could mint it a credential or act "
                            "as it; only account admins may (remove the grant in the account console)"
                        )
                    if line not in lines:
                        lines.append(line)
        for line in lines:
            self.refuse(line)

    def interim_user(self) -> str | None:
        email = self.request.interim_member
        if email is None:
            return None
        matches = _exact(self.account.users.list(filter=f"userName eq '{email}'"), "user_name", email)
        if len(matches) != 1:
            self.refuse("--interim-member: no single account user has that user name")
            return None
        return text(matches[0].id)

    def group(self, states: Mapping[str, RoleState], interim_id: str | None) -> GroupState:
        name = data_owners_group(self.inputs.identity_prefix)
        listed = self.account.groups.list(filter=f"displayName eq '{name}'")
        matches = _exact(listed, "display_name", name)
        adopted = self.request.adopted_group_id
        if not matches:
            if adopted is not None:
                self.refuse(f"--adopt data_owners: no group named {name} exists")
            return GroupState(name, None)
        if len(matches) > 1:
            self.refuse(f"data_owners: more than one group is named {name}")
            return GroupState(name, None)
        group_id = text(matches[0].id)
        record = self.account.groups.get(group_id)
        members = frozenset(text(m.value) for m in getattr(record, "members", None) or ())
        ours = {
            principal.scim_id
            for principal in (states["deployer"].principal, states["jobs"].principal)
            if principal is not None
        }
        allowed = ours | ({interim_id} if interim_id else set())
        if adopted is not None and adopted != group_id:
            self.refuse(f"--adopt data_owners: the group named {name} has a different id")
        elif members - allowed:
            self.refuse(f"data_owners: {name} holds members other than the deployer, jobs and interim identities")
        elif adopted is None and not members & ours:
            self.refuse(
                f"data_owners: a group named {name} exists that this bootstrap did not create; "
                "check it, then name it with --adopt data_owners=<group id>"
            )
        return GroupState(name, group_id, members)

    def _privileges(self, securable_type: str, full_name: str, principal: str) -> frozenset[str]:
        response = self.workspace.grants.get(securable_type, full_name, principal=principal)
        return frozenset(
            text(privilege)
            for assignment in getattr(response, "privilege_assignments", None) or ()
            if text(assignment.principal) == principal
            for privilege in assignment.privileges or ()
        )

    def share(self, group: GroupState) -> ShareState:
        catalog, schema = self.inputs.source_catalog, self.inputs.source_schema
        try:
            self.workspace.catalogs.get(catalog)
            self.workspace.schemas.get(f"{catalog}.{schema}")
        except NotFound:
            deferred = f"share_grants: {catalog}.{schema} does not exist yet; mount the share, then re-run"
            return ShareState(catalog, schema, deferred=deferred)
        except PermissionDenied:
            self.refuse(
                f"share_grants: {catalog}.{schema} is not visible to this session; "
                "run as its owner or a metastore admin"
            )
            return ShareState(catalog, schema)
        if group.group_id is None:
            return ShareState(catalog, schema)
        try:
            return ShareState(
                catalog,
                schema,
                catalog_privileges=self._privileges("catalog", catalog, group.name),
                schema_privileges=self._privileges("schema", f"{catalog}.{schema}", group.name),
            )
        except PermissionDenied:
            self.refuse(f"share_grants: the grants on {catalog}.{schema} are not readable by this session")
            return ShareState(catalog, schema)


def observe(clients: Any, request: BootstrapRequest) -> Observation:
    """Everything the apply plan depends on, read once; refuse what no plan may use."""

    reader = _Reader(clients, request)
    reader.bind_workspace()
    audience = workspace_audience(clients.workspace)
    if not audience.startswith("https://"):
        raise BootstrapRefusal(["DATABRICKS_HOST: the workspace exposes no OIDC token audience"])
    states = reader.identities()
    rule_sets = reader.rule_sets(states)
    interim_id = reader.interim_user()
    group = reader.group(states, interim_id)
    share = reader.share(group)
    if reader.lines:
        raise BootstrapRefusal(reader.lines)
    return Observation(
        request=request,
        audience=audience,
        roles=states,
        group=group,
        interim_user_id=interim_id,
        rule_sets=rule_sets,
        share=share,
    )
