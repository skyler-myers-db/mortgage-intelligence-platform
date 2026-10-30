"""The governed Genie space, resolved by a pinned id, never by its title alone (attack G12, M1-PR3).

Anyone who can create a Genie space can give it the governed title. Under the
live-first doctrine whoever controls the space controls the answers, so a
title match the deploy did not pin is never adopted.

**Pins.** Two signed sources name the governed space by id:

* the App's signed last-good rollback record (its ``genie_space_id``), written
  at every green capture;
* the pin record, ``genie-space-pin-v1-<app>`` in the App rollback secret scope
  (``tools/databricks/app_rollback_secret_scope.py`` proves the scope this
  deployer's own and admits this key among its reviewed ones), signed Ed25519 with the deploy's
  ``MIP_AI_GATEWAY_PROOF_SIGNING_KEY`` like the rollback record and verified
  against ``MIP_AI_GATEWAY_PROOF_VERIFY_KEY`` (or the previous key). The
  provisioner writes it the moment it creates the space (``created``), when it
  adopts a title match by an exact digest ack (``acked``), and when only the
  rollback record names the space (``record``), so a run interrupted before
  its capture still finds the space it created.

When both exist they must agree. A pinned space must still exist: one that is
gone refuses and never falls back to a title.

**No pin.** The title decides only between a first create and a refusal:

* no space has the title: the provisioner creates it and pins the id;
* one space has it: refused, unless ``--ack-plan`` equals the sha256 of the
  canonical pin plan for that space. The owner-attestation layer is
  deferred, so an exact digest ack replaces it: the read-only plan prints the
  candidate space id, the principals that hold a direct CAN_MANAGE on it (the
  public Genie API reports no creator; the creator holds CAN_MANAGE from
  creation, so it is among them), the deploying identity and the digest. The
  provisioner recomputes the plan immediately before it writes and refuses,
  with nothing changed, when the digest differs;
* more than one: refused as ambiguous.

Every read here is a GET or a list. The only writes are the pin record's,
which the provisioner makes under the deploy lease. Refusal messages name the
rule, never an id; identifiers that reach a plan print are registered as
runner masks first.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.clients.redact import emit_mask

PIN_VERSION = 1
PIN_KEY_PREFIX = "genie-space-pin-v1"
PIN_ALGORITHM = "ed25519-genie-space-pin-v1"
PIN_DOMAIN = b"mip-genie-space-pin\0"
PROVENANCES = frozenset({"created", "acked", "record"})
PLAN_VERSION = 1
_ABSENT = (NotFound, ResourceDoesNotExist)
_PIN_FIELDS = frozenset({"version", "app_name", "title", "space_id", "provenance", "plan_sha256", "pinned_at"})
_SIGNATURE_FIELDS = frozenset({"attestation_alg", "attestation_verify_key", "attestation_signature"})


class GeniePinRefusal(RuntimeError):
    """The governed space cannot be resolved by a pin, or the pin cannot be trusted."""

    def __init__(self, message: str, *, plan: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.plan = dict(plan) if plan is not None else None


@dataclass(frozen=True)
class Resolution:
    """How the provisioner finds the governed space.

    ``action`` is ``use`` (a pinned id), ``create`` (no pin, no title match)
    or ``adopt`` (a title match whose plan the ack names). ``write_pin`` is
    the provenance the provisioner must pin, or ``None`` when the pin record
    already names the space.
    """

    action: str
    space_id: str | None
    source: str
    write_pin: str | None = None
    plan: dict[str, Any] | None = None
    plan_sha256: str = ""
    managers: tuple[str, ...] = field(default=())


def _text(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def pin_key(app_name: str) -> str:
    # The scope's ownership proof admits exactly this key, so it is defined there.
    from tools.databricks.app_rollback_secret_scope import genie_space_pin_key

    return genie_space_pin_key(app_name)


def _decode(value: str, *, length: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value.strip() + "=" * (-len(value.strip()) % 4))
    except (TypeError, ValueError) as exc:
        raise GeniePinRefusal("the Genie pin key material is invalid") from exc
    if len(decoded) != length:
        raise GeniePinRefusal("the Genie pin key material has an invalid length")
    return decoded


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _signed_payload(record: Mapping[str, Any]) -> bytes:
    unsigned = {key: value for key, value in record.items() if key not in _SIGNATURE_FIELDS}
    return PIN_DOMAIN + json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign_pin(record: Mapping[str, Any], environ: Mapping[str, str] | None = None) -> dict[str, Any]:
    """``record`` signed with the deploy's proof key; refuses when the key pair is absent or mismatched."""

    env = os.environ if environ is None else environ
    private = Ed25519PrivateKey.from_private_bytes(
        _decode(env.get("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", ""), length=32)
    )
    verify = _encode(
        private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw
        )
    )
    if verify != env.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip():
        raise GeniePinRefusal("the Genie pin signing and verification keys do not match")
    signed = copy.deepcopy(dict(record))
    signed.update(
        attestation_alg=PIN_ALGORITHM,
        attestation_verify_key=verify,
        attestation_signature=_encode(private.sign(_signed_payload(record))),
    )
    return signed


def verified_pin(
    record: Mapping[str, Any], *, app_name: str, environ: Mapping[str, str] | None = None
) -> dict[str, Any]:
    """The pin record, its signature and shape proven; any doubt refuses."""

    env = os.environ if environ is None else environ
    trusted = {
        env.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip(),
        env.get("MIP_AI_GATEWAY_PROOF_PREVIOUS_VERIFY_KEY", "").strip(),
    } - {""}
    if not trusted:
        raise GeniePinRefusal(
            "MIP_AI_GATEWAY_PROOF_VERIFY_KEY is not exported, so the Genie pin record cannot be verified"
        )
    key = str(record.get("attestation_verify_key") or "").strip()
    if set(record) != _PIN_FIELDS | _SIGNATURE_FIELDS or record.get("attestation_alg") != PIN_ALGORITHM:
        raise GeniePinRefusal("the Genie pin record is not the reviewed shape")
    if key not in trusted:
        raise GeniePinRefusal("the Genie pin record is not signed by this deploy's verification key")
    try:
        Ed25519PublicKey.from_public_bytes(_decode(key, length=32)).verify(
            _decode(str(record.get("attestation_signature") or ""), length=64), _signed_payload(record)
        )
    except InvalidSignature as exc:
        raise GeniePinRefusal("the Genie pin record signature is invalid") from exc
    if (
        record.get("version") != PIN_VERSION
        or record.get("app_name") != app_name
        or record.get("provenance") not in PROVENANCES
        or not _text(record.get("space_id"))
    ):
        raise GeniePinRefusal("the Genie pin record does not name this App's governed space")
    return dict(record)


# --- reads -----------------------------------------------------------------------


def _scope_exists(workspace: Any, scope: str) -> bool:
    return scope in {_text(getattr(item, "name", None)) for item in workspace.secrets.list_scopes()}


def _secret_text(workspace: Any, *, scope: str, key: str) -> str | None:
    try:
        encoded = _text(workspace.secrets.get_secret(scope, key).value)
    except _ABSENT:
        return None
    try:
        return base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise GeniePinRefusal("the Genie pin record is not readable") from exc


def read_pin(workspace: Any, *, scope: str, app_name: str) -> dict[str, Any] | None:
    """The verified pin record; ``None`` when the scope or the record does not exist yet."""

    if not _scope_exists(workspace, scope):
        return None
    from tools.databricks.app_rollback_secret_scope import assert_owned_app_rollback_scope

    assert_owned_app_rollback_scope(workspace, app_name=app_name, scope=scope)
    raw = _secret_text(workspace, scope=scope, key=pin_key(app_name))
    if raw is None:
        return None
    try:
        record = json.loads(raw)
    except ValueError as exc:
        raise GeniePinRefusal("the Genie pin record is not valid JSON") from exc
    if not isinstance(record, dict):
        raise GeniePinRefusal("the Genie pin record is not an object")
    return verified_pin(record, app_name=app_name)


def record_space_id(workspace: Any, *, scope: str, app_name: str, lakebase_instance: str) -> str | None:
    """The space the App's signed rollback record names; ``None`` when no record exists."""

    if not _scope_exists(workspace, scope):
        return None
    from tools.databricks.app_rollback_record_contract import (
        _legacy_record_key,
        _load_record,
        _record_key,
    )

    keys = (_record_key(app_name), _legacy_record_key(app_name))
    if all(_secret_text(workspace, scope=scope, key=key) is None for key in keys):
        return None
    record = _load_record(workspace, app_name=app_name, scope=scope, expected_lakebase_instance=lakebase_instance)
    space_id = _text(record.get("genie_space_id"))
    if not space_id:
        raise GeniePinRefusal("the signed rollback record names no Genie space")
    return space_id


def title_matches(workspace: Any, title: str) -> list[Any]:
    matches: list[Any] = []
    page_token: str | None = None
    while True:
        response = workspace.genie.list_spaces(page_token=page_token)
        for space in getattr(response, "spaces", None) or []:
            if _text(getattr(space, "title", None) or getattr(space, "name", None)) == title:
                matches.append(space)
        page_token = getattr(response, "next_page_token", None)
        if not page_token:
            return matches


def _space_exists(workspace: Any, space_id: str) -> bool:
    try:
        workspace.genie.get_space(space_id)
    except _ABSENT:
        return False
    return True


def direct_managers(workspace: Any, space_id: str) -> tuple[str, ...]:
    """Principals holding a direct (not inherited) CAN_MANAGE on the space; the creator is one."""

    acl = workspace.api_client.do("GET", f"/api/2.0/permissions/genie/{space_id}") or {}
    managers = set()
    for entry in acl.get("access_control_list") or []:
        principal = _text(
            entry.get("user_name") or entry.get("service_principal_name") or entry.get("group_name")
        )
        for permission in entry.get("all_permissions") or []:
            if _text(permission.get("permission_level")) == "CAN_MANAGE" and not permission.get("inherited"):
                managers.add(principal)
    managers.discard("")
    return tuple(sorted(managers))


# --- the plan ---------------------------------------------------------------------


def pin_plan(*, app_name: str, title: str, space_id: str, managers: tuple[str, ...], deployer: str) -> dict[str, Any]:
    return {
        "version": PLAN_VERSION,
        "action": "pin_title_match",
        "app_name": app_name,
        "title": title,
        "space_id": space_id,
        "managers": list(managers),
        "deployer": deployer,
    }


def plan_sha256(plan: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _pins(workspace: Any, *, app_name: str, scope: str, lakebase_instance: str) -> tuple[str, str]:
    """(pin record id, signed rollback record id), each "" when absent; they must agree."""

    pin = read_pin(workspace, scope=scope, app_name=app_name)
    record = record_space_id(workspace, scope=scope, app_name=app_name, lakebase_instance=lakebase_instance)
    pinned = _text(pin["space_id"]) if pin is not None else ""
    if pinned and record and pinned != record:
        raise GeniePinRefusal("the Genie pin record and the signed rollback record name different spaces")
    return pinned, record or ""


def _existing(workspace: Any, space_id: str) -> str:
    if not _space_exists(workspace, emit_mask(space_id)):
        raise GeniePinRefusal(
            "the pinned governed Genie space no longer exists; it is never replaced by a title match "
            "(restore it, or retire the pin through a reviewed change)"
        )
    return space_id


def pinned_space_id(workspace: Any, *, app_name: str, scope: str, lakebase_instance: str) -> str | None:
    """The governed space a pin names, proven to exist; ``None`` when nothing pins one yet."""

    pinned, record = _pins(workspace, app_name=app_name, scope=scope, lakebase_instance=lakebase_instance)
    return _existing(workspace, pinned or record) if pinned or record else None


def resolve(
    workspace: Any,
    *,
    app_name: str,
    title: str,
    scope: str,
    lakebase_instance: str,
    ack_plan: str = "",
) -> Resolution:
    """How the governed space resolves now: read-only, and fail closed."""

    pinned, record = _pins(workspace, app_name=app_name, scope=scope, lakebase_instance=lakebase_instance)
    if pinned or record:
        return Resolution(
            action="use",
            space_id=_existing(workspace, pinned or record),
            source="pin" if pinned else "record",
            write_pin=None if pinned else "record",
        )
    matches = title_matches(workspace, title)
    if not matches:
        return Resolution(action="create", space_id=None, source="none", write_pin="created")
    if len(matches) > 1:
        raise GeniePinRefusal(f"{len(matches)} Genie spaces carry the governed title and none is pinned")
    candidate = emit_mask(_text(getattr(matches[0], "space_id", None) or getattr(matches[0], "id", None)))
    if not candidate:
        raise GeniePinRefusal("the Genie space with the governed title has no id")
    managers = tuple(emit_mask(name) for name in direct_managers(workspace, candidate))
    deployer = emit_mask(_text(getattr(workspace.current_user.me(), "user_name", None)))
    plan = pin_plan(app_name=app_name, title=title, space_id=candidate, managers=managers, deployer=deployer)
    digest = plan_sha256(plan)
    if ack_plan.strip().lower() != digest:
        reason = "the ack names another plan" if ack_plan.strip() else "no ack names it"
        raise GeniePinRefusal(
            "one Genie space carries the governed title but no pin names it, and "
            f"{reason}: a space the deploy did not pin is never adopted by its title (attack G12). "
            "Review the read-only plan; pass its sha256 as the ack only if this space is the governed one",
            plan={**plan, "sha256": digest},
        )
    return Resolution(
        action="adopt",
        space_id=candidate,
        source="ack",
        write_pin="acked",
        plan=plan,
        plan_sha256=digest,
        managers=managers,
    )


# --- the one write ------------------------------------------------------------------


def write_pin(
    workspace: Any,
    *,
    scope: str,
    app_name: str,
    title: str,
    space_id: str,
    provenance: str,
    plan_digest: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Sign and store the pin record, then read it back exactly (under the deploy lease)."""

    if provenance not in PROVENANCES:
        raise ValueError("Genie pin provenance is invalid")
    from tools.databricks.app_rollback_secret_scope import assert_owned_app_rollback_scope

    assert_owned_app_rollback_scope(workspace, app_name=app_name, scope=scope)
    record = {
        "version": PIN_VERSION,
        "app_name": app_name,
        "title": title,
        "space_id": space_id,
        "provenance": provenance,
        "plan_sha256": plan_digest,
        "pinned_at": (now or datetime.now(UTC)).isoformat(timespec="seconds"),
    }
    signed = sign_pin(record)
    serialized = json.dumps(signed, sort_keys=True, separators=(",", ":"))
    workspace.secrets.put_secret(scope=scope, key=pin_key(app_name), string_value=serialized)
    if _secret_text(workspace, scope=scope, key=pin_key(app_name)) != serialized:
        raise GeniePinRefusal("the Genie pin record write did not converge exactly")
    return verified_pin(signed, app_name=app_name)


def describe_plan(plan: Mapping[str, Any]) -> list[str]:
    """The read-only plan as printable lines (identifiers were masked when read)."""

    managers = ", ".join(plan.get("managers") or []) or "none"
    return [
        f"  candidate space: {plan['space_id']}",
        f"  creator (direct CAN_MANAGE; the Genie API reports no creator): {managers}",
        f"  deploying identity: {plan['deployer']}",
        f"  pin plan sha256: {plan['sha256']}",
    ]
