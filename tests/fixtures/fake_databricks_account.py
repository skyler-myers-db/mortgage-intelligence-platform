"""An in-memory Databricks account and workspace for the account-bootstrap tests.

``FakeEstate`` holds service principals, their OAuth secrets and federation
policies, workspace assignments, account groups and users (and which users
are account admins), service-principal rule sets, workspace tokens, the
Cotality source catalog and its grants, and one App. The session's operator
is an account admin, and like the account it gets Service Principal Manager
on every service principal it creates. ``estate.clients()`` returns account and workspace clients whose
API objects carry the SDK's method names and return the SDK's own dataclasses,
so the code under test is the code that would talk to a real account.

**The spy.** Every call is recorded in ``estate.calls`` as
``Call(plane, api, method, args, kwargs)``. Only the reviewed surface exists:
an API or method the bootstrap must never call (a metastore update, any
delete but a secret's, an identity patch) is simply absent, so calling it
fails the test. What exists is checked as it runs, into
``estate.violations``:

* a secret delete of a protected (foreign or secretless) identity, or of a
  secret minted in this test, unless the test expects the failed-handoff
  revocation (``compensation_allowed``); every such delete is recorded in
  ``revoked_minted`` for the test to check;
* a UC grant change that removes a privilege;
* a SCIM group patch that is not a pure ``add``;
* a rule-set update that drops any (role, principal) pair, or grants any
  role whose name holds ``admin``;
* a workspace assignment that lowers a permission;
* a federation-policy update on an identity marked protected.

``assert_safe(estate)`` fails on any violation; the ``estate`` fixtures run
it after every test.

Every value is synthetic: ``acme`` names, all-zero UUIDs and short ids, so
none of it trips the committed-identifier ratchet.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from databricks.sdk.errors import NotFound, PermissionDenied, ResourceConflict
from databricks.sdk.service.apps import App
from databricks.sdk.service.catalog import (
    CatalogInfo,
    GetPermissionsResponse,
    MetastoreAssignment,
    Privilege,
    PrivilegeAssignment,
    SchemaInfo,
)
from databricks.sdk.service.iam import (
    ComplexValue,
    GrantRule,
    Group,
    PatchOp,
    PermissionAssignment,
    PrincipalOutput,
    RuleSetResponse,
    ServicePrincipal,
    User,
    WorkspacePermission,
)
from databricks.sdk.service.oauth2 import (
    CreateServicePrincipalSecretResponse,
    FederationPolicy,
    OidcFederationPolicy,
    SecretInfo,
)
from databricks.sdk.service.settings import TokenInfo

ACCOUNT_ID = "00000000-0000-4000-8000-00000000c001"
METASTORE_ID = "00000000-0000-4000-8000-00000000c002"
WORKSPACE_ID = 4200000101
WORKSPACE_HOST = "https://acme.cloud.databricks.com"
AUDIENCE = f"{WORKSPACE_HOST}/oidc/v1/token"
REPOSITORY = "acme-org/mip-platform"
OPERATOR = "operator@owner.example"
OPERATOR_ID = "9000000000"
SP_MANAGER = "roles/servicePrincipal.manager"
SOURCE_CATALOG = "cotality_source_acme"
SOURCE_SCHEMA = "property_acme"
_FILTER = re.compile(r"(?P<attr>applicationId|displayName|userName) eq '(?P<value>[^']*)'")
_ATTRIBUTE = {"applicationId": "application_id", "displayName": "display_name", "userName": "user_name"}


def app_id(n: int) -> str:
    return f"00000000-0000-4000-8000-{n:012d}"


@dataclass(frozen=True)
class Call:
    plane: str
    api: str
    method: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


@dataclass
class FakeSp:
    scim_id: str
    application_id: str
    display_name: str
    active: bool = True
    roles: tuple[str, ...] = ()


@dataclass
class FakeSecret:
    secret_id: str
    value: str
    minted_here: bool


@dataclass
class FakeEstate:
    calls: list[Call] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    sps: dict[str, FakeSp] = field(default_factory=dict)
    secrets: dict[str, list[FakeSecret]] = field(default_factory=dict)
    policies: dict[str, list[FederationPolicy]] = field(default_factory=dict)
    assignments: dict[str, set[str]] = field(default_factory=dict)
    groups: dict[str, tuple[str, set[str]]] = field(default_factory=dict)
    users: dict[str, str] = field(default_factory=lambda: {OPERATOR_ID: OPERATOR})
    account_admins: set[str] = field(default_factory=lambda: {OPERATOR_ID})
    rule_sets: dict[str, tuple[int, dict[str, set[str]]]] = field(default_factory=dict)
    token_owners: set[str] = field(default_factory=set)
    tokens_readable: bool = True
    catalogs: set[str] = field(default_factory=lambda: {SOURCE_CATALOG})
    schemas: set[str] = field(default_factory=lambda: {f"{SOURCE_CATALOG}.{SOURCE_SCHEMA}"})
    grants: dict[tuple[str, str], dict[str, set[str]]] = field(default_factory=dict)
    apps: dict[str, str] = field(default_factory=dict)
    protected: set[str] = field(default_factory=set)
    workspace_id: int = WORKSPACE_ID
    metastore_id: str = METASTORE_ID
    token_audience: str | None = None
    minted_value: str = "minted-secret-value"
    compensation_allowed: bool = False
    revoked_minted: list[str] = field(default_factory=list)
    secret_delete_error: Exception | None = None
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1001))

    # --- builders ---------------------------------------------------------

    def next_id(self) -> int:
        return next(self._ids)

    def add_sp(self, name: str, *, roles: tuple[str, ...] = (), protected: bool = False, active: bool = True) -> FakeSp:
        n = self.next_id()
        sp = FakeSp(str(7_000_000_000 + n), app_id(n), name, active, roles)
        self.sps[sp.scim_id] = sp
        self.secrets.setdefault(sp.scim_id, [])
        self.policies.setdefault(sp.scim_id, [])
        if protected:
            self.protected.add(sp.scim_id)
        return sp

    def add_secret(self, sp: FakeSp, value: str = "older-secret") -> str:
        secret_id = f"secret-{self.next_id()}"
        self.secrets[sp.scim_id].append(FakeSecret(secret_id, value, minted_here=False))
        return secret_id

    def add_policy(self, sp: FakeSp, subject: str, *, issuer: str = "https://token.actions.githubusercontent.com") -> None:
        policy = FederationPolicy(
            policy_id=f"policy-{self.next_id()}",
            oidc_policy=OidcFederationPolicy(issuer=issuer, subject=subject, audiences=["https://old.example/oidc"]),
        )
        self.policies[sp.scim_id].append(policy)

    def add_group(self, name: str, members: set[str] | None = None) -> str:
        group_id = str(8_000_000_000 + self.next_id())
        self.groups[group_id] = (name, set(members or ()))
        return group_id

    def add_user(self, user_name: str, *, account_admin: bool = False) -> str:
        user_id = str(9_000_000_000 + self.next_id())
        self.users[user_id] = user_name
        if account_admin:
            self.account_admins.add(user_id)
        return user_id

    def rule_set(self, application_id: str) -> dict[str, set[str]]:
        name = f"accounts/{ACCOUNT_ID}/servicePrincipals/{application_id}/ruleSets/default"
        return self.rule_sets.setdefault(name, (0, {}))[1]

    def by_name(self, name: str) -> FakeSp | None:
        found = [sp for sp in self.sps.values() if sp.display_name == name]
        return found[0] if found else None

    def mutations(self) -> list[Call]:
        return [call for call in self.calls if call.method in MUTATING_METHODS]

    def clients(self) -> SimpleNamespace:
        return SimpleNamespace(account=_Account(self), workspace=_Workspace(self))


MUTATING_METHODS = frozenset({"create", "update", "patch", "delete", "update_rule_set"})


def assert_safe(estate: FakeEstate) -> None:
    assert estate.violations == [], estate.violations


class _Api:
    def __init__(self, estate: FakeEstate, plane: str, name: str) -> None:
        self.estate, self.plane, self.name = estate, plane, name

    def record(self, method: str, *args: Any, **kwargs: Any) -> None:
        self.estate.calls.append(Call(self.plane, self.name, method, args, kwargs))

    def violate(self, message: str) -> None:
        self.estate.violations.append(f"{self.name}: {message}")


def _filtered(records: list[Any], expression: str | None) -> list[Any]:
    match = _FILTER.fullmatch(expression or "")
    if match is None:
        raise ValueError("the fake serves exact eq filters only")
    attribute = _ATTRIBUTE[match.group("attr")]
    return [record for record in records if getattr(record, attribute) == match.group("value")]


class _ServicePrincipals(_Api):
    def _view(self, sp: FakeSp) -> ServicePrincipal:
        return ServicePrincipal(
            id=sp.scim_id,
            application_id=sp.application_id,
            display_name=sp.display_name,
            active=sp.active,
            roles=[ComplexValue(value=role) for role in sp.roles] or None,
        )

    def list(self, *, filter: str | None = None) -> list[ServicePrincipal]:  # noqa: A002 - SDK name
        self.record("list", filter=filter)
        return _filtered([self._view(sp) for sp in self.estate.sps.values()], filter)

    def get(self, id: str) -> ServicePrincipal:  # noqa: A002 - SDK name
        self.record("get", id)
        if id not in self.estate.sps:
            raise NotFound("no such service principal")
        return self._view(self.estate.sps[id])

    def create(self, **kwargs: Any) -> ServicePrincipal:
        self.record("create", **kwargs)
        if set(kwargs) - {"display_name", "active"}:
            self.violate("create sets more than a name and active")
        sp = self.estate.add_sp(kwargs["display_name"])
        self.estate.rule_set(sp.application_id).setdefault(SP_MANAGER, set()).add(f"users/{OPERATOR}")
        return self._view(sp)


class _Secrets(_Api):
    def list(self, service_principal_id: str) -> list[SecretInfo]:
        self.record("list", service_principal_id)
        return [SecretInfo(id=s.secret_id, status="ACTIVE") for s in self.estate.secrets[service_principal_id]]

    def create(self, service_principal_id: str, *, lifetime: str | None = None) -> CreateServicePrincipalSecretResponse:
        self.record("create", service_principal_id, lifetime=lifetime)
        secret = FakeSecret(f"secret-{self.estate.next_id()}", self.estate.minted_value, minted_here=True)
        self.estate.secrets[service_principal_id].append(secret)
        return CreateServicePrincipalSecretResponse(id=secret.secret_id, secret=secret.value, status="ACTIVE")

    def delete(self, service_principal_id: str, secret_id: str) -> None:
        self.record("delete", service_principal_id, secret_id)
        if self.estate.secret_delete_error is not None:
            raise self.estate.secret_delete_error
        if service_principal_id in self.estate.protected:
            self.violate("deleted a secret of a protected identity")
        held = self.estate.secrets[service_principal_id]
        target = [s for s in held if s.secret_id == secret_id]
        if not target:
            raise NotFound("no such secret")
        if target[0].minted_here:
            if not self.estate.compensation_allowed:
                self.violate("deleted a secret minted in this run")
            self.estate.revoked_minted.append(secret_id)
        held.remove(target[0])


class _FederationPolicies(_Api):
    def list(self, service_principal_id: int) -> list[FederationPolicy]:
        self.record("list", service_principal_id)
        return list(self.estate.policies[str(service_principal_id)])

    def create(self, service_principal_id: int, policy: FederationPolicy, *, policy_id: str | None = None) -> FederationPolicy:
        self.record("create", service_principal_id, policy=policy)
        created = FederationPolicy(policy_id=f"policy-{self.estate.next_id()}", oidc_policy=policy.oidc_policy)
        self.estate.policies[str(service_principal_id)].append(created)
        return created

    def update(self, service_principal_id: int, policy_id: str, policy: FederationPolicy, *, update_mask: str | None = None) -> FederationPolicy:
        self.record("update", service_principal_id, policy_id, policy=policy, update_mask=update_mask)
        if str(service_principal_id) in self.estate.protected:
            self.violate("updated a protected identity's federation policy")
        held = self.estate.policies[str(service_principal_id)]
        index = next(i for i, item in enumerate(held) if item.policy_id == policy_id)
        held[index] = FederationPolicy(policy_id=policy_id, oidc_policy=policy.oidc_policy)
        return held[index]


class _Assignments(_Api):
    def list(self, workspace_id: int) -> list[PermissionAssignment]:
        self.record("list", workspace_id)
        return [
            PermissionAssignment(
                principal=PrincipalOutput(principal_id=int(principal)),
                permissions=[WorkspacePermission(p) for p in sorted(permissions)],
            )
            for principal, permissions in self.estate.assignments.items()
        ]

    def update(self, workspace_id: int, principal_id: int, *, permissions: list[WorkspacePermission]) -> PermissionAssignment:
        self.record("update", workspace_id, principal_id, permissions=permissions)
        new = {p.value for p in permissions}
        old = self.estate.assignments.get(str(principal_id), set())
        if "ADMIN" in old and "ADMIN" not in new:
            self.violate("lowered a workspace permission")
        self.estate.assignments[str(principal_id)] = new
        return PermissionAssignment(permissions=permissions)


class _Groups(_Api):
    def _view(self, group_id: str) -> Group:
        name, members = self.estate.groups[group_id]
        return Group(id=group_id, display_name=name, members=[ComplexValue(value=m) for m in sorted(members)])

    def list(self, *, filter: str | None = None) -> list[Group]:  # noqa: A002 - SDK name
        self.record("list", filter=filter)
        return _filtered([self._view(g) for g in self.estate.groups], filter)

    def get(self, id: str) -> Group:  # noqa: A002 - SDK name
        self.record("get", id)
        return self._view(id)

    def create(self, *, display_name: str) -> Group:
        self.record("create", display_name=display_name)
        return self._view(self.estate.add_group(display_name))

    def patch(self, id: str, *, operations: list[Any], schemas: list[Any]) -> None:  # noqa: A002 - SDK name
        self.record("patch", id, operations=operations, schemas=schemas)
        for operation in operations:
            if operation.op is not PatchOp.ADD or operation.path != "members":
                self.violate("patched a group with something other than a member add")
                continue
            self.estate.groups[id][1].update(item["value"] for item in operation.value)


class _Users(_Api):
    def list(self, *, filter: str | None = None, attributes: str | None = None) -> list[User]:  # noqa: A002 - SDK name
        self.record("list", filter=filter, attributes=attributes)
        users = [
            User(
                id=i,
                user_name=n,
                roles=[ComplexValue(value="account_admin")] if i in self.estate.account_admins else None,
            )
            for i, n in self.estate.users.items()
        ]
        return _filtered(users, filter)


class _AccessControl(_Api):
    def get_rule_set(self, name: str, etag: str) -> RuleSetResponse:
        self.record("get_rule_set", name, etag=etag)
        version, rules = self.estate.rule_sets.setdefault(name, (0, {}))
        return RuleSetResponse(
            name=name,
            etag=f"etag-{version}",
            grant_rules=[GrantRule(role=r, principals=sorted(p)) for r, p in sorted(rules.items())] or None,
        )

    def update_rule_set(self, name: str, rule_set: Any) -> RuleSetResponse:
        self.record("update_rule_set", name, rule_set=rule_set)
        version, rules = self.estate.rule_sets.setdefault(name, (0, {}))
        if rule_set.etag != f"etag-{version}":
            raise ResourceConflict("stale etag")
        new = {rule.role: set(rule.principals or ()) for rule in rule_set.grant_rules or ()}
        old_pairs = {(r, p) for r, ps in rules.items() for p in ps}
        new_pairs = {(r, p) for r, ps in new.items() for p in ps}
        if old_pairs - new_pairs:
            self.violate("a rule-set update dropped an existing grant")
        if any("admin" in role.casefold() for role, _ in new_pairs - old_pairs):
            self.violate("a rule-set update granted an admin role")
        self.estate.rule_sets[name] = (version + 1, new)
        return self.get_rule_set(name, etag="")


class _Account:
    def __init__(self, estate: FakeEstate) -> None:
        self.service_principals = _ServicePrincipals(estate, "account", "service_principals")
        self.service_principal_secrets = _Secrets(estate, "account", "service_principal_secrets")
        self.service_principal_federation_policy = _FederationPolicies(estate, "account", "service_principal_federation_policy")
        self.workspace_assignment = _Assignments(estate, "account", "workspace_assignment")
        self.groups = _Groups(estate, "account", "groups")
        self.users = _Users(estate, "account", "users")
        self.access_control = _AccessControl(estate, "account", "access_control")


class _Metastores(_Api):
    def current(self) -> MetastoreAssignment:
        self.record("current")
        return MetastoreAssignment(workspace_id=self.estate.workspace_id, metastore_id=self.estate.metastore_id)


class _Tokens(_Api):
    def list(self) -> list[TokenInfo]:
        self.record("list")
        if not self.estate.tokens_readable:
            raise PermissionDenied("only admins can list tokens")
        return [TokenInfo(token_id=f"token-{owner}", owner_id=int(owner)) for owner in sorted(self.estate.token_owners)]


class _Catalogs(_Api):
    def get(self, name: str) -> CatalogInfo:
        self.record("get", name)
        if name not in self.estate.catalogs:
            raise NotFound("no such catalog")
        return CatalogInfo(name=name)


class _Schemas(_Api):
    def get(self, full_name: str) -> SchemaInfo:
        self.record("get", full_name)
        if full_name not in self.estate.schemas:
            raise NotFound("no such schema")
        return SchemaInfo(full_name=full_name)


class _Grants(_Api):
    def get(self, securable_type: str, full_name: str, *, principal: str | None = None) -> GetPermissionsResponse:
        self.record("get", securable_type, full_name, principal=principal)
        held = self.estate.grants.get((securable_type, full_name), {})
        return GetPermissionsResponse(
            privilege_assignments=[
                PrivilegeAssignment(principal=name, privileges=[Privilege(p) for p in sorted(privileges)])
                for name, privileges in sorted(held.items())
                if principal is None or name == principal
            ]
        )

    def update(self, securable_type: str, full_name: str, *, changes: list[Any]) -> None:
        self.record("update", securable_type, full_name, changes=changes)
        held = self.estate.grants.setdefault((securable_type, full_name), {})
        for change in changes:
            if change.remove:
                self.violate("a grant change removed a privilege")
            held.setdefault(change.principal, set()).update(p.value for p in change.add or ())


class _Apps(_Api):
    def get(self, name: str) -> App:
        self.record("get", name)
        if name not in self.estate.apps:
            raise NotFound("no such app")
        return App(name=name, service_principal_client_id=self.estate.apps[name])


class _Workspace:
    def __init__(self, estate: FakeEstate) -> None:
        self._estate = estate
        self.config = SimpleNamespace(
            host=WORKSPACE_HOST,
            token_audience=estate.token_audience,
            databricks_oidc_endpoints=SimpleNamespace(token_endpoint=AUDIENCE),
        )
        self.metastores = _Metastores(estate, "workspace", "metastores")
        self.token_management = _Tokens(estate, "workspace", "token_management")
        self.catalogs = _Catalogs(estate, "workspace", "catalogs")
        self.schemas = _Schemas(estate, "workspace", "schemas")
        self.grants = _Grants(estate, "workspace", "grants")
        self.apps = _Apps(estate, "workspace", "apps")

    def get_workspace_id(self) -> int:
        self._estate.calls.append(Call("workspace", "workspace", "get_workspace_id", (), {}))
        return self._estate.workspace_id
