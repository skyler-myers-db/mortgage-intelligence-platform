"""Deployment-lease boundaries for OAuth credential mutations and recovery."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from tools.databricks import app_deployment_lease
from tools.databricks import oauth_credential_records as records
from tools.databricks.oauth_credential_quarantine import (
    CredentialMutationFence,
    CredentialMutationQuarantineError,
    CredentialMutationTerminalFenceError,
    assert_no_credential_quarantine,
    raise_credential_quarantine,
)


def held_deployment_credential_assertion(
    workspace: Any,
    *,
    app_name: str | None = None,
    lease_id: str | None = None,
    source_git_sha: str | None = None,
) -> CredentialMutationFence:
    """Bind credential mutation to the signed deployment lease in the environment."""

    reviewed_app = (app_name or os.environ.get("MIP_APP_NAME", "")).strip()
    reviewed_lease = (
        lease_id or os.environ.get("MIP_APP_DEPLOYMENT_LEASE_ID", "")
    ).strip()
    reviewed_source = (
        source_git_sha
        or os.environ.get("MIP_DEPLOYMENT_SOURCE_GIT_SHA", "")
    ).strip()
    if not reviewed_app or not reviewed_lease or not reviewed_source:
        raise RuntimeError(
            "OAuth credential mutation requires the exact signed App deployment lease"
        )
    assert_no_credential_quarantine(workspace, app_name=reviewed_app)
    assertion = app_deployment_lease.held_assertion(
        workspace,
        app_name=reviewed_app,
        lease_id=reviewed_lease,
        source_git_sha=reviewed_source,
    )
    assertion()
    lease_record = app_deployment_lease.assert_held(
        workspace,
        app_name=reviewed_app,
        lease_id=reviewed_lease,
        source_git_sha=reviewed_source,
    )
    writer_application_id = str(
        lease_record.get("writer_application_id") or ""
    ).strip()
    if not writer_application_id:
        raise RuntimeError(
            "OAuth credential deployment lease has no delegated writer"
        )
    return CredentialMutationFence(
        workspace=workspace,
        app_name=reviewed_app,
        lease_id=reviewed_lease,
        source_git_sha=reviewed_source,
        writer_application_id=writer_application_id,
        assertion=assertion,
    )


def held_deployment_credential_recovery_assertion(
    workspace: Any,
    *,
    intent_path: str,
    app_name: str | None = None,
    lease_id: str | None = None,
    source_git_sha: str | None = None,
) -> CredentialMutationFence:
    """Bind recovery to the exact outer App lease named by a signed intent."""

    intent, _encoded = records.read_json(workspace, intent_path)
    records.validate_intent(intent_path, intent)
    reviewed_app = (app_name or os.environ.get("MIP_APP_NAME", "")).strip()
    reviewed_lease = (
        lease_id or os.environ.get("MIP_APP_DEPLOYMENT_LEASE_ID", "")
    ).strip()
    reviewed_source = (
        source_git_sha
        or os.environ.get("MIP_DEPLOYMENT_SOURCE_GIT_SHA", "")
    ).strip()
    if (
        not reviewed_app
        or not reviewed_lease
        or not reviewed_source
        or reviewed_app != records.field(intent, "outer_app_name")
    ):
        raise RuntimeError(
            "OAuth credential recovery requires the exact signed outer App lease"
        )
    unresolved = records.unresolved_record_paths(
        workspace,
        allowed_intent_path=intent_path,
    )
    if unresolved:
        raise RuntimeError(
            "OAuth credential recovery found another unresolved mutation record"
        )
    assertion = app_deployment_lease.held_assertion(
        workspace,
        app_name=reviewed_app,
        lease_id=reviewed_lease,
        source_git_sha=reviewed_source,
    )
    assertion()
    lease_record = app_deployment_lease.assert_held(
        workspace,
        app_name=reviewed_app,
        lease_id=reviewed_lease,
        source_git_sha=reviewed_source,
    )
    writer_application_id = str(
        lease_record.get("writer_application_id") or ""
    ).strip()
    if not writer_application_id:
        raise RuntimeError(
            "OAuth credential recovery lease has no delegated writer"
        )
    return CredentialMutationFence(
        workspace=workspace,
        app_name=reviewed_app,
        lease_id=reviewed_lease,
        source_git_sha=reviewed_source,
        writer_application_id=writer_application_id,
        assertion=assertion,
    )


def _raise_boundary_quarantine(
    fence: CredentialMutationFence,
    error: BaseException,
) -> None:
    if isinstance(error, CredentialMutationQuarantineError):
        label = error.label
        principal_id = error.principal_id
        before_ids = error.before_ids
        candidate_ids = error.candidate_ids
        message = str(error)
    else:
        label = "OAuth credential mutation boundary"
        principal_id = "unknown"
        before_ids = frozenset()
        candidate_ids = frozenset()
        message = "OAuth credential mutation lost its final signed-lease fence"
    raise_credential_quarantine(
        message=message,
        label=label,
        principal_id=principal_id,
        before_ids=before_ids,
        candidate_ids=candidate_ids,
        fence=fence,
        cause=error,
    )


@contextmanager
def app_credential_mutation_boundary(
    workspace: Any,
    *,
    app_name: str,
    writer_application_id: str,
    source_git_sha: str,
) -> Iterator[CredentialMutationFence]:
    """Borrow or acquire the signed App lease around one credential delivery."""

    reviewed_app = app_name.strip()
    reviewed_writer = writer_application_id.strip()
    reviewed_source = source_git_sha.strip()
    assert_no_credential_quarantine(workspace, app_name=reviewed_app)
    borrowed_lease = os.environ.get("MIP_APP_DEPLOYMENT_LEASE_ID", "").strip()
    if borrowed_lease:
        fence = held_deployment_credential_assertion(
            workspace,
            app_name=reviewed_app,
            lease_id=borrowed_lease,
            source_git_sha=reviewed_source,
        )
        try:
            yield fence
        except CredentialMutationQuarantineError as mutation_error:
            _raise_boundary_quarantine(fence, mutation_error)
        except CredentialMutationTerminalFenceError:
            raise
        else:
            try:
                fence()
            except BaseException as final_fence_error:
                raise CredentialMutationTerminalFenceError(
                    "OAuth credential mutation is terminal, but its final "
                    "deployment fence is unproven"
                ) from final_fence_error
        return

    lease_id = app_deployment_lease.acquire(
        workspace,
        app_name=reviewed_app,
        source_git_sha=reviewed_source,
        writer_application_id=reviewed_writer,
    )
    fence = CredentialMutationFence(
        workspace=workspace,
        app_name=reviewed_app,
        lease_id=lease_id,
        source_git_sha=reviewed_source,
        writer_application_id=reviewed_writer,
        assertion=app_deployment_lease.held_assertion(
            workspace,
            app_name=reviewed_app,
            lease_id=lease_id,
            source_git_sha=reviewed_source,
        ),
    )
    quarantined = False
    try:
        try:
            fence()
        except CredentialMutationQuarantineError as mutation_error:
            quarantined = True
            _raise_boundary_quarantine(fence, mutation_error)
        try:
            yield fence
        except CredentialMutationQuarantineError as mutation_error:
            quarantined = True
            _raise_boundary_quarantine(fence, mutation_error)
        except CredentialMutationTerminalFenceError:
            quarantined = True
            raise
        try:
            fence()
        except BaseException as final_fence_error:
            quarantined = True
            raise CredentialMutationTerminalFenceError(
                "OAuth credential mutation is terminal, but its final "
                "deployment fence is unproven"
            ) from final_fence_error
    finally:
        if not quarantined:
            app_deployment_lease.release(
                workspace,
                app_name=reviewed_app,
                lease_id=lease_id,
            )
