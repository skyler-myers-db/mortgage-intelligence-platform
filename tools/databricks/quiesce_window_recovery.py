"""What the next run may recover over an open quiesce window: read-only (wave 7c).

A window's journal stays open when its run ended inside it. Two things can
have restarted the App since, and each has one governed exit, taken under the
next run's lease before step 0a's fail-closed arm:

* **The dead-man** (``jobs/window_deadman.py``): its marker reads
  ``deadman_restarted`` and the App serves exactly that start. ``kind``
  ``deadman``, re-pinned by ``repin --from-deadman`` with no ack (M0-PR9,
  unchanged). A restart the App no longer serves is refined here to
  ``deadman_superseded``: it is never the dead-man path again.
* **The owner** (the break-glass, a raw ``apps start``): exactly one
  deployment nobody journaled or marked appeared since the window opened, and
  the App serves it. ``kind`` ``owner_start`` with a plan body and its
  ``owner_start_repin`` digest (``tools/databricks/operation_ack.py``), re-pinned by
  ``repin --from-owner-start`` only with that exact digest.

``classify`` reads the journal's marker, the App, its deployment listing, the
serving deployment and its predecessor, and the caller's own identity. It
downloads no tree, writes nothing and returns codes: ``Recovery.public()``
holds codes, a boolean and the digest only, never an id, a path or a value.

**The baseline.** ``window_open`` journals the App's deployment ids
(``start_baseline_ids``); a deployment is *new* when it is outside that
baseline, and *known* when the journal names it as the run's own start or the
dead-man's marker names it as its start. One new, unknown deployment that the
App serves is the candidate. More than one is ``owner_start_ambiguous`` and
has no governed exit: nothing can tell which of them the owner made.

**Why the ack is always required.** The shape check (step 9 below) compares
what ``get_deployment`` shows: the mode, the command, the git source and the
env rows. Spike S7 measured that it shows env NAMES only, never a value or a
``value_from``. So an ``apps deploy`` of blue's folder that keeps blue's env
names but changes a literal value or a ``value_from`` reads exactly like a
start. No API read can tell them apart, which is why the owner's exact digest
ack, naming this window and this deployment, is the only thing that admits
the re-pin, and why it is never inferred.

While the run is still alive the same baseline guards its own start
(``app_start_deployment.require_unmoved_baseline``): it refuses, before any
write, start or stop, once the App is no longer the stopped App the window
left, instead of starting a running App and stopping the owner's start.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from tools.databricks.app_deployment_state import deployment_state, newest_deployment
from tools.databricks.app_rollback_record_contract import _text
from tools.databricks.operation_ack import ack_digest
from tools.databricks.quiesce_window_deadman import deadman_status
from tools.databricks.quiesce_window_plan import (
    DEADMAN_STATES,
    OWNER_START_CREATORS,
    OWNER_START_REFUSALS,
    OWNER_START_STATES,
)

PURPOSE = "owner_start_repin"
# The one-run export a release reads the ack from (deploy-dev.yml carries it
# as the operation_acks pair owner_start_repin=<sha256>, release only).
ACK_ENV = "MIP_ACK_OWNER_START_REPIN"
BODY_VERSION = 1
KINDS = frozenset({"none", "deadman", "owner_start"})
# The journal states a dead-man restart is re-pinned from (jobs/window_deadman.py).
DEADMAN_RESTARTABLE = frozenset({"window_open", "window_starting"})
# A dead-man restart the App no longer serves: refined here, never a marker state.
SUPERSEDED = "deadman_superseded"
# What the marker itself may read (quiesce_window_plan.DEADMAN_STATES); any
# other text in that unsigned file reads "unreadable" and is never printed.
MARKER_READS = DEADMAN_STATES - {SUPERSEDED}
# Marker states that name the start the dead-man itself made: never the owner's.
KNOWN_START_MARKERS = frozenset(
    {"deadman_restarted", SUPERSEDED, "deadman_start_failed", "deadman_start_timeout"}
)
CANDIDATE = "candidate"
# Why an open window has no candidate (quiesce_window_plan.OWNER_START_REFUSALS).
REFUSALS = OWNER_START_REFUSALS


@dataclass(frozen=True)
class Recovery:
    """One open window, classified. ``body`` and its ids stay private; ``public`` is codes."""

    kind: str = "none"
    journal_state: str = "absent"
    deadman_state: str = "absent"
    owner_start: str = "absent"
    body: Mapping[str, Any] | None = None
    sha256: str = ""
    creator_class: str = "unknown"

    def __post_init__(self) -> None:
        if (
            self.kind not in KINDS
            or self.owner_start not in OWNER_START_STATES
            or self.creator_class not in OWNER_START_CREATORS
            or self.deadman_state not in DEADMAN_STATES
            or (self.kind == "owner_start") != (self.owner_start == CANDIDATE)
        ):
            raise ValueError("an open window's recovery carries an unreviewed code")

    def public(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "journal_state": self.journal_state,
            "deadman_state": self.deadman_state,
            "owner_start": self.owner_start,
            "owner_start_sha256": self.sha256,
            "creator_class": self.creator_class,
        }


def _state(app: object) -> str:
    raw = getattr(getattr(app, "compute_status", None), "state", "")
    return _text(raw).split(".")[-1].upper()


def _pending(app: object) -> bool:
    return getattr(app, "pending_deployment", None) is not None


def _active_id(app: object) -> str:
    return _text(getattr(getattr(app, "active_deployment", None), "deployment_id", None))


def _env_rows(deployment: object) -> tuple[tuple[str, ...], ...]:
    """A deployment's env vars as the API shows them (names only, spike S7).

    The same rows ``core_blue_repin._env_vars`` reads; kept here so the window
    plan's imports stay free of the core tier's.
    """

    rows = []
    for item in getattr(deployment, "env_vars", None) or []:
        rows.append(tuple(_text(getattr(item, key, None)) for key in ("name", "value", "value_from")))
    return tuple(sorted(rows))


def _marker(workspace: Any, *, app_name: str, current: Mapping[str, Any]) -> tuple[str, str]:
    """(the marker's reviewed state, its start id); anything else reads ``unreadable``."""

    try:
        state, start = deadman_status(workspace, app_name=app_name, current=current)
    except (RuntimeError, ValueError):
        return "unreadable", ""
    return (state, start) if state in MARKER_READS else ("unreadable", "")


def _creator_class(workspace: Any, deployment: object) -> str:
    """Whether the caller made the deployment: information for the reader, never a proof."""

    creator = _text(getattr(deployment, "creator", None)).casefold()
    try:
        caller = _text(getattr(workspace.current_user.me(), "user_name", None)).casefold()
    except Exception:  # noqa: BLE001 - informational only: an unreadable caller is "unknown"
        return "unknown"
    if not creator or not caller:
        return "unknown"
    return "deployer" if creator == caller else "other"


def baseline_sha256(baseline: frozenset[str]) -> str:
    canonical = json.dumps(sorted(baseline), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(b"mip-quiesce-window-baseline-v1\0" + canonical).hexdigest()


def _is_a_start(start: object, predecessor: object | None) -> bool:
    """The shape a start has against the deployment it copied (what the API shows of it)."""

    if predecessor is None:
        return False
    mode = _text(getattr(start, "mode", None)).split(".")[-1].upper()
    return (
        mode == "SNAPSHOT"
        and getattr(start, "git_source", None) is None
        and list(getattr(start, "command", None) or []) == list(getattr(predecessor, "command", None) or [])
        and _env_rows(start) == _env_rows(predecessor)
    )


def classify(
    workspace: Any, *, app_name: str, current: Mapping[str, Any] | None, app: object | None = None
) -> Recovery:
    """Classify the verified journal ``current``: the dead-man's restart, an owner start, or neither."""

    if current is None or current["state"] == "closed":
        return Recovery(journal_state="absent" if current is None else "closed")
    state = str(current["state"])
    marker, marker_start = _marker(workspace, app_name=app_name, current=current)
    app = workspace.apps.get(app_name) if app is None else app
    active = _active_id(app)
    if state in DEADMAN_RESTARTABLE and marker == "deadman_restarted":
        if marker_start and active == marker_start:
            return Recovery(kind="deadman", journal_state=state, deadman_state=marker)
        marker = SUPERSEDED

    def refused(code: str) -> Recovery:
        return Recovery(journal_state=state, deadman_state=marker, owner_start=code)

    blue = str(current.get("blue_deployment_id") or "")
    baseline = frozenset(current.get("start_baseline_ids") or ())
    if current.get("blue_restorable") is not True:
        return refused("owner_start_contract_window")
    if marker in {"mismatch", "unreadable"}:
        return refused("owner_start_marker_unreadable")
    if not blue or blue not in baseline:
        return refused("owner_start_no_baseline")
    if _state(app) != "ACTIVE" or _pending(app) or not active:
        return refused("owner_start_not_running")
    try:
        listing = list(workspace.apps.list_deployments(app_name))
        newest = newest_deployment(listing)
    except RuntimeError:
        return refused("owner_start_ambiguous")
    listed = {_text(getattr(item, "deployment_id", None)) for item in listing}
    known = {str(current.get("start_deployment_id") or "")}
    if marker in KNOWN_START_MARKERS:
        known.add(marker_start)
    known.discard("")
    unexplained = (listed | {active}) - baseline - known
    if not unexplained:
        return refused("owner_start_none")
    if unexplained != {active}:
        return refused("owner_start_ambiguous")
    start = workspace.apps.get_deployment(app_name, active)
    restore_source = str(current.get("restore_source") or "")
    if (
        _text(getattr(start, "deployment_id", None)) != active
        or _text(getattr(newest, "deployment_id", None)) != active
        or deployment_state(start) != "SUCCEEDED"
        or not restore_source
        or _text(getattr(start, "source_code_path", None)) != restore_source
        or _text(getattr(app, "default_source_code_path", None)) not in {"", restore_source}
    ):
        return refused("owner_start_not_current")
    predecessor = newest_deployment(
        [item for item in listing if _text(getattr(item, "deployment_id", None)) != active]
    )
    if not _is_a_start(start, predecessor):
        return refused("owner_start_not_a_start")
    body = {
        "version": BODY_VERSION,
        "app_name": app_name,
        "window_sha256": str(current.get("window_sha256") or ""),
        "window_lease_id": str(current.get("lease_id") or ""),
        "journal_state": state,
        "journal_refusal": str(current.get("refusal") or ""),
        "blue_deployment_id": blue,
        "restore_source": restore_source,
        "source_tree_sha256": str(current.get("source_tree_sha256") or ""),
        "start_baseline_sha256": baseline_sha256(baseline),
        "known_start_ids": sorted(known),
        "deadman_state": marker,
        "start_deployment_id": active,
    }
    return Recovery(
        kind="owner_start",
        journal_state=state,
        deadman_state=marker,
        owner_start=CANDIDATE,
        body=body,
        sha256=ack_digest(PURPOSE, body),
        creator_class=_creator_class(workspace, start),
    )


def plan_lines(summary: Mapping[str, Any]) -> list[str]:
    """The window plan's owner-start lines: codes and the digest only."""

    code = str(summary.get("owner_start") or "absent")
    if summary.get("owner_start_recovery"):
        return [
            "an owner start serves over the open window and its exact owner_start_repin ack was "
            "given: this run re-pins it under the lease (repin --from-owner-start, the window's "
            "own proofs) before step 0a"
        ]
    if code == CANDIDATE:
        return [
            f"owner start over the open window: candidate (journal {summary.get('journal_state')}, "
            f"dead-man {summary.get('deadman_state')}, creator {summary.get('owner_start_creator')}); "
            f"re-pin it on a release with operation_acks {PURPOSE}={summary.get('owner_start_sha256')} "
            f"(locally: {ACK_ENV}, for that run only)"
        ]
    if code != "absent":
        return [f"the open window has no owner start a release can re-pin: {code} (docs/deployment.md, Owner break-glass)"]
    return []


def status_line(found: Recovery) -> str:
    """``status --recovery``'s line: codes, and a candidate's digest."""

    detail = f", sha256 {found.sha256}" if found.kind == "owner_start" else ""
    return f"recovery {found.kind} (owner start {found.owner_start}, creator {found.creator_class}{detail})"
