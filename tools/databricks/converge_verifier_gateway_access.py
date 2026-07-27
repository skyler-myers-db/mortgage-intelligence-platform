#!/usr/bin/env python3
"""Capture and compensate the verifier's endpoint-bound serving membership."""

from __future__ import annotations

import argparse
import os
import re
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from databricks.sdk import WorkspaceClient
from tools.databricks.audit_global_m2m_access import (
    assert_workspace_admin_inventory_identity,
)
from tools.databricks.deployment_lease_authority import held_assertion_from_env
from tools.databricks.serving_endpoint_acl import revoke_direct_permissions

_IDENTITY_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")


def _field(value: object, name: str) -> str:
    raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
    return str(getattr(raw, "value", raw) or "").strip()


def _identity_id(value: str, *, label: str) -> str:
    normalized = value.strip()
    if _IDENTITY_ID_RE.fullmatch(normalized) is None:
        raise ValueError(f"{label} is invalid")
    return normalized


def _exact_service_principal(
    workspace: Any,
    *,
    application_id: str,
) -> tuple[str, str]:
    expected = _identity_id(application_id, label="verifier application ID")
    candidates = list(
        workspace.service_principals.list(
            filter=f'applicationId eq "{expected}"',
            attributes="id,applicationId",
        )
    )
    exact = [
        (_field(candidate, "id"), _field(candidate, "application_id"))
        for candidate in candidates
        if _field(candidate, "application_id") == expected
    ]
    if len(exact) != 1:
        raise RuntimeError("verifier application ID did not resolve to exactly one identity")
    scim_id, observed_application_id = exact[0]
    return (
        _identity_id(scim_id, label="verifier SCIM ID"),
        _identity_id(observed_application_id, label="verifier application ID"),
    )


def _write_capture(path: Path, *, scim_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    payload = f"MIP_VERIFIER_SCIM_ID={shlex.quote(scim_id)}\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def capture(
    workspace: Any,
    *,
    application_id: str,
    expected_inventory_principal: str,
    out_env: Path,
) -> str:
    """Capture the immutable verifier SCIM ID under reviewed admin authority."""

    assert_workspace_admin_inventory_identity(
        workspace,
        expected_principal=expected_inventory_principal,
    )
    scim_id, _observed_application_id = _exact_service_principal(
        workspace,
        application_id=application_id,
    )
    _write_capture(out_env, scim_id=scim_id)
    return scim_id


def revoke_managed(
    workspace: Any,
    *,
    endpoint: str,
    application_id: str,
    expected_scim_id: str,
    expected_inventory_principal: str,
    assert_single_writer: Callable[[], None],
) -> bool:
    """Remove only the exact verifier's managed membership from one endpoint."""

    assert_workspace_admin_inventory_identity(
        workspace,
        expected_principal=expected_inventory_principal,
    )
    expected_id = _identity_id(expected_scim_id, label="expected verifier SCIM ID")
    observed_id, observed_application_id = _exact_service_principal(
        workspace,
        application_id=application_id,
    )
    if observed_id != expected_id:
        raise RuntimeError("verifier immutable SCIM ID drifted before Gateway compensation")
    return revoke_direct_permissions(
        workspace,
        endpoint_name=endpoint,
        service_principal=observed_application_id,
        service_principal_id=expected_id,
        missing_ok=False,
        assert_single_writer=assert_single_writer,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture_parser = subparsers.add_parser("capture")
    capture_parser.add_argument("--application-id", required=True)
    capture_parser.add_argument("--expected-inventory-principal", required=True)
    capture_parser.add_argument("--out-env", type=Path, required=True)
    revoke_parser = subparsers.add_parser("revoke-managed")
    revoke_parser.add_argument("--endpoint", required=True)
    revoke_parser.add_argument("--application-id", required=True)
    revoke_parser.add_argument("--expected-scim-id", required=True)
    revoke_parser.add_argument("--expected-inventory-principal", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    workspace = WorkspaceClient()
    if args.command == "capture":
        capture(
            workspace,
            application_id=args.application_id,
            expected_inventory_principal=args.expected_inventory_principal,
            out_env=args.out_env,
        )
        print("verifier immutable Gateway identity capture: PASS")
        return 0
    assert_single_writer = held_assertion_from_env(
        workspace,
        operation="verifier Gateway compensation",
    )
    revoke_managed(
        workspace,
        endpoint=args.endpoint,
        application_id=args.application_id,
        expected_scim_id=args.expected_scim_id,
        expected_inventory_principal=args.expected_inventory_principal,
        assert_single_writer=assert_single_writer,
    )
    print("verifier green managed Gateway membership compensation: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
