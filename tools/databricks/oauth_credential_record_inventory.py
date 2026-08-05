"""Cross-record inventory validation for OAuth credential mutation evidence."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from tools.databricks.oauth_credential_records import (
    DELIVERY_ACK_SUFFIX,
    INTENT_SUFFIX,
    OBSERVED_SUFFIX,
    QUARANTINE_SUFFIX,
    RESOLUTION_SUFFIX,
    SINK_ATTEMPT_SUFFIX,
    delivery_ack_path,
    exact_string_list,
    field,
    observed_path,
    read_json,
    record_paths,
    resolution_path,
    sink_attempt_path,
    validate_delivery_ack,
    validate_intent,
    validate_observed,
    validate_quarantine,
    validate_resolution,
    validate_sink_attempt,
)
from tools.databricks.oauth_credential_resolver_lineage import (
    canonical_resolver_lease_record,
    canonical_resolver_lease_records,
)


def unresolved_record_paths(
    workspace: Any,
    *,
    allowed_intent_path: str = "",
) -> tuple[str, ...]:
    """Return every globally unresolved or malformed mutation record."""

    paths = record_paths(workspace)
    path_set = set(paths)
    unresolved: list[str] = []
    resolver_records: dict[str, dict[str, str | int]] | None = None
    for path in paths:
        record, encoded = read_json(workspace, path)
        if path.endswith(QUARANTINE_SUFFIX):
            linked_intent_path = validate_quarantine(path, record)
            if not linked_intent_path or linked_intent_path not in path_set:
                unresolved.append(path)
                continue
            linked_intent, _linked_encoded = read_json(
                workspace,
                linked_intent_path,
            )
            validate_intent(linked_intent_path, linked_intent)
            if (
                field(record, "principal_id")
                != field(linked_intent, "principal_id")
                or exact_string_list(
                    record.get("before_credential_ids"),
                    label="OAuth credential quarantine prior inventory",
                )
                != exact_string_list(
                    linked_intent.get("before_credential_ids"),
                    label="OAuth credential intent prior inventory",
                )
            ):
                raise RuntimeError(
                    "OAuth credential quarantine intent binding is invalid: "
                    f"{path}"
                )
            if (
                resolution_path(linked_intent_path) not in path_set
                and linked_intent_path != allowed_intent_path
            ):
                unresolved.append(path)
        elif path.endswith(INTENT_SUFFIX):
            validate_intent(path, record)
            observed_record_path = observed_path(path)
            observed_record: dict[str, object] | None = None
            observed_encoded: bytes | None = None
            if observed_record_path in path_set:
                observed_record, observed_encoded = read_json(
                    workspace,
                    observed_record_path,
                )
                validate_observed(
                    observed_record_path,
                    observed_record,
                    intent_record_path=path,
                    intent_encoded=encoded,
                    intent_record=record,
                )
            sink_record_path = sink_attempt_path(path)
            sink_record: dict[str, object] | None = None
            sink_encoded: bytes | None = None
            if sink_record_path in path_set:
                if observed_encoded is None:
                    raise RuntimeError(
                        "OAuth credential sink attempt has no observation: "
                        f"{sink_record_path}"
                    )
                sink_record, sink_encoded = read_json(
                    workspace,
                    sink_record_path,
                )
                validate_sink_attempt(
                    sink_record_path,
                    sink_record,
                    intent_record_path=path,
                    intent_encoded=encoded,
                    intent_record=record,
                    observed_encoded=observed_encoded,
                )
            delivery_ack_record_path = delivery_ack_path(path)
            delivery_ack_record: dict[str, object] | None = None
            delivery_ack_encoded: bytes | None = None
            if delivery_ack_record_path in path_set:
                if (
                    observed_record is None
                    or observed_encoded is None
                    or sink_encoded is None
                ):
                    raise RuntimeError(
                        "OAuth credential delivery acknowledgement has "
                        f"incomplete phases: {delivery_ack_record_path}"
                    )
                delivery_ack_record, delivery_ack_encoded = read_json(
                    workspace,
                    delivery_ack_record_path,
                )
                validate_delivery_ack(
                    delivery_ack_record_path,
                    delivery_ack_record,
                    intent_record_path=path,
                    intent_encoded=encoded,
                    intent_record=record,
                    observed_record=observed_record,
                    observed_encoded=observed_encoded,
                    sink_encoded=sink_encoded,
                )
            resolved_path = resolution_path(path)
            if resolved_path not in path_set:
                if path != allowed_intent_path:
                    unresolved.append(path)
                continue
            resolution, _resolution_encoded = read_json(workspace, resolved_path)
            if resolver_records is None:
                resolver_records = canonical_resolver_lease_records(
                    workspace,
                    app_name=field(resolution, "app_name"),
                )
            validate_resolution(
                resolved_path,
                resolution,
                intent_record_path=path,
                intent_encoded=encoded,
                intent_record=record,
                observed_record=observed_record,
                observed_encoded=observed_encoded,
                sink_record=sink_record,
                sink_encoded=sink_encoded,
                delivery_ack_record=delivery_ack_record,
                delivery_ack_encoded=delivery_ack_encoded,
                canonical_resolver_lease_record=(
                    canonical_resolver_lease_record(
                        workspace,
                        resolution,
                        canonical_records=resolver_records,
                    )
                ),
            )
        elif path.endswith(OBSERVED_SUFFIX):
            matching_intent = (
                f"{path.removesuffix(OBSERVED_SUFFIX)}{INTENT_SUFFIX}"
            )
            if matching_intent not in path_set:
                raise RuntimeError(
                    f"OAuth credential observation has no intent: {path}"
                )
        elif path.endswith(SINK_ATTEMPT_SUFFIX):
            matching_intent = (
                f"{path.removesuffix(SINK_ATTEMPT_SUFFIX)}{INTENT_SUFFIX}"
            )
            if matching_intent not in path_set:
                raise RuntimeError(
                    f"OAuth credential sink attempt has no intent: {path}"
                )
        elif path.endswith(DELIVERY_ACK_SUFFIX):
            matching_intent = (
                f"{path.removesuffix(DELIVERY_ACK_SUFFIX)}{INTENT_SUFFIX}"
            )
            if matching_intent not in path_set:
                raise RuntimeError(
                    "OAuth credential delivery acknowledgement has no intent: "
                    f"{path}"
                )
        elif path.endswith(RESOLUTION_SUFFIX):
            matching_intent = (
                f"{path.removesuffix(RESOLUTION_SUFFIX)}{INTENT_SUFFIX}"
            )
            if matching_intent not in path_set:
                raise RuntimeError(
                    f"OAuth credential resolution has no intent: {path}"
                )
    return tuple(sorted(unresolved))


def sorted_ids(values: Iterable[str]) -> list[str]:
    return sorted(set(values))
