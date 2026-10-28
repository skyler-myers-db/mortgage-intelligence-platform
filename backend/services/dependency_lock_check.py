"""Boot-time proof that the App's installed packages equal uv.lock.

The Databricks Apps build can skip the dependency reinstall (docs/deployment.md,
"App Python dependencies: the deps-digest line"), and the App's Python
environment persists across deployments, so a deploy can run new code on old
wheels. At startup the App emits ONE ``dependency_lock_check`` event on the
``mip-runtime`` logger: how many uv.lock pins it checked, every drifted package
as name / locked / installed (installed is null when missing), and an outcome:

* ``ok``: every package the App's requirements reach equals its uv.lock pin;
* ``drift``: at least one does not, or one is missing;
* ``lock_unreadable``: uv.lock or requirements.in is missing, oversized or pins
  nothing, or the check itself failed (``reason`` says which).

Which pins apply follows tools/python_deps_check.py, the deploy preflight: walk
the installed closure of requirements.in, evaluating every requirement's
markers and extras for this interpreter. A lock entry that nothing reached
needs is not applicable, never drift. tomli is the standing example: uv.lock
pins it for coverage, whose requirement holds only below Python 3.11.0a6. A pin
line carrying its own marker (a universal lock) counts only where the marker
holds.

Local reads only: two bounded files plus importlib.metadata. No network, and
it never raises.
"""

from __future__ import annotations

import importlib.metadata as metadata
import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from backend.services.observability import emit

if TYPE_CHECKING:
    from packaging.markers import Marker

log = logging.getLogger("mip-runtime")

SOURCE_ROOT = Path(__file__).resolve().parents[2]
LOCK_FILE = SOURCE_ROOT / "uv.lock"
REQUIREMENTS_IN = SOURCE_ROOT / "requirements.in"
MAX_FILE_BYTES = 1_048_576
MAX_PACKAGES = 2_000
MAX_LOGGED_DRIFT = 50
REMEDY = "python tools/requirements_digest.py --write, then redeploy"
_PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==(?P<version>[^\s;\\]+)"
    r"\s*(?:;\s*(?P<marker>[^\\]+?))?\s*\\?$"
)
Outcome = Literal["ok", "drift", "lock_unreadable"]


@dataclass(frozen=True)
class LockPin:
    name: str
    version: str
    marker: str | None = None


@dataclass(frozen=True)
class Drift:
    name: str
    locked: str | None
    installed: str | None


@dataclass(frozen=True)
class LockCheck:
    outcome: Outcome
    pins_total: int = 0
    pins_checked: int = 0
    not_applicable: int = 0
    unpinned: int = 0
    drifted: tuple[Drift, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class InstalledEnvironment:
    """What this interpreter has installed; injectable for tests."""

    markers: Mapping[str, str]
    version: Callable[[str], str | None]
    requires: Callable[[str], Sequence[str]]


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _content_lines(text: str) -> Iterable[str]:
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].strip()
        if line and not line.startswith(("#", "-")):
            yield line


def parse_lock(text: str) -> list[LockPin]:
    """Exact pins of a uv requirements-format lock; options and hashes skipped."""

    pins: list[LockPin] = []
    for line in _content_lines(text):
        match = _PIN.match(line)
        if match is not None:
            marker = (match.group("marker") or "").strip() or None
            pins.append(LockPin(canonical(match.group("name")), match.group("version"), marker))
    return pins


def parse_requirements(text: str) -> list[str]:
    return list(_content_lines(text))


def _holds(marker: Marker | None, markers: Mapping[str, str], extras: Iterable[str]) -> bool:
    if marker is None:
        return True
    try:
        return any(marker.evaluate({**markers, "extra": extra}) for extra in extras)
    except Exception:  # noqa: BLE001 -- an unevaluable marker is checked, not hidden
        return True


def _same(installed: str, locked: str) -> bool:
    from packaging.version import InvalidVersion, Version

    try:
        return Version(installed) == Version(locked)
    except InvalidVersion:
        return installed == locked


def compute_drift(
    pins: Sequence[LockPin], requirements: Sequence[str], env: InstalledEnvironment
) -> LockCheck:
    """Compare every package reached from ``requirements`` with its pin."""

    from packaging.markers import InvalidMarker, Marker
    from packaging.requirements import InvalidRequirement, Requirement

    lock: dict[str, str] = {}
    excluded = 0
    for pin in pins:
        try:
            applies = pin.marker is None or _holds(Marker(pin.marker), env.markers, ("",))
        except InvalidMarker:
            applies = True
        if not applies:
            excluded += 1
        else:
            lock.setdefault(pin.name, pin.version)

    def parsed(specs: Iterable[str]) -> Iterable[Requirement]:
        for spec in specs:
            try:
                yield Requirement(spec)
            except InvalidRequirement:
                continue

    queue = [
        (canonical(req.name), frozenset(req.extras))
        for req in parsed(requirements)
        if _holds(req.marker, env.markers, ("",))
    ]
    reached: set[str] = set()
    expanded: set[tuple[str, str]] = set()
    drifted: list[Drift] = []
    while queue and len(reached) < MAX_PACKAGES:
        name, extras = queue.pop()
        installed = env.version(name)
        if name not in reached:
            reached.add(name)
            locked = lock.get(name)
            if installed is None or (locked is not None and not _same(installed, locked)):
                drifted.append(Drift(name, locked, installed))
        if installed is None:
            continue
        for extra in ("", *sorted(extras)):
            if (name, extra) not in expanded:
                expanded.add((name, extra))
                queue.extend(
                    (canonical(dep.name), frozenset(dep.extras))
                    for dep in parsed(env.requires(name))
                    if _holds(dep.marker, env.markers, (extra,))
                )
    return LockCheck(
        outcome="drift" if drifted else "ok",
        pins_total=len(pins),
        pins_checked=len(reached & lock.keys()),
        not_applicable=excluded + len(lock.keys() - reached),
        unpinned=len(reached - lock.keys()),
        drifted=tuple(sorted(drifted, key=lambda d: d.name)),
        reason=None if len(reached) < MAX_PACKAGES else f"walk stopped at {MAX_PACKAGES}",
    )


def current_environment() -> InstalledEnvironment:
    from packaging.markers import default_environment

    def version(name: str) -> str | None:
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return None

    def requires(name: str) -> Sequence[str]:
        try:
            return metadata.requires(name) or []
        except metadata.PackageNotFoundError:
            return []

    markers = {str(key): str(value) for key, value in default_environment().items()}
    return InstalledEnvironment(markers=markers, version=version, requires=requires)


def _read(path: Path) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def check_dependency_lock(
    *,
    lock_file: Path = LOCK_FILE,
    requirements_in: Path = REQUIREMENTS_IN,
    environment: InstalledEnvironment | None = None,
) -> LockCheck:
    lock_text, requirements_text = _read(lock_file), _read(requirements_in)
    if lock_text is None or requirements_text is None:
        missing = lock_file if lock_text is None else requirements_in
        return LockCheck("lock_unreadable", reason=f"{missing.name} missing, unreadable or oversized")
    pins = parse_lock(lock_text)
    if not pins:
        return LockCheck("lock_unreadable", reason=f"{lock_file.name} pins nothing")
    env = environment or current_environment()
    return compute_drift(pins, parse_requirements(requirements_text), env)


def log_dependency_lock_check(
    *,
    lock_file: Path = LOCK_FILE,
    requirements_in: Path = REQUIREMENTS_IN,
    environment: InstalledEnvironment | None = None,
) -> LockCheck:
    """Emit the one ``dependency_lock_check`` event; never raises."""

    start = time.monotonic()
    try:
        result = check_dependency_lock(
            lock_file=lock_file, requirements_in=requirements_in, environment=environment
        )
    except Exception as exc:  # noqa: BLE001 -- a startup diagnostic must not block startup
        result = LockCheck("lock_unreadable", reason=f"check failed: {type(exc).__name__}")
    emit(
        log,
        "dependency_lock_check",
        level=logging.INFO if result.outcome == "ok" else logging.WARNING,
        dependency="python_packages",
        outcome=result.outcome,
        duration_ms=int((time.monotonic() - start) * 1000),
        pins_total=result.pins_total,
        pins_checked=result.pins_checked,
        not_applicable=result.not_applicable,
        unpinned=result.unpinned,
        drifted_count=len(result.drifted),
        drifted=[
            {"name": d.name, "locked": d.locked, "installed": d.installed}
            for d in result.drifted[:MAX_LOGGED_DRIFT]
        ],
        reason=result.reason,
        remedy=None if result.outcome == "ok" else REMEDY,
    )
    return result
