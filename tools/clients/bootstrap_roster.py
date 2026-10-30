"""The identities one client's account bootstrap creates or adopts (plan §4.3).

Order is the spec's (M2-PR1): deployer, jobs, validator, the reserved M2M
identities, then the account principal. The bootstrap plans and applies in
this order, so the deployer's federation subject is set before any later
step can depend on it, and before its own ADMIN assignment.

Display names derive from ``MIP_IDENTITY_PREFIX``. The reserved M2M names
come from ``tools/databricks/m2m_identity_contract.IDENTITY_DEFAULTS`` with
its ``mip-`` prefix swapped for the client's, so the default prefix keeps
exactly the names the deploy tooling already looks up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from tools.databricks.m2m_identity_contract import IDENTITY_DEFAULTS, IdentityRole

Kind = Literal["deployer", "jobs", "validator", "m2m", "account_principal"]
Tier = Literal["core", "agentic"]

GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
DATA_OWNERS_ROLE = "data_owners"
VALIDATE_ENVIRONMENT_SUFFIX = "-validate"
ACCOUNT_PRINCIPAL_NAME_SUFFIX = "account-scim-ci-sp"
# The reserved roles' names in the M2M contract, where they differ.
_CONTRACT_ROLE: dict[str, IdentityRole] = {"runtime": "agent_runtime", "proxy": "agent_proxy"}
_CONTRACT_PREFIX = "mip-"


@dataclass(frozen=True)
class RoleSpec:
    """One identity: what it is, where it is assigned, how it authenticates."""

    role: str
    kind: Kind
    name_suffix: str
    # The workspace permission it needs; ``None`` for no workspace assignment.
    assignment: Literal["ADMIN", "USER"] | None
    # GitHub Environment suffix of its one federation policy; ``None`` for none.
    environment_suffix: str | None
    # The profile secret its M2M credential lands in; ``None`` for no secret.
    secret_field: str | None
    # The account principal gets Service Principal Manager on it.
    managed_by_account_principal: bool
    agentic: bool = False

    def display_name(self, prefix: str) -> str:
        return f"{prefix}-{self.name_suffix}"

    @property
    def secretless(self) -> bool:
        """The deployer, jobs and validator identities may never hold a credential."""

        return self.kind in {"deployer", "jobs", "validator"}


def _reserved(role: str, secret_field: str, *, agentic: bool = False) -> RoleSpec:
    contract_name = IDENTITY_DEFAULTS[cast(IdentityRole, _CONTRACT_ROLE.get(role, role))].sp_name
    if not contract_name.startswith(_CONTRACT_PREFIX):
        raise RuntimeError(f"reserved identity {role} has an unexpected contract name")
    return RoleSpec(
        role=role,
        kind="m2m",
        name_suffix=contract_name.removeprefix(_CONTRACT_PREFIX),
        assignment="USER",
        environment_suffix=None,
        secret_field=secret_field,
        managed_by_account_principal=True,
        agentic=agentic,
    )


ROSTER: tuple[RoleSpec, ...] = (
    RoleSpec("deployer", "deployer", "deployer", "ADMIN", "", None, False),
    RoleSpec("jobs", "jobs", "jobs", "USER", None, None, False),
    RoleSpec("validator", "validator", "validator", "USER", VALIDATE_ENVIRONMENT_SUFFIX, None, False),
    _reserved("normal", "DATABRICKS_CLIENT_SECRET"),
    _reserved("operator2", "DATABRICKS_OPERATOR2_CLIENT_SECRET"),
    _reserved("admin", "DATABRICKS_ADMIN_CLIENT_SECRET"),
    _reserved("release_probe", "DATABRICKS_RELEASE_PROBE_CLIENT_SECRET"),
    _reserved("verifier", "DATABRICKS_VERIFIER_CLIENT_SECRET", agentic=True),
    _reserved("runtime", "DATABRICKS_AGENT_RUNTIME_CLIENT_SECRET", agentic=True),
    _reserved("proxy", "DATABRICKS_AGENT_PROXY_CREDENTIAL_BUNDLE", agentic=True),
    RoleSpec(
        "account_principal",
        "account_principal",
        ACCOUNT_PRINCIPAL_NAME_SUFFIX,
        None,
        None,
        "DATABRICKS_ACCOUNT_CLIENT_SECRET",
        False,
    ),
)
ROLES = tuple(spec.role for spec in ROSTER)


def roster(tier: Tier) -> tuple[RoleSpec, ...]:
    """The identities of ``tier``, in bootstrap order."""

    return tuple(spec for spec in ROSTER if tier == "agentic" or not spec.agentic)


def spec_for(role: str) -> RoleSpec:
    for spec in ROSTER:
        if spec.role == role:
            return spec
    raise KeyError(role)


def data_owners_group(prefix: str) -> str:
    return f"{prefix}-data-owners"


def federation_subject(repository: str, client: str, spec: RoleSpec) -> str:
    """The default GitHub OIDC subject of a job bound to the role's environment."""

    if spec.environment_suffix is None:
        raise ValueError(f"{spec.role} has no federation policy")
    return f"repo:{repository}:environment:{client}{spec.environment_suffix}"
