"""Provision distinct M2M identities for live Module 0 automation.

Zero-click replacement for the manual Accounts-Console workflow
documented in ``docs/security/m2m-oauth-setup.md``. The user's explicit
requirement is that the full M2M setup is scripted in this repo — no
workspace UI steps.

What this tool does (in order):

1. Create (or reuse) a workspace service principal with the requested
   ``--sp-name``. Idempotent: an SP whose ``displayName`` already matches
   is re-used rather than duplicated.
2. For normal app-access and admin identities, grant ``CAN USE`` on the
   deployed Databricks App resource (``--app-name``). The verifier role
   rejects this grant even when ``--grant-can-use`` is supplied explicitly.
3. For the verifier identity, grant ``CAN USE`` on the exact SQL warehouse
   used to validate AI Gateway inference rows. The verifier still receives no
   Databricks App access and is never an admin-group member.
4. Mint an OAuth client_id + client_secret for the SP. A live mint requires
   ``--set-gh-secrets`` and pipes the one-shot secret directly to ``gh``.
   Secret names are configurable so normal, admin, and verifier identities
   never share one client credential.

5. Optionally rotate an existing SP's secret (``--rotate``). The old
   secret stays valid until the admin revokes it in the Accounts Console
   — same zero-downtime rotation cadence as the manual flow.

Safety invariants
-----------------
* The secret never touches the repo, a local file, stdout, or stderr. It is
  passed only to the ``gh`` subprocess via stdin.
* A ``--dry-run`` flag exercises every argument-parsing + SDK-surface
  check without touching the workspace, for CI-safe import coverage.
* SDK failures that indicate "you are not a workspace admin" surface a
  pointed message directing the reader to
  ``docs/security/m2m-oauth-setup.md`` appendix (manual UI path) rather
  than a stack trace.

Usage
-----
    # canonical happy path (admin auth resolved from ~/.databrickscfg)
    python tools/databricks/provision_m2m_oauth.py \\
        --sp-name mip-nightly-ci-sp \\
        --app-name mip-app \\
        --gh-repo skyler-myers-db/mortgage-intelligence-platform \\
        --set-gh-secrets

    # rotate the existing SP's secret
    python tools/databricks/provision_m2m_oauth.py \\
        --sp-name mip-nightly-ci-sp --rotate --set-gh-secrets

    # CI-safe shape-check (no workspace calls)
    python tools/databricks/provision_m2m_oauth.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.databricks import m2m_access_policy as _access_policy  # noqa: E402
from tools.databricks import m2m_oauth_github as _github_helpers  # noqa: E402
from tools.databricks.m2m_identity_contract import (  # noqa: E402
    DEFAULT_ADMIN_GROUP,
    DEFAULT_LAKEBASE_INSTANCE,
    IDENTITY_DEFAULTS,
    IdentityRole,
    ProvisionResult,
)

_GH_SECRET_NAME_RE = _github_helpers.GH_SECRET_NAME_RE
_gh_available = _github_helpers.gh_available
_set_gh_secret = _github_helpers.set_gh_secret
_which = _github_helpers.which
_assert_no_app_permission = _access_policy.assert_no_app_permission
_assert_not_admin_group_member = _access_policy.assert_not_admin_group_member
_ensure_group_membership = _access_policy.ensure_group_membership
_find_group = _access_policy.find_group
_resolve_effective_groups = _access_policy.resolve_effective_groups
_wrap_admin_error = _access_policy.wrap_admin_error

DATABRICKS_YML = REPO_ROOT / "databricks.yml"
DOCS_RUNBOOK = _access_policy.DOCS_RUNBOOK
# Deployed App URL. Written as a GitHub secret alongside the client id/secret
# so the workflow's deployed-path detection flips on in a single admin pass.
DEFAULT_APP_URL = "https://mip-app-2543889327043640.aws.databricksapps.com"
_VERIFIER_APP_ACCESS_ERROR = (
    "--identity-role verifier forbids Databricks App CAN_USE; "
    "remove --grant-can-use before provisioning"
)


def _diag(msg: str) -> None:
    """Stderr diagnostic. Keeps stdout clean for scripted consumers."""
    print(f"[mip-m2m-provision] {msg}", file=sys.stderr)


def _validate_app_access_contract(*, identity_role: IdentityRole, grant_can_use: bool) -> None:
    """Reject verifier App access before any workspace or secret side effect."""
    if identity_role == "verifier" and grant_can_use:
        raise ValueError(_VERIFIER_APP_ACCESS_ERROR)


def _load_app_name_from_bundle(path: Path = DATABRICKS_YML) -> str:
    """Best-effort parse of the deployed App name out of databricks.yml.

    We want a default that matches what ``databricks bundle deploy`` will
    actually create, without pulling PyYAML in just for this lookup —
    a shallow regex over the known shape is sufficient and keeps this
    tool's dependency surface to the already-present databricks-sdk.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return "mip-app"
    # Look for
    #   apps:
    #     mip_app:
    #       name: mip-app
    match = re.search(
        r"^\s+apps:\s*\n(?:\s+#[^\n]*\n)*\s+\w+:\s*\n\s+name:\s*([A-Za-z0-9_-]+)",
        text,
        re.MULTILINE,
    )
    if match:
        return match.group(1)
    return "mip-app"


def _infer_gh_repo() -> str | None:
    """Infer ``owner/repo`` from the ``origin`` remote. None if unresolved."""
    try:
        out = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    # Accept both SSH (git@github.com:owner/repo.git) and HTTPS
    # (https://github.com/owner/repo[.git]).
    match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.\s]+)", out)
    if not match:
        return None
    return f"{match.group('owner')}/{match.group('repo')}"


# ---------------------------------------------------------------------------
# Core steps (each accepts an injected client so unit tests can mock)
# ---------------------------------------------------------------------------


def _find_existing_sp(client: Any, display_name: str) -> Any | None:
    """Return the first SP whose displayName matches exactly, else None.

    SCIM ``filter=displayName eq 'X'`` is the idiomatic lookup. We iterate
    the generator in case the workspace has SPs with similar prefixes and
    the server is flexible about matching; only an exact ``display_name``
    match is accepted.
    """
    filter_expr = f"displayName eq '{display_name}'"
    try:
        candidates = list(client.service_principals.list(filter=filter_expr))
    except Exception as exc:  # noqa: BLE001 — SDK raises a grab-bag of types
        raise _wrap_admin_error(exc, step="list service_principals") from exc
    for sp in candidates:
        if getattr(sp, "display_name", None) == display_name:
            return sp
    return None


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


def _ensure_lakebase_service_principal_role(
    client: Any,
    *,
    instance_name: str,
    application_id: str,
) -> bool:
    """Ensure a non-superuser Lakebase OAuth role exists for the verifier."""
    try:
        roles = list(client.database.list_database_instance_roles(instance_name))
    except Exception as exc:  # noqa: BLE001
        raise _wrap_admin_error(exc, step="list Lakebase roles") from exc
    if any(str(getattr(role, "name", "") or "") == application_id for role in roles):
        _diag(f"Lakebase service-principal role already exists on instance={instance_name!r}")
        return False

    from databricks.sdk.service.database import (
        DatabaseInstanceRole,
        DatabaseInstanceRoleIdentityType,
    )

    _diag(f"creating Lakebase verifier role on instance={instance_name!r}")
    try:
        client.database.create_database_instance_role(
            instance_name,
            DatabaseInstanceRole(
                name=application_id,
                identity_type=DatabaseInstanceRoleIdentityType.SERVICE_PRINCIPAL,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        raise _wrap_admin_error(exc, step="create Lakebase verifier role") from exc
    return True


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
                f"App {app_name!r} not found. Run `databricks bundle deploy -t dev` "
                "first so the App resource exists, then re-run this provisioner."
            ) from exc
        raise _wrap_admin_error(exc, step="update_permissions on app") from exc


def _grant_can_query_on_endpoint(
    client: Any,
    endpoint_name: str,
    sp_application_id: str,
) -> None:
    """Add the verifier's CAN_QUERY grant without replacing other endpoint ACLs."""
    from databricks.sdk.service.serving import (
        ServingEndpointAccessControlRequest,
        ServingEndpointPermissionLevel,
    )

    _diag(f"resolving serving endpoint id for endpoint={endpoint_name!r}")
    try:
        endpoint = client.serving_endpoints.get(endpoint_name)
        endpoint_id = str(getattr(endpoint, "id", "") or "").strip()
        if not endpoint_id:
            raise ValueError(f"serving endpoint {endpoint_name!r} has no immutable id")
        _diag(f"granting CAN_QUERY on endpoint={endpoint_name!r} to verifier identity")
        client.serving_endpoints.update_permissions(
            endpoint_id,
            access_control_list=[
                ServingEndpointAccessControlRequest(
                    service_principal_name=sp_application_id,
                    permission_level=ServingEndpointPermissionLevel.CAN_QUERY,
                )
            ],
        )
    except Exception as exc:  # noqa: BLE001
        raise _wrap_admin_error(exc, step="update serving endpoint permissions") from exc


def _grant_can_use_on_warehouse(
    client: Any,
    warehouse_id: str,
    sp_application_id: str,
) -> None:
    """Add verifier CAN_USE on one SQL warehouse without replacing ACLs."""
    from databricks.sdk.service.sql import (
        WarehouseAccessControlRequest,
        WarehousePermissionLevel,
    )

    _diag(f"granting CAN_USE on warehouse={warehouse_id!r} to verifier identity")
    try:
        client.warehouses.update_permissions(
            warehouse_id,
            access_control_list=[
                WarehouseAccessControlRequest(
                    service_principal_name=sp_application_id,
                    permission_level=WarehousePermissionLevel.CAN_USE,
                )
            ],
        )
    except Exception as exc:  # noqa: BLE001
        raise _wrap_admin_error(exc, step="update SQL warehouse permissions") from exc


def _mint_oauth_secret(client: Any, sp_id: str) -> Any:
    """Mint a new OAuth client_secret for the SP. Returned once, never again."""
    _diag(f"minting OAuth secret for service_principal_id={sp_id}")
    try:
        return client.service_principal_secrets_proxy.create(service_principal_id=sp_id)
    except Exception as exc:  # noqa: BLE001
        raise _wrap_admin_error(exc, step="mint OAuth secret") from exc


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


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
    app_url: str,
    client_id_secret_name: str,
    client_secret_secret_name: str,
    app_url_secret_name: str | None,
    identity_role: IdentityRole = "normal",
    client_factory: Any | None = None,
) -> ProvisionResult:
    """Provision or refresh the M2M SP and return a structured result.

    ``client_factory`` exists for unit tests — when None we resolve a
    real ``WorkspaceClient`` via the SDK's standard auth chain (env
    vars → ``~/.databrickscfg`` → workspace identity).
    """
    try:
        _validate_app_access_contract(
            identity_role=identity_role,
            grant_can_use=grant_can_use,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

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
    ):
        if name is not None and not _GH_SECRET_NAME_RE.fullmatch(name):
            raise SystemExit(f"Invalid GitHub Actions secret name: {name!r}")

    client = client_factory()

    # A missing admin group is a governance decision, not an incidental SP
    # bootstrap side effect. Fail before creating or mutating any identity
    # unless the operator explicitly approved --create-group.
    if group_name and not create_group and _find_group(client, group_name) is None:
        raise SystemExit(
            f"Required admin group {group_name!r} does not exist. "
            "Re-run with --create-group only after governance review."
        )

    sp = _find_existing_sp(client, sp_name)
    created_sp = False
    if sp is None:
        if expected_application_id:
            raise SystemExit(
                f"Service principal {sp_name!r} was not found; refusing to create a new "
                "identity because --expected-application-id was supplied"
            )
        sp = _create_sp(client, sp_name)
        created_sp = True
        _diag(f"created SP id={sp.id} application_id={sp.application_id}")
    else:
        _diag(f"reusing existing SP id={sp.id} application_id={sp.application_id}")
    if expected_application_id and sp.application_id != expected_application_id:
        raise SystemExit(
            f"Service principal {sp_name!r} application id does not match the "
            "configured client id; refusing to grant the wrong identity."
        )

    effective_groups: dict[str, str] = {}
    if identity_role != "admin":
        effective_groups = _resolve_effective_groups(client, sp_id=sp.id)
        _assert_not_admin_group_member(
            group_name=DEFAULT_ADMIN_GROUP,
            effective_groups=effective_groups,
            identity_role=identity_role,
        )
    if identity_role == "verifier":
        _assert_no_app_permission(
            client,
            app_name=app_name,
            sp_application_id=sp.application_id,
            sp_display_name=sp.display_name,
            effective_group_names=set(effective_groups.values()),
        )

    added_to_group = False
    if group_name:
        added_to_group = _ensure_group_membership(
            client,
            group_name=group_name,
            sp_id=sp.id,
            create_group=create_group,
        )

    created_lakebase_role = False
    if lakebase_instance:
        created_lakebase_role = _ensure_lakebase_service_principal_role(
            client,
            instance_name=lakebase_instance,
            application_id=sp.application_id,
        )

    granted_can_query = False
    if gateway_endpoint:
        _grant_can_query_on_endpoint(client, gateway_endpoint, sp.application_id)
        granted_can_query = True

    granted_warehouse_can_use = False
    if warehouse_id:
        _grant_can_use_on_warehouse(client, warehouse_id, sp.application_id)
        granted_warehouse_can_use = True

    granted = False
    if grant_can_use:
        _grant_can_use_on_app(client, app_name, sp.application_id)
        granted = True
    else:
        _diag("skipping CAN_USE grant (--grant-can-use=false)")

    # New identities and explicit rotations mint only when the caller enabled
    # the secure GitHub sink. --no-mint-secret supports idempotent grant repair.
    secret_value: str | None = None
    client_id = sp.application_id
    should_mint = mint_secret and (created_sp or rotate)
    if should_mint:
        resp = _mint_oauth_secret(client, sp.id)
        secret_value = getattr(resp, "secret", None)
        if not secret_value:
            raise SystemExit(
                "mint returned no .secret value; SDK contract violation. "
                f"Response fields: {list(resp.__dict__.keys()) if hasattr(resp, '__dict__') else 'unknown'}"
            )
    elif mint_secret:
        _diag(
            "SP already exists and --rotate was not passed; skipping mint. "
            "Pass --rotate to generate a fresh secret."
        )
    else:
        _diag("skipping OAuth secret mint (--no-mint-secret)")

    wrote_secrets = False
    if secret_value is not None:
        assert gh_repo is not None  # validated before any SDK mutation
        _set_gh_secret(gh_repo, client_secret_secret_name, secret_value)
        _set_gh_secret(gh_repo, client_id_secret_name, client_id)
        if app_url_secret_name:
            _set_gh_secret(gh_repo, app_url_secret_name, app_url)
        wrote_secrets = True
        secret_value = None

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
        secret_minted=should_mint,
        secret_written_to_gh=wrote_secrets,
        gh_repo=gh_repo,
    )


def _print_summary(result: ProvisionResult) -> None:
    """Human-readable, secret-free summary on stderr."""
    lines = [
        "",
        "=== M2M OAuth provisioning summary ===",
        f"  service_principal:        {result.sp_display_name} (id={result.sp_id})",
        f"  application_id (client_id): {result.client_id}",
        f"  created this run:         {result.created_sp}",
        f"  granted CAN_USE on app:   {result.granted_can_use}",
        f"  admin group:              {result.group_name or '(none)'}",
        f"  group membership added:   {result.added_to_group}",
        f"  Lakebase instance:        {result.lakebase_instance or '(none)'}",
        f"  Lakebase role created:    {result.created_lakebase_role}",
        f"  Gateway endpoint:         {result.gateway_endpoint or '(none)'}",
        f"  granted CAN_QUERY:        {result.granted_can_query}",
        f"  SQL warehouse:            {result.warehouse_id or '(none)'}",
        f"  granted warehouse CAN_USE:{result.granted_warehouse_can_use}",
        f"  OAuth secret minted:      {result.secret_minted}",
        f"  GitHub secrets updated:   {result.secret_written_to_gh}",
    ]
    if result.secret_written_to_gh:
        lines.append(f"  gh repo:                  {result.gh_repo}")
    lines.append("")
    for line in lines:
        _diag(line)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="provision_m2m_oauth",
        description=(
            "Create or converge a normal, admin, or verifier M2M identity "
            "without printing one-shot OAuth secrets."
        ),
    )
    parser.add_argument(
        "--identity-role",
        choices=tuple(IDENTITY_DEFAULTS),
        default="normal",
        help="Identity contract to provision (default: normal).",
    )
    parser.add_argument(
        "--sp-name",
        default=None,
        help="Override the role-specific service-principal display name.",
    )
    parser.add_argument(
        "--expected-application-id",
        default=None,
        help="Fail closed unless the resolved SP has this OAuth application/client id.",
    )
    parser.add_argument(
        "--app-name",
        default=None,
        help=(
            "Deployed App name to grant CAN_USE on "
            f"(default: resolved from {DATABRICKS_YML.name})."
        ),
    )
    parser.add_argument(
        "--app-url",
        default=os.environ.get("MIP_APP_URL", DEFAULT_APP_URL),
        help="Deployed App URL written as MIP_APP_URL GitHub secret.",
    )
    parser.add_argument(
        "--grant-can-use",
        dest="grant_can_use",
        action="store_true",
        default=None,
        help="Grant CAN_USE on the App to the SP.",
    )
    parser.add_argument(
        "--no-grant-can-use",
        dest="grant_can_use",
        action="store_false",
        help="Skip the CAN_USE grant.",
    )
    parser.add_argument(
        "--group-name",
        default=None,
        help=f"Admin group override (admin default: {DEFAULT_ADMIN_GROUP}).",
    )
    parser.add_argument(
        "--create-group",
        action="store_true",
        help=(
            "Create the configured admin group if absent. Without this explicit "
            "flag, a missing group fails closed."
        ),
    )
    parser.add_argument(
        "--lakebase-instance",
        default=None,
        help=(
            "Provision an OAuth role on this Lakebase instance "
            f"(verifier default: {DEFAULT_LAKEBASE_INSTANCE})."
        ),
    )
    parser.add_argument(
        "--gateway-endpoint",
        default=None,
        help="Serving endpoint on which the verifier receives CAN_QUERY.",
    )
    parser.add_argument(
        "--warehouse-id",
        default=None,
        help="SQL warehouse on which the verifier receives CAN_USE.",
    )
    parser.add_argument(
        "--gh-repo",
        default=None,
        help="GitHub repo owner/name (default: inferred from `git remote get-url origin`).",
    )
    parser.add_argument(
        "--set-gh-secrets",
        action="store_true",
        help=(
            "Write client_id / client_secret / MIP_APP_URL to the GitHub repo's "
            "Actions secrets via the `gh` CLI. Requires `gh auth login`."
        ),
    )
    parser.add_argument(
        "--client-id-secret-name",
        default=None,
        help="GitHub Actions secret name for this identity's OAuth client id.",
    )
    parser.add_argument(
        "--client-secret-secret-name",
        default=None,
        help="GitHub Actions secret name for this identity's OAuth client secret.",
    )
    parser.add_argument(
        "--app-url-secret-name",
        default=None,
        help="GitHub Actions secret name for the app URL (normal default: MIP_APP_URL).",
    )
    parser.add_argument(
        "--no-app-url-secret",
        action="store_true",
        help="Do not write an app URL secret for this identity.",
    )
    parser.add_argument(
        "--no-mint-secret",
        dest="mint_secret",
        action="store_false",
        default=True,
        help="Converge grants/membership without minting or rotating an OAuth secret.",
    )
    parser.add_argument(
        "--rotate",
        action="store_true",
        help=(
            "If the SP already exists, mint a fresh OAuth secret. Old secret "
            "stays valid until revoked in the Accounts Console."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve defaults and validate the argument set; no SDK calls.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    role: IdentityRole = args.identity_role
    defaults = IDENTITY_DEFAULTS[role]
    grant_can_use = defaults.grant_can_use if args.grant_can_use is None else args.grant_can_use
    try:
        _validate_app_access_contract(
            identity_role=role,
            grant_can_use=grant_can_use,
        )
    except ValueError as exc:
        parser.error(str(exc))

    app_name = args.app_name or _load_app_name_from_bundle()
    gh_repo = args.gh_repo or _infer_gh_repo()
    sp_name = args.sp_name or defaults.sp_name
    group_name = args.group_name or defaults.group_name
    lakebase_instance = args.lakebase_instance or defaults.lakebase_instance
    client_id_secret_name = args.client_id_secret_name or defaults.client_id_secret_name
    client_secret_secret_name = args.client_secret_secret_name or defaults.client_secret_secret_name
    app_url_secret_name = args.app_url_secret_name or defaults.app_url_secret_name
    if args.no_app_url_secret:
        app_url_secret_name = None

    if role != "admin" and group_name:
        parser.error("only --identity-role admin may be assigned to an admin group")
    if args.create_group and role != "admin":
        parser.error("--create-group is valid only with --identity-role admin")
    if role != "verifier" and (
        args.lakebase_instance or args.gateway_endpoint or args.warehouse_id
    ):
        parser.error(
            "--lakebase-instance, --gateway-endpoint, and --warehouse-id are valid only with "
            "--identity-role verifier"
        )
    if role == "verifier" and args.gateway_endpoint and not args.warehouse_id:
        parser.error("--gateway-endpoint requires --warehouse-id for exact proof verification")

    _diag(
        f"provisioning plan: identity_role={role!r} sp_name={sp_name!r} "
        f"app_name={app_name!r} group_name={group_name!r} "
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
            expected_application_id=args.expected_application_id,
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
            app_url=args.app_url,
            client_id_secret_name=client_id_secret_name,
            client_secret_secret_name=client_secret_secret_name,
            app_url_secret_name=app_url_secret_name,
            identity_role=role,
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
