"""The deployment ``apps start`` creates: start, find, wait, prove (attack O1, M0-PR8).

``apps start`` does not resume the stopped deployment. Spike S7 (a throwaway
App, 2026-11-06 and 2026-11-10, CLI v1.10.0, the SDK pinned in ``uv.lock``)
measured what it does instead:

* every start creates a NEW deployment (SNAPSHOT mode) whose ``/src/``
  snapshot is the CURRENT bytes of the App's ``default_source_code_path``,
  with exactly the env-name set of the App's newest deployment;
* neither the start response nor an ``apps.get`` right after it shows the
  new deployment, as ``pending_deployment`` or as ``active_deployment``: the
  compute is STARTING. It turns ACTIVE after 96-108 s (three samples, a
  trivial App); only then does the new id appear, in ``pending_deployment``
  for about 1 s, then as ``active_deployment`` IN_PROGRESS, then SUCCEEDED or
  FAILED (3-6 s later for the trivial App; the real App installs and serves a
  whole tree, so its own deploy time comes on top). ``list_deployments``
  shows it from the same moment;
* a STOPPED App's GET omits ``active_deployment`` entirely;
* a FAILED deployment does become active and does move
  ``default_source_code_path`` to its folder, so a start on a FAILED-active
  App reruns the failed folder and env set: the newest deployment in ANY
  state decides what a start runs, never the newest SUCCEEDED one;
* ``default_source_code_path`` is not settable (create-update rejects the
  mask, update ignores the field); only a deployment moves it.

So this module proves everything from what the API shows:

* ``start_app`` lists the App's deployment ids BEFORE the start (the
  baseline; the window journals it so a resume finds the same start), starts
  without waiting and polls, at most every 5 s within
  ``START_WAIT_TIMEOUT_S``, until exactly ONE id outside the baseline shows
  in ``pending_deployment``, ``active_deployment`` or ``list_deployments``.
  Two refuse (``start_ambiguous``), none by the deadline refuses
  (``start_timeout``); an id in the baseline, an active deployment that
  changed to an older one included, is never the start's.
* ``wait_for_terminal`` polls ``get_deployment`` for that id until SUCCEEDED,
  FAILED or CANCELLED. It never reads the active deployment.
* ``prove_start_deployment`` requires SUCCEEDED, the source path blue's own
  deployment named, a ``/src/`` snapshot whose full-tree digest equals the
  expected one, and the exact env-name set of the signed record's payload.
* ``last_deployment_is`` (tier a) judges a stopped App by its newest
  deployment of any state and its default source, since its GET has no
  active deployment.
* ``start_reruns_blue`` (tier a, before any start) re-hashes that folder
  against blue's signed digest and checks blue's env-name set: a start
  snapshots the folder's CURRENT bytes, so anyone editing it silently
  changes what a start runs, and tier a never starts bytes it already
  knows are not blue. (Tier b over a stopped App still starts it first,
  since Databricks deploys only to a running App: that start runs the
  folder's current bytes until blue's immutable copy replaces them.)

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

from databricks.sdk.errors import NotFound, ResourceDoesNotExist
from tools.databricks.app_deployment_state import (
    active_deployment_id,
    deployment_state,
    immutable_source,
    newest_deployment,
)
from tools.databricks.app_release_contract import ReleaseContractError, api_path
from tools.databricks.app_release_retry import RATE_LIMIT_RETRY, RateLimitRetry
from tools.databricks.app_release_source import remote_files
from tools.databricks.app_rollback_record_contract import _text

TERMINAL_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
START_WAIT_TIMEOUT_S = 20 * 60.0
START_POLL_INTERVAL_S = 10.0
# The start's new id shows in pending_deployment for about 1 s (spike S7), and
# in active_deployment and list_deployments after it: poll at most every 5 s.
START_ID_POLL_INTERVAL_S = 5.0
TREE_DIGEST_WORKERS = 4
_TREE_DOMAIN = "mip-app-source-tree-v1"
_CHUNK = 1 << 20

PROOF_CODES = frozenset(
    {
        "start_unreadable",
        "start_ambiguous",
        "start_timeout",
        "start_not_succeeded",
        "start_source_path",
        "start_tree_digest",
        "start_folder_digest",
        "start_env_names",
        "start_record_changed",
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


def _active_id(app: object) -> str:
    return _text(getattr(getattr(app, "active_deployment", None), "deployment_id", None))


def deployment_ids(workspace: Any, *, app_name: str) -> frozenset[str]:
    """Every deployment id the App lists: the baseline a start's new id is found against."""

    ids = [_text(getattr(item, "deployment_id", None)) for item in workspace.apps.list_deployments(app_name)]
    if any(not deployment_id for deployment_id in ids):
        raise StartProofError("start_unreadable", "a listed deployment has no immutable id")
    return frozenset(ids)


def find_start_deployment(
    workspace: Any,
    *,
    app_name: str,
    baseline: frozenset[str],
    resumed: bool = False,
    timeout_s: float = START_WAIT_TIMEOUT_S,
    interval_s: float = START_ID_POLL_INTERVAL_S,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> str:
    """The one deployment id outside ``baseline`` the App shows, polled until the deadline.

    A start's id shows only once the compute is ACTIVE, about 100 s after the
    start (spike S7), in ``pending_deployment``, ``active_deployment`` or
    ``list_deployments``. Two new ids refuse (``start_ambiguous``), none by the
    deadline refuses (``start_timeout``). A resumed start whose App is still
    STOPPED with nothing new never started: it refuses at once, never waits.
    """

    if not baseline:
        raise StartProofError("start_unreadable", "there is no deployment baseline to find the start against")
    sleep, clock = sleep or time.sleep, clock or time.monotonic
    deadline = clock() + timeout_s
    while True:
        app = workspace.apps.get(app_name)
        shown = {_pending_id(app), _active_id(app)} | deployment_ids(workspace, app_name=app_name)
        created = sorted(shown - baseline - {""})
        if len(created) > 1:
            raise StartProofError("start_ambiguous", "more than one new deployment appeared after the start")
        if created:
            return created[0]
        if resumed and _state(app) == "STOPPED":
            raise StartProofError(
                "start_unreadable", "the journaled start never ran: the App is stopped with no new deployment"
            )
        if clock() >= deadline:
            raise StartProofError("start_timeout", "no new deployment appeared after the start")
        sleep(min(interval_s, START_ID_POLL_INTERVAL_S))


def start_app(
    workspace: Any, *, app_name: str, baseline: frozenset[str] | None = None, **poll: Any
) -> str:
    """Start the App without waiting and return the id of the deployment it created.

    ``baseline`` is the App's deployment ids read before the start (the window
    journals it first); without one it is read here.
    """

    before = deployment_ids(workspace, app_name=app_name) if baseline is None else baseline
    if not before:
        raise StartProofError("start_unreadable", "the App lists no deployment before the start")
    workspace.apps.start(app_name)  # the Wait is never awaited: compute ACTIVE is no deployment
    return find_start_deployment(workspace, app_name=app_name, baseline=before, **poll)


def resume_start(workspace: Any, *, app_name: str, baseline: frozenset[str], **poll: Any) -> str:
    """The deployment an earlier, unjournaled start created, found against its journaled baseline.

    Never a new start: the journal says a start may have run, and a second one
    would hide the first. No baseline, an App still stopped with nothing new,
    two new ids or none by the deadline refuse (the caller stops the App and
    journals the refusal).
    """

    return find_start_deployment(workspace, app_name=app_name, baseline=baseline, resumed=True, **poll)


def current_deployment_id(workspace: Any, *, app_name: str) -> str:
    """The deployment a running App serves, or the one a STOPPED App's start reruns (spike S7).

    A stopped App's GET has no active deployment: its newest deployment of any
    state, nothing pending, is the one whose folder and env set a start
    snapshots. Any other state reads the active deployment, fail-closed.
    """

    app = workspace.apps.get(app_name)
    if _state(app) != "STOPPED":
        return active_deployment_id(workspace, app_name=app_name)
    if getattr(app, "pending_deployment", None) is not None:
        raise RuntimeError("App has a pending deployment; rollback identity is not stable")
    newest = newest_deployment(workspace.apps.list_deployments(app_name))
    if newest is None:
        raise RuntimeError("the stopped App has no deployment to bind")
    return _text(getattr(newest, "deployment_id", None))


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
    return digest_entries(entries)


def digest_entries(entries: Iterable[tuple[str, int, str]]) -> str:
    """The full-tree digest of ``(relpath, size, sha256)`` rows, in relpath order."""

    canonical = json.dumps([list(row) for row in sorted(entries)], separators=(",", ":"))
    return hashlib.sha256(_TREE_DOMAIN.encode("ascii") + b"\0" + canonical.encode("utf-8")).hexdigest()


def last_deployment_is(workspace: Any, *, app_name: str, deployment_id: str) -> bool:
    """True when ``deployment_id`` is what the App runs and what its next start reruns (tier a).

    Nothing pending, and the App's newest deployment of ANY state is that one
    and SUCCEEDED: a FAILED later deployment becomes active and moves the
    default source, so a start would rerun it (spike S7). A running App must
    serve it as its active deployment, and its default source (when returned)
    must be that deployment's own source. A STOPPED App's GET has no active
    deployment, so there the default source must equal it, always: the folder
    a start snapshots. The post-start proofs catch any byte drift in it.
    """

    app = workspace.apps.get(app_name)
    if getattr(app, "pending_deployment", None) is not None or _pending_id(app):
        return False
    stopped = _state(app) == "STOPPED"
    active = _active_id(app)
    if active != deployment_id and (not stopped or active):
        return False
    try:
        newest = newest_deployment(workspace.apps.list_deployments(app_name))
    except RuntimeError:
        return False
    if (
        newest is None
        or _text(getattr(newest, "deployment_id", None)) != deployment_id
        or deployment_state(newest) != "SUCCEEDED"
    ):
        return False
    default_source = _text(getattr(app, "default_source_code_path", None))
    if not default_source and not stopped:
        return True
    blue_source = _text(getattr(workspace.apps.get_deployment(app_name, deployment_id), "source_code_path", None))
    return bool(blue_source) and default_source == blue_source


def start_reruns_blue(
    workspace: Any,
    *,
    app_name: str,
    deployment_id: str,
    tree_sha256: str,
    names: frozenset[str],
    folder: str | None = None,
    digest: Callable[[Any, str], str] = tree_digest,
) -> bool:
    """True when a start of the stopped App would snapshot exactly blue: its bytes and env set.

    Tier a's proof before any start (spike S7). ``last_deployment_is`` proved
    the folder a start snapshots is blue's deployment's own and that blue is
    the newest deployment, whose env-name set a start carries. This re-hashes
    that folder against blue's signed full-tree digest and checks blue's env
    names, and (``folder``) that it is the folder the signed record names. A
    folder gone or unreadable, another folder, another env set or other bytes
    is False: the caller takes tier b and never starts it.
    """

    blue = workspace.apps.get_deployment(app_name, deployment_id)
    source = _text(getattr(blue, "source_code_path", None))
    if not source or not tree_sha256 or (folder is not None and source != folder):
        return False
    try:
        if env_names(blue) != names:
            return False
        return digest(workspace, source) == tree_sha256
    except (StartProofError, NotFound, ResourceDoesNotExist, ReleaseContractError):
        return False


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
