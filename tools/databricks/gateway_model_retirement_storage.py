"""Workspace Files storage for signed Gateway model retirement records."""

from __future__ import annotations

import hashlib
import io
import json
import re
from collections.abc import Callable, Mapping
from typing import Any
from uuid import UUID

from databricks.sdk.errors import (
    AlreadyExists,
    NotFound,
    ResourceAlreadyExists,
    ResourceDoesNotExist,
)
from databricks.sdk.service.workspace import ImportFormat
from tools.databricks.app_deployment_lease import LEASE_ROOT

_MODEL_NAME = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")
_APP_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")


def _safe_model_key(model_name: str) -> str:
    if _MODEL_NAME.fullmatch(model_name) is None:
        raise ValueError("Gateway retirement model name is invalid")
    return hashlib.sha256(model_name.encode("utf-8")).hexdigest()


def operation_root(app_name: str, model_name: str, lease_id: str) -> str:
    if _APP_NAME.fullmatch(app_name) is None:
        raise ValueError("Gateway retirement App name is invalid")
    try:
        normalized_lease = str(UUID(lease_id))
    except ValueError as exc:
        raise ValueError("Gateway retirement lease ID is invalid") from exc
    return (
        f"{LEASE_ROOT}/{app_name}.gateway-model-retirement/"
        f"{_safe_model_key(model_name)}/{normalized_lease}"
    )


def stage_path(app_name: str, model_name: str, lease_id: str) -> str:
    return f"{operation_root(app_name, model_name, lease_id)}/stage.json"


def completion_path(app_name: str, model_name: str, lease_id: str) -> str:
    return f"{operation_root(app_name, model_name, lease_id)}/complete.json"


def archived_head_path(app_name: str, model_name: str) -> str:
    if _APP_NAME.fullmatch(app_name) is None:
        raise ValueError("Gateway retirement App name is invalid")
    return (
        f"{LEASE_ROOT}/{app_name}.gateway-model-retirement/"
        f"{_safe_model_key(model_name)}/archived.json"
    )


def in_progress_path(app_name: str, model_name: str) -> str:
    return archived_head_path(app_name, model_name).replace(
        "/archived.json",
        "/in-progress.json",
    )


def load_retirement_record(workspace: Any, path: str) -> dict[str, Any] | None:
    from tools.databricks.gateway_model_retirement_record import verify_retirement_record

    try:
        stream = workspace.workspace.download(path)
    except (NotFound, ResourceDoesNotExist):
        return None
    try:
        value = json.loads(stream.read().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Gateway retirement record is not valid JSON") from exc
    return verify_retirement_record(value)


def persist_retirement_record(
    workspace: Any,
    path: str,
    record: Mapping[str, Any],
    *,
    assert_before_mutation: Callable[[], None] | None = None,
) -> None:
    from tools.databricks.gateway_model_retirement_record import (
        canonical_json,
        verify_retirement_record,
    )

    signed = verify_retirement_record(record)
    assert_mutation_allowed = assert_before_mutation or (lambda: None)
    assert_mutation_allowed()
    workspace.workspace.mkdirs(path.rsplit("/", 1)[0])
    assert_mutation_allowed()
    try:
        workspace.workspace.upload(
            path,
            io.BytesIO(canonical_json(signed).encode("utf-8")),
            format=ImportFormat.AUTO,
            overwrite=False,
        )
    except (AlreadyExists, ResourceAlreadyExists):
        if load_retirement_record(workspace, path) != signed:
            raise RuntimeError("Gateway retirement immutable record already differs") from None
        return
    except Exception as upload_error:
        try:
            persisted = load_retirement_record(workspace, path)
        except Exception as read_error:
            raise RuntimeError(
                "Gateway retirement upload failed and commit is ambiguous"
            ) from read_error
        if persisted != signed:
            raise RuntimeError(
                "Gateway retirement upload failed without an exact commit"
            ) from upload_error
    if load_retirement_record(workspace, path) != signed:
        raise RuntimeError("Gateway retirement record did not persist exactly")
