"""The signed journal of one quiesce window (M0-PR8), read by the dead-man (M0-PR9).

The journal lives beside the deployment lease, at
``<LEASE_ROOT>/<app>.quiesce-window.json``, signed with the proof-signing key
like the first-install journal and verified against the same key registry. It
is the only durable statement that the App was stopped on purpose, so it is
written before the stop and closed only after the re-pinned deployment is
proven healthy. A run that dies in between leaves it open; the dead-man
restarts the App only when ``blue_restorable`` is true, and the next governed
run re-pins after the same proofs.

States move one way, each write a compare-and-swap against the exact record
read, under the held lease:

``absent|closed`` -> ``window_open`` (before the stop) -> ``window_started``
(the start's deployment id) -> ``window_proven`` (that deployment is blue) ->
``closed`` (after the re-pin). A failed proof writes ``window_refused`` and the
App is stopped again. A contract window (``blue_restorable`` false) goes
``window_open`` -> ``closed`` when green activation supersedes it.
"""

from __future__ import annotations

import base64
import io
import json
import os
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
from tools.databricks.app_deployment_lease import LEASE_ROOT
from tools.databricks.app_deployment_lease_support import key_registry

JOURNAL_VERSION = 1
ATTESTATION_ALG = "ed25519-app-quiesce-window-v1"
STATES = frozenset({"window_open", "window_started", "window_proven", "window_refused", "closed"})
TRANSITIONS = {
    "absent": frozenset({"window_open"}),
    "closed": frozenset({"window_open"}),
    "window_open": frozenset({"window_started", "closed"}),
    "window_started": frozenset({"window_proven", "window_refused"}),
    "window_proven": frozenset({"closed"}),
    "window_refused": frozenset(),
}
FIELDS = frozenset(
    {
        "version",
        "app_name",
        "state",
        "window_sha256",
        "reasons",
        "blue_deployment_id",
        "restore_source",
        "source_tree_sha256",
        "blue_restorable",
        "lease_id",
        "source_git_sha",
        "start_deployment_id",
        "repinned_deployment_id",
        "refusal",
        "closed_by",
        "updated_at",
    }
)
_SIGNATURE_FIELDS = ("attestation_alg", "attestation_verify_key", "attestation_signature")


def journal_path(app_name: str) -> str:
    normalized = app_name.strip()
    if not normalized or any(
        character not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for character in normalized
    ):
        raise ValueError("quiesce-window journal App name is invalid")
    return f"{LEASE_ROOT}/{normalized}.quiesce-window.json"


def _decode(value: str, *, length: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("quiesce-window journal key is invalid") from exc
    if len(decoded) != length:
        raise RuntimeError("quiesce-window journal key has an invalid length")
    return decoded


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _message(record: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in record.items() if key not in _SIGNATURE_FIELDS}
    return b"mip-app-quiesce-window\0" + json.dumps(
        unsigned, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sign(record: dict[str, Any]) -> dict[str, Any]:
    signing = os.environ.get("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "").strip()
    verify = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    private = Ed25519PrivateKey.from_private_bytes(_decode(signing, length=32))
    derived = _encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    )
    if derived != verify:
        raise RuntimeError("quiesce-window journal signing and verification keys do not match")
    return {
        **record,
        "attestation_alg": ATTESTATION_ALG,
        "attestation_verify_key": verify,
        "attestation_signature": _encode(private.sign(_message(record))),
    }


def _validated(value: object, *, app_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("quiesce-window journal is not an object")
    verify = str(value.get("attestation_verify_key") or "").strip()
    if value.get("attestation_alg") != ATTESTATION_ALG or verify not in key_registry():
        raise RuntimeError("quiesce-window journal attestation identity is invalid")
    try:
        public = Ed25519PublicKey.from_public_bytes(_decode(verify, length=32))
        signature = _decode(str(value.get("attestation_signature") or ""), length=64)
        public.verify(signature, _message(value))
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise RuntimeError("quiesce-window journal signature is invalid") from exc
    unsigned = {key: item for key, item in value.items() if key not in _SIGNATURE_FIELDS}
    if set(unsigned) != FIELDS:
        raise RuntimeError("quiesce-window journal fields are not the reviewed set")
    if (
        unsigned["version"] != JOURNAL_VERSION
        or unsigned["app_name"] != app_name
        or unsigned["state"] not in STATES
        or not isinstance(unsigned["blue_restorable"], bool)
        or not isinstance(unsigned["reasons"], list)
    ):
        raise RuntimeError("quiesce-window journal is not a valid window record")
    return value


def read(workspace: Any, *, app_name: str) -> dict[str, Any] | None:
    """The verified journal, or None when no window was ever journaled."""

    try:
        stream = workspace.workspace.download(journal_path(app_name))
    except (NotFound, ResourceDoesNotExist):
        return None
    try:
        value = json.loads(stream.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("quiesce-window journal is not valid JSON") from exc
    return _validated(value, app_name=app_name)


def state_of(record: dict[str, Any] | None) -> str:
    return "absent" if record is None else str(record["state"])


def write(
    workspace: Any,
    *,
    app_name: str,
    expected: dict[str, Any] | None,
    changes: dict[str, Any],
    assert_lease_held: Any,
) -> dict[str, Any]:
    """Compare-and-swap one transition; return the persisted, verified record."""

    current = read(workspace, app_name=app_name)
    if current != expected:
        raise RuntimeError("quiesce-window journal changed since it was read")
    target = str(changes.get("state") or "")
    if target not in TRANSITIONS[state_of(current)]:
        raise RuntimeError("quiesce-window journal transition is not allowed")
    base: dict[str, Any] = {name: "" for name in FIELDS}
    base.update(version=JOURNAL_VERSION, app_name=app_name, reasons=[], blue_restorable=False)
    if current is not None and target != "window_open":
        base.update({key: current[key] for key in FIELDS})
    base.update(changes)
    base["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    if set(base) != FIELDS:
        raise RuntimeError("quiesce-window journal change names an unreviewed field")
    assert_lease_held()
    signed = _sign(base)
    workspace.workspace.upload(
        journal_path(app_name),
        io.BytesIO(json.dumps(signed, sort_keys=True).encode("utf-8")),
        format=ImportFormat.AUTO,
        overwrite=current is not None,
    )
    persisted = read(workspace, app_name=app_name)
    if persisted != _validated(signed, app_name=app_name):
        raise RuntimeError("quiesce-window journal did not persist exactly")
    return persisted
