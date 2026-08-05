"""Lease-bound creation and delivery of one M2M OAuth credential."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tools.databricks import m2m_oauth_credential_delivery as credential_delivery
from tools.databricks.m2m_identity_contract import IDENTITY_DEFAULTS, IdentityRole
from tools.databricks.oauth_credential_creation import (
    ExactOAuthCredential,
    create_exact_oauth_credential,
    resolve_exact_oauth_credential_delivery,
    revoke_exact_oauth_credential,
)
from tools.databricks.oauth_credential_quarantine import (
    CredentialMutationContext,
    CredentialMutationQuarantineError,
    raise_credential_quarantine,
)

_SOURCE_GIT_SHA_RE = re.compile(r"[0-9a-f]{40}")


def credential_retirement_mode(identity_role: IdentityRole) -> str:
    """Map only the live proxy caller to signed App cutover retirement."""

    return (
        "signed_app_cutover"
        if identity_role == "agent_proxy"
        else "immediate"
    )


def credential_source_git_sha(repo_root: Path) -> str:
    """Return the exact reviewed deployment source for credential evidence."""

    configured = os.environ.get("MIP_DEPLOYMENT_SOURCE_GIT_SHA", "").strip()
    borrowed_lease = os.environ.get(
        "MIP_APP_DEPLOYMENT_LEASE_ID",
        "",
    ).strip()
    if borrowed_lease:
        if not _SOURCE_GIT_SHA_RE.fullmatch(configured):
            raise RuntimeError(
                "borrowed credential mutation lease requires its exact source Git SHA"
            )
        return configured
    try:
        head_response = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("credential mutation source Git SHA is unavailable") from exc
    source_git_sha = head_response.stdout.strip()
    if not _SOURCE_GIT_SHA_RE.fullmatch(source_git_sha):
        raise RuntimeError("credential mutation source Git SHA is invalid")
    if configured and configured != source_git_sha:
        raise RuntimeError(
            "configured credential mutation source Git SHA does not match HEAD"
        )
    try:
        status_response = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "credential mutation source worktree state is unavailable"
        ) from exc
    if status_response.stdout:
        raise RuntimeError(
            "standalone credential mutation requires a clean tracked and "
            "untracked source worktree"
        )
    return source_git_sha


def credential_lease_writer_application_id(
    client: Any,
    *,
    target: Any,
    identity_role: IdentityRole,
    find_existing_sp: Callable[[Any, str], Any | None],
) -> str:
    """Resolve the sole reserved deployment writer without widening target roles."""

    writer = (
        target
        if identity_role == "agent_runtime"
        else find_existing_sp(
            client,
            IDENTITY_DEFAULTS["agent_runtime"].sp_name,
        )
    )
    writer_application_id = str(
        getattr(writer, "application_id", "") or ""
    ).strip()
    if (
        writer is None
        or not writer_application_id
        or getattr(writer, "active", True) is False
    ):
        raise SystemExit(
            "OAuth credential minting requires the active reserved agent-runtime "
            "service principal as the signed deployment-lease writer. Bootstrap "
            "the agent_runtime identity first."
        )
    return writer_application_id


def _mint_oauth_secret(
    client: Any,
    sp_id: str,
    *,
    assert_single_writer: Callable[[], None],
    mutation_context: CredentialMutationContext,
    diagnostic: Callable[[str], None],
    error_factory: Callable[..., BaseException],
) -> ExactOAuthCredential:
    diagnostic(f"minting OAuth secret for service_principal_id={sp_id}")
    try:
        return create_exact_oauth_credential(
            principal_id=sp_id,
            list_credentials=lambda: (
                client.service_principal_secrets_proxy.list(sp_id)
            ),
            create_credential=lambda: (
                client.service_principal_secrets_proxy.create(
                    service_principal_id=sp_id
                )
            ),
            delete_credential=lambda credential_id: (
                client.service_principal_secrets_proxy.delete(
                    sp_id,
                    credential_id,
                )
            ),
            assert_single_writer=assert_single_writer,
            mutation_context=mutation_context,
            label="M2M OAuth",
        )
    except CredentialMutationQuarantineError:
        raise
    except Exception as exc:
        raise error_factory(exc, step="mint OAuth secret") from exc


def mint_and_deliver_oauth_credential(
    *,
    client: Any,
    sp_id: str,
    client_id: str,
    identity_role: IdentityRole,
    app_name: str,
    writer_application_id: str,
    source_git_sha: str,
    boundary_factory: Callable[..., Any],
    secret_writer: Callable[[str, str, str], None],
    sink_acknowledger: Callable[[str, frozenset[str]], None],
    secret_invalidator: Callable[[str, frozenset[str]], None],
    diagnostic: Callable[[str], None],
    error_factory: Callable[..., BaseException],
    gh_repo: str,
    client_id_secret_name: str,
    client_secret_secret_name: str,
    credential_id_secret_name: str | None,
    app_url_secret_name: str | None,
    app_url: str | None,
    atomic_credential_bundle: bool,
) -> str:
    """Mint, publish, and compensate one credential under the exact App lease."""

    sink_names = (
        [client_secret_secret_name]
        if atomic_credential_bundle
        else sorted(
            name
            for name in (
                client_id_secret_name,
                client_secret_secret_name,
                credential_id_secret_name,
                app_url_secret_name,
            )
            if name
        )
    )
    mutation_context = CredentialMutationContext(
        authority_scope="workspace",
        authority_identity=client_id,
        provider_api="workspace.service_principal_secrets_proxy",
        operation_mode="persistent_delivery",
        sink_descriptor=(
            f"github:{gh_repo}:atomic={str(atomic_credential_bundle).lower()}:"
            + ",".join(sink_names)
        ),
        credential_lifetime_seconds=0,
        sink_repository=gh_repo,
        sink_secret_names=frozenset(sink_names),
        sink_atomic_credential_bundle=atomic_credential_bundle,
        retirement_mode=credential_retirement_mode(identity_role),
    )
    with boundary_factory(
        client,
        app_name=app_name,
        writer_application_id=writer_application_id,
        source_git_sha=source_git_sha,
    ) as assert_single_writer:
        credential = _mint_oauth_secret(
            client,
            sp_id,
            assert_single_writer=assert_single_writer,
            mutation_context=mutation_context,
            diagnostic=diagnostic,
            error_factory=error_factory,
        )
        credential.intent.arm_sink(
            repository=gh_repo,
            secret_names=frozenset(sink_names),
            atomic_credential_bundle=atomic_credential_bundle,
        )

        def compensate_failed_sink(**_kwargs: object) -> None:
            sink_error: BaseException | None = None
            try:
                secret_invalidator(gh_repo, frozenset(sink_names))
            except BaseException as exc:
                sink_error = exc
            revoke_exact_oauth_credential(
                credential,
                principal_id=sp_id,
                list_credentials=lambda: (
                    client.service_principal_secrets_proxy.list(sp_id)
                ),
                delete_credential=lambda revoked_id: (
                    client.service_principal_secrets_proxy.delete(
                        sp_id,
                        revoked_id,
                    )
                ),
                assert_single_writer=assert_single_writer,
                label="M2M OAuth",
                finalize_resolution=sink_error is None,
                sink_invalidated=sink_error is None,
            )
            if sink_error is not None:
                raise_credential_quarantine(
                    message=(
                        "M2M OAuth credential provider state was restored, "
                        "but its GitHub sink invalidation is unproven"
                    ),
                    label="M2M OAuth",
                    principal_id=sp_id,
                    before_ids=credential.before_ids,
                    candidate_ids=frozenset({credential.credential_id}),
                    fence=credential.intent,
                    cause=sink_error,
                )

        credential_delivery.deliver_oauth_credential(
            writer=secret_writer,
            revoker=compensate_failed_sink,
            gh_repo=gh_repo,
            client_id_secret_name=client_id_secret_name,
            client_id=client_id,
            client_secret_secret_name=client_secret_secret_name,
            client_secret=credential.secret,
            credential_id=credential.credential_id,
            credential_id_secret_name=credential_id_secret_name,
            app_url_secret_name=app_url_secret_name,
            app_url=app_url,
            atomic_credential_bundle=atomic_credential_bundle,
        )
        try:
            sink_acknowledger(gh_repo, frozenset(sink_names))
        except BaseException:
            compensate_failed_sink()
            raise
        resolve_exact_oauth_credential_delivery(
            credential,
            list_credentials=lambda: (
                client.service_principal_secrets_proxy.list(sp_id)
            ),
            delete_credential=lambda retired_id: (
                client.service_principal_secrets_proxy.delete(
                    sp_id,
                    retired_id,
                )
            ),
            label="M2M OAuth",
        )
        return credential.credential_id
