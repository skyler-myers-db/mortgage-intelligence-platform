"""Provision role-separated M2M identities for live Module 0 automation."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
from databricks.sdk.errors import NotFound, ResourceDoesNotExist  # noqa: E402
from tools.databricks import m2m_access_policy as _access_policy  # noqa: E402
from tools.databricks import m2m_oauth_cli as _cli_helpers  # noqa: E402
from tools.databricks import m2m_oauth_config as _config_helpers  # noqa: E402
from tools.databricks import m2m_oauth_credential_mutation as _credential_mutation  # noqa: E402
from tools.databricks import m2m_oauth_github as _github_helpers  # noqa: E402
from tools.databricks.m2m_identity_contract import (  # noqa: E402
    DEFAULT_ADMIN_GROUP,
    DEFAULT_LAKEBASE_INSTANCE,
    IDENTITY_DEFAULTS,
    IdentityRole,
    ProvisionResult,
    validate_app_access_contract,
    validate_provisioning_contract,
)
from tools.databricks.m2m_provisioning_summary import print_summary as _print_summary  # noqa: E402
from tools.databricks.oauth_credential_boundary import (  # noqa: E402
    app_credential_mutation_boundary,
)
from tools.databricks.oauth_credential_quarantine import (  # noqa: E402
    CredentialMutationQuarantineError,
    CredentialMutationTerminalFenceError,
)

_GH_SECRET_NAME_RE = _github_helpers.GH_SECRET_NAME_RE
_credential_delivery = _credential_mutation.credential_delivery
_gh_available = _github_helpers.gh_available
_confirm_gh_secrets = _github_helpers.confirm_gh_secrets
_invalidate_gh_secrets = _github_helpers.invalidate_gh_secrets
_set_gh_secret = _github_helpers.set_gh_secret
_which = _github_helpers.which
_assert_no_app_permission = _access_policy.assert_no_app_permission
_assert_non_admin_service_principal = _access_policy.assert_non_admin_service_principal
_assert_not_admin_group_member = _access_policy.assert_not_admin_group_member
_ensure_group_membership = _access_policy.ensure_group_membership
_find_group = _access_policy.find_group
_grant_can_use_on_warehouse = _access_policy.grant_can_use_on_warehouse
_grant_can_query_on_endpoint = _access_policy.grant_can_query_on_endpoint
_reserved_gateway_endpoints = _access_policy.reserved_gateway_endpoints
_resolve_effective_groups = _access_policy.resolve_effective_groups
_revoke_can_query_on_obsolete_endpoint = _access_policy.revoke_can_query_on_obsolete_endpoint
_wrap_admin_error = _access_policy.wrap_admin_error
_validate_app_access_contract = validate_app_access_contract
_validate_provisioning_contract = validate_provisioning_contract

DATABRICKS_YML = REPO_ROOT / "databricks.yml"
DOCS_RUNBOOK = _access_policy.DOCS_RUNBOOK
CANONICAL_GH_REPO = "skyler-myers-db/mortgage-intelligence-platform"
_GH_REPO_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


def _diag(msg: str) -> None:
    print(f"[mip-m2m-provision] {msg}", file=sys.stderr)


def _load_app_name_from_bundle(path: Path = DATABRICKS_YML) -> str:
    return _config_helpers.load_deployment_app_name(path)


_resolve_live_app_url = _config_helpers.resolve_live_app_url


def _infer_gh_repo() -> str | None:
    return _config_helpers.infer_gh_repo(REPO_ROOT, runner=subprocess.run)


def _reviewed_gh_repo() -> str | None:
    configured = os.environ.get("MIP_M2M_GITHUB_REPOSITORY", "").strip()
    return configured or _infer_gh_repo()


def _validate_gh_repo(gh_repo: str | None, *, bind_secret_sink: bool = False) -> None:
    """Validate a GitHub target and bind one-shot secrets to the reviewed origin."""
    if gh_repo is not None and not _GH_REPO_PATTERN.fullmatch(gh_repo):
        raise ValueError("--gh-repo must be a valid GitHub owner/repository target")
    if not bind_secret_sink:
        return
    reviewed_repo = _reviewed_gh_repo()
    if reviewed_repo is None or not _GH_REPO_PATTERN.fullmatch(reviewed_repo):
        raise ValueError(
            "Secret minting requires a reviewed GitHub repository from the git origin "
            "or MIP_M2M_GITHUB_REPOSITORY"
        )
    if gh_repo != reviewed_repo:
        raise ValueError(
            f"--gh-repo must match the reviewed credential sink {reviewed_repo!r}; "
            f"refusing target {gh_repo!r}"
        )

def _find_existing_sp(client: Any, display_name: str) -> Any | None:
    """Return the unique exact display-name match, else None.

    SCIM ``filter=displayName eq 'X'`` is the idiomatic lookup. We iterate
    the generator in case the workspace has SPs with similar prefixes and
    the server is flexible about matching; only an exact ``display_name``
    match is accepted. Duplicate reserved-name identities are ambiguous and
    must never receive a newly minted credential.
    """
    filter_expr = f"displayName eq '{display_name}'"
    try:
        candidates = list(client.service_principals.list(filter=filter_expr))
    except Exception as exc:  # noqa: BLE001 — SDK raises a grab-bag of types
        raise _wrap_admin_error(exc, step="list service_principals") from exc
    exact = [sp for sp in candidates if getattr(sp, "display_name", None) == display_name]
    if len(exact) > 1:
        raise SystemExit(
            f"Multiple service principals use reserved display name {display_name!r}; "
            "refusing ambiguous credential or permission provisioning"
        )
    return exact[0] if exact else None


def _create_sp(client: Any, display_name: str) -> Any:
    """Create a new SP with the requested display name."""
    _diag(f"creating service principal display_name={display_name!r}")
    try:
        return client.service_principals.create(
            display_name=display_name,
            active=True,
        )
    except Exception as exc:  # noqa: BLE001
        raise _wrap_admin_error(exc, step="create service_principal") from exc


def _delete_failed_pre_app_service_principal(
    client: Any,
    *,
    sp_id: str | None,
    sp_name: str,
) -> None:
    target_id = str(sp_id or "").strip()
    if not target_id:
        appeared = _find_existing_sp(client, sp_name)
        inventory = "one exact reserved-name identity" if appeared is not None else "no identity"
        raise RuntimeError(
            "ambiguous service-principal create returned no immutable SCIM id; "
            f"post-error inventory found {inventory}, which cannot be safely deleted"
        )
    client.service_principals.delete(target_id)
    try:
        survivor = client.service_principals.get(target_id)
    except (NotFound, ResourceDoesNotExist):
        survivor = None
    if survivor is not None:
        raise RuntimeError("deleted bootstrap service principal ID is still present")
    if _find_existing_sp(client, sp_name) is not None:
        raise RuntimeError("reserved bootstrap service-principal name is still present")


@dataclass
class _PreAppCleanupState:
    client: Any | None = None
    sp_name: str = ""
    sp_id: str | None = None
    armed: bool = False

    def arm(self, *, client: Any, sp_name: str) -> None:
        self.client = client
        self.sp_name = sp_name
        self.armed = True


def _compensate_pre_app_creation(function: Any) -> Any:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> ProvisionResult:
        if "_pre_app_cleanup" in kwargs:
            raise TypeError("pre-App cleanup state is internal")
        state = _PreAppCleanupState()
        try:
            result = function(*args, _pre_app_cleanup=state, **kwargs)
        except (
            CredentialMutationQuarantineError,
            CredentialMutationTerminalFenceError,
        ):
            # An unresolved mutation or a terminal mutation whose lease
            # release is still unproven must preserve the immutable principal.
            # Deleting it outside the signed recovery path would either erase
            # the only attributable target or strand a delivered GitHub sink
            # that still names this principal.
            raise
        except BaseException:
            if state.armed:
                try:
                    _delete_failed_pre_app_service_principal(
                        state.client,
                        sp_id=state.sp_id,
                        sp_name=state.sp_name,
                    )
                except BaseException as cleanup_error:
                    raise RuntimeError(
                        "pre-App identity provisioning failed and the newly created "
                        "service principal could not be removed and proven absent; "
                        "manual security reconciliation is required"
                    ) from cleanup_error
            raise
        state.armed = False
        return result

    return wrapped


def _ensure_lakebase_service_principal_role(
    client: Any,
    *,
    instance_name: str,
    application_id: str,
) -> bool:
    """Assert the safely bootstrapped Lakebase OAuth role exists for the verifier.

    The legacy Database Instances create-role endpoint grants PostgreSQL
    REPLICATION despite exposing only disabled CREATEDB/CREATEROLE/BYPASSRLS
    attributes.  Role creation therefore belongs exclusively to
    ``converge_lakebase_oauth_role``'s documented SQL path.
    """
    try:
        roles = list(client.database.list_database_instance_roles(instance_name))
    except Exception as exc:  # noqa: BLE001
        raise _wrap_admin_error(exc, step="list Lakebase roles") from exc
    existing_role = next(
        (role for role in roles if str(getattr(role, "name", "") or "") == application_id),
        None,
    )
    if existing_role is not None:
        from databricks.sdk.service.database import DatabaseInstanceRoleIdentityType

        identity_type = getattr(existing_role, "identity_type", None)
        identity_type_value = getattr(identity_type, "value", identity_type)
        if identity_type_value != DatabaseInstanceRoleIdentityType.SERVICE_PRINCIPAL.value:
            rendered_type = (
                str(identity_type_value) if identity_type_value is not None else "absent"
            )
            raise SystemExit(
                f"Existing Lakebase role {application_id!r} on instance {instance_name!r} "
                f"has identity_type={rendered_type!r}; verifier grants require "
                "identity_type='SERVICE_PRINCIPAL'. Refusing to reuse a USER or "
                "untyped role."
            )
        _diag(f"Lakebase service-principal role already exists on instance={instance_name!r}")
        return False

    raise SystemExit(
        f"Lakebase verifier role {application_id!r} is absent on {instance_name!r}. "
        "Create it with tools.databricks.converge_lakebase_oauth_role; the legacy "
        "Database Instances role-create API is forbidden because it grants REPLICATION."
    )


def _grant_can_use_on_app(
    client: Any,
    app_name: str,
    sp_application_id: str,
) -> None:
    """Grant CAN USE on the Databricks App to the SP.

    Uses ``AppsAPI.update_permissions`` so provisioning one role does not
    replace another role's existing app ACL. The SDK sends the request to
    ``/api/2.0/preview/permissions/apps/{app_name}/accessControlList``
    under the hood). ``service_principal_name`` expects the SP's
    ``application_id`` (a.k.a. the OAuth client_id), NOT the SCIM ``id``.
    """
    from databricks.sdk.service.apps import (
        AppAccessControlRequest,
        AppPermissionLevel,
    )

    _diag(f"granting CAN_USE on app={app_name} to application_id={sp_application_id}")
    try:
        client.apps.update_permissions(
            app_name=app_name,
            access_control_list=[
                AppAccessControlRequest(
                    service_principal_name=sp_application_id,
                    permission_level=AppPermissionLevel.CAN_USE,
                )
            ],
        )
    except Exception as exc:  # noqa: BLE001
        # Common failure here is "app not found" when the bundle has not
        # been deployed yet. Give the operator that hint directly.
        msg = str(exc).lower()
        if "not found" in msg or "does not exist" in msg:
            raise SystemExit(
                f"App {app_name!r} not found. Run `./scripts/deploy.sh -t dev` "
                "so the signed command of record creates it, then re-run this provisioner."
            ) from exc
        raise _wrap_admin_error(exc, step="update_permissions on app") from exc


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@_compensate_pre_app_creation
def provision(
    *,
    sp_name: str,
    expected_application_id: str | None,
    app_name: str,
    grant_can_use: bool,
    group_name: str | None,
    create_group: bool,
    lakebase_instance: str | None,
    gateway_endpoint: str | None,
    warehouse_id: str | None,
    gh_repo: str | None,
    set_gh_secrets: bool,
    mint_secret: bool,
    rotate: bool,
    app_url: str | None,
    client_id_secret_name: str,
    client_secret_secret_name: str,
    app_url_secret_name: str | None,
    credential_id_secret_name: str | None = None,
    identity_role: IdentityRole = "normal",
    client_factory: Any | None = None,
    revoke_gateway_endpoints: tuple[str, ...] = (),
    preserve_gateway_endpoints: tuple[str, ...] = (),
    pre_app_bootstrap: bool = False,
    _pre_app_cleanup: _PreAppCleanupState | None = None,
    credential_boundary_factory: Callable[..., Any] | None = None,
    credential_writer_application_id: str | None = None,
    gateway_mutation_assertion: Callable[[], None] | None = None,
) -> ProvisionResult:
    """Provision or refresh the M2M SP and return a structured result.

    ``client_factory`` is test-only; otherwise the SDK auth chain resolves the identity.
    """
    if pre_app_bootstrap:
        incompatible = []
        if grant_can_use:
            incompatible.append("App CAN_USE")
        if lakebase_instance:
            incompatible.append("Lakebase instance")
        if gateway_endpoint or revoke_gateway_endpoints or preserve_gateway_endpoints:
            incompatible.append("Gateway endpoint")
        if warehouse_id:
            incompatible.append("SQL warehouse")
        if incompatible:
            raise SystemExit(
                "--pre-app-bootstrap forbids resource access: " + ", ".join(incompatible)
            )
        if not mint_secret or not set_gh_secrets or not gh_repo:
            raise SystemExit(
                "--pre-app-bootstrap requires one-shot OAuth minting through "
                "--set-gh-secrets and a reviewed --gh-repo sink"
            )
    preserved_gateway_endpoints = {
        str(name).strip() for name in preserve_gateway_endpoints if str(name).strip()
    }
    if len(preserved_gateway_endpoints) != len(preserve_gateway_endpoints):
        raise SystemExit("preserved Gateway endpoint names must be non-empty and distinct")
    if preserved_gateway_endpoints and not gateway_endpoint:
        raise SystemExit("--preserve-gateway-endpoint requires --gateway-endpoint")
    if gateway_endpoint in preserved_gateway_endpoints:
        raise SystemExit("the green Gateway cannot also be a preserved signed-blue endpoint")
    try:
        expected_application_id = _validate_provisioning_contract(
            identity_role=identity_role,
            sp_name=sp_name,
            expected_application_id=expected_application_id,
            grant_can_use=grant_can_use,
            group_name=group_name,
            create_group=create_group,
            lakebase_instance=lakebase_instance,
            gateway_endpoint=gateway_endpoint,
            warehouse_id=warehouse_id,
            client_id_secret_name=client_id_secret_name,
            client_secret_secret_name=client_secret_secret_name,
            app_url_secret_name=app_url_secret_name,
            credential_id_secret_name=credential_id_secret_name,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    try:
        _validate_gh_repo(gh_repo, bind_secret_sink=mint_secret)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    credential_source_git_sha = (
        _credential_mutation.credential_source_git_sha(REPO_ROOT)
        if mint_secret else ""
    )
    if client_factory is None:
        def client_factory() -> Any:
            from databricks.sdk import WorkspaceClient
            return WorkspaceClient()
    if create_group and not group_name:
        raise SystemExit("--create-group requires an identity role with --group-name")
    if mint_secret and (not set_gh_secrets or not gh_repo):
        raise SystemExit(
            "Secret minting requires --set-gh-secrets and --gh-repo; "
            "this tool never prints or stores one-shot OAuth secrets locally."
        )
    if mint_secret and not _gh_available():
        raise SystemExit(
            "Secret minting requires an installed, authenticated gh CLI; "
            "run `gh auth login` before retrying."
        )
    for name in (
        client_id_secret_name,
        client_secret_secret_name,
        app_url_secret_name,
        credential_id_secret_name,
    ):
        if name is not None and not _GH_SECRET_NAME_RE.fullmatch(name):
            raise SystemExit(f"Invalid GitHub Actions secret name: {name!r}")

    client = client_factory()
    resolved_app_url = app_url.strip() if app_url else None
    if mint_secret and app_url_secret_name and not pre_app_bootstrap and not resolved_app_url:
        resolved_app_url = _resolve_live_app_url(client, app_name=app_name)

    # A missing identity group is a governance decision, not an incidental SP
    # bootstrap side effect. Fail before creating or mutating any identity
    # unless the operator explicitly approved --create-group.
    if group_name and not create_group and _find_group(client, group_name) is None:
        raise SystemExit(
            f"Required identity group {group_name!r} does not exist. "
            "Re-run with --create-group only after governance review."
        )

    sp = _find_existing_sp(client, sp_name)
    created_sp = False
    cleanup = _pre_app_cleanup
    if sp is None:
        if expected_application_id:
            raise SystemExit(
                f"Service principal {sp_name!r} was not found; refusing to create a new "
                "identity because --expected-application-id was supplied"
            )
        if pre_app_bootstrap:
            assert cleanup is not None
            cleanup.arm(client=client, sp_name=sp_name)
        sp = _create_sp(client, sp_name)
        if pre_app_bootstrap:
            assert cleanup is not None
            cleanup.sp_id = str(getattr(sp, "id", "") or "").strip() or None
            exact = _find_existing_sp(client, sp_name)
            if (
                exact is None
                or not cleanup.sp_id
                or str(getattr(exact, "id", "") or "").strip()
                != cleanup.sp_id
            ):
                raise RuntimeError(
                    "new pre-App service principal did not converge to one exact "
                    "reserved-name immutable identity"
                )
        created_sp = True
        _diag(f"created SP id={sp.id} application_id={sp.application_id}")
    else:
        _diag(f"reusing existing SP id={sp.id} application_id={sp.application_id}")
    if pre_app_bootstrap and not created_sp:
        raise SystemExit(
            "--pre-app-bootstrap is creation-only and refuses every existing service "
            "principal. Use the normal identity flow with --expected-application-id "
            "for a reviewed rotation, or explicitly remove a failed bootstrap identity "
            "before retrying."
        )
    if expected_application_id and sp.application_id != expected_application_id:
        raise SystemExit(
            f"Service principal {sp_name!r} application id does not match the "
            "configured client id; refusing to grant the wrong identity."
        )
    effective_groups: dict[str, str] = _resolve_effective_groups(client, sp_id=sp.id)
    if identity_role != "admin":
        if identity_role != "release_probe":
            _assert_not_admin_group_member(
                group_name=DEFAULT_ADMIN_GROUP,
                effective_groups=effective_groups,
                identity_role=identity_role,
            )
        _assert_non_admin_service_principal(
            client,
            sp_id=sp.id,
            effective_groups=effective_groups,
            identity_role=identity_role,
        )
    if not pre_app_bootstrap and identity_role in {
        "release_probe",
        "verifier",
        "agent_runtime",
        "agent_proxy",
    }:
        _assert_no_app_permission(
            client,
            app_name=app_name,
            sp_application_id=sp.application_id,
            sp_display_name=sp.display_name,
            effective_group_names=set(effective_groups.values()),
            identity_role=identity_role,
        )
    if not pre_app_bootstrap and identity_role in {"agent_runtime", "agent_proxy"}:
        _access_policy.assert_agent_runtime_infrastructure_isolation(
            client,
            instance_name=DEFAULT_LAKEBASE_INSTANCE,
            application_id=sp.application_id,
            effective_group_names=set(effective_groups.values()),
            identity_role=identity_role,
        )
    if not pre_app_bootstrap and identity_role == "verifier":
        _access_policy.assert_lakebase_role_scope(
            client,
            application_id=sp.application_id,
            allowed_instance_names={lakebase_instance} if lakebase_instance else set(),
            identity_role="verifier",
        )
    added_to_group = False
    if group_name:
        target_group = _find_group(client, group_name)
        if target_group is not None and identity_role != "admin":
            target_group_id = str(getattr(target_group, "id", "") or "").strip()
            target_parents = _resolve_effective_groups(client, sp_id=target_group_id)
            _assert_not_admin_group_member(
                group_name=DEFAULT_ADMIN_GROUP,
                effective_groups=target_parents,
                identity_role=identity_role,
            )
            forbidden_parent_names = {
                "admins",
                "account admins",
                "workspace admins",
                "metastore admins",
            }
            if forbidden_parent_names.intersection(
                {name.casefold() for name in target_parents.values()}
            ):
                raise SystemExit(
                    f"Reserved group {group_name!r} is nested into an administrator group; "
                    "remove that privilege before provisioning"
                )
        added_to_group = _ensure_group_membership(
            client,
            group_name=group_name,
            sp_id=sp.id,
            create_group=create_group,
        )
        effective_groups = _resolve_effective_groups(client, sp_id=sp.id)
        if identity_role != "admin":
            if identity_role != "release_probe":
                _assert_not_admin_group_member(
                    group_name=DEFAULT_ADMIN_GROUP,
                    effective_groups=effective_groups,
                    identity_role=identity_role,
                )
            _assert_non_admin_service_principal(
                client,
                sp_id=sp.id,
                effective_groups=effective_groups,
                identity_role=identity_role,
            )
            if not pre_app_bootstrap and identity_role in {
                "release_probe",
                "verifier",
                "agent_runtime",
                "agent_proxy",
            }:
                # Group repair can change effective App access after the first
                # isolation preflight. Re-read every App ACL against the
                # authoritative post-mutation memberships before provisioning
                # any downstream capability or credential.
                _assert_no_app_permission(
                    client,
                    app_name=app_name,
                    sp_application_id=sp.application_id,
                    sp_display_name=sp.display_name,
                    effective_group_names=set(effective_groups.values()),
                    identity_role=identity_role,
                )

    if pre_app_bootstrap:
        # This mode publishes a usable credential before any reviewed App or
        # data-resource grant exists. Prove the final direct+nested group graph
        # is still credential-only immediately before minting.
        _assert_no_app_permission(
            client,
            app_name="",
            sp_application_id=sp.application_id,
            sp_display_name=sp.display_name,
            effective_group_names=set(effective_groups.values()),
            identity_role=identity_role,
        )
        _access_policy.assert_agent_runtime_infrastructure_isolation(
            client,
            instance_name="",
            application_id=sp.application_id,
            effective_group_names=set(effective_groups.values()),
            identity_role=identity_role,
        )
        from tools.databricks.serving_endpoint_acl import (
            audit_global_no_serving_endpoint_access,
        )

        audit_global_no_serving_endpoint_access(
            client,
            service_principal=sp.application_id,
            service_principal_id=sp.id,
            effective_group_names=set(effective_groups.values()),
        )

    if not pre_app_bootstrap and identity_role in {"normal", "operator2", "admin"}:
        _access_policy.assert_no_app_manager_permission(
            client,
            sp_application_id=sp.application_id,
            sp_display_name=sp.display_name,
            effective_group_names=set(effective_groups.values()),
            identity_role=identity_role,
        )
    created_lakebase_role = False
    if lakebase_instance:
        created_lakebase_role = _ensure_lakebase_service_principal_role(
            client,
            instance_name=lakebase_instance,
            application_id=sp.application_id,
        )
    granted_can_query = False
    obsolete_gateway_endpoints = set(revoke_gateway_endpoints)
    if gateway_endpoint:
        obsolete_gateway_endpoints.update(_reserved_gateway_endpoints(client))
    if gateway_endpoint:
        _grant_can_query_on_endpoint(
            client,
            gateway_endpoint,
            sp.application_id,
            sp_id=sp.id,
            effective_group_names=set(effective_groups.values()),
            assert_single_writer=gateway_mutation_assertion,
        )
        granted_can_query = True
    for obsolete_endpoint in sorted(
        obsolete_gateway_endpoints.difference(preserved_gateway_endpoints)
    ):
        if obsolete_endpoint and obsolete_endpoint != gateway_endpoint:
            _revoke_can_query_on_obsolete_endpoint(
                client,
                obsolete_endpoint,
                sp.application_id,
                sp_id=sp.id,
                effective_group_names=set(effective_groups.values()),
                assert_single_writer=gateway_mutation_assertion,
            )

    granted_warehouse_can_use = False
    if warehouse_id:
        _grant_can_use_on_warehouse(
            client,
            warehouse_id,
            sp.application_id,
            effective_group_names=set(effective_groups.values()),
        )
        granted_warehouse_can_use = True

    granted = False
    if grant_can_use:
        _grant_can_use_on_app(client, app_name, sp.application_id)
        granted = True
    else:
        _diag("skipping CAN_USE grant (--grant-can-use=false)")

    # New identities and explicit rotations mint only when the caller enabled
    # the secure GitHub sink. --no-mint-secret supports idempotent grant repair.
    credential_id: str | None = None
    client_id = sp.application_id
    should_mint = mint_secret and (created_sp or rotate)
    if not should_mint and mint_secret:
        _diag(
            "SP already exists and --rotate was not passed; skipping mint. "
            "Pass --rotate to generate a fresh secret."
        )
    elif not mint_secret:
        _diag("skipping OAuth secret mint (--no-mint-secret)")

    wrote_secrets = False
    if should_mint:
        boundary_name = app_name or _load_app_name_from_bundle()
        boundary_factory = (
            credential_boundary_factory or app_credential_mutation_boundary
        )
        boundary_writer = (
            credential_writer_application_id
            or _credential_mutation.credential_lease_writer_application_id(
                client,
                target=sp,
                identity_role=identity_role,
                find_existing_sp=_find_existing_sp,
            )
        )
        assert gh_repo is not None  # validated before any SDK mutation
        credential_id = _credential_mutation.mint_and_deliver_oauth_credential(
            client=client,
            sp_id=sp.id,
            client_id=client_id,
            identity_role=identity_role,
            app_name=boundary_name,
            writer_application_id=boundary_writer,
            source_git_sha=credential_source_git_sha,
            boundary_factory=boundary_factory,
            secret_writer=_set_gh_secret,
            sink_acknowledger=_confirm_gh_secrets,
            secret_invalidator=_invalidate_gh_secrets,
            diagnostic=_diag,
            error_factory=_wrap_admin_error,
            gh_repo=gh_repo,
            client_id_secret_name=client_id_secret_name,
            client_secret_secret_name=client_secret_secret_name,
            credential_id_secret_name=credential_id_secret_name,
            app_url_secret_name=(
                app_url_secret_name if not pre_app_bootstrap else None
            ),
            app_url=resolved_app_url,
            atomic_credential_bundle=identity_role == "agent_proxy",
        )
        wrote_secrets = True
    return ProvisionResult(
        sp_id=sp.id,
        sp_application_id=sp.application_id,
        sp_display_name=sp.display_name,
        created_sp=created_sp,
        granted_can_use=granted,
        group_name=group_name,
        added_to_group=added_to_group,
        lakebase_instance=lakebase_instance,
        created_lakebase_role=created_lakebase_role,
        gateway_endpoint=gateway_endpoint,
        granted_can_query=granted_can_query,
        warehouse_id=warehouse_id,
        granted_warehouse_can_use=granted_warehouse_can_use,
        client_id=client_id,
        credential_id=credential_id,
        secret_minted=should_mint,
        secret_written_to_gh=wrote_secrets,
        gh_repo=gh_repo,
    )


def _build_parser() -> argparse.ArgumentParser:
    return _cli_helpers.build_parser()


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    role: IdentityRole = args.identity_role
    defaults = IDENTITY_DEFAULTS[role]
    if args.pre_app_bootstrap:
        incompatible = []
        if args.app_name is not None:
            incompatible.append("--app-name")
        if args.app_url is not None:
            incompatible.append("--app-url")
        if args.grant_can_use is not None:
            incompatible.append("--grant-can-use/--no-grant-can-use")
        if args.lakebase_instance is not None:
            incompatible.append("--lakebase-instance")
        if (
            args.gateway_endpoint is not None
            or args.revoke_gateway_endpoint
            or args.preserve_gateway_endpoint
        ):
            incompatible.append(
                "--gateway-endpoint/--revoke-gateway-endpoint/--preserve-gateway-endpoint"
            )
        if args.warehouse_id is not None:
            incompatible.append("--warehouse-id")
        if args.no_app_url_secret or args.app_url_secret_name is not None:
            incompatible.append("--app-url-secret-name/--no-app-url-secret")
        if incompatible:
            parser.error(
                "--pre-app-bootstrap forbids resource or App-sink options: "
                + ", ".join(incompatible)
            )
        if not args.mint_secret or not args.set_gh_secrets:
            parser.error("--pre-app-bootstrap requires OAuth minting through --set-gh-secrets")
    grant_can_use = (
        False
        if args.pre_app_bootstrap
        else defaults.grant_can_use
        if args.grant_can_use is None
        else args.grant_can_use
    )
    sp_name = args.sp_name or defaults.sp_name
    group_name = args.group_name or defaults.group_name
    lakebase_instance = (
        None if args.pre_app_bootstrap else args.lakebase_instance or defaults.lakebase_instance
    )
    client_id_secret_name = args.client_id_secret_name or defaults.client_id_secret_name
    client_secret_secret_name = args.client_secret_secret_name or defaults.client_secret_secret_name
    app_url_secret_name = args.app_url_secret_name or defaults.app_url_secret_name
    credential_id_secret_name = args.credential_id_secret_name or defaults.credential_id_secret_name
    if args.no_app_url_secret:
        app_url_secret_name = None
    try:
        expected_application_id = _validate_provisioning_contract(
            identity_role=role,
            sp_name=sp_name,
            expected_application_id=args.expected_application_id,
            grant_can_use=grant_can_use,
            group_name=group_name,
            create_group=args.create_group,
            lakebase_instance=lakebase_instance,
            gateway_endpoint=args.gateway_endpoint,
            warehouse_id=args.warehouse_id,
            client_id_secret_name=client_id_secret_name,
            client_secret_secret_name=client_secret_secret_name,
            app_url_secret_name=app_url_secret_name,
            credential_id_secret_name=credential_id_secret_name,
        )
    except ValueError as exc:
        parser.error(str(exc))

    if args.gh_repo is not None:
        try:
            _validate_gh_repo(args.gh_repo)
        except ValueError as exc:
            parser.error(str(exc))

    app_name = "" if args.pre_app_bootstrap else args.app_name or _load_app_name_from_bundle()
    app_url = args.app_url or os.environ.get("MIP_APP_URL")
    gh_repo = args.gh_repo or _infer_gh_repo()
    try:
        _validate_gh_repo(gh_repo, bind_secret_sink=args.mint_secret)
    except ValueError as exc:
        parser.error(str(exc))

    _diag(
        f"provisioning plan: identity_role={role!r} sp_name={sp_name!r} "
        f"pre_app_bootstrap={args.pre_app_bootstrap} app_name={app_name!r} "
        f"group_name={group_name!r} "
        f"create_group={args.create_group} lakebase_instance={lakebase_instance!r} "
        f"warehouse_id={args.warehouse_id!r} "
        f"gh_repo={gh_repo!r} set_gh_secrets={args.set_gh_secrets} rotate={args.rotate}"
    )

    if args.dry_run:
        _diag("--dry-run requested; no SDK calls will be made")
        if args.set_gh_secrets and not gh_repo:
            _diag("WARNING: --set-gh-secrets requires --gh-repo (or a detectable origin)")
        if args.mint_secret and not args.set_gh_secrets:
            _diag("NOTE: a real secret mint would require --set-gh-secrets")
        if args.set_gh_secrets and not _gh_available():
            _diag("NOTE: gh CLI unavailable; a real secret mint would fail closed")
        return 0

    try:
        result = provision(
            sp_name=sp_name,
            expected_application_id=expected_application_id,
            app_name=app_name,
            grant_can_use=grant_can_use,
            group_name=group_name,
            create_group=args.create_group,
            lakebase_instance=lakebase_instance,
            gateway_endpoint=args.gateway_endpoint,
            warehouse_id=args.warehouse_id,
            gh_repo=gh_repo,
            set_gh_secrets=args.set_gh_secrets,
            mint_secret=args.mint_secret,
            rotate=args.rotate,
            app_url=app_url,
            client_id_secret_name=client_id_secret_name,
            client_secret_secret_name=client_secret_secret_name,
            app_url_secret_name=app_url_secret_name,
            credential_id_secret_name=credential_id_secret_name,
            identity_role=role,
            revoke_gateway_endpoints=tuple(args.revoke_gateway_endpoint),
            preserve_gateway_endpoints=tuple(args.preserve_gateway_endpoint),
            pre_app_bootstrap=args.pre_app_bootstrap,
        )
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 — surface root cause
        _diag(f"ERROR unhandled {type(exc).__name__}: {exc}")
        return 1

    _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
