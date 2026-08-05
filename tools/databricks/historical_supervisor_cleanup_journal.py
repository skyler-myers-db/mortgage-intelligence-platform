"""Durable App-bound proof for interrupted managed-Supervisor cleanup."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Sequence
from dataclasses import asdict
from typing import Any
from uuid import UUID

from tools.databricks.app_rollback_secret_scope import (
    assert_owned_app_rollback_scope,
    historical_supervisor_cleanup_journal_key,
)
from tools.databricks.historical_agent_endpoint_types import (
    ReviewedSupervisor,
    SupervisorCleanupProof,
    SupervisorPin,
)

_VERSION = 1
_FIELDS = frozenset(asdict(
    SupervisorCleanupProof("", "", "", "", "", "", "", "")
))


def _field(value: object, name: str) -> str:
    raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return str(getattr(raw, "value", raw) or "").strip()


def _canonical(proof: SupervisorCleanupProof) -> str:
    return json.dumps(
        asdict(proof),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _validate(
    value: object,
    *,
    app_name: str,
    lease_id: str | None,
    source_git_sha: str | None,
    runtime_application_id: str,
) -> SupervisorCleanupProof:
    if not isinstance(value, dict) or set(value) != _FIELDS:
        raise RuntimeError("historical Supervisor cleanup journal is malformed")
    if (
        value.get("version") != _VERSION
        or isinstance(value.get("version"), bool)
        or any(
            not isinstance(value.get(field), str)
            or not str(value[field])
            or str(value[field]) != str(value[field]).strip()
            for field in _FIELDS - {"version"}
        )
    ):
        raise RuntimeError("historical Supervisor cleanup journal is malformed")
    try:
        proof = SupervisorCleanupProof(**value)
    except TypeError as exc:  # pragma: no cover - exact field set closes this path
        raise RuntimeError("historical Supervisor cleanup journal is malformed") from exc
    try:
        UUID(proof.lease_id)
    except ValueError as exc:
        raise RuntimeError("historical Supervisor cleanup journal is malformed") from exc
    if (
        proof.app_name != app_name
        or (lease_id is not None and proof.lease_id != lease_id)
        or (source_git_sha is not None and proof.source_git_sha != source_git_sha)
        or proof.runtime_application_id != runtime_application_id
        or len(proof.source_git_sha) != 40
        or any(character not in "0123456789abcdef" for character in proof.source_git_sha)
        or _canonical(proof)
        != json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
    ):
        raise RuntimeError(
            "historical Supervisor cleanup journal belongs to a different deployment"
        )
    return proof


def validate_pending_cleanup_inventory(
    proof: SupervisorCleanupProof,
    *,
    runtime_application_id: str,
    supervisor_pins: Sequence[SupervisorPin],
    observed_supervisors: list[SupervisorPin],
    endpoint_details: dict[str, Any],
) -> None:
    """Reject preservation conflicts and every live immutable-tuple reuse."""

    if (
        proof.runtime_application_id != runtime_application_id
        or proof.creator != runtime_application_id
        or any(
            not isinstance(value, str) or not value or value != value.strip()
            for key, value in asdict(proof).items()
            if key != "version"
        )
        or proof.version != _VERSION
    ):
        raise RuntimeError("historical Supervisor cleanup proof is incomplete")
    expected = SupervisorPin(
        proof.supervisor_id,
        proof.endpoint,
        proof.endpoint_id,
        proof.creator,
    )
    if any(
        pin.supervisor_id == proof.supervisor_id
        or pin.endpoint == proof.endpoint
        or pin.endpoint_id == proof.endpoint_id
        for pin in supervisor_pins
    ):
        raise RuntimeError(
            "historical Supervisor cleanup proof conflicts with a preserved tuple"
        )
    conflicts = [
        pin
        for pin in observed_supervisors
        if (
            pin.supervisor_id == proof.supervisor_id
            or pin.endpoint == proof.endpoint
            or pin.endpoint_id == proof.endpoint_id
        )
    ]
    if conflicts and (len(conflicts) != 1 or conflicts[0] != expected):
        raise RuntimeError("historical Supervisor cleanup tuple was reused or drifted")
    endpoint = endpoint_details.get(proof.endpoint)
    if endpoint is not None and (
        _field(endpoint, "id"),
        _field(endpoint, "creator"),
    ) != (proof.endpoint_id, proof.creator):
        raise RuntimeError("historical Supervisor cleanup endpoint was reused or drifted")
    if any(
        _field(details, "id") == proof.endpoint_id and name != proof.endpoint
        for name, details in endpoint_details.items()
    ):
        raise RuntimeError("historical Supervisor cleanup endpoint ID was reused")


class HistoricalSupervisorCleanupJournal:
    """Single-slot journal protected by the deterministic rollback scope."""

    def __init__(
        self,
        workspace: Any,
        *,
        app_name: str,
        scope: str,
        lease_id: str,
        source_git_sha: str,
        runtime_application_id: str,
        assert_single_writer: Callable[[], None],
    ) -> None:
        self._workspace = workspace
        self._app_name = app_name.strip()
        self._scope = scope.strip()
        self._lease_id = lease_id.strip()
        self._source_git_sha = source_git_sha.strip()
        self._runtime_application_id = runtime_application_id.strip()
        self._assert_single_writer = assert_single_writer
        self._key = historical_supervisor_cleanup_journal_key(self._app_name)

    def _assert_scope(self) -> None:
        assert_owned_app_rollback_scope(
            self._workspace,
            app_name=self._app_name,
            scope=self._scope,
        )

    def proof_for(
        self,
        supervisor: ReviewedSupervisor,
        *,
        runtime_application_id: str,
    ) -> SupervisorCleanupProof:
        if runtime_application_id.strip() != self._runtime_application_id:
            raise RuntimeError("historical cleanup inventory runtime identity drifted")
        return SupervisorCleanupProof(
            app_name=self._app_name,
            lease_id=self._lease_id,
            source_git_sha=self._source_git_sha,
            runtime_application_id=self._runtime_application_id,
            supervisor_id=supervisor.supervisor_id,
            endpoint=supervisor.endpoint,
            endpoint_id=supervisor.endpoint_id,
            creator=supervisor.creator,
        )

    def read(self) -> SupervisorCleanupProof | None:
        self._assert_scope()
        keys = [
            _field(item, "key")
            for item in self._workspace.secrets.list_secrets(scope=self._scope)
        ]
        if any(not key for key in keys) or len(keys) != len(set(keys)):
            raise RuntimeError("App rollback secret-key inventory is malformed")
        if self._key not in keys:
            return None
        encoded = _field(
            self._workspace.secrets.get_secret(self._scope, self._key),
            "value",
        )
        try:
            raw = base64.b64decode(encoded, validate=True).decode("utf-8")
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "historical Supervisor cleanup journal is malformed"
            ) from exc
        return _validate(
            value,
            app_name=self._app_name,
            lease_id=None,
            source_git_sha=None,
            runtime_application_id=self._runtime_application_id,
        )

    def stage(self, proof: SupervisorCleanupProof) -> None:
        """Persist one exact tuple before its Supervisor deletion can begin."""

        self._assert_single_writer()
        existing = self.read()
        if existing is not None:
            if existing != proof:
                raise RuntimeError(
                    "historical Supervisor cleanup journal already pins another tuple"
                )
            return
        expected = _validate(
            asdict(proof),
            app_name=self._app_name,
            lease_id=self._lease_id,
            source_git_sha=self._source_git_sha,
            runtime_application_id=self._runtime_application_id,
        )
        self._assert_single_writer()
        try:
            self._workspace.secrets.put_secret(
                scope=self._scope,
                key=self._key,
                string_value=_canonical(expected),
            )
        except Exception as write_error:  # noqa: BLE001 - resolve ambiguous commit
            try:
                persisted = self.read()
            except Exception as read_error:  # noqa: BLE001
                raise RuntimeError(
                    "historical Supervisor cleanup journal write state is unknown"
                ) from read_error
            if persisted != expected:
                raise RuntimeError(
                    "historical Supervisor cleanup journal did not persist exactly"
                ) from write_error
        self._assert_single_writer()
        if self.read() != expected:
            raise RuntimeError(
                "historical Supervisor cleanup journal did not persist exactly"
            )

    def clear(
        self,
        proof: SupervisorCleanupProof,
        *,
        assert_resources_absent: Callable[[], None],
    ) -> None:
        """Clear only the exact journal entry after both resources are absent."""

        if self.read() != proof:
            raise RuntimeError("historical Supervisor cleanup journal changed before clear")
        self._assert_single_writer()
        if self.read() != proof:
            raise RuntimeError("historical Supervisor cleanup journal changed at clear boundary")
        assert_resources_absent()
        try:
            self._workspace.secrets.delete_secret(self._scope, self._key)
        except Exception as delete_error:  # noqa: BLE001 - resolve ambiguous commit
            if self.read() is not None:
                raise RuntimeError(
                    "historical Supervisor cleanup journal deletion did not converge"
                ) from delete_error
            return
        if self.read() is not None:
            raise RuntimeError(
                "historical Supervisor cleanup journal deletion did not converge"
            )
