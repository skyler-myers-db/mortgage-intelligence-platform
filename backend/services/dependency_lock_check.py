"""Boot-time proof that the App's installed packages equal uv.lock.

The Databricks Apps build can skip the dependency reinstall (docs/deployment.md,
"App Python dependencies: the deps-digest line"), and the App's Python
environment persists across deployments, so a deploy can run new code on old
wheels. At startup the App emits ONE ``dependency_lock_check`` event on the
``mip-runtime`` logger: how many uv.lock pins it checked, every drifted package
as name / locked / installed (installed is null when missing), the names
behind the ``unpinned`` and ``not_applicable`` counts (sorted, at most 50
each), the running ``python`` and the ``lock_python`` uv.lock was compiled for,
whether requirements.txt's ``requirements_digest`` line is ``current`` with
uv.lock and requirements.in (or ``stale``, ``missing``, ``unreadable``), and an
outcome:

* ``ok``: every package the App's requirements reach equals its uv.lock pin;
* ``drift``: at least one does not, one is missing, or the running Python is
  not the one uv.lock is compiled for (tools/python_deps_check.py counts that
  as a problem too);
* ``lock_unreadable``: uv.lock or requirements.in is missing, oversized or pins
  nothing, or the check itself failed (``reason`` says which).

The ``remedy`` follows the cause. A stale or missing digest can leave the bytes
of requirements.txt unchanged, which the build treats as no reason to
reinstall (confirmed 2026-10-28): restamp and redeploy. A
current digest with drift means
the build skipped or failed the reinstall anyway, so restamping changes
nothing: read the deployment build log, then the digest-named ``-c`` fallback
in docs/deployment.md.

Which pins apply follows tools/python_deps_check.py, the deploy preflight: walk
the installed closure of requirements.in, evaluating every requirement's
markers and extras for this interpreter. A lock entry that nothing reached
needs is not applicable, never drift. tomli is the standing example: uv.lock
pins it for coverage, whose requirement holds only below Python 3.11.0a6. A pin
line carrying its own marker (a universal lock) counts only where the marker
holds, and its package is named not applicable only when none of its lines
does. A reached package uv.lock does not pin is unpinned: named, never drift.
uv.lock is compiled for the App's Linux x86_64 platform, so on the App nothing
is unpinned, and on Apple Silicon greenlet is not applicable (sqlalchemy
requires it only where ``platform_machine`` is x86_64, aarch64, ppc64le, amd64
or win32, never arm64).

Local reads only: three bounded files plus importlib.metadata. No network, and
it never raises. The lifespan runs it in a worker thread, off the event loop.
"""

from __future__ import annotations

import hashlib
import importlib.metadata as metadata
import logging
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from backend.services.observability import emit

if TYPE_CHECKING:
    from packaging.markers import Marker

log = logging.getLogger("mip-runtime")

SOURCE_ROOT = Path(__file__).resolve().parents[2]
LOCK_FILE = SOURCE_ROOT / "uv.lock"
REQUIREMENTS_IN = SOURCE_ROOT / "requirements.in"
REQUIREMENTS_TXT = SOURCE_ROOT / "requirements.txt"
MAX_FILE_BYTES = 1_048_576
MAX_PACKAGES = 2_000
MAX_LOGGED_DRIFT = 50
MAX_LOGGED_NAMES = 50
# tools/requirements_digest.py owns this line; tools/ is not in the App source,
# so the format is restated here and a test pins the two to the same output.
DIGEST_PREFIX = "# deps-digest:"
REMEDY_RESTAMP = "python tools/requirements_digest.py --write, commit, then redeploy"
REMEDY_REINSTALL = (
    "the deps-digest line is current, so the Apps build skipped or failed the reinstall: "
    "read the deployment build log, then docs/deployment.md (App Python dependencies) "
    "for the digest-named -c fallback"
)
REMEDY_PYTHON = "the App runtime Python is not uv.lock's --python-version: recompile uv.lock"
REMEDY_SOURCE = (
    "the App source must carry uv.lock, requirements.in and requirements.txt "
    "(tools/databricks/app_runtime_closure.py)"
)
_LOCK_PYTHON = re.compile(r"--python-version\s+(\d+\.\d+)\b")
_PIN = re.compile(
    r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==(?P<version>[^\s;\\]+)"
    r"\s*(?:;\s*(?P<marker>[^\\]+?))?\s*\\?$"
)
Outcome = Literal["ok", "drift", "lock_unreadable"]
DigestState = Literal["current", "stale", "missing", "unreadable"]


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
    not_applicable_names: tuple[str, ...] = ()
    unpinned_names: tuple[str, ...] = ()
    reason: str | None = None
    python: str | None = None
    lock_python: str | None = None
    requirements_digest: DigestState | None = None


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
    excluded: list[str] = []
    for pin in pins:
        try:
            applies = pin.marker is None or _holds(Marker(pin.marker), env.markers, ("",))
        except InvalidMarker:
            applies = True
        if not applies:
            excluded.append(pin.name)
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
    unreached, unpinned = lock.keys() - reached, reached - lock.keys()
    return LockCheck(
        outcome="drift" if drifted else "ok",
        pins_total=len(pins),
        pins_checked=len(reached & lock.keys()),
        not_applicable=len(excluded) + len(unreached),
        unpinned=len(unpinned),
        drifted=tuple(sorted(drifted, key=lambda d: d.name)),
        # A forked package with one applying line was checked, so it is not named.
        not_applicable_names=tuple(sorted((set(excluded) - lock.keys()) | unreached)),
        unpinned_names=tuple(sorted(unpinned)),
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
    """The file's text with its line endings as written (no newline translation).

    The digest folds only CRLF, as tools/requirements_digest.py does, so the
    text must reach it untranslated: universal newlines would also turn a lone
    CR into LF and hash bytes the stamping tool never hashed.
    """

    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _lf_sha256(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def expected_digest_line(lock_text: str, requirements_text: str) -> str:
    """tools/requirements_digest.digest_line_for: sha256 with only CRLF folded to LF."""

    parts = [
        f"{name} sha256={_lf_sha256(text)}"
        for name, text in (("uv.lock", lock_text), ("requirements.in", requirements_text))
    ]
    return f"{DIGEST_PREFIX} {' '.join(parts)}"


def digest_state(requirements_txt: str | None, lock_text: str, requirements_text: str) -> DigestState:
    if requirements_txt is None:
        return "unreadable"
    first = requirements_txt.splitlines()[0] if requirements_txt else ""
    if first == expected_digest_line(lock_text, requirements_text):
        return "current"
    return "stale" if first.startswith(DIGEST_PREFIX) else "missing"


def check_dependency_lock(
    *,
    lock_file: Path = LOCK_FILE,
    requirements_in: Path = REQUIREMENTS_IN,
    requirements_txt: Path = REQUIREMENTS_TXT,
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
    result = compute_drift(pins, parse_requirements(requirements_text), env)
    header = _LOCK_PYTHON.search(lock_text)
    lock_python = header.group(1) if header else None
    python = env.markers.get("python_version")
    mismatch = lock_python is not None and python != lock_python
    return replace(
        result,
        outcome="drift" if mismatch else result.outcome,
        reason=f"python {python}, uv.lock is compiled for {lock_python}" if mismatch else result.reason,
        python=python,
        lock_python=lock_python,
        requirements_digest=digest_state(_read(requirements_txt), lock_text, requirements_text),
    )


def remedy(result: LockCheck) -> str | None:
    """What an operator does about ``result``; None when nothing is wrong."""

    if result.outcome == "ok":
        return None
    if result.outcome == "lock_unreadable":
        return REMEDY_SOURCE
    if result.lock_python is not None and result.python != result.lock_python:
        return REMEDY_PYTHON
    return REMEDY_REINSTALL if result.requirements_digest == "current" else REMEDY_RESTAMP


def log_dependency_lock_check(
    *,
    lock_file: Path = LOCK_FILE,
    requirements_in: Path = REQUIREMENTS_IN,
    requirements_txt: Path = REQUIREMENTS_TXT,
    environment: InstalledEnvironment | None = None,
) -> LockCheck:
    """Emit the one ``dependency_lock_check`` event; never raises."""

    start = time.monotonic()
    try:
        result = check_dependency_lock(
            lock_file=lock_file,
            requirements_in=requirements_in,
            requirements_txt=requirements_txt,
            environment=environment,
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
        not_applicable_names=list(result.not_applicable_names[:MAX_LOGGED_NAMES]),
        unpinned=result.unpinned,
        unpinned_names=list(result.unpinned_names[:MAX_LOGGED_NAMES]),
        drifted_count=len(result.drifted),
        drifted=[
            {"name": d.name, "locked": d.locked, "installed": d.installed}
            for d in result.drifted[:MAX_LOGGED_DRIFT]
        ],
        reason=result.reason,
        python=result.python,
        lock_python=result.lock_python,
        requirements_digest=result.requirements_digest,
        remedy=remedy(result),
    )
    return result
