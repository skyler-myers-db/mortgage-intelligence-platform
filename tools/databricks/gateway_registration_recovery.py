"""Fail-closed reconciliation for interrupted Gateway model registrations."""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from mlflow.exceptions import MlflowException

from backend.agents.gateway_contract import (
    GATEWAY_MODEL_CANONICAL_TAGS,
    gateway_model_version_tags,
)
from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.agent_runtime_access import assert_runtime_creator

_UC_MODEL_VERSION_TAG_KEY = re.compile(r"[A-Za-z0-9_]{1,256}\Z")
_UC_MODEL_VERSION_TAG_LIMIT = 50
_UC_MODEL_VERSION_TAG_VALUE_LIMIT = 256
_MODEL_VERSION_READY_STATUS = "READY"
_INCOMPLETE_MODEL_VERSION_STATUSES = frozenset({"PENDING_REGISTRATION", "FAILED_REGISTRATION"})
_REGISTRATION_VISIBILITY_ATTEMPTS = 10
_REGISTRATION_VISIBILITY_INTERVAL_S = 0.5
_JOURNAL_VISIBILITY_ATTEMPTS = 10
_JOURNAL_VISIBILITY_INTERVAL_S = 0.5
_EXPERIMENT_TAG_VISIBILITY_ATTEMPTS = 10
_EXPERIMENT_TAG_VISIBILITY_INTERVAL_S = 0.5
_MODEL_VERSION_SEARCH_PAGE_SIZE = 1000
_LOGGED_MODEL_SEARCH_PAGE_SIZE = 1000
_DURABLE_REGISTRATION_JOURNAL_TAG = "mip.gateway_registration_journal_v1"
_DURABLE_REGISTRATION_JOURNAL_SCHEMA = "mip.gateway.registration.v1"
_DURABLE_REGISTRATION_JOURNAL_MAX_BYTES = 5000
_MLFLOW_MISSING_RESOURCE_CODES = frozenset({"NOT_FOUND", "RESOURCE_DOES_NOT_EXIST"})

@dataclass(frozen=True)
class RegistrationCleanupJournal:
    """Immutable identities required to safely unwind a failed registration."""

    model_source: str
    logged_model_id: str
    source_run_id: str
    experiment_id: str


class RegistrationJournalVisibilityError(RuntimeError):
    """The exact log result exists but its authoritative read is still delayed."""

    def __init__(self, message: str, *, journal: RegistrationCleanupJournal) -> None:
        super().__init__(message)
        self.journal = journal


class RegistrationReconciliationPendingError(RuntimeError):
    """An ambiguous registration was preserved for a later exact retry."""


class RegistrationJournalPersistencePendingError(RegistrationReconciliationPendingError):
    """A journal write may have committed; preserve its source for restart."""


@dataclass(frozen=True)
class IncompleteModelVersion:
    version: str
    source: str
    tags: dict[str, str]
    status: str


@dataclass(frozen=True)
class DurableRegistrationJournal:
    model_name: str
    journal: RegistrationCleanupJournal
    registration_tags: dict[str, str]


@dataclass(frozen=True)
class RegistrationRecovery:
    durable: DurableRegistrationJournal
    ready_version: int | None = None
    journal_requires_clear: bool = True


def validated_model_version_tags(tags: dict[str, str]) -> dict[str, str]:
    """Reject tag keys that Unity Catalog model versions cannot persist."""

    if len(tags) > _UC_MODEL_VERSION_TAG_LIMIT or set(tags) != GATEWAY_MODEL_CANONICAL_TAGS:
        raise ValueError("Gateway model version tag set is invalid for Unity Catalog")
    normalized = dict(tags)
    if any(
        not isinstance(key, str)
        or _UC_MODEL_VERSION_TAG_KEY.fullmatch(key) is None
        or not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > _UC_MODEL_VERSION_TAG_VALUE_LIMIT
        for key, value in normalized.items()
    ):
        raise ValueError("Gateway model version tag key or value is invalid for Unity Catalog")
    gateway_model_version_tags(normalized)
    return normalized


def model_version_status(version: Any, *, resource: str) -> str:
    raw_status = getattr(version, "status", None)
    status = (
        str(getattr(raw_status, "name", getattr(raw_status, "value", raw_status)) or "")
        .strip()
        .upper()
    )
    if not status:
        raise RuntimeError(f"{resource} has no authoritative registration status")
    return status


def require_ready_model_version(version: Any, *, resource: str) -> None:
    status = model_version_status(version, resource=resource)
    if status != _MODEL_VERSION_READY_STATUS:
        raise RuntimeError(f"{resource} is not ready ({status})")


def _search_model_versions(
    client: Any,
    *,
    filter_string: str | None = None,
) -> list[Any]:
    """Exhaust every authoritative MLflow model-version search page."""

    versions: list[Any] = []
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        page = client.search_model_versions(
            filter_string=filter_string,
            max_results=_MODEL_VERSION_SEARCH_PAGE_SIZE,
            page_token=page_token,
        )
        versions.extend(page)
        next_token = str(getattr(page, "token", "") or "").strip()
        if not next_token:
            return versions
        if next_token in seen_tokens:
            raise RuntimeError("MLflow model-version search repeated a pagination token")
        seen_tokens.add(next_token)
        page_token = next_token


def _search_logged_models(client: Any, *, experiment_id: str) -> list[Any]:
    """Exhaust logged-model pages before deciding whether a run is unreferenced."""

    logged_models: list[Any] = []
    page_token: str | None = None
    seen_tokens: set[str] = set()
    while True:
        page = client.search_logged_models(
            experiment_ids=[experiment_id],
            max_results=_LOGGED_MODEL_SEARCH_PAGE_SIZE,
            page_token=page_token,
        )
        logged_models.extend(page)
        next_token = str(getattr(page, "token", "") or "").strip()
        if not next_token:
            return logged_models
        if next_token in seen_tokens:
            raise RuntimeError("MLflow logged-model search repeated a pagination token")
        seen_tokens.add(next_token)
        page_token = next_token


def attested_source_versions(
    client: Any,
    *,
    model_name: str,
    source_hash: str,
    supervisor_id: str,
    supervisor_endpoint_id: str,
    upstream_endpoint: str,
    runtime_application_id: str,
    model_family: str,
    experiment_base: str,
    catalog: str,
    genie_space_id: str,
    inference_schema: str,
    inference_table_prefix: str,
    verify_attestation: Callable[..., Any],
) -> tuple[list[int], list[IncompleteModelVersion]]:
    """Classify exact signed versions only after authoritative status validation."""

    versions = _search_model_versions(client, filter_string=f"name='{model_name}'")
    ready: list[int] = []
    incomplete: list[IncompleteModelVersion] = []
    for version in versions:
        version_number = str(getattr(version, "version", "") or "")
        model_source = str(getattr(version, "source", "") or "").strip()
        if not version_number or not model_source:
            raise RuntimeError("attested Gateway model version lacks immutable source metadata")
        tags = {
            str(key): str(value)
            for key, value in dict(getattr(version, "tags", None) or {}).items()
        }
        contract = {
            "full_name": model_name,
            "model_source": model_source,
            "source_hash": source_hash,
            "supervisor_id": supervisor_id,
            "supervisor_endpoint_id": supervisor_endpoint_id,
            "upstream_endpoint": upstream_endpoint,
            "runtime_application_id": runtime_application_id,
            "model_family": model_family,
            "experiment_base": experiment_base,
            "catalog": catalog,
            "genie_space_id": genie_space_id,
            "inference_schema": inference_schema,
            "inference_table_prefix": inference_table_prefix,
        }
        try:
            current_attestation = verify_attestation(tags=tags, **contract)
        except RuntimeError as exc:
            raise RuntimeError(
                f"attested Gateway model version {model_name} v{version_number} drifted"
            ) from exc
        if not current_attestation:
            raise RuntimeError(
                f"Gateway candidate model {model_name} v{version_number} "
                "uses a previous attestation epoch"
            )
        resource = f"Gateway candidate model {model_name} v{version_number}"
        status = model_version_status(version, resource=resource)
        if status == _MODEL_VERSION_READY_STATUS:
            ready.append(int(version_number))
        elif status in _INCOMPLETE_MODEL_VERSION_STATUSES:
            incomplete.append(
                IncompleteModelVersion(
                    version=version_number,
                    source=model_source,
                    tags=tags,
                    status=status,
                )
            )
        else:
            raise RuntimeError(f"{resource} has unsupported registration status ({status})")
    return ready, incomplete


def _logged_model_id(model_source: str) -> str:
    match = re.fullmatch(r"models:/(?P<model_id>m-[A-Za-z0-9][A-Za-z0-9-]*)", model_source)
    if match is None:
        raise RuntimeError("Gateway registration source is not an immutable logged model URI")
    return match.group("model_id")


def _field(value: Any, name: str) -> str:
    return str(getattr(value, name, "") or "").strip()


def _source_run_experiment_id(run: Any) -> str:
    info = getattr(run, "info", None)
    return _field(info, "experiment_id") or _field(run, "experiment_id")


def registration_cleanup_journal(
    client: Any,
    *,
    model_source: str,
    expected_experiment_id: str,
    logged: Any | None = None,
) -> RegistrationCleanupJournal:
    """Capture exact authoritative artifact identities before registration."""

    logged_model_id = _logged_model_id(model_source)
    logged_id = _field(logged, "model_id") if logged is not None else ""
    if logged_id and logged_id != logged_model_id:
        raise RuntimeError("logged Gateway model identity disagrees with its immutable URI")
    logged_run_id = ""
    if logged is not None:
        logged_run_id = _field(logged, "source_run_id") or _field(logged, "run_id")
    authoritative: Any | None = None
    for attempt in range(_JOURNAL_VISIBILITY_ATTEMPTS):
        try:
            authoritative = client.get_logged_model(logged_model_id)
        except Exception as exc:  # noqa: BLE001 - SDKs expose multiple not-found types
            if not _missing_resource(exc):
                raise
        if authoritative is not None:
            break
        if attempt + 1 < _JOURNAL_VISIBILITY_ATTEMPTS:
            time.sleep(_JOURNAL_VISIBILITY_INTERVAL_S)
    if authoritative is None:
        if not logged_run_id:
            raise RuntimeError(
                "newly logged Gateway model is invisible and its log result has no source run"
            )
        journal = RegistrationCleanupJournal(
            model_source=model_source,
            logged_model_id=logged_model_id,
            source_run_id=logged_run_id,
            experiment_id=expected_experiment_id,
        )
        raise RegistrationJournalVisibilityError(
            "newly logged Gateway model did not become authoritatively visible",
            journal=journal,
        )
    if _field(authoritative, "model_id") != logged_model_id:
        raise RuntimeError("MLflow returned an unexpected logged Gateway model identity")
    source_run_id = _field(authoritative, "source_run_id")
    experiment_id = _field(authoritative, "experiment_id")
    if not source_run_id or not experiment_id:
        raise RuntimeError("logged Gateway model lacks authoritative run or experiment identity")
    if experiment_id != expected_experiment_id:
        raise RuntimeError("logged Gateway model experiment identity drifted")
    if logged is not None:
        logged_experiment_id = _field(logged, "experiment_id")
        if logged_run_id and logged_run_id != source_run_id:
            raise RuntimeError("logged Gateway model run identity drifted")
        if logged_experiment_id and logged_experiment_id != experiment_id:
            raise RuntimeError("logged Gateway model experiment identity drifted")
    run: Any | None = None
    for attempt in range(_JOURNAL_VISIBILITY_ATTEMPTS):
        try:
            run = client.get_run(source_run_id)
        except Exception as exc:  # noqa: BLE001 - SDKs expose multiple not-found types
            if not _missing_resource(exc):
                raise
        if run is not None:
            break
        if attempt + 1 < _JOURNAL_VISIBILITY_ATTEMPTS:
            time.sleep(_JOURNAL_VISIBILITY_INTERVAL_S)
    if run is None:
        journal = RegistrationCleanupJournal(
            model_source=model_source,
            logged_model_id=logged_model_id,
            source_run_id=source_run_id,
            experiment_id=experiment_id,
        )
        raise RegistrationJournalVisibilityError(
            "newly logged Gateway source run did not become authoritatively visible",
            journal=journal,
        )
    if _source_run_experiment_id(run) != experiment_id:
        raise RuntimeError("logged Gateway model source run experiment identity drifted")
    return RegistrationCleanupJournal(
        model_source=model_source,
        logged_model_id=logged_model_id,
        source_run_id=source_run_id,
        experiment_id=experiment_id,
    )


def _durable_journal_value(durable: DurableRegistrationJournal) -> str:
    payload = {
        "journal": {
            "experiment_id": durable.journal.experiment_id,
            "logged_model_id": durable.journal.logged_model_id,
            "model_source": durable.journal.model_source,
            "source_run_id": durable.journal.source_run_id,
        },
        "model_name": durable.model_name,
        "registration_tags": durable.registration_tags,
        "schema": _DURABLE_REGISTRATION_JOURNAL_SCHEMA,
    }
    value = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    if len(value.encode("utf-8")) > _DURABLE_REGISTRATION_JOURNAL_MAX_BYTES:
        raise RuntimeError("Gateway registration journal exceeds the MLflow tag size limit")
    return value


def _no_duplicate_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeError("Gateway registration journal contains duplicate JSON keys")
        result[key] = value
    return result


def _parse_durable_journal(value: str) -> DurableRegistrationJournal:
    if not value or len(value.encode("utf-8")) > _DURABLE_REGISTRATION_JOURNAL_MAX_BYTES:
        raise RuntimeError("Gateway registration journal is empty or oversized")
    try:
        payload = json.loads(value, object_pairs_hook=_no_duplicate_json_object)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Gateway registration journal is not strict JSON") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "journal",
        "model_name",
        "registration_tags",
        "schema",
    }:
        raise RuntimeError("Gateway registration journal has an invalid top-level shape")
    if payload["schema"] != _DURABLE_REGISTRATION_JOURNAL_SCHEMA:
        raise RuntimeError("Gateway registration journal schema drifted")
    model_name = payload["model_name"]
    journal_payload = payload["journal"]
    tag_payload = payload["registration_tags"]
    if (
        not isinstance(model_name, str)
        or not model_name.strip()
        or model_name != model_name.strip()
    ):
        raise RuntimeError("Gateway registration journal model name is invalid")
    if not isinstance(journal_payload, dict) or set(journal_payload) != {
        "experiment_id",
        "logged_model_id",
        "model_source",
        "source_run_id",
    }:
        raise RuntimeError("Gateway registration journal identity shape is invalid")
    if any(
        not isinstance(value, str) or not value or value != value.strip()
        for value in journal_payload.values()
    ):
        raise RuntimeError("Gateway registration journal identity value is invalid")
    if not isinstance(tag_payload, dict) or any(
        not isinstance(key, str) or not isinstance(tag, str) for key, tag in tag_payload.items()
    ):
        raise RuntimeError("Gateway registration journal tags are invalid")
    durable = DurableRegistrationJournal(
        model_name=model_name,
        journal=RegistrationCleanupJournal(**journal_payload),
        registration_tags=validated_model_version_tags(tag_payload),
    )
    if value != _durable_journal_value(durable):
        raise RuntimeError("Gateway registration journal is not canonical JSON")
    return durable


def _experiment_tag(client: Any, *, experiment_id: str) -> str | None:
    experiment = client.get_experiment(experiment_id)
    if _field(experiment, "experiment_id") != experiment_id:
        raise RuntimeError("MLflow returned an unexpected Gateway experiment identity")
    tags = dict(getattr(experiment, "tags", None) or {})
    value = tags.get(_DURABLE_REGISTRATION_JOURNAL_TAG)
    if value is None:
        return None
    if not isinstance(value, str):
        raise RuntimeError("Gateway registration journal experiment tag is not text")
    return value


def persist_registration_journal(
    client: Any,
    durable: DurableRegistrationJournal,
) -> None:
    """Persist and authoritatively read back the journal before registration."""

    value = _durable_journal_value(durable)
    last_error: Exception | None = None
    for attempt in range(_EXPERIMENT_TAG_VISIBILITY_ATTEMPTS):
        try:
            current = _experiment_tag(client, experiment_id=durable.journal.experiment_id)
        except Exception as exc:  # noqa: BLE001 - never overwrite an unproved tag
            last_error = exc
        else:
            if current == value:
                return
            if current is not None:
                raise RegistrationJournalPersistencePendingError(
                    "Gateway registration journal conflicts with another durable write"
                )
            try:
                client.set_experiment_tag(
                    durable.journal.experiment_id,
                    _DURABLE_REGISTRATION_JOURNAL_TAG,
                    value,
                )
            except Exception as exc:  # noqa: BLE001 - response may be lost after commit
                last_error = exc
            try:
                current = _experiment_tag(
                    client,
                    experiment_id=durable.journal.experiment_id,
                )
            except Exception as exc:  # noqa: BLE001 - readback may be temporarily unavailable
                last_error = exc
            else:
                if current == value:
                    return
                if current is not None:
                    raise RegistrationJournalPersistencePendingError(
                        "Gateway registration journal read back a conflicting durable write"
                    )
        if attempt + 1 < _EXPERIMENT_TAG_VISIBILITY_ATTEMPTS:
            time.sleep(_EXPERIMENT_TAG_VISIBILITY_INTERVAL_S)
    pending = RegistrationJournalPersistencePendingError(
        "Gateway registration journal readback was ambiguous; preserving source"
    )
    if last_error is not None:
        raise pending from last_error
    raise pending


def clear_registration_journal(
    client: Any,
    durable: DurableRegistrationJournal,
    *,
    allow_absent: bool = False,
) -> None:
    """Delete only the exact journal and prove the durable tag is absent."""

    current = _experiment_tag(client, experiment_id=durable.journal.experiment_id)
    if current is None and allow_absent:
        return
    if current != _durable_journal_value(durable):
        raise RuntimeError("Gateway registration journal drifted before deletion")
    client.delete_experiment_tag(
        durable.journal.experiment_id,
        _DURABLE_REGISTRATION_JOURNAL_TAG,
    )
    absent_reads = 0
    for attempt in range(_EXPERIMENT_TAG_VISIBILITY_ATTEMPTS):
        if _experiment_tag(client, experiment_id=durable.journal.experiment_id) is None:
            absent_reads += 1
            if absent_reads == 2:
                return
        else:
            absent_reads = 0
        if attempt + 1 < _EXPERIMENT_TAG_VISIBILITY_ATTEMPTS:
            time.sleep(_EXPERIMENT_TAG_VISIBILITY_INTERVAL_S)
    raise RuntimeError("Gateway registration journal deletion was not authoritative")


def load_registration_journal(
    client: Any,
    *,
    model_name: str,
    experiment_id: str,
    attestation_contract: dict[str, str],
    verify_attestation: Callable[..., Any],
) -> DurableRegistrationJournal | None:
    """Strictly validate the durable journal and its current signed contract."""

    value = None
    for attempt in range(_EXPERIMENT_TAG_VISIBILITY_ATTEMPTS):
        value = _experiment_tag(client, experiment_id=experiment_id)
        if value is not None:
            break
        if attempt + 1 < _EXPERIMENT_TAG_VISIBILITY_ATTEMPTS:
            time.sleep(_EXPERIMENT_TAG_VISIBILITY_INTERVAL_S)
    if value is None:
        return None
    durable = _parse_durable_journal(value)
    if durable.model_name != model_name or durable.journal.experiment_id != experiment_id:
        raise RuntimeError("Gateway registration journal target identity drifted")
    if not verify_attestation(
        tags=durable.registration_tags,
        full_name=model_name,
        model_source=durable.journal.model_source,
        **attestation_contract,
    ):
        raise RuntimeError("Gateway registration journal uses a previous attestation epoch")
    authoritative = registration_cleanup_journal(
        client,
        model_source=durable.journal.model_source,
        expected_experiment_id=experiment_id,
    )
    if authoritative != durable.journal:
        raise RuntimeError("Gateway registration journal artifact identity drifted")
    return durable


def _exact_cleanup_candidates(
    versions: list[Any],
    *,
    model_name: str,
    journal: RegistrationCleanupJournal,
    registration_tags: dict[str, str],
) -> tuple[list[IncompleteModelVersion], list[int]]:
    candidates: list[IncompleteModelVersion] = []
    ready_versions: list[int] = []
    for version in versions:
        source = _field(version, "source")
        tags = {
            str(key): str(value)
            for key, value in dict(getattr(version, "tags", None) or {}).items()
        }
        if source != journal.model_source:
            continue
        if tags != registration_tags:
            raise RuntimeError(
                f"Gateway cleanup candidate {model_name} uses the exact journaled source "
                "with drifted registration tags"
            )
        version_number = _field(version, "version")
        if not version_number:
            raise RuntimeError("exact Gateway cleanup candidate has no immutable version number")
        resource = f"Gateway cleanup candidate {model_name} v{version_number}"
        status = model_version_status(version, resource=resource)
        if status == _MODEL_VERSION_READY_STATUS:
            ready_versions.append(int(version_number))
        elif status in _INCOMPLETE_MODEL_VERSION_STATUSES:
            candidates.append(
                IncompleteModelVersion(
                    version=version_number,
                    source=source,
                    tags=tags,
                    status=status,
                )
            )
        else:
            raise RuntimeError(f"{resource} has unsupported registration status ({status})")
    return candidates, ready_versions


def _target_model_versions(client: Any, model_name: str) -> list[Any]:
    return _search_model_versions(client, filter_string=f"name='{model_name}'")


def _logged_model_run_references(
    client: Any,
    journal: RegistrationCleanupJournal,
) -> list[str]:
    references: list[str] = []
    for logged_model in _search_logged_models(
        client,
        experiment_id=journal.experiment_id,
    ):
        if _field(logged_model, "source_run_id") != journal.source_run_id:
            continue
        references.append(_field(logged_model, "model_id") or "UNKNOWN_LOGGED_MODEL")
    return references


def compensate_unregistered_logged_model(
    client: Any,
    journal: RegistrationCleanupJournal,
) -> None:
    """Remove a fresh log that failed authoritative journaling before registration."""

    failures: list[str] = []
    try:
        version_references = _source_or_run_references(client, journal)
    except Exception as exc:  # noqa: BLE001 - preserve on inconclusive search
        failures.append(f"search pre-registration model-version references: {exc}")
        version_references = ["INCONCLUSIVE"]
    if version_references:
        detail = ", ".join(version_references)
        raise RuntimeError(
            "pre-registration logged model unexpectedly has registered references: " + detail
        )

    logged_deleted = False
    try:
        client.delete_logged_model(journal.logged_model_id)
    except Exception as exc:  # noqa: BLE001 - delayed visibility must be explicit
        failures.append(f"delete unjournaled logged model: {exc}")
    else:
        logged_deleted = True

    if logged_deleted:
        try:
            logged_references = _logged_model_run_references(client, journal)
        except Exception as exc:  # noqa: BLE001 - never delete run after inconclusive search
            failures.append(f"search unjournaled run references: {exc}")
            logged_references = ["INCONCLUSIVE"]
        if not logged_references:
            try:
                client.delete_run(journal.source_run_id)
            except Exception as exc:  # noqa: BLE001 - report any eventual-consistency leak
                failures.append(f"delete unjournaled source run: {exc}")

    if failures:
        raise RuntimeError(
            "pre-registration Gateway log cleanup did not converge: " + "; ".join(failures)
        )


def _source_or_run_references(
    client: Any,
    journal: RegistrationCleanupJournal,
) -> list[str]:
    """Search only exact immutable source/run references across model names."""

    if re.fullmatch(r"models:/m-[A-Za-z0-9][A-Za-z0-9-]*", journal.model_source) is None:
        raise RuntimeError("journaled model source is unsafe for an exact MLflow filter")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*", journal.source_run_id) is None:
        raise RuntimeError("journaled source run is unsafe for an exact MLflow filter")
    candidates = [
        *_search_model_versions(
            client,
            filter_string=f"source_path='{journal.model_source}'",
        ),
        *_search_model_versions(
            client,
            filter_string=f"run_id='{journal.source_run_id}'",
        ),
    ]
    references: dict[tuple[str, str, str, str], str] = {}
    for version in candidates:
        source = _field(version, "source")
        run_id = _field(version, "run_id")
        if source != journal.model_source and run_id != journal.source_run_id:
            continue
        name = _field(version, "name") or "UNKNOWN_MODEL"
        number = _field(version, "version") or "UNKNOWN_VERSION"
        references[(name, number, source, run_id)] = f"{name} v{number}"
    return list(references.values())


def _missing_resource(exc: Exception) -> bool:
    if isinstance(exc, NotFound | ResourceDoesNotExist):
        return True
    if not isinstance(exc, MlflowException):
        return False
    error_code = str(getattr(exc, "error_code", "") or "").strip().upper()
    return error_code in _MLFLOW_MISSING_RESOURCE_CODES


def require_no_unjournaled_gateway_sources(
    client: Any,
    *,
    experiment_id: str,
    expected_logged_model_name: str,
) -> None:
    """Quarantine any unjournaled source; experiment location is not authorship."""
    candidates: dict[str, RegistrationCleanupJournal] = {}
    for logged in _search_logged_models(client, experiment_id=experiment_id):
        logged_name = _field(logged, "name")
        if logged_name and logged_name != expected_logged_model_name:
            raise RuntimeError("Gateway experiment contains an unexpected logged model")
        logged_model_id = _field(logged, "model_id")
        if not logged_model_id:
            raise RuntimeError("Gateway experiment contains a logged model without identity")
        journal = registration_cleanup_journal(
            client,
            model_source=f"models:/{logged_model_id}",
            expected_experiment_id=experiment_id,
            logged=logged,
        )
        if not _source_or_run_references(client, journal):
            candidates[logged_model_id] = journal
    if candidates:
        raise RuntimeError(
            "Gateway experiment has unjournaled sources; operator quarantine required: "
            + ",".join(sorted(candidates))
        )


def compensate_failed_model_registration(
    client: Any,
    workspace: Any,
    *,
    model_name: str,
    journal: RegistrationCleanupJournal,
    registration_tags: dict[str, str],
    expected_creator_application_id: str,
) -> int | None:
    """Delete only proven incomplete versions; preserve every container and artifact."""

    validated_model_version_tags(registration_tags)
    failures: list[str] = []
    candidates: list[IncompleteModelVersion] = []
    ready_versions: list[int] = []
    for attempt in range(_REGISTRATION_VISIBILITY_ATTEMPTS):
        candidates, ready_versions = _exact_cleanup_candidates(
            _target_model_versions(client, model_name),
            model_name=model_name,
            journal=journal,
            registration_tags=registration_tags,
        )
        if candidates or ready_versions:
            break
        if attempt + 1 < _REGISTRATION_VISIBILITY_ATTEMPTS:
            time.sleep(_REGISTRATION_VISIBILITY_INTERVAL_S)

    if ready_versions and not candidates:
        return max(ready_versions)
    if not candidates:
        raise RegistrationReconciliationPendingError(
            "Gateway registration cleanup found no authoritative incomplete "
            "version; preserving the durable journal and source for reconciliation"
        )

    model_owned = False
    try:
        model_details = workspace.registered_models.get(model_name)
    except Exception as exc:  # noqa: BLE001 - SDKs expose multiple not-found types
        failures.append(f"read registered model owner: {exc}")
    else:
        try:
            assert_runtime_creator(
                getattr(model_details, "owner", None),
                application_id=expected_creator_application_id,
                resource=f"registered model {model_name}",
            )
        except Exception as exc:  # noqa: BLE001 - aggregate before safe cleanup
            failures.append(f"prove registered model owner: {exc}")
        else:
            model_owned = True

    if not model_owned:
        detail = "; ".join(failures) or "owner proof was inconclusive"
        raise RuntimeError(
            "Gateway registration cleanup could not prove runtime ownership: " + detail
        )

    for candidate in candidates:
        try:
            client.delete_model_version(model_name, candidate.version)
        except Exception as exc:  # noqa: BLE001 - attempt every safe deletion
            failures.append(f"delete model version {candidate.version}: {exc}")

    if failures:
        raise RuntimeError("Gateway registration cleanup did not converge: " + "; ".join(failures))
    remaining, ready_versions = _exact_cleanup_candidates(
        _target_model_versions(client, model_name),
        model_name=model_name,
        journal=journal,
        registration_tags=registration_tags,
    )
    if remaining:
        raise RuntimeError("Gateway registration cleanup left an incomplete exact version")
    return max(ready_versions) if ready_versions else None


def reconcile_incomplete_source_versions(
    client: Any,
    workspace: Any,
    *,
    model_name: str,
    experiment_id: str,
    expected_creator_application_id: str,
    source_hash: str,
    supervisor_id: str,
    supervisor_endpoint_id: str,
    upstream_endpoint: str,
    runtime_application_id: str,
    model_family: str,
    experiment_base: str,
    catalog: str,
    genie_space_id: str,
    inference_schema: str,
    inference_table_prefix: str,
    verify_attestation: Callable[..., Any],
) -> RegistrationRecovery | None:
    """Recover the durable source, deleting only proven incomplete exact versions."""

    contract = {
        "source_hash": source_hash,
        "supervisor_id": supervisor_id,
        "supervisor_endpoint_id": supervisor_endpoint_id,
        "upstream_endpoint": upstream_endpoint,
        "runtime_application_id": runtime_application_id,
        "model_family": model_family,
        "experiment_base": experiment_base,
        "catalog": catalog,
        "genie_space_id": genie_space_id,
        "inference_schema": inference_schema,
        "inference_table_prefix": inference_table_prefix,
    }
    durable = load_registration_journal(
        client,
        model_name=model_name,
        experiment_id=experiment_id,
        attestation_contract=contract,
        verify_attestation=verify_attestation,
    )
    if durable is None:
        _ready, incomplete = attested_source_versions(
            client,
            model_name=model_name,
            **contract,
            verify_attestation=verify_attestation,
        )
        if incomplete:
            raise RuntimeError(
                "incomplete Gateway model version has no durable registration journal"
            )
        return None

    versions = _target_model_versions(client, model_name)
    if any(_field(version, "source") != durable.journal.model_source for version in versions):
        raise RuntimeError("Gateway registration journal conflicts with another model source")
    candidates, ready_versions = _exact_cleanup_candidates(
        versions,
        model_name=model_name,
        journal=durable.journal,
        registration_tags=durable.registration_tags,
    )
    if candidates:
        recovered_ready = compensate_failed_model_registration(
            client,
            workspace,
            model_name=model_name,
            journal=durable.journal,
            registration_tags=durable.registration_tags,
            expected_creator_application_id=expected_creator_application_id,
        )
        ready_versions = [recovered_ready] if recovered_ready is not None else []
    if ready_versions:
        ready_version = max(ready_versions)
        clear_registration_journal(client, durable)
        return RegistrationRecovery(
            durable=durable,
            ready_version=ready_version,
            journal_requires_clear=False,
        )
    return RegistrationRecovery(durable=durable)
