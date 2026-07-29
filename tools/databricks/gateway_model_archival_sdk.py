"""Narrow SDK adapters used by Gateway model archival convergence."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from mlflow.entities import ViewType

from databricks.sdk.service.sql import ExecuteStatementRequestOnWaitTimeout

_UC_NAME = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\Z")


def _field(value: Any, name: str) -> str:
    raw = value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)
    enum_value = getattr(raw, "value", None)
    return str(enum_value if type(enum_value) is str else raw or "").strip()


def delta_version_resolver(
    workspace: Any,
    *,
    warehouse_id: str,
) -> Callable[[str], str]:
    """Build an authoritative Delta DESCRIBE HISTORY latest-version reader."""

    warehouse = warehouse_id.strip()
    if not warehouse:
        raise ValueError("Gateway archival requires a SQL warehouse ID")

    def resolve(full_name: str) -> str:
        if _UC_NAME.fullmatch(full_name) is None:
            raise ValueError("Gateway archival Delta table name is invalid")
        quoted = ".".join(f"`{part}`" for part in full_name.split("."))
        response = workspace.statement_execution.execute_statement(
            statement=f"DESCRIBE HISTORY {quoted} LIMIT 1",
            warehouse_id=warehouse,
            wait_timeout="50s",
            on_wait_timeout=ExecuteStatementRequestOnWaitTimeout.CANCEL,
            row_limit=1,
        )
        status = getattr(response, "status", None)
        state = _field(status, "state").upper()
        if state != "SUCCEEDED":
            raise RuntimeError("Gateway archival Delta history query did not succeed")
        rows = getattr(getattr(response, "result", None), "data_array", None)
        if not isinstance(rows, list) or len(rows) != 1 or not rows[0]:
            raise RuntimeError("Gateway archival Delta history is not exact")
        version = str(rows[0][0] if rows[0][0] is not None else "").strip()
        if not version.isdigit():
            raise RuntimeError("Gateway archival Delta history version is invalid")
        return version

    return resolve


def experiments_named(client: Any, name: str) -> list[Any]:
    """Search exact active and deleted experiment names with complete pagination."""

    matches: list[Any] = []
    token: str | None = None
    seen: set[str] = set()
    while True:
        page = client.search_experiments(
            view_type=ViewType.ALL,
            max_results=1000,
            filter_string=f"name = '{name}'",
            page_token=token,
        )
        matches.extend(item for item in page if _field(item, "name") == name)
        next_token = str(getattr(page, "token", "") or "").strip()
        if not next_token:
            return matches
        if next_token in seen:
            raise RuntimeError("Gateway archival experiment search repeated a page token")
        seen.add(next_token)
        token = next_token


def experiment_state(client: Any, experiment_id: str) -> dict[str, Any]:
    """Read immutable experiment content and exact owner tags by ID."""

    experiment = client.get_experiment(experiment_id)
    tags = getattr(experiment, "tags", None)
    if _field(experiment, "experiment_id") != experiment_id or not isinstance(tags, Mapping):
        raise RuntimeError("Gateway archival experiment identity drifted")
    exact_tags = {str(key): str(value) for key, value in sorted(tags.items())}
    return {
        "experiment_id": experiment_id,
        "name": _field(experiment, "name"),
        "artifact_location": _field(experiment, "artifact_location"),
        "lifecycle_state": _field(experiment, "lifecycle_stage").lower(),
        "owner": str(exact_tags.get("mlflow.ownerEmail") or "").strip(),
        "tags": exact_tags,
    }
