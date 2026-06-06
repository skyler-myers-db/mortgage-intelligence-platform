"""Admin-scoped Databricks job controls.

The app exposes a small operator surface for refreshing Module 0 data without
requiring a low-tech customer operator to run `databricks bundle run` locally.
Only bundle-declared jobs are addressable here; arbitrary job IDs or names are
never accepted from the browser.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from backend.config.settings import settings
from backend.services.observability import emit

log = logging.getLogger(__name__)

ManagedJobKey = Literal[
    "fred_rates",
    "silver_refresh",
    "gold_refresh",
    "lifecycle_sync",
]

ACTIVE_LIFECYCLE_STATES = {"PENDING", "QUEUED", "RUNNING", "BLOCKED", "TERMINATING"}


class JobOperationError(RuntimeError):
    """Raised when Databricks Jobs cannot be resolved or triggered."""


class JobAlreadyRunningError(JobOperationError):
    """Raised when an operator tries to launch a job that is already active."""

    def __init__(self, job_key: str, run_id: int | None) -> None:
        super().__init__(f"{job_key} already has an active run")
        self.job_key = job_key
        self.run_id = run_id


@dataclass(frozen=True)
class ManagedJobDefinition:
    key: ManagedJobKey
    label: str
    job_name: str
    env_var: str
    description: str
    run_order: int


@dataclass(frozen=True)
class ManagedJobRun:
    run_id: int | None
    life_cycle_state: str | None
    result_state: str | None
    state_message: str | None
    started_at: str | None
    ended_at: str | None
    run_page_url: str | None

    @property
    def active(self) -> bool:
        return (self.life_cycle_state or "").upper() in ACTIVE_LIFECYCLE_STATES


@dataclass(frozen=True)
class ManagedJobStatus:
    key: ManagedJobKey
    label: str
    job_name: str
    job_id: int | None
    configured: bool
    description: str
    run_order: int
    latest_run: ManagedJobRun | None = None
    recent_runs: list[ManagedJobRun] = field(default_factory=list)


@dataclass(frozen=True)
class JobLaunch:
    key: ManagedJobKey
    label: str
    job_name: str
    job_id: int
    run_id: int | None
    run_page_url: str | None


MANAGED_JOBS: dict[ManagedJobKey, ManagedJobDefinition] = {
    "fred_rates": ManagedJobDefinition(
        key="fred_rates",
        label="Refresh market rates",
        job_name="mip_fred_rates_ingest",
        env_var="MIP_FRED_RATES_JOB_ID",
        description="Pull the latest FRED MORTGAGE30US rate into silver.market_rates_weekly.",
        run_order=1,
    ),
    "silver_refresh": ManagedJobDefinition(
        key="silver_refresh",
        label="Refresh source features",
        job_name="mip_refresh_silver",
        env_var="MIP_SILVER_REFRESH_JOB_ID",
        description="Rebuild silver feature tables from governed Cotality and first-party inputs.",
        run_order=2,
    ),
    "gold_refresh": ManagedJobDefinition(
        key="gold_refresh",
        label="Rebuild scoring snapshot",
        job_name="mip_refresh_scores",
        env_var="MIP_GOLD_REFRESH_JOB_ID",
        description="Rebuild borrower_360, lead scores, segment populations, source readiness, and semantic views.",
        run_order=3,
    ),
    "lifecycle_sync": ManagedJobDefinition(
        key="lifecycle_sync",
        label="Sync workflow state",
        job_name="mip_sync_lifecycle_state",
        env_var="MIP_LIFECYCLE_SYNC_JOB_ID",
        description="Mirror Lakebase approvals and outreach state into gold lifecycle tables.",
        run_order=4,
    ),
}


def _enum_value(value: Any) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    text = str(raw)
    return text.rsplit(".", 1)[-1].upper()


def _ms_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat()


def _job_id_from_env(definition: ManagedJobDefinition) -> int | None:
    raw = os.environ.get(definition.env_var)
    if raw is None or not raw.strip():
        return None
    try:
        job_id = int(raw.strip())
    except ValueError:
        emit(
            log,
            "databricks_job_config_invalid",
            level=logging.WARNING,
            job_key=definition.key,
            job_name=definition.job_name,
            env_var=definition.env_var,
        )
        return None
    return job_id if job_id > 0 else None


def _job_name_matches(candidate: str | None, expected: str) -> bool:
    if not candidate:
        return False
    return candidate == expected or candidate.endswith(expected)


def _allow_name_lookup_fallback() -> bool:
    """Only local/test operators may resolve jobs by listing workspace jobs.

    Deployed Databricks Apps bind exact job IDs as resources. If those env vars
    are missing in the app, falling back to `jobs.list()` would require broader
    workspace permissions than the product needs.
    """

    return settings.app_env == "local"


def _safe_attr(value: Any, name: str) -> Any | None:
    try:
        return getattr(value, name, None)
    except (AttributeError, KeyError):
        return None


def _safe_bind_value(value: Any, name: str) -> Any | None:
    bind = _safe_attr(value, "bind")
    if not callable(bind):
        return None
    try:
        bound = bind()
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(bound, dict):
        return None
    return bound.get(name)


def _workspace_host(workspace: Any) -> str | None:
    configured = (settings.databricks_host or "").strip()
    candidates = [
        configured,
        _safe_attr(_safe_attr(workspace, "_config"), "host"),
        _safe_attr(_safe_attr(workspace, "config"), "host"),
        _safe_attr(_safe_attr(_safe_attr(workspace, "_api"), "_cfg"), "host"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        host = str(candidate).strip()
        if not host:
            continue
        if not host.startswith("http"):
            host = f"https://{host}"
        return host.rstrip("/")
    return None


def _workspace_org_id(workspace: Any) -> str | None:
    candidates = [
        _safe_attr(_safe_attr(workspace, "_config"), "workspace_id"),
        _safe_attr(_safe_attr(workspace, "config"), "workspace_id"),
        _safe_attr(_safe_attr(_safe_attr(workspace, "_api"), "_cfg"), "workspace_id"),
    ]
    for candidate in candidates:
        if candidate:
            return str(candidate).strip() or None
    return None


def _run_page_url(workspace: Any, *, job_id: int, run_id: int | None) -> str | None:
    if run_id is None:
        return None
    host = _workspace_host(workspace)
    if not host:
        return None
    org_id = _workspace_org_id(workspace)
    org_query = f"?o={org_id}" if org_id else ""
    return f"{host}/{org_query}#job/{job_id}/run/{run_id}"


class DatabricksJobOperations:
    """Resolve and trigger the small allowlisted set of Module 0 jobs."""

    def __init__(self, workspace: Any | None = None) -> None:
        self._workspace = workspace

    def list_statuses(self) -> list[ManagedJobStatus]:
        workspace = self._client()
        return [self.status_for(key, workspace=workspace) for key in sorted(MANAGED_JOBS, key=lambda k: MANAGED_JOBS[k].run_order)]

    def status_for(self, key: ManagedJobKey, *, workspace: Any | None = None) -> ManagedJobStatus:
        definition = MANAGED_JOBS[key]
        ws = workspace or self._client()
        job_id = self._resolve_job_id(ws, definition)
        recent_runs = self._recent_runs(ws, job_id, limit=5) if job_id is not None else []
        latest_run = recent_runs[0] if recent_runs else None
        return ManagedJobStatus(
            key=definition.key,
            label=definition.label,
            job_name=definition.job_name,
            job_id=job_id,
            configured=job_id is not None,
            description=definition.description,
            run_order=definition.run_order,
            latest_run=latest_run,
            recent_runs=recent_runs,
        )

    def run_now(self, key: ManagedJobKey) -> JobLaunch:
        definition = MANAGED_JOBS[key]
        workspace = self._client()
        job_id = self._resolve_job_id(workspace, definition)
        if job_id is None:
            raise JobOperationError(f"{definition.job_name} is not configured")

        active_run = self._active_run(workspace, job_id)
        if active_run is not None:
            raise JobAlreadyRunningError(definition.key, active_run.run_id)

        try:
            run = workspace.jobs.run_now(job_id=job_id)
        except Exception as exc:  # noqa: BLE001
            emit(
                log,
                "databricks_job_run_error",
                level=logging.WARNING,
                job_key=definition.key,
                job_name=definition.job_name,
                job_id=job_id,
                exc_type=type(exc).__name__,
            )
            raise JobOperationError(f"{definition.job_name} run_now failed") from exc

        run_id = _int_or_none(_safe_attr(run, "run_id") or _safe_bind_value(run, "run_id"))
        run_page_url = _safe_attr(run, "run_page_url") or _run_page_url(
            workspace,
            job_id=job_id,
            run_id=run_id,
        )
        emit(
            log,
            "databricks_job_run_started",
            job_key=definition.key,
            job_name=definition.job_name,
            job_id=job_id,
            run_id=run_id,
        )
        return JobLaunch(
            key=definition.key,
            label=definition.label,
            job_name=definition.job_name,
            job_id=job_id,
            run_id=run_id,
            run_page_url=run_page_url,
        )

    def _client(self) -> Any:
        if self._workspace is not None:
            return self._workspace
        try:
            from databricks.sdk import WorkspaceClient
        except Exception as exc:  # noqa: BLE001
            raise JobOperationError("databricks sdk unavailable") from exc
        return WorkspaceClient()

    def _resolve_job_id(self, workspace: Any, definition: ManagedJobDefinition) -> int | None:
        configured = _job_id_from_env(definition)
        if configured is not None:
            return configured
        if not _allow_name_lookup_fallback():
            return None
        try:
            results = list(workspace.jobs.list(name=definition.job_name))
        except Exception:
            try:
                results = list(workspace.jobs.list())
            except Exception as exc:  # noqa: BLE001
                emit(
                    log,
                    "databricks_job_resolve_error",
                    level=logging.WARNING,
                    job_key=definition.key,
                    job_name=definition.job_name,
                    exc_type=type(exc).__name__,
                )
                raise JobOperationError(f"{definition.job_name} lookup failed") from exc
        for job in results:
            settings = getattr(job, "settings", None)
            name = getattr(settings, "name", None) if settings is not None else None
            if _job_name_matches(name, definition.job_name):
                return _int_or_none(getattr(job, "job_id", None))
        return None

    def _latest_run(self, workspace: Any, job_id: int) -> ManagedJobRun | None:
        recent = self._recent_runs(workspace, job_id, limit=1)
        return recent[0] if recent else None

    def _recent_runs(self, workspace: Any, job_id: int, *, limit: int) -> list[ManagedJobRun]:
        try:
            runs = list(workspace.jobs.list_runs(job_id=job_id, limit=limit))
        except Exception as exc:  # noqa: BLE001
            emit(
                log,
                "databricks_job_recent_runs_error",
                level=logging.WARNING,
                job_id=job_id,
                exc_type=type(exc).__name__,
            )
            raise JobOperationError("recent job run lookup failed") from exc
        return [_run_from_sdk(run) for run in runs]

    def _active_run(self, workspace: Any, job_id: int) -> ManagedJobRun | None:
        try:
            runs = list(workspace.jobs.list_runs(job_id=job_id, active_only=True, limit=1))
        except TypeError:
            runs = [
                run for run in list(workspace.jobs.list_runs(job_id=job_id, limit=10))
                if _run_from_sdk(run).active
            ][:1]
        except Exception as exc:  # noqa: BLE001
            emit(
                log,
                "databricks_job_active_run_error",
                level=logging.WARNING,
                job_id=job_id,
                exc_type=type(exc).__name__,
            )
            raise JobOperationError("active job run lookup failed") from exc
        if not runs:
            return None
        run = _run_from_sdk(runs[0])
        return run if run.active else None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _run_from_sdk(run: Any) -> ManagedJobRun:
    state = _safe_attr(run, "state")
    return ManagedJobRun(
        run_id=_int_or_none(_safe_attr(run, "run_id")),
        life_cycle_state=_enum_value(_safe_attr(state, "life_cycle_state")),
        result_state=_enum_value(_safe_attr(state, "result_state")),
        state_message=None,
        started_at=_ms_to_iso(_safe_attr(run, "start_time")),
        ended_at=_ms_to_iso(_safe_attr(run, "end_time")),
        run_page_url=_safe_attr(run, "run_page_url"),
    )


def get_job_operations() -> DatabricksJobOperations:
    return DatabricksJobOperations()


__all__ = [
    "DatabricksJobOperations",
    "JobAlreadyRunningError",
    "JobLaunch",
    "JobOperationError",
    "ManagedJobKey",
    "ManagedJobRun",
    "ManagedJobStatus",
    "get_job_operations",
]
