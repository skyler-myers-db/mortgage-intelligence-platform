"""Deterministic recovery for one interrupted OAuth credential mutation."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, cast

from tools.databricks import app_deployment_lease
from tools.databricks import oauth_credential_records as records
from tools.databricks.oauth_credential_creation import prove_stable_credential_ids
from tools.databricks.oauth_credential_quarantine import (
    CREDENTIAL_MUTATION_LEASE_NAME,
    CredentialMutationFence,
    CredentialMutationQuarantineError,
    CredentialMutationTerminalFenceError,
    raise_credential_quarantine,
)
from tools.databricks.oauth_credential_resolver_lineage import (
    canonical_resolver_lease_record,
)


@dataclass(frozen=True)
class CredentialMutationRecoveryResult:
    """Non-secret terminal evidence from one recovered mutation."""

    intent_path: str
    principal_id: str
    outcome: str
    revoked_credential_id: str
    sink_disposition: str


@dataclass(frozen=True)
class OrphanCredentialLeaseRecoveryResult:
    """Terminal evidence for a recovered lease that never reached intent."""

    lease_id: str
    recovery_root_lease_id: str
    expected_intent_path: str


@dataclass(frozen=True)
class OrphanCredentialLeaseCoordinates:
    """Signed active-lease coordinates safe to present for reviewed recovery."""

    lease_id: str
    recovery_root_lease_id: str
    source_git_sha: str
    expected_intent_path: str
    intent_present: bool


@dataclass(frozen=True)
class _RecoveryFence:
    outer_fence: CredentialMutationFence
    mutation_fence: CredentialMutationFence
    intent_path: str

    def __call__(self) -> None:
        self.outer_fence.assertion()
        self.mutation_fence.assertion()
        unresolved = records.unresolved_record_paths(
            self.mutation_fence.workspace,
            allowed_intent_path=self.intent_path,
        )
        if unresolved:
            raise RuntimeError(
                "OAuth credential recovery found another unresolved mutation record"
            )

    def quarantine(self, error: CredentialMutationQuarantineError) -> None:
        error.intent_path = self.intent_path
        self.mutation_fence.quarantine(error)


def orphan_credential_mutation_lease_coordinates(
    workspace: Any,
) -> OrphanCredentialLeaseCoordinates:
    """Read the signed global lease coordinate for pre-intent recovery."""

    record = app_deployment_lease._download(  # noqa: SLF001
        workspace,
        app_name=CREDENTIAL_MUTATION_LEASE_NAME,
    )
    if record is None or records.field(record, "state") != "active":
        raise RuntimeError("No active OAuth credential mutation lease exists")
    lease_id = records.field(record, "lease_id")
    recovery_root = records.field(record, "recovery_root_lease_id")
    source_git_sha = records.field(record, "source_git_sha")
    if not lease_id or not recovery_root or len(source_git_sha) != 40:
        raise RuntimeError("OAuth credential mutation lease coordinate is malformed")
    expected_intent_path = records.intent_path(
        CREDENTIAL_MUTATION_LEASE_NAME,
        lease_id,
        lease_id,
    )
    return OrphanCredentialLeaseCoordinates(
        lease_id=lease_id,
        recovery_root_lease_id=recovery_root,
        source_git_sha=source_git_sha,
        expected_intent_path=expected_intent_path,
        intent_present=expected_intent_path
        in set(records.record_paths(workspace)),
    )


def recover_orphan_credential_mutation_lease(
    workspace: Any,
    *,
    outer_fence: CredentialMutationFence,
    expected_lease_id: str,
    expected_recovery_root_lease_id: str,
) -> OrphanCredentialLeaseRecoveryResult:
    """Take over and release an expired global lease that has no intent."""

    outer_fence.assertion()
    coordinate = orphan_credential_mutation_lease_coordinates(workspace)
    if (
        expected_lease_id.strip() != coordinate.lease_id
        or expected_recovery_root_lease_id.strip()
        != coordinate.recovery_root_lease_id
    ):
        raise RuntimeError(
            "OAuth credential orphan-lease confirmations do not match"
        )
    if coordinate.intent_present:
        raise RuntimeError(
            "OAuth credential mutation has an authoritative intent; use "
            "intent recovery"
        )
    unresolved = records.unresolved_record_paths(workspace)
    if unresolved:
        raise RuntimeError(
            "OAuth credential orphan-lease recovery found mutation evidence"
        )
    resolver_lease_id = app_deployment_lease.acquire(
        workspace,
        app_name=CREDENTIAL_MUTATION_LEASE_NAME,
        source_git_sha=outer_fence.source_git_sha,
        writer_application_id=outer_fence.writer_application_id,
        expired_recovery_lease_id=coordinate.recovery_root_lease_id,
    )
    try:
        app_deployment_lease.assert_held(
            workspace,
            app_name=CREDENTIAL_MUTATION_LEASE_NAME,
            lease_id=resolver_lease_id,
            source_git_sha=outer_fence.source_git_sha,
        )
        if (
            coordinate.expected_intent_path
            in set(records.record_paths(workspace))
            or records.unresolved_record_paths(workspace)
        ):
            raise RuntimeError(
                "OAuth credential intent appeared during orphan-lease recovery"
            )
        app_deployment_lease.release(
            workspace,
            app_name=CREDENTIAL_MUTATION_LEASE_NAME,
            lease_id=resolver_lease_id,
        )
        outer_fence.assertion()
    except BaseException as recovery_error:
        raise CredentialMutationTerminalFenceError(
            "OAuth credential orphan-lease recovery is unproven"
        ) from recovery_error
    return OrphanCredentialLeaseRecoveryResult(
        lease_id=coordinate.lease_id,
        recovery_root_lease_id=coordinate.recovery_root_lease_id,
        expected_intent_path=coordinate.expected_intent_path,
    )


def _linked_phases(
    workspace: Any,
    *,
    intent_path: str,
    intent: dict[str, object],
    intent_encoded: bytes,
) -> tuple[
    dict[str, object] | None,
    bytes | None,
    dict[str, object] | None,
    bytes | None,
    dict[str, object] | None,
    bytes | None,
]:
    paths = set(records.record_paths(workspace))
    observed: dict[str, object] | None = None
    observed_encoded: bytes | None = None
    observed_path = records.observed_path(intent_path)
    if observed_path in paths:
        observed, observed_encoded = records.read_json(workspace, observed_path)
        records.validate_observed(
            observed_path,
            observed,
            intent_record_path=intent_path,
            intent_encoded=intent_encoded,
            intent_record=intent,
        )
    sink: dict[str, object] | None = None
    sink_encoded: bytes | None = None
    sink_path = records.sink_attempt_path(intent_path)
    if sink_path in paths:
        if observed_encoded is None:
            raise RuntimeError(
                "OAuth credential recovery sink attempt has no observation"
            )
        sink, sink_encoded = records.read_json(workspace, sink_path)
        records.validate_sink_attempt(
            sink_path,
            sink,
            intent_record_path=intent_path,
            intent_encoded=intent_encoded,
            intent_record=intent,
            observed_encoded=observed_encoded,
        )
    delivery_ack: dict[str, object] | None = None
    delivery_ack_encoded: bytes | None = None
    delivery_ack_path = records.delivery_ack_path(intent_path)
    if delivery_ack_path in paths:
        if (
            observed is None
            or observed_encoded is None
            or sink_encoded is None
        ):
            raise RuntimeError(
                "OAuth credential recovery acknowledgement has incomplete phases"
            )
        delivery_ack, delivery_ack_encoded = records.read_json(
            workspace,
            delivery_ack_path,
        )
        records.validate_delivery_ack(
            delivery_ack_path,
            delivery_ack,
            intent_record_path=intent_path,
            intent_encoded=intent_encoded,
            intent_record=intent,
            observed_record=observed,
            observed_encoded=observed_encoded,
            sink_encoded=sink_encoded,
        )
    return (
        observed,
        observed_encoded,
        sink,
        sink_encoded,
        delivery_ack,
        delivery_ack_encoded,
    )


def _linked_resolution(
    workspace: Any,
    *,
    intent_path: str,
    intent: dict[str, object],
    intent_encoded: bytes,
    observed: dict[str, object] | None,
    observed_encoded: bytes | None,
    sink: dict[str, object] | None,
    sink_encoded: bytes | None,
    delivery_ack: dict[str, object] | None,
    delivery_ack_encoded: bytes | None,
) -> dict[str, object] | None:
    path = records.resolution_path(intent_path)
    if path not in records.record_paths(workspace):
        return None
    resolution, _encoded = records.read_json(workspace, path)
    records.validate_resolution(
        path,
        resolution,
        intent_record_path=intent_path,
        intent_encoded=intent_encoded,
        intent_record=intent,
        observed_record=observed,
        observed_encoded=observed_encoded,
        sink_record=sink,
        sink_encoded=sink_encoded,
        delivery_ack_record=delivery_ack,
        delivery_ack_encoded=delivery_ack_encoded,
        canonical_resolver_lease_record=canonical_resolver_lease_record(
            workspace,
            resolution,
        ),
    )
    return resolution


def _finish_terminal_resolver_lease(
    workspace: Any,
    *,
    outer_fence: CredentialMutationFence,
    resolution: dict[str, object],
) -> None:
    """Take over and release a resolver whose terminal record already exists."""

    outer_fence.assertion()
    lease_id = app_deployment_lease.acquire(
        workspace,
        app_name=CREDENTIAL_MUTATION_LEASE_NAME,
        source_git_sha=outer_fence.source_git_sha,
        writer_application_id=outer_fence.writer_application_id,
        expired_recovery_lease_id=records.field(
            resolution,
            "resolver_lease_recovery_root_id",
        ),
    )
    app_deployment_lease.assert_held(
        workspace,
        app_name=CREDENTIAL_MUTATION_LEASE_NAME,
        lease_id=lease_id,
        source_git_sha=outer_fence.source_git_sha,
    )
    app_deployment_lease.release(
        workspace,
        app_name=CREDENTIAL_MUTATION_LEASE_NAME,
        lease_id=lease_id,
    )
    outer_fence.assertion()


def _write_recovered_observation(
    workspace: Any,
    *,
    intent_path: str,
    intent: dict[str, object],
    intent_encoded: bytes,
    credential_id: str,
    observed_ids: frozenset[str],
) -> tuple[dict[str, object], bytes]:
    payload = {
        "version": records.OBSERVED_VERSION,
        "intent_path": intent_path,
        "intent_sha256": hashlib.sha256(intent_encoded).hexdigest(),
        **{
            name: intent[name]
            for name in (
                "app_name",
                "lease_id",
                "lease_generation_id",
                "lease_generation_seq",
                "lease_record_sha256",
                "mutation_id",
                "principal_id",
            )
        },
        "credential_id": credential_id,
        "observed_credential_ids": sorted(observed_ids),
    }
    path = records.observed_path(intent_path)
    encoded = records.write_immutable_json(
        workspace,
        path=path,
        payload=payload,
    )
    observed, read_encoded = records.read_json(workspace, path)
    if read_encoded != encoded:
        raise RuntimeError("OAuth credential recovered observation changed")
    records.validate_observed(
        path,
        observed,
        intent_record_path=intent_path,
        intent_encoded=intent_encoded,
        intent_record=intent,
    )
    return observed, encoded


def _resolution_payload(
    *,
    intent_path: str,
    intent: dict[str, object],
    intent_encoded: bytes,
    observed: dict[str, object] | None,
    observed_encoded: bytes | None,
    sink: dict[str, object] | None,
    sink_encoded: bytes | None,
    delivery_ack: dict[str, object] | None,
    delivery_ack_encoded: bytes | None,
    resolver_record: dict[str, str | int],
    resolver_source_git_sha: str,
    outcome: str,
    final_ids: frozenset[str],
    retained_credential_id: str,
    sink_disposition: str,
) -> dict[str, object]:
    return {
        "version": records.RESOLUTION_VERSION,
        "intent_path": intent_path,
        "intent_sha256": hashlib.sha256(intent_encoded).hexdigest(),
        **{
            name: intent[name]
            for name in (
                "app_name",
                "outer_app_name",
                "lease_id",
                "lease_recovery_root_id",
                "lease_generation_id",
                "lease_generation_seq",
                "lease_record_sha256",
                "mutation_id",
                "source_git_sha",
                "principal_id",
                "authority_scope",
                "authority_identity",
                "provider_api",
                "operation_mode",
                "sink_descriptor",
                "sink_repository",
                "sink_secret_names",
                "sink_atomic_credential_bundle",
                "retirement_mode",
                "credential_lifetime_seconds",
            )
        },
        "resolver_lease_id": resolver_record["lease_id"],
        "resolver_lease_recovery_root_id": resolver_record[
            "recovery_root_lease_id"
        ],
        "resolver_lease_generation_id": resolver_record["generation_id"],
        "resolver_lease_generation_seq": resolver_record["generation_seq"],
        "resolver_lease_record_sha256": records.canonical_sha256(
            {str(key): value for key, value in resolver_record.items()}
        ),
        "resolver_source_git_sha": resolver_source_git_sha,
        "outcome": outcome,
        "observed_path": records.observed_path(intent_path) if observed else "",
        "observed_sha256": (
            hashlib.sha256(observed_encoded).hexdigest()
            if observed_encoded
            else ""
        ),
        "sink_attempt_path": (
            records.sink_attempt_path(intent_path) if sink else ""
        ),
        "sink_attempt_sha256": (
            hashlib.sha256(sink_encoded).hexdigest() if sink_encoded else ""
        ),
        "delivery_ack_path": (
            records.delivery_ack_path(intent_path) if delivery_ack else ""
        ),
        "delivery_ack_sha256": (
            hashlib.sha256(delivery_ack_encoded).hexdigest()
            if delivery_ack_encoded
            else ""
        ),
        "final_credential_ids": sorted(final_ids),
        "pending_retirement_credential_ids": sorted(
            cast(list[str], intent["before_credential_ids"])
            if outcome == "delivered"
            and records.field(intent, "retirement_mode")
            == "signed_app_cutover"
            else ()
        ),
        "retained_credential_id": retained_credential_id,
        "sink_disposition": sink_disposition,
    }


def recover_oauth_credential_mutation(
    workspace: Any,
    *,
    intent_path: str,
    outer_fence: CredentialMutationFence,
    principal_id: str,
    authority_identity: str,
    provider_api: str,
    list_credentials: Callable[[], Iterable[object]],
    delete_credential: Callable[[str], None],
    invalidate_sink: Callable[[str, frozenset[str]], None] | None = None,
    sleep: Callable[[float], None] | None = None,
) -> CredentialMutationRecoveryResult:
    """Restore one signed interrupted intent under an explicit lease takeover."""

    intent, intent_encoded = records.read_json(workspace, intent_path)
    records.validate_intent(intent_path, intent)
    reviewed_principal = principal_id.strip()
    reviewed_authority = authority_identity.strip()
    reviewed_provider = provider_api.strip()
    if (
        outer_fence.workspace is not workspace
        or outer_fence.app_name != records.field(intent, "outer_app_name")
        or reviewed_principal != records.field(intent, "principal_id")
        or reviewed_authority != records.field(intent, "authority_identity")
        or reviewed_provider != records.field(intent, "provider_api")
    ):
        raise RuntimeError(
            "OAuth credential recovery callback binding does not match its signed intent"
        )
    unresolved = records.unresolved_record_paths(
        workspace,
        allowed_intent_path=intent_path,
    )
    if unresolved:
        raise RuntimeError(
            "OAuth credential recovery found another unresolved mutation record"
        )
    (
        observed,
        observed_encoded,
        sink,
        sink_encoded,
        delivery_ack,
        delivery_ack_encoded,
    ) = _linked_phases(
        workspace,
        intent_path=intent_path,
        intent=intent,
        intent_encoded=intent_encoded,
    )
    terminal_resolution = _linked_resolution(
        workspace,
        intent_path=intent_path,
        intent=intent,
        intent_encoded=intent_encoded,
        observed=observed,
        observed_encoded=observed_encoded,
        sink=sink,
        sink_encoded=sink_encoded,
        delivery_ack=delivery_ack,
        delivery_ack_encoded=delivery_ack_encoded,
    )
    if terminal_resolution is not None:
        try:
            _finish_terminal_resolver_lease(
                workspace,
                outer_fence=outer_fence,
                resolution=terminal_resolution,
            )
        except BaseException as release_error:
            raise CredentialMutationTerminalFenceError(
                "OAuth credential mutation is terminal, but resolver lease "
                "recovery is unproven"
            ) from release_error
        return CredentialMutationRecoveryResult(
            intent_path=intent_path,
            principal_id=reviewed_principal,
            outcome=records.field(terminal_resolution, "outcome"),
            revoked_credential_id="",
            sink_disposition=records.field(
                terminal_resolution,
                "sink_disposition",
            ),
        )
    outer_fence.assertion()
    resolver_lease_id = app_deployment_lease.acquire(
        workspace,
        app_name=CREDENTIAL_MUTATION_LEASE_NAME,
        source_git_sha=outer_fence.source_git_sha,
        writer_application_id=outer_fence.writer_application_id,
        expired_recovery_lease_id=records.field(
            intent,
            "lease_recovery_root_id",
        ),
    )
    resolver_record = app_deployment_lease.assert_held(
        workspace,
        app_name=CREDENTIAL_MUTATION_LEASE_NAME,
        lease_id=resolver_lease_id,
        source_git_sha=outer_fence.source_git_sha,
    )
    mutation_fence = CredentialMutationFence(
        workspace=workspace,
        app_name=CREDENTIAL_MUTATION_LEASE_NAME,
        lease_id=resolver_lease_id,
        source_git_sha=outer_fence.source_git_sha,
        writer_application_id=outer_fence.writer_application_id,
        assertion=app_deployment_lease.held_assertion(
            workspace,
            app_name=CREDENTIAL_MUTATION_LEASE_NAME,
            lease_id=resolver_lease_id,
            source_git_sha=outer_fence.source_git_sha,
        ),
    )
    fence = _RecoveryFence(
        outer_fence=outer_fence,
        mutation_fence=mutation_fence,
        intent_path=intent_path,
    )
    before_ids = frozenset(
        cast(list[str], intent["before_credential_ids"])
    )
    revoked_id = ""
    outcome = "restored"
    final_ids = before_ids
    retained_id = ""
    sink_disposition = "invalidated" if sink else "not_attempted"
    settle = sleep or time.sleep
    try:
        fence()
        current_ids = prove_stable_credential_ids(
            list_credentials,
            assert_single_writer=fence,
            label=records.field(intent, "label"),
            sleep=settle,
            require_full_window=True,
        )
        observed_id = records.field(observed, "credential_id") if observed else ""
        if delivery_ack is not None:
            if not observed_id or sink is None:
                raise RuntimeError(
                    "OAuth credential delivery acknowledgement is incomplete"
                )
            allowed_ids = before_ids | {observed_id}
            if observed_id not in current_ids or current_ids.difference(allowed_ids):
                raise_credential_quarantine(
                    message=(
                        "OAuth credential delivered recovery inventory drifted"
                    ),
                    label=records.field(intent, "label"),
                    principal_id=reviewed_principal,
                    before_ids=before_ids,
                    candidate_ids=current_ids,
                    fence=fence,
                )
            if records.field(intent, "retirement_mode") == "signed_app_cutover":
                if current_ids != allowed_ids:
                    raise_credential_quarantine(
                        message=(
                            "OAuth credential staged-cutover recovery "
                            "inventory drifted"
                        ),
                        label=records.field(intent, "label"),
                        principal_id=reviewed_principal,
                        before_ids=before_ids,
                        candidate_ids=current_ids,
                        fence=fence,
                    )
                final_ids = current_ids
            else:
                retirement_errors: list[BaseException] = []
                for credential_id in sorted(
                    before_ids.intersection(current_ids)
                ):
                    try:
                        fence()
                        delete_credential(credential_id)
                    except BaseException as retirement_error:
                        retirement_errors.append(retirement_error)
                final_ids = prove_stable_credential_ids(
                    list_credentials,
                    assert_single_writer=fence,
                    label=records.field(intent, "label"),
                    sleep=settle,
                    require_full_window=True,
                )
                if final_ids != {observed_id}:
                    raise_credential_quarantine(
                        message=(
                            "OAuth credential delivered recovery retirement "
                            "did not converge"
                        ),
                        label=records.field(intent, "label"),
                        principal_id=reviewed_principal,
                        before_ids=before_ids,
                        candidate_ids=final_ids,
                        fence=fence,
                        cause=(
                            retirement_errors[0]
                            if retirement_errors
                            else None
                        ),
                    )
            outcome = "delivered"
            retained_id = observed_id
            sink_disposition = "acknowledged"
        else:
            extra_ids = current_ids.difference(before_ids)
            missing_ids = before_ids.difference(current_ids)
            if missing_ids or (
                observed_id
                and extra_ids not in (frozenset(), frozenset({observed_id}))
            ):
                raise_credential_quarantine(
                    message="OAuth credential recovery inventory drifted",
                    label=records.field(intent, "label"),
                    principal_id=reviewed_principal,
                    before_ids=before_ids,
                    candidate_ids=extra_ids,
                    fence=fence,
                )
            if not observed_id and extra_ids:
                if len(extra_ids) != 1:
                    raise_credential_quarantine(
                        message=(
                            "OAuth credential recovery has no uniquely "
                            "attributable provider delta"
                        ),
                        label=records.field(intent, "label"),
                        principal_id=reviewed_principal,
                        before_ids=before_ids,
                        candidate_ids=extra_ids,
                        fence=fence,
                    )
                observed_id = next(iter(extra_ids))
                observed, observed_encoded = _write_recovered_observation(
                    workspace,
                    intent_path=intent_path,
                    intent=intent,
                    intent_encoded=intent_encoded,
                    credential_id=observed_id,
                    observed_ids=current_ids,
                )
            if not observed_id:
                raise_credential_quarantine(
                    message=(
                        "OAuth credential recovery has no attributable "
                        "credential and no provider proof that a delayed "
                        "create cannot still commit"
                    ),
                    label=records.field(intent, "label"),
                    principal_id=reviewed_principal,
                    before_ids=before_ids,
                    candidate_ids=frozenset(),
                    fence=fence,
                )
            restored_ids = current_ids
            if observed_id in current_ids:
                delete_error: BaseException | None = None
                try:
                    fence()
                    delete_credential(observed_id)
                except BaseException as exc:
                    delete_error = exc
                restored_ids = prove_stable_credential_ids(
                    list_credentials,
                    assert_single_writer=fence,
                    label=records.field(intent, "label"),
                    sleep=settle,
                    require_full_window=True,
                )
                if delete_error is not None or restored_ids != before_ids:
                    raise_credential_quarantine(
                        message=(
                            "OAuth credential recovery delete result is ambiguous"
                        ),
                        label=records.field(intent, "label"),
                        principal_id=reviewed_principal,
                        before_ids=before_ids,
                        candidate_ids=frozenset({observed_id}),
                        fence=fence,
                        cause=delete_error,
                    )
                revoked_id = observed_id
            if restored_ids != before_ids:
                raise_credential_quarantine(
                    message="OAuth credential recovery did not restore inventory",
                    label=records.field(intent, "label"),
                    principal_id=reviewed_principal,
                    before_ids=before_ids,
                    candidate_ids=restored_ids,
                    fence=fence,
                )
            final_ids = restored_ids
            if sink is not None:
                if invalidate_sink is None:
                    raise RuntimeError(
                        "OAuth credential recovery requires an exact sink invalidator"
                    )
                repository = records.field(sink, "repository")
                secret_names = frozenset(
                    cast(list[str], sink["secret_names"])
                )
                invalidate_sink(repository, secret_names)
                fence()
        payload = _resolution_payload(
            intent_path=intent_path,
            intent=intent,
            intent_encoded=intent_encoded,
            observed=observed,
            observed_encoded=observed_encoded,
            sink=sink,
            sink_encoded=sink_encoded,
            delivery_ack=delivery_ack,
            delivery_ack_encoded=delivery_ack_encoded,
            resolver_record={
                str(key): value for key, value in resolver_record.items()
            },
            resolver_source_git_sha=outer_fence.source_git_sha,
            outcome=outcome,
            final_ids=final_ids,
            retained_credential_id=retained_id,
            sink_disposition=sink_disposition,
        )
        resolution_path = records.resolution_path(intent_path)
        records.write_immutable_json(
            workspace,
            path=resolution_path,
            payload=payload,
        )
        resolution, _encoded = records.read_json(workspace, resolution_path)
        records.validate_resolution(
            resolution_path,
            resolution,
            intent_record_path=intent_path,
            intent_encoded=intent_encoded,
            intent_record=intent,
            observed_record=observed,
            observed_encoded=observed_encoded,
            sink_record=sink,
            sink_encoded=sink_encoded,
            delivery_ack_record=delivery_ack,
            delivery_ack_encoded=delivery_ack_encoded,
            canonical_resolver_lease_record=canonical_resolver_lease_record(
                workspace,
                resolution,
            ),
        )
        if records.unresolved_record_paths(workspace):
            raise RuntimeError(
                "OAuth credential recovery did not clear global admission"
            )
    except CredentialMutationQuarantineError:
        raise
    except BaseException as recovery_error:
        raise_credential_quarantine(
            message="OAuth credential mutation recovery is unproven",
            label=records.field(intent, "label"),
            principal_id=reviewed_principal,
            before_ids=before_ids,
            candidate_ids=(
                frozenset({records.field(observed, "credential_id")})
                if observed
                else frozenset()
            ),
            fence=fence,
            cause=recovery_error,
        )
    try:
        app_deployment_lease.release(
            workspace,
            app_name=CREDENTIAL_MUTATION_LEASE_NAME,
            lease_id=resolver_lease_id,
        )
        outer_fence.assertion()
    except BaseException as release_error:
        raise CredentialMutationTerminalFenceError(
            "OAuth credential recovery is terminal, but its resolver lease "
            "release is unproven"
        ) from release_error
    return CredentialMutationRecoveryResult(
        intent_path=intent_path,
        principal_id=reviewed_principal,
        outcome=outcome,
        revoked_credential_id=revoked_id,
        sink_disposition=sink_disposition,
    )
