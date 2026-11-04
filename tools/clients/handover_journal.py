"""The ownership handovers' digests and private per-object journal (M2-PR8).

Both handovers (Unity Catalog, ``handover_uc_ownership.py``; Lakebase,
``lakebase_handover.py``) are plan-first. The owner hardware-key operation
attestation is deferred (review mode ``single_maintainer_audited``), so a
mutating run is gated by an **exact digest ack** instead: the read-only plan
prints the sha256 of its canonical document, ``--ack-plan`` must name exactly
that digest, and the tool recomputes the plan immediately before its first
mutation and refuses, with zero mutations, when the two differ.

The journal is the handover's only durable record of what it changed. It is
a local JSON file at an explicit path, created mode 0600 and never through a
symlink, rewritten atomically (a private temporary file, fsync, rename) after
every state change, so a crash leaves the last complete state:

* each object moves ``pending`` -> ``applying`` (written before the mutation
  is sent) -> ``applied``; a failed attempt goes back to ``pending`` with its
  attempt count and a value-free error code;
* revert moves ``applied`` or ``applying`` (the mutation may have landed) ->
  ``reverting`` -> ``reverted``, and only for an object whose live owner is
  still the one the handover gave it (compare and swap): the exact inverse.

``create`` refuses a path that exists, so one journal records one apply; a
second apply after a partial failure is a revert first. ``load`` refuses a
journal another user owns or anyone else can read.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

JOURNAL_SCHEMA = "mip.ownership-handover.journal.v1"
OBJECT_STATES = frozenset({"pending", "applying", "applied", "reverting", "reverted"})
TRANSITIONS = {
    "pending": frozenset({"applying"}),
    "applying": frozenset({"applied", "pending", "reverting", "reverted"}),
    "applied": frozenset({"reverting"}),
    "reverting": frozenset({"reverted", "reverting"}),
    "reverted": frozenset(),
}
# Journal-level status; the Lakebase handover adds its window states.
STATUSES = frozenset(
    {
        "applying",
        "applied",
        "exhausted",
        "refused",
        "reverting",
        "reverted",
        "window_open",
        "window_applied",
        "window_closed",
    }
)
# States whose mutation may have landed, so revert must judge them live.
MAY_HAVE_LANDED = frozenset({"applying", "applied", "reverting"})


class JournalRefused(RuntimeError):
    """The journal refused; the message names no path, owner or object."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def plan_sha256(document: Mapping[str, Any], *, domain: str) -> str:
    """The sha256 the ack names: domain-separated canonical JSON of the plan document."""

    return hashlib.sha256(domain.encode("ascii") + b"\0" + canonical_json(document).encode("ascii")).hexdigest()


def ack_state(acknowledgement: str, digest: str) -> str:
    return "matched" if acknowledgement == digest else "absent" if not acknowledgement else "mismatch"


def write_private(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically replace ``path`` with ``payload``, mode 0600; never through a symlink."""

    if path.is_symlink():
        raise JournalRefused("the private file is a symlink")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Journal:
    """One handover's journal: its kind, plan digest, status and per-object entries."""

    def __init__(self, path: Path, document: dict[str, Any]) -> None:
        self.path = path
        self.document = document

    @property
    def kind(self) -> str:
        return str(self.document["kind"])

    @property
    def plan_sha256(self) -> str:
        return str(self.document["plan_sha256"])

    @property
    def status(self) -> str:
        return str(self.document["status"])

    @property
    def entries(self) -> list[dict[str, Any]]:
        return list(self.document["entries"])

    def entry(self, key: str) -> dict[str, Any]:
        for entry in self.document["entries"]:
            if entry["key"] == key:
                return dict(entry)
        raise JournalRefused("the journal has no such object")

    def mark(self, key: str, state: str, **fields: Any) -> None:
        """Move one object to ``state`` and persist before returning."""

        for entry in self.document["entries"]:
            if entry["key"] != key:
                continue
            if state not in TRANSITIONS[entry["state"]]:
                raise JournalRefused(f"an object cannot move from {entry['state']} to {state}")
            entry.update(fields, state=state, updated_at=_now())
            write_private(self.path, self.document)
            return
        raise JournalRefused("the journal has no such object")

    def set_status(self, status: str, **fields: Any) -> None:
        if status not in STATUSES:
            raise JournalRefused("unreviewed journal status")
        self.document.update(fields, status=status, updated_at=_now())
        write_private(self.path, self.document)

    def counts(self) -> dict[str, int]:
        found = {state: 0 for state in sorted(OBJECT_STATES)}
        for entry in self.document["entries"]:
            found[entry["state"]] += 1
        return found


def create(
    path: Path,
    *,
    kind: str,
    plan_sha256: str,
    entries: Iterable[Mapping[str, Any]],
    extra: Mapping[str, Any] | None = None,
) -> Journal:
    """A new journal, every object ``pending``; an existing path refuses (O_EXCL)."""

    rows = [{**dict(entry), "state": "pending", "attempts": 0} for entry in entries]
    keys = [row["key"] for row in rows]
    if len(set(keys)) != len(keys):
        raise JournalRefused("the plan names one object twice")
    document = {
        "schema": JOURNAL_SCHEMA,
        "kind": kind,
        "plan_sha256": plan_sha256,
        "status": "applying",
        "created_at": _now(),
        "updated_at": _now(),
        **dict(extra or {}),
        "entries": rows,
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise JournalRefused(
            "the journal path already exists: revert that journal, or name a new path"
        ) from None
    os.close(descriptor)
    write_private(path, document)
    return Journal(path, document)


def load(path: Path, *, kind: str) -> Journal:
    """Read a journal this user wrote; refuse a symlink, a shared mode or another kind."""

    try:
        info = path.lstat()
    except FileNotFoundError:
        raise JournalRefused("no journal exists at the given path") from None
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise JournalRefused("the journal is not a regular file")
    if info.st_mode & 0o077:
        raise JournalRefused("the journal is readable by others; it must be mode 0600")
    if info.st_uid != os.getuid():
        raise JournalRefused("the journal belongs to another user")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise JournalRefused("the journal is not readable JSON") from None
    if not isinstance(document, dict) or document.get("schema") != JOURNAL_SCHEMA:
        raise JournalRefused("the journal is not an ownership-handover journal")
    if document.get("kind") != kind:
        raise JournalRefused("the journal belongs to the other handover")
    if document.get("status") not in STATUSES:
        raise JournalRefused("the journal carries an unreviewed status")
    entries = document.get("entries")
    if not isinstance(entries, list) or any(
        not isinstance(entry, dict) or entry.get("state") not in OBJECT_STATES for entry in entries
    ):
        raise JournalRefused("the journal carries an unreviewed object state")
    return Journal(path, document)


__all__ = [
    "JOURNAL_SCHEMA",
    "MAY_HAVE_LANDED",
    "Journal",
    "JournalRefused",
    "ack_state",
    "canonical_json",
    "create",
    "load",
    "plan_sha256",
    "write_private",
]
