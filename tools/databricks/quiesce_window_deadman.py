"""Arm and cancel the quiesce window's dead-man (M0-PR9, threat T5).

``arm_deadman`` runs inside ``quiesce_window open``, after the budget admission
and before the journal write and the stop:

1. **Publish** this run's staged release with ``app_release_source``:
   create-only uploads, an exact listing and the sha256 of every file. The
   window opens before step 5's publish, so the folder the dead-man runs from
   is published here, the same way and to the same path; step 5's own publish
   of that release then reuses every object and uploads nothing.
2. **Source.** The dead-man is that folder's ``jobs/window_deadman.py``
   (``jobs/`` is in the App's staged upload set, so it ships with every
   release and nothing is added to the App's runtime closure). The manifest
   must name it, the folder must be a governed ``app-src/<release id>``
   folder (never the bundle files root, never a mutable path), and its bytes
   are downloaded again and compared with the manifest right before the
   submit.
3. **Submit.** The budget is admitted again (the publish took time), then one
   ``jobs submit``: a one-time run as the submitting deployer (no ``run_as``
   and no ACL, so only the deployer and workspace admins can see or manage
   it, and the App has no permission on it), serverless like the bundle's
   jobs (environment version 5, ``databricks-sdk`` pinned to the deploy's
   own, the uv.lock pin), no retries, ``timeout_seconds`` = (deadline - now)
   + the start budget, and the idempotency token ``window_token(window
   sha256, lease id)``: one run per window, a retried submit returns it.

``cancel_deadman`` cancels the run once the run's journal is closed (cancel
only: nothing is deleted). It first reads the run and refuses one that is not
this App's dead-man. A cancel that fails never fails the deploy: the dead-man
reads the closed journal at its next poll and exits doing nothing.

**Why a marker, not a signature.** The window journal is signed with the
proof-signing key, which never leaves the deployer's process (deploy.sh hands
it only to a bounded child). Giving it to a job run would put it in run
parameters or a new secret scope. So the dead-man signs nothing: it writes an
unsigned, create-only marker beside the journal, and the next governed run
re-proves everything the marker claims against the signed journal and the App
before ``app_deployment_rollback repin --from-deadman`` re-pins it.

The submit and the cancel are the only one-time job-run sites
(``tools/databricks/stop_primitive_map.py`` ``JOB_RUN_SITES``), reached only
through ``quiesce_window open`` and ``cancel-deadman``, which only the window
library calls.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from importlib.metadata import version
from typing import Any

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks import app_release_contract as contract
from tools.databricks import quiesce_window_journal as journal
from tools.databricks.app_release_source import PublishPlan, PublishReport, upload_release
from tools.databricks.quiesce_window_plan import STEP_BUDGET_S

DEADMAN_RELPATH = "jobs/window_deadman.py"
TOKEN_DOMAIN = b"mip-window-deadman-v1\0"
TASK_KEY = "window_deadman"
ENVIRONMENT_KEY = "deadman"
# The bundle's serverless Python jobs use environment version 5 (databricks.yml).
ENVIRONMENT_VERSION = "5"
POLL_S = 60
# The start budget the run's timeout adds to the deadline: the dead-man's own
# wait for the start deployment (the window's start step) plus a slack for its
# last poll, the App reads and the marker write.
DEADMAN_START_WAIT_S = STEP_BUDGET_S["start"]
DEADMAN_SLACK_S = 300
DEADMAN_START_BUDGET_S = DEADMAN_START_WAIT_S + DEADMAN_SLACK_S
TERMINAL_RUN_STATES = frozenset({"TERMINATED", "SKIPPED", "INTERNAL_ERROR"})
_RUN_PREFIX = "mip-window-deadman-"


def window_token(window_sha256: str, lease_id: str) -> str:
    """One window's dead-man token: its plan digest and its lease (64 hex, the jobs cap)."""

    message = TOKEN_DOMAIN + window_sha256.encode("utf-8") + b"\0" + lease_id.encode("utf-8")
    return hashlib.sha256(message).hexdigest()


def marker_path(app_name: str, token: str) -> str:
    """The dead-man's marker beside the window journal, one per window."""

    if len(token) != 64 or any(character not in "0123456789abcdef" for character in token):
        raise ValueError("the dead-man token is not sha256 hex")
    return journal.journal_path(app_name).removesuffix(".json") + f".deadman-{token}.json"


def run_name(app_name: str, token: str) -> str:
    return f"{_RUN_PREFIX}{app_name}-{token[:12]}"


def _text(value: object) -> str:
    return "" if value is None else str(getattr(value, "value", value)).strip()


@dataclass(frozen=True)
class DeadmanRequest:
    """Everything one submit names: the run, its source, its parameters and its bounds."""

    app_name: str
    token: str
    source: str
    parameters: tuple[str, ...]
    timeout_seconds: int
    sdk_requirement: str

    def submit_kwargs(self) -> dict[str, Any]:
        from databricks.sdk.service.compute import Environment
        from databricks.sdk.service.jobs import JobEnvironment, Source, SparkPythonTask, SubmitTask

        return {
            "run_name": run_name(self.app_name, self.token),
            "idempotency_token": self.token,
            "timeout_seconds": self.timeout_seconds,
            "tasks": [
                SubmitTask(
                    task_key=TASK_KEY,
                    environment_key=ENVIRONMENT_KEY,
                    max_retries=0,
                    timeout_seconds=self.timeout_seconds,
                    spark_python_task=SparkPythonTask(
                        python_file=self.source,
                        parameters=list(self.parameters),
                        source=Source.WORKSPACE,
                    ),
                )
            ],
            "environments": [
                JobEnvironment(
                    environment_key=ENVIRONMENT_KEY,
                    spec=Environment(
                        environment_version=ENVIRONMENT_VERSION,
                        dependencies=[self.sdk_requirement],
                    ),
                )
            ],
        }


def deadman_source(report: PublishReport, plan: PublishPlan) -> str:
    """The dead-man's file in exactly the published, governed release folder."""

    release_id = contract.release_id_in(report.release_path)
    if report.release_path != plan.release_path or release_id != plan.manifest.release_id:
        raise RuntimeError("the published folder is not this run's release")
    if DEADMAN_RELPATH not in plan.manifest.by_path():
        raise RuntimeError("the release manifest does not carry the dead-man")
    return f"{report.release_path}/{DEADMAN_RELPATH}"


def verify_source(workspace: Any, source: str, plan: PublishPlan) -> None:
    """Re-read the dead-man's published bytes and compare them with the manifest."""

    entry = plan.manifest.by_path()[DEADMAN_RELPATH]
    stream = workspace.workspace.download(contract.api_path(source))
    data = stream.read()
    if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
        raise RuntimeError("the published dead-man is not the manifest's bytes")


def request(
    *,
    app_name: str,
    app_id: str,
    canonical: Mapping[str, Any],
    window_sha256: str,
    lease_id: str,
    source: str,
    deadline: int,
    now: float,
) -> DeadmanRequest:
    """The one submit a window arms; its timeout is (deadline - now) + the start budget."""

    token = window_token(window_sha256, lease_id)
    if deadline <= now or not app_id:
        raise RuntimeError("the dead-man needs a future deadline and the App's id")
    values = {
        "--app": app_name,
        "--app-id": app_id,
        "--journal": journal.journal_path(app_name),
        "--marker": marker_path(app_name, token),
        "--deadline": str(deadline),
        "--blue-restorable": "true" if canonical["blue_restorable"] is True else "false",
        "--token": token,
        "--window-sha256": window_sha256,
        "--blue-deployment-id": str(canonical["blue_deployment_id"]),
        "--restore-source": str(canonical["restore_source"]),
        "--start-timeout-seconds": str(DEADMAN_START_WAIT_S),
        "--poll-seconds": str(POLL_S),
    }
    return DeadmanRequest(
        app_name=app_name,
        token=token,
        source=source,
        parameters=tuple(item for pair in values.items() for item in pair),
        timeout_seconds=int(deadline - now) + DEADMAN_START_BUDGET_S,
        sdk_requirement=f"databricks-sdk=={version('databricks-sdk')}",
    )


def submit_deadman(workspace: Any, armed: DeadmanRequest) -> str:
    """The one-time run; returns its run id."""

    waiter = workspace.jobs.submit(**armed.submit_kwargs())
    run_id = _text(getattr(getattr(waiter, "response", None), "run_id", None))
    if not run_id.isdigit():
        raise RuntimeError("the dead-man submit returned no run id")
    return run_id


def arm_deadman(
    workspace: Any,
    *,
    app_name: str,
    app_id: str,
    canonical: Mapping[str, Any],
    window_sha256: str,
    lease_id: str,
    release: PublishPlan,
    deadline: int,
    admit: Callable[[], object],
    clock: Callable[[], float] = time.time,
    publish: Callable[[Any, PublishPlan], PublishReport] = upload_release,
) -> str:
    """Publish the release, verify the dead-man's bytes, re-admit, submit; the run id."""

    report = publish(workspace, release)
    source = deadman_source(report, release)
    verify_source(workspace, source, release)
    admit()
    armed = request(
        app_name=app_name,
        app_id=app_id,
        canonical=canonical,
        window_sha256=window_sha256,
        lease_id=lease_id,
        source=source,
        deadline=deadline,
        now=clock(),
    )
    return submit_deadman(workspace, armed)


def arm_for_release(
    workspace: Any,
    *,
    app_name: str,
    app_id: str,
    canonical: Mapping[str, Any],
    window_sha256: str,
    lease_id: str,
    deadline: object,
    release: Mapping[str, Any],
    admit: Callable[[], object],
) -> str:
    """``open``'s arm: every local publish check first, then ``arm_deadman``."""

    from tools.databricks.app_release_source import prepare_publish
    from tools.databricks.quiesce_window_budget import parse_deadline

    epoch = parse_deadline(deadline)
    if epoch is None:
        raise RuntimeError("the dead-man needs the job deadline")
    plan = prepare_publish(
        files_root=contract.files_root(
            principal=release["principal"],
            target=release["target"],
            root_leaf=release["root_leaf"],
        ),
        **release,
    )
    return arm_deadman(
        workspace,
        app_name=app_name,
        app_id=app_id,
        canonical=canonical,
        window_sha256=window_sha256,
        lease_id=lease_id,
        release=plan,
        deadline=epoch,
        admit=admit,
    )


def _is_this_apps_deadman(run: object, app_name: str) -> bool:
    name = _text(getattr(run, "run_name", None))
    tasks = list(getattr(run, "tasks", None) or ())
    if not name.startswith(f"{_RUN_PREFIX}{app_name}-") or len(tasks) != 1:
        return False
    python_file = _text(getattr(getattr(tasks[0], "spark_python_task", None), "python_file", None))
    folder = python_file.removesuffix(f"/{DEADMAN_RELPATH}")
    try:
        contract.release_id_in(folder)
    except contract.ReleaseContractError:
        return False
    return folder != python_file


def cancel_deadman(workspace: Any, *, app_name: str, run_id: str) -> str:
    """Cancel this App's dead-man run: ``cancelled`` or ``already_terminated``."""

    if not run_id.isdigit():
        raise RuntimeError("the dead-man run id is not a run id")
    run = workspace.jobs.get_run(int(run_id))
    if not _is_this_apps_deadman(run, app_name):
        raise RuntimeError("the run is not this App's dead-man; nothing was cancelled")
    state = _text(getattr(getattr(run, "state", None), "life_cycle_state", None)).split(".")[-1]
    if state.upper() in TERMINAL_RUN_STATES:
        return "already_terminated"
    workspace.jobs.cancel_run(int(run_id))
    return "cancelled"


def read_marker(workspace: Any, *, app_name: str, token: str) -> dict[str, Any] | None:
    """The dead-man's marker for one window, or None: a pointer, never proof."""

    try:
        stream = workspace.workspace.download(marker_path(app_name, token))
    except (NotFound, ResourceDoesNotExist):
        return None
    value = json.loads(stream.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("the dead-man marker is not an object")
    return value
