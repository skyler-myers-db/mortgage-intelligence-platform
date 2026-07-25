"""Exact terminal-record validation for OAuth credential mutation recovery."""

from __future__ import annotations

import hashlib

from tools.databricks import oauth_credential_records as records
from tools.databricks.oauth_credential_record_schema import (
    has_exact_string_fields,
)
from tools.databricks.oauth_credential_resolver_lineage import (
    validate_resolution_resolver,
)


def validate_resolution(
    path: str,
    record: dict[str, object],
    *,
    intent_record_path: str,
    intent_encoded: bytes,
    intent_record: dict[str, object],
    observed_record: dict[str, object] | None,
    observed_encoded: bytes | None,
    sink_record: dict[str, object] | None,
    sink_encoded: bytes | None,
    delivery_ack_record: dict[str, object] | None,
    delivery_ack_encoded: bytes | None,
    canonical_resolver_lease_record: dict[str, str | int],
) -> None:
    expected = {
        "version",
        "intent_path",
        "intent_sha256",
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
        "resolver_lease_id",
        "resolver_lease_recovery_root_id",
        "resolver_lease_generation_id",
        "resolver_lease_generation_seq",
        "resolver_lease_record_sha256",
        "resolver_source_git_sha",
        "outcome",
        "observed_path",
        "observed_sha256",
        "sink_attempt_path",
        "sink_attempt_sha256",
        "delivery_ack_path",
        "delivery_ack_sha256",
        "final_credential_ids",
        "pending_retirement_credential_ids",
        "retained_credential_id",
        "sink_disposition",
    }
    if (
        set(record) != expected
        or not has_exact_string_fields(
            record,
            expected
            - {
                "version",
                "lease_generation_seq",
                "sink_secret_names",
                "sink_atomic_credential_bundle",
                "credential_lifetime_seconds",
                "resolver_lease_generation_seq",
                "final_credential_ids",
                "pending_retirement_credential_ids",
            },
        )
        or type(record.get("version")) is not int
        or record.get("version") != records.RESOLUTION_VERSION
        or path != records.resolution_path(intent_record_path)
        or records.field(record, "intent_path") != intent_record_path
        or records.field(record, "intent_sha256")
        != hashlib.sha256(intent_encoded).hexdigest()
        or any(
            records.field(record, name)
            != records.field(intent_record, name)
            for name in (
                "app_name",
                "outer_app_name",
                "lease_id",
                "lease_recovery_root_id",
                "lease_generation_id",
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
                "retirement_mode",
            )
        )
        or type(record.get("lease_generation_seq")) is not int
        or record.get("lease_generation_seq")
        != intent_record.get("lease_generation_seq")
        or records.exact_string_list(
            record.get("sink_secret_names"),
            label="OAuth credential resolution sink secret names",
        )
        != records.exact_string_list(
            intent_record.get("sink_secret_names"),
            label="OAuth credential intent sink secret names",
        )
        or record.get("sink_atomic_credential_bundle")
        is not intent_record.get("sink_atomic_credential_bundle")
        or type(record.get("credential_lifetime_seconds")) is not int
        or record.get("credential_lifetime_seconds")
        != intent_record.get("credential_lifetime_seconds")
        or records.field(record, "outcome") not in {"delivered", "restored"}
    ):
        raise RuntimeError(f"OAuth credential resolution is malformed: {path}")
    final_ids = records.exact_string_list(
        record.get("final_credential_ids"),
        label="OAuth credential resolution inventory",
    )
    before_ids = records.exact_string_list(
        intent_record.get("before_credential_ids"),
        label="OAuth credential intent prior inventory",
    )
    pending_retirement_ids = records.exact_string_list(
        record.get("pending_retirement_credential_ids"),
        label="OAuth credential pending retirement inventory",
    )
    outcome = records.field(record, "outcome")
    retained_id = records.field(record, "retained_credential_id")
    sink_disposition = records.field(record, "sink_disposition")
    validate_resolution_resolver(
        record,
        intent_record,
        canonical_resolver_lease_record,
    )
    if outcome == "delivered":
        if observed_record is None or observed_encoded is None:
            raise RuntimeError(
                f"OAuth credential delivered resolution has no observation: {path}"
            )
        if sink_record is None or sink_encoded is None:
            raise RuntimeError(
                f"OAuth credential delivered resolution has no sink attempt: {path}"
            )
        if delivery_ack_record is None or delivery_ack_encoded is None:
            raise RuntimeError(
                "OAuth credential delivered resolution has no "
                f"acknowledgement: {path}"
            )
        observed_id = records.field(observed_record, "credential_id")
        retirement_mode = records.field(intent_record, "retirement_mode")
        expected_final_ids = (
            set(before_ids) | {observed_id}
            if retirement_mode == "signed_app_cutover"
            else {observed_id}
        )
        expected_pending_ids = (
            before_ids if retirement_mode == "signed_app_cutover" else ()
        )
        if (
            retained_id != observed_id
            or set(final_ids) != expected_final_ids
            or pending_retirement_ids != expected_pending_ids
            or sink_disposition != "acknowledged"
            or records.field(record, "observed_path")
            != records.observed_path(intent_record_path)
            or records.field(record, "observed_sha256")
            != hashlib.sha256(observed_encoded).hexdigest()
            or records.field(record, "sink_attempt_path")
            != records.sink_attempt_path(intent_record_path)
            or records.field(record, "sink_attempt_sha256")
            != hashlib.sha256(sink_encoded).hexdigest()
            or records.field(record, "delivery_ack_path")
            != records.delivery_ack_path(intent_record_path)
            or records.field(record, "delivery_ack_sha256")
            != hashlib.sha256(delivery_ack_encoded).hexdigest()
        ):
            raise RuntimeError(
                "OAuth credential delivered resolution is semantically "
                f"invalid: {path}"
            )
    elif (
        retained_id
        or final_ids != before_ids
        or pending_retirement_ids
        or sink_disposition
        not in ({"not_attempted"} if sink_record is None else {"invalidated"})
        or records.field(record, "observed_path")
        != (
            records.observed_path(intent_record_path)
            if observed_record
            else ""
        )
        or records.field(record, "observed_sha256")
        != (
            hashlib.sha256(observed_encoded).hexdigest()
            if observed_encoded
            else ""
        )
        or records.field(record, "sink_attempt_path")
        != (
            records.sink_attempt_path(intent_record_path)
            if sink_record
            else ""
        )
        or records.field(record, "sink_attempt_sha256")
        != (
            hashlib.sha256(sink_encoded).hexdigest()
            if sink_encoded
            else ""
        )
        or delivery_ack_record is not None
        or delivery_ack_encoded is not None
        or records.field(record, "delivery_ack_path")
        or records.field(record, "delivery_ack_sha256")
    ):
        raise RuntimeError(
            f"OAuth credential restored resolution is semantically invalid: {path}"
        )
