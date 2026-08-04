#!/usr/bin/env python3
"""Converge exact app access to the governed campaign-treatment Delta table.

Object presence and effective authority are read through authoritative Unity
Catalog APIs. Quiesce removes writes before bundle promotion; runtime restores
only exact table-scoped SELECT and MODIFY after constraints converge.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Literal

from databricks.sdk import AccountClient, WorkspaceClient
from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.ensure_campaign_treatment_table import execute_sql
from tools.databricks.m2m_access_policy import (
    assert_non_admin_service_principal,
    resolve_effective_groups,
)
from tools.databricks.oauth_credential_boundary import (
    held_deployment_credential_assertion,
)
from tools.databricks.oauth_credential_creation import (
    ExactOAuthCredential,
    create_exact_oauth_credential,
    revoke_exact_oauth_credential,
)
from tools.databricks.oauth_credential_quarantine import (
    CredentialMutationContext,
    CredentialMutationQuarantineError,
)
from tools.databricks.uc_owner_policy import (
    ApprovedOwnerPolicy,
    TargetServicePrincipal,
    account_client_from_env,
    parse_approved_owner_principals,
)
from tools.databricks.uc_target_identity import workspace_target_identity
from tools.databricks.workspace_auth import deployment_workspace_client

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Bounded settle window for new-credential propagation.
#
# CORRECTION (2026-08-04): this was first added believing propagation was the
# cause of the step-4 `invalid_client` failures. That premise was DISPROVEN by
# measurement — three trials against this same principal, at this same 300s
# lifetime, authenticated in 1.4-2.3s, and the window ran its full deadline in
# CI without ever succeeding. It is retained only as defense-in-depth for a
# genuinely slow mint, and costs at most `_CREDENTIAL_SETTLE_DEADLINE_S` on a
# doomed attempt. The actual CI failure is being diagnosed by
# `_describe_probe_failure` instead.
#
# It is not an authorization fallback: the credential stays bound to the same
# principal, every identity assertion after the call is unchanged, and cleanup
# remains fatal. Any non-auth error, and any auth rejection still standing at
# the deadline, propagates unchanged.
_OAUTH_AUTH_REJECTION_CODES = frozenset(
    {
        "invalid_client",
        "invalid_grant",
        "unauthenticated",
        "unauthorized_client",
    }
)
_OAUTH_ERROR_CODE_RE = re.compile(r"^(?P<code>[a-z][a-z0-9_]*)\s*:")
_CREDENTIAL_SETTLE_DEADLINE_S = 90.0
_CREDENTIAL_SETTLE_INTERVAL_S = 5.0


def _is_oauth_auth_rejection(error: BaseException) -> bool:
    match = _OAUTH_ERROR_CODE_RE.match(str(error).strip())
    return bool(match) and match.group("code") in _OAUTH_AUTH_REJECTION_CODES


def read_identity_with_credential_settle(
    read_identity: Callable[[], Any],
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    deadline_s: float = _CREDENTIAL_SETTLE_DEADLINE_S,
    interval_s: float = _CREDENTIAL_SETTLE_INTERVAL_S,
) -> Any:
    """Read the target identity, absorbing new-credential propagation only."""

    deadline = monotonic() + deadline_s
    while True:
        try:
            return read_identity()
        except BaseException as exc:
            if not _is_oauth_auth_rejection(exc) or monotonic() >= deadline:
                raise
        sleep(interval_s)
_TABLE = "campaign_treatment_snapshot"
_SCHEMA = "audit"
_SAFE_METASTORE_PRIVILEGES = {"USE_MARKETPLACE_ASSETS"}
Mode = Literal["quiesce", "runtime"]
_TEMPORARY_PROBE_SECRET_LIFETIME = "300s"


# Ambient deployer credentials that must not leak into the bounded target
# identity proof. ``account_client_from_env`` already builds the account plane
# with an explicit Config "without inheriting workspace credentials"; the
# target workspace client was constructed from bare kwargs, so the SDK could
# still merge whatever the deploy shell exported (the workflow sets
# DATABRICKS_HOST/TOKEN/AUTH_TYPE=pat for the deployer). The SDK resolves its
# token lazily at request time, so the read must run inside the same isolation
# as the construction.
_AMBIENT_AUTH_ENV_VARS = (
    "DATABRICKS_TOKEN",
    "DATABRICKS_AUTH_TYPE",
    "DATABRICKS_CLIENT_ID",
    "DATABRICKS_CLIENT_SECRET",
    "DATABRICKS_CONFIG_PROFILE",
    "DATABRICKS_CONFIG_FILE",
    "DATABRICKS_HOST",
    "DATABRICKS_USERNAME",
    "DATABRICKS_PASSWORD",
)


@contextmanager
def isolated_target_auth_env() -> Iterator[tuple[str, ...]]:
    """Remove ambient Databricks auth env for the duration of a probe.

    Yields the names (never values) of the variables that were removed so a
    failure can report the ambient state it ran against. Always restored.
    """

    saved = {name: os.environ.pop(name) for name in _AMBIENT_AUTH_ENV_VARS if name in os.environ}
    try:
        yield tuple(sorted(saved))
    finally:
        os.environ.update(saved)


def _raw_token_endpoint_verdict(
    host: str,
    client_id: str,
    client_secret: str,
) -> str:
    """Ask the workspace token endpoint directly and return ITS explanation.

    The SDK reduces an OAuth rejection to ``{error}: {summary}`` — for the
    step-4 failures that is always ``invalid_client: Client authentication
    failed``, which names the symptom, not the cause. The endpoint's raw
    JSON usually carries an ``error_description`` that does (wrong audience,
    unknown client in this workspace, expired secret, …). Returns a bounded,
    secret-free string; never raises.
    """

    import urllib.error
    import urllib.parse
    import urllib.request

    url = f"{host.rstrip('/')}/oidc/v1/token"
    body = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "scope": "all-apis",
            "client_id": client_id,
            "client_secret": client_secret,
        }
    ).encode("ascii")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return f"raw_token_status={response.status} (authentication SUCCEEDED on direct call)"
    except urllib.error.HTTPError as http_error:
        payload = http_error.read()[:600].decode("utf-8", errors="replace")
        # The request body holds the secret; the RESPONSE body never does —
        # it is the server's error JSON. Redact defensively anyway.
        sanitized = payload.replace(client_secret, "<redacted>")
        return f"raw_token_status={http_error.code} raw_token_body={sanitized}"
    except Exception as transport_error:  # noqa: BLE001 - diagnostic only
        return f"raw_token_probe_error={type(transport_error).__name__}: {str(transport_error)[:120]}"


def _fingerprint(value: object) -> str:
    """Stable, non-secret fingerprint of an identifier.

    CI masks secret VALUES in logs, which hides exactly the identifiers a
    failure analysis needs (which service principal, which host, which
    account). A truncated SHA-256 is not the secret, so it survives masking
    and can be compared against a locally computed digest of a known-good id.
    """

    import hashlib

    text = str(value or "").strip()
    if not text:
        return "<unset>"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _credential_presence(account: object, principal_id: str, credential_id: str) -> str:
    """Report whether the just-minted credential still exists account-side.

    Distinguishes "something deleted our secret between mint and use" (a
    concurrency/reaper problem in our own tooling) from "the secret exists and
    the platform refuses it" (a Databricks-side problem). Never raises.
    """

    try:
        secrets = list(account.service_principal_secrets.list(principal_id))  # type: ignore[attr-defined]
        ids = {str(getattr(item, "id", "")) for item in secrets}
        return (
            f"minted_credential_present={str(credential_id in ids).lower()} "
            f"account_secret_count={len(ids)}"
        )
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        return f"credential_presence_error={type(exc).__name__}: {str(exc)[:80]}"


def _describe_probe_failure(
    error: BaseException,
    *,
    workspace: object,
    application_id: str,
    host: str,
    stripped_env: tuple[str, ...],
    raw_verdict: str = "",
    account_scim_id: str = "",
    credential_state: str = "",
) -> str:
    """Build a secret-free description of a target-identity auth failure.

    The step-4 ``invalid_client`` rejection reproduced only on CI runners and
    never locally (three trials at the same 300s lifetime authenticated in
    1.4-2.3s), so the deciding evidence — which client id and auth mode the
    SDK actually resolved — has to come from the failing environment itself.
    Values are never emitted: only identifiers already present in deploy logs
    and the NAMES of ambient variables.
    """

    config = getattr(getattr(workspace, "api_client", None), "_cfg", None) or getattr(
        workspace, "config", None
    )
    resolved_client = str(getattr(config, "client_id", "") or "")
    return (
        "[identity-probe] target authentication failed: "
        f"error={type(error).__name__}: {str(error)[:120]} | "
        f"intended_client_id={application_id} | "
        f"resolved_client_id={resolved_client or '<unset>'} | "
        f"client_id_matches={str(resolved_client == application_id).lower()} | "
        f"resolved_auth_type={getattr(config, 'auth_type', None) or '<unset>'} | "
        f"resolved_host={str(getattr(config, 'host', '') or host)} | "
        f"ambient_auth_env_removed={','.join(stripped_env) or '<none>'} | "
        f"ambient_auth_env_remaining="
        f"{','.join(sorted(n for n in _AMBIENT_AUTH_ENV_VARS if n in os.environ)) or '<none>'} | "
        # Mask-proof identity fingerprints: CI redacts the values themselves,
        # which is precisely what a failure analysis needs to compare.
        f"fp_client_id={_fingerprint(application_id)} | "
        f"fp_host={_fingerprint(host)} | "
        f"fp_account_scim_id={_fingerprint(account_scim_id)} | "
        f"fp_account_id={_fingerprint(os.environ.get('DATABRICKS_ACCOUNT_ID'))} | "
        f"fp_account_client_id={_fingerprint(os.environ.get('DATABRICKS_ACCOUNT_CLIENT_ID'))} | "
        f"account_host={os.environ.get('DATABRICKS_ACCOUNT_HOST') or '<unset>'} | "
        f"{credential_state}"
        + (f" | {raw_verdict}" if raw_verdict else "")
    )


def _canonical(value: object) -> str:
    return str(value or "").strip().casefold()


def _validate_identifier(label: str, value: str) -> str:
    text = value.strip()
    if not _IDENTIFIER_RE.fullmatch(text):
        raise ValueError(f"Invalid {label} identifier: {value!r}")
    return text


def _quoted_identifier(value: str) -> str:
    return f"`{_validate_identifier('SQL', value)}`"


def _quoted_principal(value: str) -> str:
    principal = value.strip()
    if not principal or "`" in principal:
        raise ValueError("principal must be non-empty and contain no backticks")
    return f"`{principal}`"


def target_identity_groups_probe(
    account: AccountClient,
    account_sp_id: str,
    application_id: str,
    *,
    expected_workspace_scim_id: str,
    workspace_host: str,
    assert_single_writer: Callable[[], None],
    workspace_factory: Callable[..., WorkspaceClient] = WorkspaceClient,
) -> dict[str, str]:
    """Return authoritative effective groups as the target App identity.

    Account SCIM cannot prove a negative membership result when Automatic
    Identity Management is enabled. Mint a bounded target-SP credential and
    read that identity's own SCIM ``groups`` collection instead. SCIM defines
    that collection to include direct, nested, and dynamically calculated
    membership, so this proof needs no SQL warehouse authority. Cleanup
    failure is always fatal.
    """

    host = workspace_host.strip()
    if not host:
        raise RuntimeError("Workspace host is required for target identity proof")
    principal_id = account_sp_id.strip()
    if not principal_id:
        raise RuntimeError("Account service-principal id is required for identity proof")
    workspace_principal_id = expected_workspace_scim_id.strip()
    if not workspace_principal_id:
        raise RuntimeError("Workspace service-principal id is required for identity proof")
    credential: ExactOAuthCredential | None = None
    probe_error: BaseException | None = None
    effective_groups: dict[str, str] = {}
    try:
        credential = create_exact_oauth_credential(
            principal_id=principal_id,
            list_credentials=lambda: account.service_principal_secrets.list(
                principal_id
            ),
            create_credential=lambda: account.service_principal_secrets.create(
                principal_id,
                lifetime=_TEMPORARY_PROBE_SECRET_LIFETIME,
            ),
            delete_credential=lambda credential_id: (
                account.service_principal_secrets.delete(
                    principal_id,
                    credential_id,
                )
            ),
            assert_single_writer=assert_single_writer,
            mutation_context=CredentialMutationContext(
                authority_scope="account",
                authority_identity=application_id,
                provider_api="account.service_principal_secrets",
                operation_mode="temporary_probe",
                sink_descriptor="temporary:target-identity-membership-probe",
                credential_lifetime_seconds=300,
            ),
            label="temporary target identity",
        )
        # Construct AND read inside one isolation window: the SDK fetches its
        # token lazily on the first request, so ambient deployer credentials
        # would otherwise still be in scope when the token is minted.
        with isolated_target_auth_env() as stripped_env:
            target_workspace = workspace_factory(
                host=host,
                client_id=application_id,
                client_secret=credential.secret,
                auth_type="oauth-m2m",
            )
            try:
                identity = read_identity_with_credential_settle(
                    lambda: target_workspace.api_client.do(
                        "GET",
                        "/api/2.0/preview/scim/v2/Me",
                        query={"attributes": "id,userName,groups"},
                        headers={"Accept": "application/json"},
                    )
                )
            except BaseException as exc:
                # Emit the resolved-config evidence before unwinding; this is
                # the only place the failing environment can be observed. The
                # raw token-endpoint call captures the server's own
                # error_description, which the SDK truncates away.
                print(
                    _describe_probe_failure(
                        exc,
                        workspace=target_workspace,
                        application_id=application_id,
                        host=host,
                        stripped_env=stripped_env,
                        account_scim_id=principal_id,
                        # Decisive: does the credential we just minted still
                        # EXIST at the moment authentication is refused? If it
                        # vanished, something reaped it (concurrency); if it is
                        # present, the credential is real and the platform is
                        # rejecting it.
                        credential_state=_credential_presence(
                            account, principal_id, credential.credential_id
                        ),
                        raw_verdict=_raw_token_endpoint_verdict(
                            host, application_id, credential.secret
                        ),
                    ),
                    file=sys.stderr,
                    flush=True,
                )
                raise
        if not isinstance(identity, dict):
            raise RuntimeError("Target identity membership proof returned a malformed identity")
        identity_id = identity.get("id")
        identity_name = identity.get("userName")
        if (
            not isinstance(identity_id, str)
            or not identity_id
            or identity_id != identity_id.strip()
            or not isinstance(identity_name, str)
            or not identity_name
            or identity_name != identity_name.strip()
        ):
            raise RuntimeError(
                "Target identity membership proof returned a malformed identity"
            )
        if (
            identity_id != workspace_principal_id
            or identity_name != application_id
        ):
            raise RuntimeError("Temporary credential authenticated as a different target identity")
        if "groups" not in identity:
            raise RuntimeError(
                "Target identity membership proof omitted the authoritative groups collection"
            )
        groups = identity["groups"]
        if not isinstance(groups, list):
            raise RuntimeError(
                "Target identity membership proof returned a malformed groups collection"
            )
        group_ids_by_name: dict[str, str] = {}
        for group in groups:
            if not isinstance(group, dict):
                raise RuntimeError("Target identity membership proof returned a malformed group")
            observed_id = group.get("value")
            observed_name = group.get("display")
            if observed_id in (None, ""):
                raise RuntimeError(
                    "Target identity membership proof returned a group without an id"
                )
            if observed_name in (None, ""):
                raise RuntimeError(
                    "Target identity membership proof returned a group without a display name"
                )
            if (
                not isinstance(observed_id, str)
                or observed_id != observed_id.strip()
                or not isinstance(observed_name, str)
                or observed_name != observed_name.strip()
            ):
                raise RuntimeError(
                    "Target identity membership proof returned a malformed group"
                )
            canonical_name = observed_name.casefold()
            if observed_id in effective_groups:
                raise RuntimeError(
                    "Target identity membership proof returned a duplicate group id"
                )
            if canonical_name in group_ids_by_name:
                raise RuntimeError(
                    "Target identity membership proof returned a duplicate group name"
                )
            effective_groups[observed_id] = observed_name
            group_ids_by_name[canonical_name] = observed_id
    except BaseException as exc:
        probe_error = exc
    finally:
        if credential is not None:
            try:
                revoke_exact_oauth_credential(
                    credential,
                    principal_id=principal_id,
                    list_credentials=lambda: account.service_principal_secrets.list(
                        principal_id
                    ),
                    delete_credential=lambda credential_id: (
                        account.service_principal_secrets.delete(
                            principal_id,
                            credential_id,
                        )
                    ),
                    assert_single_writer=assert_single_writer,
                    label="temporary target identity",
                )
            except CredentialMutationQuarantineError:
                raise
            except BaseException as cleanup_error:
                raise RuntimeError(
                    "Temporary target identity credential cleanup could not be proven"
                ) from cleanup_error
    if probe_error is not None:
        raise probe_error
    return effective_groups


def target_group_membership_probe(
    account: AccountClient,
    account_sp_id: str,
    application_id: str,
    owner_group_id: str,
    owner_group: str,
    *,
    expected_workspace_scim_id: str,
    workspace_host: str,
    assert_single_writer: Callable[[], None],
    workspace_factory: Callable[..., WorkspaceClient] = WorkspaceClient,
) -> bool:
    """Evaluate one owner group against the target's authoritative snapshot."""

    group_id = owner_group_id.strip()
    if not group_id:
        raise RuntimeError("Account group id is required for identity proof")
    group_name = owner_group.strip()
    if not group_name:
        raise RuntimeError("Account group name is required for identity proof")
    effective_groups = target_identity_groups_probe(
        account,
        account_sp_id,
        application_id,
        expected_workspace_scim_id=expected_workspace_scim_id,
        workspace_host=workspace_host,
        assert_single_writer=assert_single_writer,
        workspace_factory=workspace_factory,
    )
    expected_name = _canonical(group_name)
    observed_name = effective_groups.get(group_id)
    if observed_name is not None:
        if _canonical(observed_name) != expected_name:
            raise RuntimeError(
                "Target identity membership proof returned a mismatched group name"
            )
        return True
    if any(_canonical(name) == expected_name for name in effective_groups.values()):
        raise RuntimeError("Target identity membership proof returned a mismatched group id")
    return False


def _get_or_none(getter: Callable[[str], object], name: str) -> object | None:
    try:
        return getter(name)
    except (NotFound, ResourceDoesNotExist):
        return None


def _object_presence(
    workspace: WorkspaceClient, *, catalog: str
) -> tuple[object | None, object | None, object | None]:
    catalog_object = _get_or_none(workspace.catalogs.get, catalog)
    if catalog_object is None:
        return None, None, None
    schema_name = f"{catalog}.{_SCHEMA}"
    schema_object = _get_or_none(workspace.schemas.get, schema_name)
    if schema_object is None:
        return catalog_object, None, None
    table_object = _get_or_none(workspace.tables.get, f"{schema_name}.{_TABLE}")
    return catalog_object, schema_object, table_object


def _identity_context(workspace: WorkspaceClient, principal: str) -> TargetServicePrincipal:
    try:
        target = workspace_target_identity(
            workspace,
            application_id=principal,
        )
    except RuntimeError as exc:
        raise RuntimeError(
            f"App service principal {principal!r} did not resolve to one exact, "
            "active workspace identity"
        ) from exc
    groups = resolve_effective_groups(workspace, sp_id=target.scim_id)
    assert_non_admin_service_principal(
        workspace,
        sp_id=target.scim_id,
        effective_groups=groups,
        identity_role="app-runtime",
    )
    return target


def _privilege_name(privilege: object) -> str:
    raw = getattr(privilege, "privilege", privilege)
    value = getattr(raw, "value", raw)
    return str(value or "").split(".")[-1].strip().upper().replace(" ", "_")


def _effective_privileges(
    workspace: WorkspaceClient,
    *,
    securable_type: str,
    full_name: str,
    principal: str,
) -> set[str]:
    privileges: set[str] = set()
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        response = workspace.grants.get_effective(
            securable_type,
            full_name,
            principal=principal,
            page_token=page_token,
            max_results=0,
        )
        for assignment in getattr(response, "privilege_assignments", None) or []:
            for privilege in getattr(assignment, "privileges", None) or []:
                name = _privilege_name(privilege)
                if not name:
                    raise RuntimeError("Effective grants API returned an empty privilege")
                privileges.add(name)
        next_token = str(getattr(response, "next_page_token", "") or "").strip()
        if not next_token:
            break
        if next_token in seen_tokens:
            raise RuntimeError("Effective grants API repeated a pagination token")
        seen_tokens.add(next_token)
        page_token = next_token
    return privileges


def _assert_effective_privileges(
    workspace: WorkspaceClient,
    *,
    securable_type: str,
    full_name: str,
    principal: str,
    expected: set[str],
) -> None:
    actual = _effective_privileges(
        workspace,
        securable_type=securable_type,
        full_name=full_name,
        principal=principal,
    )
    if actual != expected:
        raise RuntimeError(
            f"Effective {securable_type} privileges are not exact for {full_name!r}: "
            f"expected {sorted(expected)}, observed {sorted(actual)}"
        )


def _assert_metastore_boundary(
    workspace: WorkspaceClient, *, principal: str, owner_policy: ApprovedOwnerPolicy
) -> None:
    assignment = workspace.metastores.current()
    metastore_id = str(getattr(assignment, "metastore_id", "") or "").strip()
    if not metastore_id:
        raise RuntimeError("Current workspace has no authoritative metastore identifier")
    metastore = workspace.metastores.get(metastore_id)
    owner_policy.assert_objects((metastore,))
    actual = _effective_privileges(
        workspace,
        securable_type="metastore",
        full_name=metastore_id,
        principal=principal,
    )
    forbidden = actual - _SAFE_METASTORE_PRIVILEGES
    if forbidden:
        raise RuntimeError(
            f"App service principal has forbidden metastore privileges: {sorted(forbidden)}"
        )


def _set_table_actions(
    workspace: WorkspaceClient,
    *,
    warehouse_id: str,
    relation: str,
    principal_sql: str,
    actions: list[str],
) -> None:
    execute_sql(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"REVOKE ALL PRIVILEGES ON TABLE {relation} FROM {principal_sql}",
    )
    execute_sql(
        workspace,
        warehouse_id=warehouse_id,
        statement=f"GRANT {', '.join(actions)} ON TABLE {relation} TO {principal_sql}",
    )


def _assert_existing_boundaries(
    workspace: WorkspaceClient,
    *,
    catalog: str,
    principal: str,
    owner_policy: ApprovedOwnerPolicy,
    table_actions: list[str] | None,
) -> tuple[object | None, object | None, object | None]:
    objects = _object_presence(workspace, catalog=catalog)
    catalog_object, schema_object, table_object = objects
    if catalog_object is None:
        return objects
    owner_policy.assert_objects(objects)
    _assert_effective_privileges(
        workspace,
        securable_type="catalog",
        full_name=catalog,
        principal=principal,
        expected={"USE_CATALOG"},
    )
    if schema_object is not None:
        _assert_effective_privileges(
            workspace,
            securable_type="schema",
            full_name=f"{catalog}.{_SCHEMA}",
            principal=principal,
            expected={"USE_SCHEMA"},
        )
    if table_object is not None and table_actions is not None:
        _assert_effective_privileges(
            workspace,
            securable_type="table",
            full_name=f"{catalog}.{_SCHEMA}.{_TABLE}",
            principal=principal,
            expected=set(table_actions),
        )
    return objects


def converge_campaign_treatment_access(
    *,
    warehouse_id: str,
    catalog: str,
    principal: str,
    mode: Mode,
    approved_owner_principals: set[str] | None = None,
    account_factory: Callable[[], AccountClient] | None = None,
    group_membership_probe: Callable[[AccountClient, str, str, str, str], bool] | None = None,
    assert_single_writer: Callable[[], None] | None = None,
    workspace: WorkspaceClient | None = None,
) -> bool:
    warehouse = warehouse_id.strip()
    if not warehouse:
        raise ValueError("warehouse_id must be non-empty")
    if mode not in {"quiesce", "runtime"}:
        raise ValueError(f"Unsupported access convergence mode: {mode!r}")
    catalog_name = _validate_identifier("catalog", catalog)
    principal_name = principal.strip()
    if principal_name != principal:
        raise ValueError("principal must be canonical")
    principal_sql = _quoted_principal(principal_name)
    catalog_sql = _quoted_identifier(catalog_name)
    schema_sql = f"{catalog_sql}.{_quoted_identifier(_SCHEMA)}"
    relation = f"{schema_sql}.{_quoted_identifier(_TABLE)}"
    client = workspace or deployment_workspace_client()
    target = _identity_context(client, principal_name)
    workspace_host = str(
        getattr(getattr(client, "config", None), "host", "")
        or os.environ.get("DATABRICKS_HOST", "")
    ).strip()
    credential_lease = assert_single_writer
    if group_membership_probe is None and credential_lease is None:
        credential_lease = held_deployment_credential_assertion(client)
    membership_probe = group_membership_probe or (
        lambda account, account_sp_id, application_id, owner_group_id, owner_group: (
            target_group_membership_probe(
                account,
                account_sp_id,
                application_id,
                owner_group_id,
                owner_group,
                expected_workspace_scim_id=target.scim_id,
                workspace_host=workspace_host,
                assert_single_writer=credential_lease,  # type: ignore[arg-type]
            )
        )
    )
    owner_policy = ApprovedOwnerPolicy(
        workspace=client,
        target=target,
        configured_principals=approved_owner_principals or set(),
        account_factory=account_factory or account_client_from_env,
        group_membership_probe=membership_probe,
    )
    _assert_metastore_boundary(client, principal=principal_name, owner_policy=owner_policy)
    objects = _object_presence(client, catalog=catalog_name)
    catalog_object, schema_object, table_object = objects
    if catalog_object is None:
        return False

    if table_object is not None:
        execute_sql(
            client,
            warehouse_id=warehouse,
            statement=f"REVOKE ALL PRIVILEGES ON TABLE {relation} FROM {principal_sql}",
        )
    if schema_object is not None:
        execute_sql(
            client,
            warehouse_id=warehouse,
            statement=f"REVOKE ALL PRIVILEGES ON SCHEMA {schema_sql} FROM {principal_sql}",
        )
    execute_sql(
        client,
        warehouse_id=warehouse,
        statement=f"REVOKE ALL PRIVILEGES ON CATALOG {catalog_sql} FROM {principal_sql}",
    )
    execute_sql(
        client,
        warehouse_id=warehouse,
        statement=f"GRANT USE CATALOG ON CATALOG {catalog_sql} TO {principal_sql}",
    )
    if schema_object is not None:
        execute_sql(
            client,
            warehouse_id=warehouse,
            statement=f"GRANT USE SCHEMA ON SCHEMA {schema_sql} TO {principal_sql}",
        )
    verified_objects = _assert_existing_boundaries(
        client,
        catalog=catalog_name,
        principal=principal_name,
        owner_policy=owner_policy,
        table_actions=None,
    )
    table_object = verified_objects[2]
    if table_object is None:
        if mode == "runtime":
            raise RuntimeError("Cannot grant runtime access before the treatment table exists")
        return False

    actions = ["SELECT"] if mode == "quiesce" else ["SELECT", "MODIFY"]
    try:
        _set_table_actions(
            client,
            warehouse_id=warehouse,
            relation=relation,
            principal_sql=principal_sql,
            actions=actions,
        )
        _assert_existing_boundaries(
            client,
            catalog=catalog_name,
            principal=principal_name,
            owner_policy=owner_policy,
            table_actions=actions,
        )
    except BaseException:
        if mode == "runtime":
            try:
                _set_table_actions(
                    client,
                    warehouse_id=warehouse,
                    relation=relation,
                    principal_sql=principal_sql,
                    actions=["SELECT"],
                )
                _assert_existing_boundaries(
                    client,
                    catalog=catalog_name,
                    principal=principal_name,
                    owner_policy=owner_policy,
                    table_actions=["SELECT"],
                )
            except BaseException as cleanup_error:
                raise RuntimeError(
                    "Runtime grant verification failed and compensating write "
                    "quiescence could not be proven"
                ) from cleanup_error
        raise
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--catalog", default="mip")
    parser.add_argument("--principal", required=True)
    parser.add_argument("--mode", choices=("quiesce", "runtime"), required=True)
    parser.add_argument(
        "--approved-owner-principal",
        action="append",
        default=[],
        help="Explicit trusted UC owner principal; repeat for multiple owners.",
    )
    args = parser.parse_args()
    approved_owners = parse_approved_owner_principals(
        os.environ.get("MIP_UC_APPROVED_OWNER_PRINCIPALS", "")
    )
    approved_owners.update(args.approved_owner_principal)
    existed = converge_campaign_treatment_access(
        warehouse_id=args.warehouse_id,
        catalog=args.catalog,
        principal=args.principal,
        mode=args.mode,
        approved_owner_principals=approved_owners,
    )
    if existed:
        print(f"Verified exact {args.mode} privileges on the campaign treatment table")
    else:
        print("Treatment table not yet present; verified no table write path exists")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
