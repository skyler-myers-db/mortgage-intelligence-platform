#!/usr/bin/env python3
"""Converge uploaded App source to a prebuilt, Python-served frontend."""

from __future__ import annotations

import argparse
import re
from pathlib import PurePosixPath
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.errors import NotFound, ResourceDoesNotExist

_ROOT_NODE_MANIFESTS = ("package.json", "package-lock.json")


def _expected_source_path(*, principal: str, target: str) -> str:
    if (
        type(principal) is not str
        or not principal
        or principal != principal.strip()
        or principal in {".", ".."}
        or "/" in principal
        or any(character.isspace() or ord(character) < 32 for character in principal)
    ):
        raise RuntimeError("expected App source principal is not canonical")
    if (
        type(target) is not str
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", target) is None
    ):
        raise RuntimeError("expected App source target is not canonical")
    return (
        f"/Workspace/Users/{principal}/.bundle/"
        f"mortgage-intelligence-platform/{target}/files"
    )


def _workspace_api_path(
    source_code_path: str,
    *,
    expected_principal: str,
    expected_target: str,
) -> str:
    if (
        type(source_code_path) is not str
        or not source_code_path
        or source_code_path != source_code_path.strip()
    ):
        raise RuntimeError("App source path is not the exact MIP bundle files root")
    source = source_code_path
    expected_source = _expected_source_path(
        principal=expected_principal,
        target=expected_target,
    )
    if source != expected_source:
        raise RuntimeError("App source path does not match the authenticated bundle identity")
    parsed = PurePosixPath(source)
    parts = parsed.parts
    if (
        str(parsed) != source
        or len(parts) != 8
        or parts[:3] != ("/", "Workspace", "Users")
        or not parts[3]
        or parts[4:6] != (".bundle", "mortgage-intelligence-platform")
        or parts[7] != "files"
        or any(part in {".", ".."} for part in parts)
    ):
        raise RuntimeError("App source path is not the exact MIP bundle files root")
    api_path = source.removeprefix("/Workspace")
    return api_path


def _status_or_none(workspace: Any, path: str) -> object | None:
    try:
        status = workspace.workspace.get_status(path)
    except (NotFound, ResourceDoesNotExist):
        return None
    except Exception as exc:  # noqa: BLE001 - source proof is fail-closed
        raise RuntimeError(f"could not read uploaded App source object {path}") from exc
    if status is None:
        raise RuntimeError(f"uploaded App source returned no status for {path}")
    actual_path = getattr(status, "path", None)
    object_type = str(
        getattr(getattr(status, "object_type", None), "value", getattr(status, "object_type", ""))
        or ""
    ).upper()
    if type(actual_path) is not str or actual_path != path or object_type != "FILE":
        raise RuntimeError(f"uploaded App source object has invalid identity: {path}")
    return status


def _delete_exact_file(workspace: Any, path: str) -> None:
    before = _status_or_none(workspace, path)
    if before is None:
        return
    try:
        workspace.workspace.delete(path, recursive=False)
    except Exception as delete_error:  # noqa: BLE001 - reconcile ambiguous deletion
        try:
            remaining = _status_or_none(workspace, path)
        except Exception as read_error:  # noqa: BLE001
            raise RuntimeError(
                f"could not authenticate App source after ambiguous deletion: {path}"
            ) from read_error
        if remaining is None:
            return
        raise RuntimeError(f"root Node manifest remained after deletion: {path}") from delete_error
    if _status_or_none(workspace, path) is not None:
        raise RuntimeError(f"root Node manifest remained after deletion: {path}")


def converge_static_app_source(
    workspace: Any,
    *,
    source_code_path: str,
    expected_principal: str,
    expected_target: str,
) -> None:
    """Require a built SPA and remove only exact root npm build manifests."""

    root = _workspace_api_path(
        source_code_path,
        expected_principal=expected_principal,
        expected_target=expected_target,
    )
    index_path = f"{root}/frontend/dist/index.html"
    index_status = _status_or_none(workspace, index_path)
    index_size = getattr(index_status, "size", None) if index_status is not None else None
    if index_status is None or type(index_size) is not int or index_size <= 0:
        raise RuntimeError("uploaded App source is missing a non-empty frontend/dist/index.html")
    for manifest in _ROOT_NODE_MANIFESTS:
        _delete_exact_file(workspace, f"{root}/{manifest}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-code-path", required=True)
    parser.add_argument("--expected-principal", required=True)
    parser.add_argument("--expected-target", required=True)
    args = parser.parse_args(argv)
    converge_static_app_source(
        WorkspaceClient(),
        source_code_path=args.source_code_path,
        expected_principal=args.expected_principal,
        expected_target=args.expected_target,
    )
    print("[static-app-source] prebuilt frontend present; root Node manifests absent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
