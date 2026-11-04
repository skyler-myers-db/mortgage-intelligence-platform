#!/usr/bin/env python3
"""A first-install release stops for the owner bootstrap, then resumes (M1-PR6, attack G15).

The account principal holds Service Principal Manager on the enumerated
service principals only (plan section 4.10). A first install creates the App,
and with it a new service principal the account principal cannot manage yet:
every App-identity proof after that needs the owner's human-run
``python -m tools.clients.bootstrap_account --post-first-install``. So a
``--phase release`` that just created the App (stopped, source-free, bound,
its signed first-install journal claimed) stops there, before any App-identity
proof, with exit 3 and ``awaiting owner bootstrap --post-first-install``. The
next ``--phase release`` resumes at the same point.

* ``check`` (read-only, the bounded account identity): does the account
  principal hold ``roles/servicePrincipal.manager`` on the App's service
  principal? Exit 0 held, 3 not held (a rule set it may not read is not held),
  2 undecided.
* ``mark`` (under the lease): the signed **awaiting** marker, beside the
  lease at ``<LEASE_ROOT>/<app>.first-install-bootstrap.json``. It binds the
  first-install journal's bootstrap id and its claimed App identity, and is
  written only while that journal reads ``recover`` and the App is stopped with
  no deployment. The deploy keeps the App on that exit; without the marker the
  next run's first-install recovery would delete it (and the owner's grant
  with its service principal).
* ``status`` (under the lease): ``resume`` when a valid marker names the
  journal's own claim and the App is still stopped with no deployment;
  ``absent`` with no marker; anything else refuses (exit 2), never reads as
  absent.
* ``verify-resume`` (read-only): the resumed App's bindings are still the ones
  its journal recorded and the release planned.
* ``consume`` (under the lease): delete the marker once ``check`` passed; from
  here the run is an ordinary first install again, whose failure is compensated
  as before.

The marker is signed with the deploy's proof key, like the quiesce journal and
the data-phase ledger, whose key decoding it reuses.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from databricks.sdk.errors import NotFound, PermissionDenied, ResourceDoesNotExist
from databricks.sdk.service.workspace import ImportFormat
from tools.databricks import app_first_install_journal as journal
from tools.databricks import quiesce_window_journal as signed_journal
from tools.databricks.app_deployment_lease import LEASE_ROOT, assert_held
from tools.databricks.app_deployment_lease_support import key_registry
from tools.databricks.app_rollback_resource_contract import (
    app_resource_contract,
    validated_app_resource_contract,
)

MARKER_VERSION = 1
ATTESTATION_ALG = "ed25519-first-install-bootstrap-v1"
SP_MANAGER = "roles/servicePrincipal.manager"
MESSAGE = "awaiting owner bootstrap --post-first-install"
FIELDS = frozenset(
    {
        "version",
        "app_name",
        "bootstrap_id",
        "app_id",
        "app_service_principal_client_id",
        "app_service_principal_scim_id",
        "lease_id",
        "source_git_sha",
        "marked_at",
    }
)
_SIGNATURE_FIELDS = ("attestation_alg", "attestation_verify_key", "attestation_signature")
EXIT_HELD, EXIT_REFUSED, EXIT_AWAITING = 0, 2, 3


class Refused(RuntimeError):
    """The marker, the journal or the App is not what a resume needs; nothing was changed."""


def marker_path(app_name: str) -> str:
    normalized = app_name.strip()
    if not normalized or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for ch in normalized):
        raise ValueError("first-install bootstrap marker App name is invalid")
    return f"{LEASE_ROOT}/{normalized}.first-install-bootstrap.json"


def _message(record: Mapping[str, Any]) -> bytes:
    unsigned = {key: value for key, value in record.items() if key not in _SIGNATURE_FIELDS}
    return b"mip-first-install-bootstrap\0" + json.dumps(unsigned, sort_keys=True, separators=(",", ":")).encode()


def _sign(record: dict[str, Any]) -> dict[str, Any]:
    signing = os.environ.get("MIP_AI_GATEWAY_PROOF_SIGNING_KEY", "").strip()
    verify = os.environ.get("MIP_AI_GATEWAY_PROOF_VERIFY_KEY", "").strip()
    private = Ed25519PrivateKey.from_private_bytes(signed_journal._decode(signing, length=32))
    derived = signed_journal._encode(
        private.public_key().public_bytes(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
    )
    if derived != verify:
        raise RuntimeError("first-install bootstrap marker signing and verification keys do not match")
    return {
        **record,
        "attestation_alg": ATTESTATION_ALG,
        "attestation_verify_key": verify,
        "attestation_signature": signed_journal._encode(private.sign(_message(record))),
    }


def read(workspace: Any, *, app_name: str) -> dict[str, Any] | None:
    """The verified marker, or None when no release awaits the owner bootstrap."""

    try:
        value = json.loads(workspace.workspace.download(marker_path(app_name)).read().decode("utf-8"))
    except (NotFound, ResourceDoesNotExist):
        return None
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Refused("the first-install bootstrap marker is not valid JSON") from exc
    verify = str(value.get("attestation_verify_key") or "") if isinstance(value, dict) else ""
    if not isinstance(value, dict) or value.get("attestation_alg") != ATTESTATION_ALG or verify not in key_registry():
        raise Refused("the first-install bootstrap marker's attestation identity is invalid")
    try:
        Ed25519PublicKey.from_public_bytes(signed_journal._decode(verify, length=32)).verify(
            signed_journal._decode(str(value.get("attestation_signature") or ""), length=64), _message(value)
        )
    except (InvalidSignature, RuntimeError, ValueError) as exc:
        raise Refused("the first-install bootstrap marker's signature is invalid") from exc
    unsigned = {key: item for key, item in value.items() if key not in _SIGNATURE_FIELDS}
    if set(unsigned) != FIELDS or unsigned["version"] != MARKER_VERSION or unsigned["app_name"] != app_name:
        raise Refused("the first-install bootstrap marker is not the reviewed record")
    return value


def _claim(workspace: Any, *, app_name: str, lease_id: str, source_git_sha: str, scope: str, instance: str) -> dict[str, Any]:
    """The journal's claimed first install, read under the lease; its App must still be stopped and source-free."""

    state = journal.status(
        workspace,
        app_name=app_name,
        lease_id=lease_id,
        source_git_sha=source_git_sha,
        rollback_scope=scope,
        expected_lakebase_instance=instance,
    )
    record = journal._download(workspace, app_name=app_name)
    if state != "recover" or record is None:
        raise Refused(f"the first-install journal reads {state}, not a claimed first install")
    app = workspace.apps.get(app_name)
    compute = str(getattr(getattr(app, "compute_status", None), "state", "") or "")
    if compute.split(".")[-1].upper() != "STOPPED" or app.active_deployment is not None or app.pending_deployment is not None:
        raise Refused("the first-install App is no longer stopped and source-free")
    return record


def _identity(record: Mapping[str, Any]) -> dict[str, str]:
    return {
        "bootstrap_id": str(record["bootstrap_id"]),
        "app_id": str(record["app_id"]),
        "app_service_principal_client_id": str(record["app_service_principal_client_id"]),
        "app_service_principal_scim_id": str(record["app_service_principal_scim_id"]),
    }


def mark(workspace: Any, *, app_name: str, lease_id: str, source_git_sha: str, scope: str, instance: str) -> dict[str, Any]:
    record = _claim(workspace, app_name=app_name, lease_id=lease_id, source_git_sha=source_git_sha, scope=scope, instance=instance)
    current = read(workspace, app_name=app_name)
    if current is not None:
        if {key: current[key] for key in _identity(record)} != _identity(record):
            raise Refused("a bootstrap marker names another first install")
        return current
    marker = _sign(
        {
            "version": MARKER_VERSION,
            "app_name": app_name,
            **_identity(record),
            "lease_id": lease_id,
            "source_git_sha": source_git_sha,
            "marked_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
    )
    assert_held(workspace, app_name=app_name, lease_id=lease_id, source_git_sha=source_git_sha)
    workspace.workspace.upload(
        marker_path(app_name), io.BytesIO(json.dumps(marker, sort_keys=True).encode()), format=ImportFormat.AUTO, overwrite=False
    )
    if read(workspace, app_name=app_name) != marker:
        raise Refused("the first-install bootstrap marker did not persist exactly")
    return marker


def status(workspace: Any, *, app_name: str, lease_id: str, source_git_sha: str, scope: str, instance: str) -> str:
    current = read(workspace, app_name=app_name)
    if current is None:
        return "absent"
    record = _claim(workspace, app_name=app_name, lease_id=lease_id, source_git_sha=source_git_sha, scope=scope, instance=instance)
    if {key: current[key] for key in _identity(record)} != _identity(record):
        raise Refused("the bootstrap marker does not name the journal's claimed first install")
    return "resume"


def verify_resume(workspace: Any, *, app_name: str, payload: Mapping[str, Any]) -> None:
    """The resumed App is bound exactly as its journal recorded and this release planned."""

    record = journal._download(workspace, app_name=app_name)
    if record is None:
        raise Refused("no first-install journal to resume")
    planned = validated_app_resource_contract(payload.get("resources"))
    live = app_resource_contract(workspace, app_name=app_name)
    if not (planned == live == record["app_resources"]):
        raise Refused(
            "the first-install App's bindings changed while it awaited the owner bootstrap; the release "
            "plans other bindings than the journal recorded"
        )


def consume(workspace: Any, *, app_name: str, lease_id: str, source_git_sha: str) -> None:
    current = read(workspace, app_name=app_name)
    if current is None:
        return
    assert_held(workspace, app_name=app_name, lease_id=lease_id, source_git_sha=source_git_sha)
    workspace.workspace.delete(marker_path(app_name))
    if read(workspace, app_name=app_name) is not None:
        raise Refused("the first-install bootstrap marker remained after its deletion")


def manager_held(account: Any, *, account_id: str, manager_application_id: str, app_application_id: str) -> bool:
    """Read-only: the account principal is a Service Principal Manager of the App's identity."""

    name = f"accounts/{account_id}/servicePrincipals/{app_application_id}/ruleSets/default"
    try:
        rules = account.access_control.get_rule_set(name=name, etag="")
    except (PermissionDenied, NotFound, ResourceDoesNotExist):
        return False
    principal = f"servicePrincipals/{manager_application_id}"
    return any(
        str(getattr(rule, "role", "") or "") == SP_MANAGER and principal in (getattr(rule, "principals", None) or ())
        for rule in getattr(rules, "grant_rules", None) or ()
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check").add_argument("--app-application-id", required=True)
    for name in ("mark", "status", "consume"):
        mode = sub.add_parser(name)
        mode.add_argument("--app-name", required=True)
        mode.add_argument("--lease-id", required=True)
        mode.add_argument("--source-git-sha", required=True)
        mode.add_argument("--rollback-scope", default="mip-app-rollback")
        mode.add_argument("--lakebase-instance", default="")
    resume = sub.add_parser("verify-resume")
    resume.add_argument("--app-name", required=True)
    resume.add_argument("--payload", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None, *, workspace: Any = None, account: Any = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "check":
            if account is None:
                from tools.databricks.uc_owner_policy import account_client_from_env

                account = account_client_from_env()
            held = manager_held(
                account,
                account_id=os.environ.get("DATABRICKS_ACCOUNT_ID", ""),
                manager_application_id=os.environ.get("DATABRICKS_ACCOUNT_CLIENT_ID", "").strip().lower(),
                app_application_id=args.app_application_id.strip().lower(),
            )
            print(f"[first-install] Service Principal Manager on the new App identity: {'held' if held else 'not held'}")
            return EXIT_HELD if held else EXIT_AWAITING
        if workspace is None:
            from databricks.sdk import WorkspaceClient

            workspace = WorkspaceClient()
        if args.command == "verify-resume":
            verify_resume(workspace, app_name=args.app_name, payload=json.loads(args.payload.read_text(encoding="utf-8")))
            print("[first-install] the resumed App is bound exactly as its journal recorded")
            return 0
        common = {"app_name": args.app_name, "lease_id": args.lease_id, "source_git_sha": args.source_git_sha}
        if args.command == "consume":
            consume(workspace, **common)
            return 0
        located = {"scope": args.rollback_scope, "instance": args.lakebase_instance}
        if args.command == "mark":
            mark(workspace, **common, **located)
            print(f"[first-install] {MESSAGE}: the App is kept, stopped and source-free")
            return 0
        print(status(workspace, **common, **located))
        return 0
    except Refused as exc:
        print(f"[first-install] refused: {exc}; nothing was changed", file=sys.stderr)
        return EXIT_REFUSED
    except Exception as exc:  # the class only: an SDK message can carry a host or an id
        print(f"[first-install] refused: {type(exc).__name__}; nothing was changed", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
