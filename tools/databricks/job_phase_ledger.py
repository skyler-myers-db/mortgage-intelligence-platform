"""The signed ledger of the governed data phase's job runs (M1-PR6), read and written under the lease.

The ledger lives beside the deployment lease, at
``<LEASE_ROOT>/<app>.data-phase-ledger.json``, signed with the proof-signing
key like the quiesce-window journal (whose key decoding it reuses) and
verified against the same key registry. Every write is a compare-and-swap
against the exact record read, under the held lease. It is the durable
statement of which data job ran under which definition, so a killed data phase
re-attaches instead of starting a second run.

One **generation** is one data phase until it completes. It goes ``open`` ->
``closed`` once every planned job succeeded and its grants were reconciled and
verified, or ``open`` -> ``superseded`` when an operator acknowledges, by the
exact digest of its purpose's plan, that a stale generation (or one that must
run again) is retired. Only a
``closed`` or ``superseded`` generation (or none) is followed by a new one.

Each job entry moves ``submitted`` (the idempotency token is journaled before
the run-now) -> ``running`` (the run id) -> ``succeeded`` or ``failed``. A
``failed`` entry is rerun only as the next attempt, with a new token; a
``succeeded`` one is never run again in its generation.
"""

from __future__ import annotations

import io
import json
import os
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from databricks.sdk.service.workspace import ImportFormat
from tools.databricks import quiesce_window_journal as signed_journal
from tools.databricks.app_deployment_lease import LEASE_ROOT
from tools.databricks.app_deployment_lease_support import key_registry

LEDGER_VERSION = 1
ATTESTATION_ALG = "ed25519-data-phase-ledger-v1"
STATES = frozenset({"open", "closed", "superseded"})
TRANSITIONS = {
    "absent": frozenset({"open"}),
    "closed": frozenset({"open"}),
    "superseded": frozenset({"open"}),
    "open": frozenset({"open", "closed", "superseded"}),
}
ENTRY_STATUSES = frozenset({"submitted", "running", "succeeded", "failed"})
FIELDS = frozenset(
    {
        "version",
        "app_name",
        "client_key",
        "generation",
        "state",
        "opened_source_git_sha",
        "jobs",
        "lease_id",
        "source_git_sha",
        "updated_at",
    }
)
ENTRY_FIELDS = frozenset({"job_id", "definition_sha256", "attempt", "token", "run_id", "status", "result"})
_SIGNATURE_FIELDS = ("attestation_alg", "attestation_verify_key", "attestation_signature")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[a-z0-9-]{1,64}\Z")


def ledger_path(app_name: str) -> str:
    normalized = app_name.strip()
    if not normalized or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in normalized
    ):
        raise ValueError("data-phase ledger App name is invalid")
    return f"{LEASE_ROOT}/{normalized}.data-phase-ledger.json"


def _message(record: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in record.items() if key not in _SIGNATURE_FIELDS}
    return b"mip-data-phase-ledger\0" + json.dumps(
        unsigned, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sign(record: dict[str, Any]) -> dict[str, Any]:
    signing = os.environ.get("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "").strip()
    verify = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    private = Ed25519PrivateKey.from_private_bytes(signed_journal._decode(signing, length=32))
    derived = signed_journal._encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    if derived != verify:
        raise RuntimeError("data-phase ledger signing and verification keys do not match")
    return {
        **record,
        "attestation_alg": ATTESTATION_ALG,
        "attestation_verify_key": verify,
        "attestation_signature": signed_journal._encode(private.sign(_message(record))),
    }


def _validated_entry(key: str, entry: object) -> None:
    if not isinstance(entry, dict) or set(entry) != ENTRY_FIELDS:
        raise RuntimeError(f"data-phase ledger entry {key} is not the reviewed shape")
    attempt = entry["attempt"]
    if (
        entry["status"] not in ENTRY_STATUSES
        or not isinstance(attempt, int)
        or isinstance(attempt, bool)
        or attempt < 1
        or _DIGEST.fullmatch(str(entry["definition_sha256"])) is None
        or _TOKEN.fullmatch(str(entry["token"])) is None
        or not str(entry["job_id"]).isdigit()
        or (entry["status"] != "submitted" and not str(entry["run_id"]).isdigit())
    ):
        raise RuntimeError(f"data-phase ledger entry {key} is not a valid job run")


def _validated(value: object, *, app_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("data-phase ledger is not an object")
    verify = str(value.get("attestation_verify_key") or "").strip()
    if value.get("attestation_alg") != ATTESTATION_ALG or verify not in key_registry():
        raise RuntimeError("data-phase ledger attestation identity is invalid")
    try:
        public = Ed25519PublicKey.from_public_bytes(signed_journal._decode(verify, length=32))
        signature = signed_journal._decode(str(value.get("attestation_signature") or ""), length=64)
        public.verify(signature, _message(value))
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise RuntimeError("data-phase ledger signature is invalid") from exc
    unsigned = {key: item for key, item in value.items() if key not in _SIGNATURE_FIELDS}
    if set(unsigned) != FIELDS:
        raise RuntimeError("data-phase ledger fields are not the reviewed set")
    if (
        unsigned["version"] != LEDGER_VERSION
        or unsigned["app_name"] != app_name
        or unsigned["state"] not in STATES
        or not isinstance(unsigned["jobs"], dict)
    ):
        raise RuntimeError("data-phase ledger is not a valid ledger record")
    for key, entry in unsigned["jobs"].items():
        _validated_entry(str(key), entry)
    return value


def read(workspace: Any, *, app_name: str) -> dict[str, Any] | None:
    """The verified ledger, or None when no data phase ever ran."""

    try:
        stream = workspace.workspace.download(ledger_path(app_name))
    except (NotFound, ResourceDoesNotExist):
        return None
    try:
        value = json.loads(stream.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("data-phase ledger is not valid JSON") from exc
    return _validated(value, app_name=app_name)


def state_of(record: dict[str, Any] | None) -> str:
    return "absent" if record is None else str(record["state"])


def _assert_entry_moves(current: dict[str, Any] | None, target: dict[str, Any]) -> None:
    """Within one generation an entry only moves forward; a succeeded run is final."""

    if current is None or current["generation"] != target["generation"]:
        return
    for key, before in current["jobs"].items():
        after = target["jobs"].get(key)
        if after is None:
            raise RuntimeError(f"data-phase ledger write drops the entry {key}")
        if before["status"] == "succeeded" and after != before:
            raise RuntimeError(f"data-phase ledger write rewrites the succeeded entry {key}")
        if after["attempt"] < before["attempt"]:
            raise RuntimeError(f"data-phase ledger write rewinds the attempt of {key}")
        if after["attempt"] == before["attempt"] and after["token"] != before["token"]:
            raise RuntimeError(f"data-phase ledger write changes the token of {key} within an attempt")


def new_generation(*, app_name: str, client_key: str, source_git_sha: str) -> dict[str, Any]:
    return {
        "version": LEDGER_VERSION,
        "app_name": app_name,
        "client_key": client_key,
        "generation": str(uuid.uuid4()),
        "state": "open",
        "opened_source_git_sha": source_git_sha,
        "jobs": {},
        "lease_id": "",
        "source_git_sha": source_git_sha,
        "updated_at": "",
    }


def write(
    workspace: Any,
    *,
    app_name: str,
    expected: dict[str, Any] | None,
    record: dict[str, Any],
    lease_id: str,
    source_git_sha: str,
    assert_lease_held: Any,
) -> dict[str, Any]:
    """Compare-and-swap ``record`` over ``expected``; return the persisted, verified ledger."""

    current = read(workspace, app_name=app_name)
    if current != expected:
        raise RuntimeError("data-phase ledger changed since it was read")
    unsigned = {key: value for key, value in record.items() if key not in _SIGNATURE_FIELDS}
    target = str(unsigned.get("state") or "")
    if target not in TRANSITIONS[state_of(current)]:
        raise RuntimeError("data-phase ledger transition is not allowed")
    if current is not None and current["state"] == "open" and unsigned["generation"] != current["generation"]:
        raise RuntimeError("an open data-phase generation is closed or superseded, never replaced")
    if (current is None or current["state"] != "open") and unsigned["jobs"]:
        raise RuntimeError("a new data-phase generation starts with no job entry")
    unsigned = {
        **unsigned,
        "lease_id": lease_id,
        "source_git_sha": source_git_sha,
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if set(unsigned) != FIELDS:
        raise RuntimeError("data-phase ledger change names an unreviewed field")
    for key, entry in unsigned["jobs"].items():
        _validated_entry(str(key), entry)
    _assert_entry_moves(current, unsigned)
    assert_lease_held()
    signed = _sign(unsigned)
    workspace.workspace.upload(
        ledger_path(app_name),
        io.BytesIO(json.dumps(signed, sort_keys=True).encode("utf-8")),
        format=ImportFormat.AUTO,
        overwrite=current is not None,
    )
    persisted = read(workspace, app_name=app_name)
    if persisted != _validated(signed, app_name=app_name):
        raise RuntimeError("data-phase ledger did not persist exactly")
    return persisted
