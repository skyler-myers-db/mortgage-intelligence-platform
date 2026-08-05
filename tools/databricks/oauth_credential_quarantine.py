"""Durable fail-closed fencing for ambiguous OAuth credential mutations."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from tools.databricks import oauth_credential_records as records

CREDENTIAL_MUTATION_LEASE_NAME = records.CREDENTIAL_MUTATION_LEASE_NAME


class CredentialMutationQuarantineError(RuntimeError):
    """Credential state is not provably restored and must remain fenced."""

    def __init__(
        self,
        message: str,
        *,
        label: str,
        principal_id: str,
        before_ids: frozenset[str],
        candidate_ids: frozenset[str] = frozenset(),
        intent_path: str = "",
    ) -> None:
        super().__init__(message)
        self.label = label
        self.principal_id = principal_id
        self.before_ids = before_ids
        self.candidate_ids = candidate_ids
        self.intent_path = intent_path


class CredentialMutationTerminalFenceError(RuntimeError):
    """Provider/sink state is terminal, but a surrounding lease is unproven."""


@dataclass(frozen=True)
class CredentialMutationContext:
    """Non-secret provider and sink coordinates needed for crash recovery."""

    authority_scope: str
    authority_identity: str
    provider_api: str
    operation_mode: str
    sink_descriptor: str
    credential_lifetime_seconds: int
    sink_repository: str = ""
    sink_secret_names: frozenset[str] = frozenset()
    sink_atomic_credential_bundle: bool = False
    retirement_mode: str = "immediate"

    def __post_init__(self) -> None:
        values = (
            self.authority_scope,
            self.authority_identity,
            self.provider_api,
            self.operation_mode,
            self.sink_descriptor,
        )
        repository = self.sink_repository
        secret_names = self.sink_secret_names
        canonical_sink = (
            f"github:{repository}:"
            f"atomic={str(self.sink_atomic_credential_bundle).lower()}:"
            + ",".join(sorted(secret_names))
        )
        if (
            self.authority_scope not in {"workspace", "account"}
            or self.operation_mode not in {"persistent_delivery", "temporary_probe"}
            or not isinstance(self.credential_lifetime_seconds, int)
            or isinstance(self.credential_lifetime_seconds, bool)
            or self.credential_lifetime_seconds < 0
            or (
                self.operation_mode == "temporary_probe"
                and self.credential_lifetime_seconds == 0
            )
            or (
                self.operation_mode == "persistent_delivery"
                and self.credential_lifetime_seconds != 0
            )
            or any(
                not value
                or value != value.strip()
                or len(value) > 512
                or "\n" in value
                for value in values
            )
            or not isinstance(secret_names, frozenset)
            or not isinstance(self.sink_atomic_credential_bundle, bool)
            or self.retirement_mode
            not in {"immediate", "signed_app_cutover"}
            or any(
                not name
                or name != name.strip()
                or len(name) > 256
                or "\n" in name
                for name in secret_names
            )
            or (
                self.operation_mode == "persistent_delivery"
                and (
                    not repository
                    or repository != repository.strip()
                    or len(repository) > 512
                    or "\n" in repository
                    or not secret_names
                    or self.sink_descriptor != canonical_sink
                )
            )
            or (
                self.operation_mode == "temporary_probe"
                and (
                    repository
                    or secret_names
                    or self.sink_atomic_credential_bundle
                    or self.retirement_mode != "immediate"
                )
            )
            or (
                self.retirement_mode == "signed_app_cutover"
                and (
                    self.operation_mode != "persistent_delivery"
                    or not self.sink_atomic_credential_bundle
                )
            )
        ):
            raise ValueError("OAuth credential mutation context is invalid")


def _quarantine_path(app_name: str, lease_id: str) -> str:
    return records.quarantine_path(app_name, lease_id)


def assert_no_credential_quarantine(
    workspace: Any,
    *,
    app_name: str,
    allowed_intent_path: str = "",
) -> None:
    """Globally block baselines while any mutation evidence is unresolved."""

    records.validate_app_name(app_name)
    paths = records.unresolved_record_paths(
        workspace,
        allowed_intent_path=allowed_intent_path,
    )
    if paths:
        raise CredentialMutationQuarantineError(
            "OAuth credential mutation is unresolved; reconcile the durable workspace "
            f"record before retrying: {paths[0]}",
            label="existing credential recovery record",
            principal_id="unknown",
            before_ids=frozenset(),
        )


def _write_parent_marker() -> None:
    marker = os.environ.get("MIP_OAUTH_CREDENTIAL_QUARANTINE_FILE", "").strip()
    if marker:
        Path(marker).write_text(
            "OAuth credential cleanup is unproven; retain the deployment lease.\n",
            encoding="utf-8",
        )


@dataclass(frozen=True)
class CredentialMutationFence:
    """Callable lease assertion with an immutable quarantine recorder."""

    workspace: Any
    app_name: str
    lease_id: str
    source_git_sha: str
    writer_application_id: str
    assertion: Callable[[], None]

    def __call__(self) -> None:
        self.assertion()
        assert_no_credential_quarantine(self.workspace, app_name=self.app_name)

    def quarantine(self, error: CredentialMutationQuarantineError) -> None:
        _write_parent_marker()
        payload = {
            "version": records.QUARANTINE_VERSION,
            "app_name": self.app_name,
            "lease_id": self.lease_id,
            "source_git_sha": self.source_git_sha,
            "label": error.label,
            "principal_id": error.principal_id,
            "intent_path": error.intent_path,
            "before_credential_ids": sorted(error.before_ids),
            "candidate_credential_ids": sorted(error.candidate_ids),
        }
        records.write_immutable_json(
            self.workspace,
            path=_quarantine_path(self.app_name, self.lease_id),
            payload=payload,
        )

    def begin_session(
        self,
        *,
        label: str,
        principal_id: str,
        context: CredentialMutationContext,
    ) -> CredentialMutationSession:
        """Acquire the one global credential lease before reading a baseline."""

        from tools.databricks import app_deployment_lease

        self()
        lease_id = app_deployment_lease.acquire(
            self.workspace,
            app_name=CREDENTIAL_MUTATION_LEASE_NAME,
            source_git_sha=self.source_git_sha,
            writer_application_id=self.writer_application_id,
        )
        try:
            lease_record = app_deployment_lease.assert_held(
                self.workspace,
                app_name=CREDENTIAL_MUTATION_LEASE_NAME,
                lease_id=lease_id,
                source_git_sha=self.source_git_sha,
            )
            generation_id = str(lease_record.get("generation_id") or "").strip()
            generation_seq = lease_record.get("generation_seq")
            recovery_root_lease_id = str(
                lease_record.get("recovery_root_lease_id") or ""
            ).strip()
            if (
                not generation_id
                or not recovery_root_lease_id
                or not isinstance(generation_seq, int)
                or isinstance(generation_seq, bool)
                or generation_seq < 0
            ):
                raise RuntimeError(
                    "OAuth credential mutation lease generation is invalid"
                )
            mutation_fence = CredentialMutationFence(
                workspace=self.workspace,
                app_name=CREDENTIAL_MUTATION_LEASE_NAME,
                lease_id=lease_id,
                source_git_sha=self.source_git_sha,
                writer_application_id=self.writer_application_id,
                assertion=app_deployment_lease.held_assertion(
                    self.workspace,
                    app_name=CREDENTIAL_MUTATION_LEASE_NAME,
                    lease_id=lease_id,
                    source_git_sha=self.source_git_sha,
                ),
            )
            session = CredentialMutationSession(
                outer_fence=self,
                mutation_fence=mutation_fence,
                label=label,
                principal_id=principal_id,
                context=context,
                lease_recovery_root_id=recovery_root_lease_id,
                lease_generation_id=generation_id,
                lease_generation_seq=generation_seq,
                lease_record_sha256=records.canonical_sha256(
                    {str(key): value for key, value in lease_record.items()}
                ),
            )
            session()
            return session
        except BaseException:
            try:
                app_deployment_lease.release(
                    self.workspace,
                    app_name=CREDENTIAL_MUTATION_LEASE_NAME,
                    lease_id=lease_id,
                )
            except BaseException as release_error:
                _write_parent_marker()
                raise CredentialMutationTerminalFenceError(
                    "OAuth credential mutation lease setup failed and its "
                    "pre-intent lease release is unproven"
                ) from release_error
            raise


@dataclass
class CredentialMutationSession:
    """One globally serialized credential operation before its provider baseline."""

    outer_fence: CredentialMutationFence
    mutation_fence: CredentialMutationFence
    label: str
    principal_id: str
    context: CredentialMutationContext
    lease_recovery_root_id: str
    lease_generation_id: str
    lease_generation_seq: int
    lease_record_sha256: str
    intent_path: str = ""
    released: bool = False

    def __call__(self) -> None:
        if self.released:
            raise RuntimeError("OAuth credential mutation session is already released")
        self.outer_fence.assertion()
        self.mutation_fence.assertion()
        assert_no_credential_quarantine(
            self.mutation_fence.workspace,
            app_name=CREDENTIAL_MUTATION_LEASE_NAME,
            allowed_intent_path=self.intent_path,
        )

    def quarantine(self, error: CredentialMutationQuarantineError) -> None:
        error.intent_path = self.intent_path
        self.mutation_fence.quarantine(error)

    def persist_intent(
        self,
        *,
        before_ids: frozenset[str],
    ) -> CredentialMutationIntent:
        """Persist exact recovery coordinates before the provider create."""

        self()
        mutation_id = self.mutation_fence.lease_id
        path = records.intent_path(
            CREDENTIAL_MUTATION_LEASE_NAME,
            self.mutation_fence.lease_id,
            mutation_id,
        )
        payload = {
            "version": records.INTENT_VERSION,
            "app_name": CREDENTIAL_MUTATION_LEASE_NAME,
            "outer_app_name": self.outer_fence.app_name,
            "lease_id": self.mutation_fence.lease_id,
            "lease_recovery_root_id": self.lease_recovery_root_id,
            "lease_generation_id": self.lease_generation_id,
            "lease_generation_seq": self.lease_generation_seq,
            "lease_record_sha256": self.lease_record_sha256,
            "mutation_id": mutation_id,
            "source_git_sha": self.mutation_fence.source_git_sha,
            "label": self.label,
            "principal_id": self.principal_id,
            "authority_scope": self.context.authority_scope,
            "authority_identity": self.context.authority_identity,
            "provider_api": self.context.provider_api,
            "operation_mode": self.context.operation_mode,
            "sink_descriptor": self.context.sink_descriptor,
            "sink_repository": self.context.sink_repository,
            "sink_secret_names": sorted(self.context.sink_secret_names),
            "sink_atomic_credential_bundle": (
                self.context.sink_atomic_credential_bundle
            ),
            "retirement_mode": self.context.retirement_mode,
            "credential_lifetime_seconds": (
                self.context.credential_lifetime_seconds
            ),
            "before_credential_ids": sorted(before_ids),
        }
        self.intent_path = path
        try:
            encoded = records.write_immutable_json(
                self.mutation_fence.workspace,
                path=path,
                payload=payload,
            )
            intent = CredentialMutationIntent(
                session=self,
                path=path,
                mutation_id=mutation_id,
                before_ids=before_ids,
                encoded=encoded,
            )
            intent()
            return intent
        except BaseException as intent_error:
            _write_parent_marker()
            raise CredentialMutationTerminalFenceError(
                f"{self.label} credential intent persistence is unproven; "
                "the signed global lease is retained for recovery"
            ) from intent_error

    def abort_before_intent(self) -> None:
        """Release the global lease only when no provider mutation was attempted."""

        from tools.databricks import app_deployment_lease

        if self.intent_path:
            raise RuntimeError("OAuth credential mutation intent cannot be aborted")
        try:
            self()
            app_deployment_lease.release(
                self.mutation_fence.workspace,
                app_name=CREDENTIAL_MUTATION_LEASE_NAME,
                lease_id=self.mutation_fence.lease_id,
            )
            self.released = True
            self.outer_fence()
        except BaseException as release_error:
            _write_parent_marker()
            raise CredentialMutationTerminalFenceError(
                f"{self.label} unused credential lease release is unproven"
            ) from release_error

    def release_after_resolution(self) -> None:
        from tools.databricks import app_deployment_lease

        try:
            self.outer_fence.assertion()
            self.mutation_fence.assertion()
            app_deployment_lease.release(
                self.mutation_fence.workspace,
                app_name=CREDENTIAL_MUTATION_LEASE_NAME,
                lease_id=self.mutation_fence.lease_id,
            )
            self.released = True
        except BaseException as release_error:
            raise CredentialMutationTerminalFenceError(
                f"{self.label} credential mutation is terminal, but its global "
                "lease release is unproven"
            ) from release_error


@dataclass
class CredentialMutationIntent:
    """One durable unresolved mutation and its intent-specific lease assertion."""

    session: CredentialMutationSession
    path: str
    mutation_id: str
    before_ids: frozenset[str]
    encoded: bytes
    observed_path: str = ""
    observed_encoded: bytes = b""
    observed_credential_id: str = ""
    sink_path: str = ""
    sink_encoded: bytes = b""
    delivery_ack_path: str = ""
    delivery_ack_encoded: bytes = b""

    def __call__(self) -> None:
        self.session()

    def quarantine(self, error: CredentialMutationQuarantineError) -> None:
        error.intent_path = self.path
        self.session.quarantine(error)

    @property
    def fence(self) -> CredentialMutationFence:
        return self.session.outer_fence

    @property
    def principal_id(self) -> str:
        return self.session.principal_id

    @property
    def retirement_mode(self) -> str:
        return self.session.context.retirement_mode

    def observe(
        self,
        *,
        credential_id: str,
        observed_ids: frozenset[str],
    ) -> None:
        """Persist the immutable provider candidate before exposing its secret."""

        self()
        path = records.observed_path(self.path)
        payload = {
            "version": records.OBSERVED_VERSION,
            "intent_path": self.path,
            "intent_sha256": hashlib.sha256(self.encoded).hexdigest(),
            "app_name": CREDENTIAL_MUTATION_LEASE_NAME,
            "lease_id": self.session.mutation_fence.lease_id,
            "lease_generation_id": self.session.lease_generation_id,
            "lease_generation_seq": self.session.lease_generation_seq,
            "lease_record_sha256": self.session.lease_record_sha256,
            "mutation_id": self.mutation_id,
            "principal_id": self.principal_id,
            "credential_id": credential_id,
            "observed_credential_ids": sorted(observed_ids),
        }
        try:
            self.observed_encoded = records.write_immutable_json(
                self.session.mutation_fence.workspace,
                path=path,
                payload=payload,
            )
            self.observed_path = path
            self.observed_credential_id = credential_id
            self()
        except BaseException as observation_error:
            raise_credential_quarantine(
                message=f"{self.session.label} credential observation is unproven",
                label=self.session.label,
                principal_id=self.principal_id,
                before_ids=self.before_ids,
                candidate_ids=frozenset({credential_id}),
                fence=self,
                cause=observation_error,
            )

    def arm_sink(
        self,
        *,
        repository: str,
        secret_names: frozenset[str],
        atomic_credential_bundle: bool,
    ) -> None:
        """Persist every sink coordinate before the first external write."""

        if not self.observed_path or not self.observed_encoded:
            raise RuntimeError("OAuth credential sink requires an observed candidate")
        expected = self.session.context
        if (
            repository != expected.sink_repository
            or secret_names != expected.sink_secret_names
            or atomic_credential_bundle
            != expected.sink_atomic_credential_bundle
        ):
            raise RuntimeError(
                "OAuth credential sink coordinates do not match the signed intent"
            )
        self()
        path = records.sink_attempt_path(self.path)
        payload = {
            "version": records.SINK_ATTEMPT_VERSION,
            "intent_path": self.path,
            "intent_sha256": hashlib.sha256(self.encoded).hexdigest(),
            "observed_path": self.observed_path,
            "observed_sha256": hashlib.sha256(
                self.observed_encoded
            ).hexdigest(),
            "repository": repository,
            "secret_names": sorted(secret_names),
            "atomic_credential_bundle": atomic_credential_bundle,
        }
        try:
            self.sink_encoded = records.write_immutable_json(
                self.session.mutation_fence.workspace,
                path=path,
                payload=payload,
            )
            self.sink_path = path
            self()
        except BaseException as sink_error:
            raise_credential_quarantine(
                message=f"{self.session.label} credential sink intent is unproven",
                label=self.session.label,
                principal_id=self.principal_id,
                before_ids=self.before_ids,
                candidate_ids=frozenset({self.observed_credential_id}),
                fence=self,
                cause=sink_error,
            )

    def acknowledge_delivery(
        self,
        *,
        acknowledged_ids: frozenset[str],
    ) -> None:
        """Persist the sink/provider commit boundary before retiring old secrets."""

        if (
            not self.observed_path
            or not self.observed_encoded
            or not self.sink_path
            or not self.sink_encoded
            or not self.observed_credential_id
            or acknowledged_ids
            != self.before_ids | {self.observed_credential_id}
        ):
            raise RuntimeError(
                "OAuth credential delivery acknowledgement is incomplete"
            )
        self()
        path = records.delivery_ack_path(self.path)
        payload = {
            "version": records.DELIVERY_ACK_VERSION,
            "intent_path": self.path,
            "intent_sha256": hashlib.sha256(self.encoded).hexdigest(),
            "observed_path": self.observed_path,
            "observed_sha256": hashlib.sha256(
                self.observed_encoded
            ).hexdigest(),
            "sink_attempt_path": self.sink_path,
            "sink_attempt_sha256": hashlib.sha256(
                self.sink_encoded
            ).hexdigest(),
            "credential_id": self.observed_credential_id,
            "acknowledged_credential_ids": sorted(acknowledged_ids),
            "retire_credential_ids": sorted(self.before_ids),
            "retirement_mode": self.retirement_mode,
        }
        try:
            self.delivery_ack_encoded = records.write_immutable_json(
                self.session.mutation_fence.workspace,
                path=path,
                payload=payload,
            )
            self.delivery_ack_path = path
            self()
        except BaseException as acknowledgement_error:
            raise_credential_quarantine(
                message=(
                    f"{self.session.label} credential delivery acknowledgement "
                    "is unproven"
                ),
                label=self.session.label,
                principal_id=self.principal_id,
                before_ids=self.before_ids,
                candidate_ids=frozenset({self.observed_credential_id}),
                fence=self,
                cause=acknowledgement_error,
            )

    def resolve(
        self,
        *,
        outcome: str,
        final_ids: frozenset[str],
        retained_credential_id: str = "",
        sink_disposition: str = "not_attempted",
    ) -> None:
        """Append the exact terminal record and prove global admission is clear."""

        self()
        payload = {
            "version": records.RESOLUTION_VERSION,
            "intent_path": self.path,
            "intent_sha256": hashlib.sha256(self.encoded).hexdigest(),
            "app_name": CREDENTIAL_MUTATION_LEASE_NAME,
            "lease_id": self.session.mutation_fence.lease_id,
            "lease_generation_id": self.session.lease_generation_id,
            "lease_generation_seq": self.session.lease_generation_seq,
            "lease_record_sha256": self.session.lease_record_sha256,
            "mutation_id": self.mutation_id,
            "source_git_sha": self.session.mutation_fence.source_git_sha,
            "principal_id": self.principal_id,
            "authority_scope": self.session.context.authority_scope,
            "authority_identity": self.session.context.authority_identity,
            "provider_api": self.session.context.provider_api,
            "operation_mode": self.session.context.operation_mode,
            "sink_descriptor": self.session.context.sink_descriptor,
            "sink_repository": self.session.context.sink_repository,
            "sink_secret_names": sorted(
                self.session.context.sink_secret_names
            ),
            "sink_atomic_credential_bundle": (
                self.session.context.sink_atomic_credential_bundle
            ),
            "retirement_mode": self.retirement_mode,
            "credential_lifetime_seconds": (
                self.session.context.credential_lifetime_seconds
            ),
            "outer_app_name": self.session.outer_fence.app_name,
            "lease_recovery_root_id": self.session.lease_recovery_root_id,
            "resolver_lease_id": self.session.mutation_fence.lease_id,
            "resolver_lease_recovery_root_id": (
                self.session.lease_recovery_root_id
            ),
            "resolver_lease_generation_id": self.session.lease_generation_id,
            "resolver_lease_generation_seq": self.session.lease_generation_seq,
            "resolver_lease_record_sha256": self.session.lease_record_sha256,
            "resolver_source_git_sha": (
                self.session.mutation_fence.source_git_sha
            ),
            "outcome": outcome,
            "observed_path": self.observed_path,
            "observed_sha256": (
                hashlib.sha256(self.observed_encoded).hexdigest()
                if self.observed_encoded
                else ""
            ),
            "sink_attempt_path": self.sink_path,
            "sink_attempt_sha256": (
                hashlib.sha256(self.sink_encoded).hexdigest()
                if self.sink_encoded
                else ""
            ),
            "delivery_ack_path": self.delivery_ack_path,
            "delivery_ack_sha256": (
                hashlib.sha256(self.delivery_ack_encoded).hexdigest()
                if self.delivery_ack_encoded
                else ""
            ),
            "final_credential_ids": sorted(final_ids),
            "pending_retirement_credential_ids": sorted(
                self.before_ids
                if outcome == "delivered"
                and self.retirement_mode == "signed_app_cutover"
                else ()
            ),
            "retained_credential_id": retained_credential_id,
            "sink_disposition": sink_disposition,
        }
        try:
            records.write_immutable_json(
                self.session.mutation_fence.workspace,
                path=records.resolution_path(self.path),
                payload=payload,
            )
            unresolved = records.unresolved_record_paths(
                self.session.mutation_fence.workspace,
                allowed_intent_path=self.path,
            )
            if unresolved:
                raise RuntimeError(
                    "OAuth credential resolution did not clear global admission"
                )
            self.session.release_after_resolution()
        except CredentialMutationTerminalFenceError:
            raise
        except BaseException as resolution_error:
            raise_credential_quarantine(
                message=(
                    f"{self.session.label} credential intent resolution is unproven"
                ),
                label=self.session.label,
                principal_id=self.principal_id,
                before_ids=self.before_ids,
                candidate_ids=(
                    frozenset({retained_credential_id})
                    if retained_credential_id
                    else frozenset()
                ),
                fence=self,
                cause=resolution_error,
            )


def begin_credential_mutation_session(
    fence: Callable[[], None],
    *,
    label: str,
    principal_id: str,
    context: CredentialMutationContext,
) -> CredentialMutationSession:
    """Acquire the required global session through a deployment-bound fence."""

    begin = getattr(fence, "begin_session", None)
    if not callable(begin):
        raise RuntimeError(
            "OAuth credential creation requires a durable global mutation fence"
        )
    session = begin(
        label=label,
        principal_id=principal_id,
        context=context,
    )
    if not isinstance(session, CredentialMutationSession):
        raise RuntimeError("OAuth credential mutation session is invalid")
    return session


def raise_credential_quarantine(
    *,
    message: str,
    label: str,
    principal_id: str,
    before_ids: frozenset[str],
    candidate_ids: frozenset[str] = frozenset(),
    fence: Callable[[], None],
    cause: BaseException | None = None,
) -> NoReturn:
    """Persist recovery evidence and raise the distinct fail-closed outcome."""

    error = CredentialMutationQuarantineError(
        message,
        label=label,
        principal_id=principal_id,
        before_ids=before_ids,
        candidate_ids=candidate_ids,
    )
    _write_parent_marker()
    recorder = getattr(fence, "quarantine", None)
    if callable(recorder):
        try:
            recorder(error)
        except BaseException as record_error:
            raise CredentialMutationQuarantineError(
                f"{message}; durable quarantine persistence also failed",
                label=label,
                principal_id=principal_id,
                before_ids=before_ids,
                candidate_ids=candidate_ids,
            ) from record_error
    raise error from cause
