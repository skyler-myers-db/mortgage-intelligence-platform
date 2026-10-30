"""The deployment ``apps start`` creates: start, wait, prove (attack O1, M0-PR8).

``apps start`` does not resume the stopped deployment. The SDK pinned in
``uv.lock`` (databricks-sdk 0.103.0, ``databricks/sdk/service/apps.py``)
documents ``AppsAPI.start`` as "Start the last active deployment of the app"
and ``App.default_source_code_path`` as "the workspace source code path of the
last active deployment", and ``start`` returns the App (``Wait`` over
``wait_get_app_active``, compute ACTIVE only, never a deployment state). The
Databricks CLI v1.10.0 ``apps start NAME`` waits for compute ACTIVE unless
``--no-wait``. A governed run's log (``run_30417008206``) showed the start
creating a ``pending_deployment`` with a fresh ``deployment_id`` in SNAPSHOT
mode. Spike S7 (a throwaway App) is still owed for the rest: whether the env
config carries over, and the bookkeeping of a start on a FAILED-active App.

Until then this module assumes the worst case and proves everything:

* ``start_app`` starts without waiting and returns the id of the deployment
  the start created: the start response's ``pending_deployment``, else the
  App's ``pending_deployment`` read right after. No readable id refuses; a
  changed ``active_deployment`` is never taken as that id.
* ``wait_for_terminal`` polls ``get_deployment`` for that id until SUCCEEDED,
  FAILED or CANCELLED. It never reads the active deployment.
* ``prove_start_deployment`` requires SUCCEEDED, the source path blue's own
  deployment named, a ``/src/`` snapshot whose full-tree digest equals the
  expected one, and the exact env-name set of the signed record's payload.

Every refusal is a ``StartProofError`` with a reviewed code, and no message
names a path, an id or a value.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from tools.databricks.app_deployment_state import deployment_state, immutable_source
from tools.databricks.app_release_contract import api_path
from tools.databricks.app_release_retry import RATE_LIMIT_RETRY, RateLimitRetry
from tools.databricks.app_release_source import remote_files
from tools.databricks.app_rollback_record_contract import _text

TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
START_WAIT_TIMEOUT_S = 20 * 60.0
START_POLL_INTERVAL_S = 10.0
TREE_DIGEST_WORKERS = 4
_TREE_DOMAIN = "mip-app-source-tree-v1"
_CHUNK = 1 << 20

PROOF_CODES = frozenset(
    {
        "start_unreadable",
        "start_timeout",
        "start_not_succeeded",
        "start_source_path",
        "start_tree_digest",
        "start_folder_digest",
        "start_env_names",
    }
)


class StartProofError(RuntimeError):
    """The deployment a start created is not provably blue."""

    def __init__(self, code: str, message: str) -> None:
        if code not in PROOF_CODES:
            raise ValueError("start proof refusal code is not reviewed")
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class StartProof:
    """What a proven start deployment is: blue's bytes, source and env names."""

    deployment_id: str
    source_code_path: str
    artifact: str
    tree_sha256: str
    env_names: frozenset[str]


def _state(app: object) -> str:
    raw = getattr(getattr(app, "compute_status", None), "state", "")
    return _text(raw).split(".")[-1].upper()


def _pending_id(app: object) -> str:
    return _text(getattr(getattr(app, "pending_deployment", None), "deployment_id", None))


def start_app(workspace: Any, *, app_name: str) -> str:
    """Start the App without waiting and return the id of the deployment it created."""

    waiter = workspace.apps.start(app_name)
    created = _pending_id(getattr(waiter, "response", None))
    if not created:
        created = _pending_id(workspace.apps.get(app_name))
    if not created:
        raise StartProofError(
            "start_unreadable",
            "the start returned no pending deployment id, and the App shows none",
        )
    return created


def wait_for_terminal(
    workspace: Any,
    *,
    app_name: str,
    deployment_id: str,
    timeout_s: float = START_WAIT_TIMEOUT_S,
    interval_s: float = START_POLL_INTERVAL_S,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> object:
    """Poll that one deployment until it is terminal; return it."""

    sleep, clock = sleep or time.sleep, clock or time.monotonic
    deadline = clock() + timeout_s
    while True:
        deployment = workspace.apps.get_deployment(app_name, deployment_id)
        if _text(getattr(deployment, "deployment_id", None)) != deployment_id:
            raise StartProofError("start_unreadable", "get_deployment returned another deployment")
        if deployment_state(deployment) in TERMINAL_STATES:
            return deployment
        if clock() >= deadline:
            raise StartProofError(
                "start_timeout", "the start deployment never reached a terminal state"
            )
        sleep(interval_s)


def wait_for_compute_active(
    workspace: Any,
    *,
    app_name: str,
    timeout_s: float = START_WAIT_TIMEOUT_S,
    interval_s: float = START_POLL_INTERVAL_S,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> None:
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    deadline = clock() + timeout_s
    while _state(workspace.apps.get(app_name)) != "ACTIVE":
        if clock() >= deadline:
            raise StartProofError("start_timeout", "the App compute never became ACTIVE")
        sleep(interval_s)


def is_stopped(workspace: Any, *, app_name: str) -> bool:
    """True once the App is STOPPED (a STOPPING App is waited out first)."""

    state = _state(workspace.apps.get(app_name))
    if state == "STOPPING":
        workspace.apps.wait_get_app_stopped(
            app_name, timeout=timedelta(seconds=START_WAIT_TIMEOUT_S)
        )
        state = "STOPPED"
    return state == "STOPPED"


def ensure_started(workspace: Any, *, app_name: str) -> str:
    """Start a stopped App; return the deployment the start created ("" if it ran).

    A start creates a new deployment from the App's default source (attack O1),
    so this waits for exactly that deployment to be terminal, never for "active
    changed". The caller decides what that deployment may become: re-pinned
    (tier a) or replaced by a redeploy of the signed snapshot (tier b).
    """

    if not is_stopped(workspace, app_name=app_name):
        return ""
    started = start_app(workspace, app_name=app_name)
    wait_for_terminal(workspace, app_name=app_name, deployment_id=started)
    return started


def env_names(deployment: object) -> frozenset[str]:
    """The env var names a deployment (or a payload dict) carries."""

    raw = (
        deployment.get("env_vars")
        if isinstance(deployment, dict)
        else getattr(deployment, "env_vars", None)
    )
    names = []
    for item in raw or []:
        name = item.get("name") if isinstance(item, dict) else getattr(item, "name", None)
        names.append(_text(name))
    if any(not name for name in names) or len(names) != len(set(names)):
        raise StartProofError("start_env_names", "an env var name is empty or repeated")
    return frozenset(names)


def env_names_digest(names: Iterable[str]) -> str:
    canonical = json.dumps(sorted(names), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(b"mip-app-env-names-v1\0" + canonical).hexdigest()


def tree_digest(
    workspace: Any,
    workspace_path: str,
    *,
    workers: int = TREE_DIGEST_WORKERS,
    retry: RateLimitRetry = RATE_LIMIT_RETRY,
) -> str:
    """sha256 over every file below a ``/Workspace/...`` folder: path, size and bytes."""

    root = api_path(workspace_path)
    relpaths = sorted(remote_files(workspace, root, retry=retry))

    def digest_one(relpath: str) -> tuple[str, int, str]:
        def read() -> bytes:
            stream = workspace.workspace.download(f"{root}/{relpath}")
            chunks = []
            while chunk := stream.read(_CHUNK):
                chunks.append(chunk)
            return b"".join(chunks)

        data = retry.call(f"download of {root}/{relpath}", read)
        return relpath, len(data), hashlib.sha256(data).hexdigest()

    pool = ThreadPoolExecutor(max_workers=max(1, workers))
    try:
        entries = list(pool.map(digest_one, relpaths))
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
    canonical = json.dumps(entries, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(_TREE_DOMAIN.encode("ascii") + b"\0" + canonical).hexdigest()


def last_deployment_is(workspace: Any, *, app_name: str, deployment_id: str) -> bool:
    """True when ``deployment_id`` is the App's last active deployment (tier a).

    Active, the newest SUCCEEDED, nothing pending, and the App's default source
    (when the API returns one) is that deployment's own source: the source a
    start snapshots. A FAILED later attempt never became active, so it never
    moved the default source; the post-start proofs catch any other drift.
    """

    app = workspace.apps.get(app_name)
    if _pending_id(app):
        return False
    active = _text(getattr(getattr(app, "active_deployment", None), "deployment_id", None))
    succeeded = [
        item
        for item in workspace.apps.list_deployments(app_name)
        if deployment_state(item) == "SUCCEEDED" and _text(getattr(item, "deployment_id", None))
    ]
    succeeded.sort(
        key=lambda item: (
            _text(getattr(item, "create_time", None)),
            _text(getattr(item, "update_time", None)),
            _text(getattr(item, "deployment_id", None)),
        )
    )
    if active != deployment_id or not succeeded:
        return False
    if _text(getattr(succeeded[-1], "deployment_id", None)) != deployment_id:
        return False
    default_source = _text(getattr(app, "default_source_code_path", None))
    if default_source:
        blue = workspace.apps.get_deployment(app_name, deployment_id)
        return default_source == _text(getattr(blue, "source_code_path", None))
    return True


def prove_start_deployment(
    workspace: Any,
    *,
    app_name: str,
    deployment: object,
    restore_source: str,
    expected_tree_sha256: str,
    expected_env_names: frozenset[str],
    digest: Callable[[Any, str], str] = tree_digest,
) -> StartProof:
    """Prove the start deployment is blue: state, source, bytes and env names."""

    deployment_id = _text(getattr(deployment, "deployment_id", None))
    if deployment_state(deployment) != "SUCCEEDED":
        raise StartProofError("start_not_succeeded", "the start deployment did not succeed")
    source = _text(getattr(deployment, "source_code_path", None))
    if not restore_source or source != restore_source:
        raise StartProofError(
            "start_source_path", "the start snapshotted another source than blue's restore source"
        )
    try:
        artifact = immutable_source(deployment)
    except RuntimeError as exc:
        raise StartProofError(
            "start_tree_digest", "the start deployment has no /src/ snapshot"
        ) from exc
    if digest(workspace, artifact) != expected_tree_sha256:
        raise StartProofError(
            "start_tree_digest", "the start deployment's snapshot is not blue's source tree"
        )
    names = env_names(workspace.apps.get_deployment(app_name, deployment_id))
    if names != expected_env_names:
        raise StartProofError(
            "start_env_names", "the start deployment's env-name set is not the signed record's"
        )
    return StartProof(
        deployment_id=deployment_id,
        source_code_path=source,
        artifact=artifact,
        tree_sha256=expected_tree_sha256,
        env_names=names,
    )
