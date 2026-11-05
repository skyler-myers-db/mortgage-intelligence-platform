"""Freeze the emergency publisher with a signed marker (M2-PR7, attack O8).

  python -m tools.databricks.app_deployment_lease freeze --app-name N --until <epoch>
  python -m tools.databricks.app_deployment_lease thaw --app-name N --marker-id ID

Cutover step 1 freezes peer-session piecewise deploys. A message to the peers
is not a control, so the freeze is a marker the emergency recipe's publisher
(``app_release_source publish``) reads and obeys. Markers live in the lease
root beside the App's lease (``/.mip-deployment-leases``: the lease holder
manages it, its delegated writer reads it, admins manage it) and are signed
with the deploy's proof-signing key, verified by the lease's own key registry:

* ``freeze`` writes ``<app>.freeze-<marker id>.json`` create-only: a new
  marker id, the freezing identity, ``created_at`` and ``until`` (epoch
  seconds, in the future and at most MAX_FREEZE_SECONDS ahead). It refuses
  while a marker for the App is still frozen, and it is signed before the root
  is touched, so a refused freeze changes nothing. It never sets a permission
  (``prepare_root``): the owner runs it from their own shell, and converging
  the root's ACL for them would remove the deployer's MANAGE and break a run
  that holds the lease. A missing root is created bare (the first lease
  acquisition converges it). An existing one is checked read-only: exactly
  protected for the App lease's own holder and writer, or, when the App has
  no lease record yet, held by no accessor but admins and the freezing
  identity (``freeze_root_unprotected`` otherwise).
* ``thaw`` writes ``<app>.freeze-<marker id>.thaw.json`` create-only, bound
  to the freeze marker's sha256. Nothing is ever overwritten or deleted.

The publisher judges every marker in the lease root, for every App (it
cannot know which App a folder will be deployed to, so a freeze of one App
in a workspace freezes the publisher for all of them). A marker reads as
**not** frozen only when its signature verifies and it is thawed by a signed
thaw, or its ``until`` has passed. Anything else is frozen: an unreadable or
unsigned marker, or one this shell cannot verify because the deploy's public
verify key (``MIP_AI_GATEWAY_PROOF_VERIFY_KEY``) is not exported. A missing
lease root holds no marker; any other listing error refuses (fail closed).

While frozen, publish refuses with exit 2 unless one of:

* ``--override-freeze <marker id>`` with ``--override-freeze-ack <sha256>``:
  the ``override_freeze`` digest (tools/databricks/operation_ack.py) over the
  marker id and the exact release folder, so an override never carries to
  another publish; it lifts that one marker only;
* the held signed lease (``--held-lease-app-name`` and ``--held-lease-id``,
  which scripts/deploy.sh passes): the governed run under the lease is the
  coordinated path the freeze exists to protect, and only the deploy's
  signing authority can take that lease. It exempts that App's own markers
  only; every other App's marker is still judged, so a lease taken on a decoy
  App lifts no freeze.

Who can publish while no marker is frozen: the publisher lists the lease root
on every publish, so once it exists (a governed lease creates it, its holder
managing it, its writer reading it, admins managing it; or a freeze creates
it bare until the first lease converges it) a publishing identity that
cannot list it refuses (``freeze_unreadable``) even with no marker; and
since markers are never deleted, every later publish needs the deploy's
public verify key exported to read an expired or thawed marker as such.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from databricks.sdk.errors import AlreadyExists, NotFound, ResourceAlreadyExists
from databricks.sdk.service.workspace import ImportFormat
from tools.databricks import app_deployment_lease_holder as lease_holder
from tools.databricks import app_deployment_lease_support as lease_support
from tools.databricks.operation_ack import AckRefused, ack_digest, require_ack

LEASE_ROOT = "/.mip-deployment-leases"
MARKER_VERSION = 1
MAX_FREEZE_SECONDS = 30 * 24 * 3600
_DOMAIN = b"mip-app-freeze-marker\0"
_SIGNATURE_KEYS = frozenset({"attestation_verify_key", "attestation_signature"})
_FREEZE_KEYS = frozenset(
    {"version", "kind", "app_name", "marker_id", "frozen_by", "created_at", "until"}
)
_THAW_KEYS = frozenset(
    {"version", "kind", "app_name", "marker_id", "frozen_by", "created_at", "freeze_sha256"}
)


class FreezeRefused(RuntimeError):
    """A freeze, thaw or frozen publish refuses; ``code`` is reviewed."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Marker:
    """One freeze marker as the publisher judges it."""

    name: str
    marker_id: str
    app_name: str
    frozen: bool
    reason: str


def _app(app_name: str) -> str:
    normalized = app_name.strip()
    if not normalized or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in normalized):
        raise FreezeRefused("freeze_app_invalid", "the App name is invalid for a freeze marker")
    return normalized


def freeze_path(app_name: str, marker_id: str) -> str:
    return f"{LEASE_ROOT}/{_app(app_name)}.freeze-{UUID(marker_id)}.json"


def thaw_path(app_name: str, marker_id: str) -> str:
    return freeze_path(app_name, marker_id).removesuffix(".json") + ".thaw.json"


def _message(document: dict[str, Any]) -> bytes:
    unsigned = {key: value for key, value in document.items() if key not in _SIGNATURE_KEYS}
    return _DOMAIN + json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _unb64(value: str, length: int) -> bytes:
    decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if len(decoded) != length:
        raise ValueError("freeze marker key or signature has an invalid length")
    return decoded


def _sign(document: dict[str, Any], *, environ: dict[str, str]) -> dict[str, Any]:
    signing = environ.get("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "").strip()
    verify = environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    try:
        private = Ed25519PrivateKey.from_private_bytes(_unb64(signing, 32))
    except ValueError as exc:
        raise FreezeRefused(
            "freeze_unsigned", "the deploy's proof-signing key is required"
        ) from exc
    if _b64(private.public_key().public_bytes_raw()) != verify:
        raise FreezeRefused("freeze_unsigned", "the proof signing and verify keys do not match")
    return {
        **document,
        "attestation_verify_key": verify,
        "attestation_signature": _b64(private.sign(_message(document))),
    }


def _verified(document: object, *, keys: frozenset[str], registry: list[str]) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != keys | _SIGNATURE_KEYS:
        raise ValueError("freeze marker fields are not exact")
    verify = str(document["attestation_verify_key"])
    if verify not in registry:
        raise ValueError("freeze marker is signed by an unregistered key")
    try:
        Ed25519PublicKey.from_public_bytes(_unb64(verify, 32)).verify(
            _unb64(str(document["attestation_signature"]), 64), _message(document)
        )
    except InvalidSignature as exc:
        raise ValueError("freeze marker signature is invalid") from exc
    return document


def _digest(document: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _read_json(workspace: Any, path: str) -> object | None:
    try:
        raw = workspace.workspace.download(path).read()
    except NotFound:
        return None
    return json.loads(raw.decode("utf-8"))


def _write_create_only(workspace: Any, path: str, document: dict[str, Any]) -> None:
    data = json.dumps(document, sort_keys=True).encode("utf-8")
    try:
        workspace.workspace.upload(
            path, io.BytesIO(data), format=ImportFormat.AUTO, overwrite=False
        )
    except (AlreadyExists, ResourceAlreadyExists) as exc:
        raise FreezeRefused(
            "freeze_exists", "a marker already exists at that path; nothing was overwritten"
        ) from exc
    if _read_json(workspace, path) != document:
        raise FreezeRefused("freeze_unverified", "the marker did not read back exactly")


def _registry() -> list[str] | None:
    try:
        return lease_support.key_registry()
    except RuntimeError:
        return None


def judge_markers(workspace: Any, *, now: float | None = None) -> list[Marker]:
    """Every freeze marker in the lease root, frozen or not; any listing error raises."""

    current = time.time() if now is None else now
    try:
        listing = list(workspace.workspace.list(LEASE_ROOT))
    except NotFound:
        return []
    names = {str(getattr(item, "path", "")).rsplit("/", 1)[-1] for item in listing}
    registry = _registry()
    markers: list[Marker] = []
    for name in sorted(
        n for n in names if ".freeze-" in n and n.endswith(".json") and not n.endswith(".thaw.json")
    ):
        app_name, _, rest = name.partition(".freeze-")
        marker_id = rest.removesuffix(".json")
        path = f"{LEASE_ROOT}/{name}"
        try:
            if registry is None:
                raise ValueError("the deploy's public verify key is not exported")
            freeze = _verified(_read_json(workspace, path), keys=_FREEZE_KEYS, registry=registry)
            if (freeze["kind"], freeze["marker_id"], freeze["app_name"]) != (
                "freeze",
                marker_id,
                app_name,
            ):
                raise ValueError("freeze marker does not name its own path")
        except Exception as exc:  # noqa: BLE001 - an unreadable marker is frozen
            markers.append(Marker(name, marker_id, app_name, True, f"unverifiable ({exc})"))
            continue
        thaw_name = name.removesuffix(".json") + ".thaw.json"
        thawed = False
        if thaw_name in names:
            try:
                thaw = _verified(
                    _read_json(workspace, f"{LEASE_ROOT}/{thaw_name}"),
                    keys=_THAW_KEYS,
                    registry=registry,
                )
                thawed = (thaw["kind"], thaw["marker_id"], thaw["freeze_sha256"]) == (
                    "thaw",
                    marker_id,
                    _digest(freeze),
                )
            except Exception:  # noqa: BLE001 - an unverifiable thaw thaws nothing
                thawed = False
        expired = int(freeze["until"]) <= current
        frozen = not thawed and not expired
        reason = (
            "thawed" if thawed else "expired" if expired else f"frozen until {int(freeze['until'])}"
        )
        markers.append(Marker(name, marker_id, app_name, frozen, reason))
    return markers


def _assert_bare_root(workspace: Any, *, lease: Any, holder: str, object_id: str) -> None:
    """A root with no lease for the App: only admins and the freezing identity may hold it."""

    holder_field = lease_holder.holder_acl_field(workspace, holder)
    permissions = workspace.workspace.get_permissions("directories", object_id)
    for entry in lease._items(lease._field(permissions, "access_control_list")):
        levels = lease._permission_levels(entry)
        group_name = str(lease._field(entry, "group_name") or "").strip().casefold()
        named = any(
            str(lease._field(entry, name) or "").strip()
            for name in ("user_name", "service_principal_name")
        )
        if not levels:
            continue
        if levels != {"CAN_MANAGE"} or not (
            lease_holder.is_holder_entry(holder_field, holder, entry)
            or (group_name == "admins" and not named)
        ):
            raise RuntimeError(
                "the root has an accessor other than admins and the freezing identity, and "
                "the App has no lease record to name its holder and writer"
            )


def prepare_root(workspace: Any, *, app_name: str, lease: Any, holder: str) -> None:
    """The lease root for a marker, without ever setting a permission (see the module doc)."""

    try:
        workspace.workspace.get_status(LEASE_ROOT)
    except NotFound:
        workspace.workspace.mkdirs(LEASE_ROOT)  # bare: the first lease acquisition converges it
        return
    try:
        record = lease._download(workspace, app_name=_app(app_name))
        object_id = lease._root_object_id(workspace)
        if record is None:
            # No lease for this App yet (a root a freeze created bare, or another
            # App's): still judged read-only, so nobody else can delete a marker.
            _assert_bare_root(workspace, lease=lease, holder=holder, object_id=object_id)
            return
        lease._assert_protected_root(
            workspace,
            holder=str(record.get("holder") or "").strip(),
            writer_application_id=str(record.get("writer_application_id") or "").strip(),
            object_id=object_id,
        )
    except RuntimeError as exc:
        raise FreezeRefused(
            "freeze_root_unprotected",
            f"the lease root is not exactly protected for the App's lease holder ({exc}); "
            "nothing was written and no permission was changed",
        ) from exc


def override_plan(*, marker_id: str, release_path: str) -> dict[str, str]:
    """The canonical plan an ``override_freeze`` ack covers: one marker, one release folder."""

    return {"marker_id": marker_id, "release_path": release_path}


def assert_publish_allowed(
    workspace: Any,
    *,
    release_path: str,
    override_marker_id: str | None = None,
    override_ack: str | None = None,
    held_lease: Callable[[], None] | None = None,
    held_lease_app_name: str | None = None,
    now: float | None = None,
) -> list[str]:
    """Refuse a publish while frozen; return notes. A proven held lease exempts its own App."""

    exempt = None
    if held_lease is not None:
        try:
            exempt = _app(held_lease_app_name or "")
            held_lease()
        except Exception as exc:  # noqa: BLE001 - an unproven lease exempts nothing
            raise FreezeRefused(
                "held_lease_unproven", f"the held signed lease is not proven ({type(exc).__name__})"
            ) from exc
    try:
        markers = judge_markers(workspace, now=now)
    except Exception as exc:  # noqa: BLE001 - a lease root that cannot be listed may hold a marker
        raise FreezeRefused(
            "freeze_unreadable", f"the freeze markers could not be listed ({type(exc).__name__})"
        ) from exc
    frozen = [marker for marker in markers if marker.frozen and marker.app_name != exempt]
    notes = [f"freeze marker {marker.name}: {marker.reason}" for marker in markers]
    if exempt is not None:
        notes.append(
            f"the publish runs under {exempt}'s held signed lease: that App's freeze markers do "
            "not apply, every other App's still do"
        )
    if override_marker_id:
        if override_marker_id not in {marker.marker_id for marker in frozen}:
            raise FreezeRefused("override_unknown", "--override-freeze names no frozen marker")
        try:
            require_ack(
                "override_freeze",
                override_plan(marker_id=override_marker_id, release_path=release_path),
                override_ack,
            )
        except AckRefused as exc:
            raise FreezeRefused(exc.code, str(exc)) from exc
        frozen = [marker for marker in frozen if marker.marker_id != override_marker_id]
        notes.append(
            f"freeze marker {override_marker_id} overridden by its exact override_freeze ack"
        )
    if frozen:
        first = frozen[0]
        digest = ack_digest(
            "override_freeze", override_plan(marker_id=first.marker_id, release_path=release_path)
        )
        raise FreezeRefused(
            "frozen",
            f"the emergency publisher is frozen by marker {first.marker_id} ({first.reason}); "
            "nothing was published. Thaw it, or, to publish exactly this release folder anyway, "
            f"pass --override-freeze {first.marker_id} --override-freeze-ack {digest}",
        )
    return notes


def freeze(
    workspace: Any,
    *,
    app_name: str,
    until: int,
    holder: str,
    ensure_root: Callable[[], None],
    environ: dict[str, str],
    now: float | None = None,
) -> str:
    """Write a signed, create-only freeze marker; return its id."""

    current = time.time() if now is None else now
    if not current < until <= current + MAX_FREEZE_SECONDS:
        raise FreezeRefused(
            "freeze_until_invalid",
            f"--until must be in the future and at most {MAX_FREEZE_SECONDS} s ahead",
        )
    marker_id = str(uuid4())
    document = {
        "version": MARKER_VERSION,
        "kind": "freeze",
        "app_name": _app(app_name),
        "marker_id": marker_id,
        "frozen_by": holder,
        "created_at": datetime.fromtimestamp(current, UTC).isoformat(),
        "until": int(until),
    }
    # Signed before the root is touched: a refused freeze changes nothing.
    signed = _sign(document, environ=environ)
    ensure_root()
    active = [
        marker
        for marker in judge_markers(workspace, now=current)
        if marker.frozen and marker.app_name == _app(app_name)
    ]
    if active:
        raise FreezeRefused(
            "already_frozen", f"the App is already frozen by marker {active[0].marker_id}"
        )
    _write_create_only(workspace, freeze_path(app_name, marker_id), signed)
    return marker_id


def thaw(
    workspace: Any, *, app_name: str, marker_id: str, holder: str, environ: dict[str, str]
) -> None:
    """Write the signed, create-only thaw of one verified freeze marker."""

    registry = _registry()
    if registry is None:
        raise FreezeRefused("freeze_unsigned", "the deploy's verify key registry is not configured")
    try:
        freeze_document = _verified(
            _read_json(workspace, freeze_path(app_name, marker_id)),
            keys=_FREEZE_KEYS,
            registry=registry,
        )
    except Exception as exc:  # noqa: BLE001 - only a verified marker is thawed
        raise FreezeRefused(
            "thaw_unverified", f"the freeze marker does not verify ({type(exc).__name__})"
        ) from exc
    document = {
        "version": MARKER_VERSION,
        "kind": "thaw",
        "app_name": _app(app_name),
        "marker_id": str(UUID(marker_id)),
        "frozen_by": holder,
        "created_at": datetime.now(UTC).isoformat(),
        "freeze_sha256": _digest(freeze_document),
    }
    _write_create_only(workspace, thaw_path(app_name, marker_id), _sign(document, environ=environ))
