#!/usr/bin/env python3
"""Does the deploy interpreter match the requirements.txt pins?

scripts/deploy.sh step 0 asks this before any workspace call
(full_deploy_readiness.md 3.12: a drifted mlflow or databricks-sdk
registers and serves a stack CI never proved).
requirements.txt is ``-c uv.lock`` plus ``-r requirements.in``; uv.lock pins
the whole resolved closure for one Python minor (its ``--python-version``
header). The check walks the installed closure of requirements.in, with each
package's markers and extras evaluated for this interpreter as pip evaluates
them, and compares every reached package with its uv.lock pin. A lock entry
that this interpreter never needs (a marker excludes it) is not required.

Exit codes are distinct so the caller never mistakes an error for drift:
  0   pinned: the interpreter is the lock's Python minor, every requirement
      in requirements.in is installed and satisfies its own specifier, and
      every reached package equals its uv.lock pin;
  10  drift: prints the first 20 problems (package, installed, pinned) and the
      exact remedy.
Any other code (argparse's 2, an uncaught error's 1) is a hard error. A
reached package that uv.lock does not pin is a note, never drift: it has no
pin to equal.
"""

from __future__ import annotations

import argparse
import importlib.metadata as metadata
import re
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parents[1]
EXIT_PINNED = 0
EXIT_DRIFT = 10
MAX_LISTED = 20
_LOCK_PYTHON = re.compile(r"--python-version\s+(\d+)\.(\d+)\b")
_LOCK_PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)$")
_INCLUDE = re.compile(r"^-(c|r)\s+(\S+)$")


@dataclass(frozen=True)
class Environment:
    """What the interpreter has installed; injectable for tests."""

    python: tuple[int, int]
    markers: dict[str, str]
    version: Callable[[str], str | None]
    requires: Callable[[str], list[str]]


def _installed_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _installed_requires(name: str) -> list[str]:
    return list(metadata.requires(name) or [])


def current_environment() -> Environment:
    return Environment(
        python=(sys.version_info.major, sys.version_info.minor),
        markers={str(key): str(value) for key, value in default_environment().items()},
        version=_installed_version,
        requires=_installed_requires,
    )


@dataclass(frozen=True)
class Pins:
    python: tuple[int, int]
    lock: dict[str, str]
    requirements: tuple[Requirement, ...] = field(repr=False)


def _content_lines(text: str) -> Iterable[str]:
    for raw in text.splitlines():
        line = raw.split(" #", 1)[0].strip()
        if line and not line.startswith("#"):
            yield line


def parse_lock(text: str) -> tuple[tuple[int, int], dict[str, str]]:
    header = _LOCK_PYTHON.search(text)
    if header is None:
        raise ValueError("uv.lock has no --python-version header")
    pins: dict[str, str] = {}
    for line in _content_lines(text):
        match = _LOCK_PIN.match(line)
        if match is None:
            raise ValueError(f"uv.lock line is not a bare exact pin: {line!r}")
        pins[canonicalize_name(match.group(1))] = match.group(2)
    if not pins:
        raise ValueError("uv.lock pins nothing")
    return (int(header.group(1)), int(header.group(2))), pins


def load_pins(repo: Path) -> Pins:
    """Resolve requirements.txt's ``-c`` lock and ``-r`` requirement files."""

    locks: list[Path] = []
    inputs: list[Path] = []
    for line in _content_lines((repo / "requirements.txt").read_text(encoding="utf-8")):
        match = _INCLUDE.match(line)
        if match is None:
            raise ValueError(f"requirements.txt line is not -c/-r: {line!r}")
        (locks if match.group(1) == "c" else inputs).append(repo / match.group(2))
    if len(locks) != 1 or not inputs:
        raise ValueError("requirements.txt must name exactly one -c lock and at least one -r file")
    python, lock = parse_lock(locks[0].read_text(encoding="utf-8"))
    requirements: list[Requirement] = []
    for path in inputs:
        for line in _content_lines(path.read_text(encoding="utf-8")):
            if line.startswith("-"):
                raise ValueError(f"{path.name} option lines are not supported: {line!r}")
            requirements.append(Requirement(line))
    return Pins(python=python, lock=lock, requirements=tuple(requirements))


def _applies(requirement: Requirement, markers: dict[str, str], extras: Iterable[str]) -> bool:
    if requirement.marker is None:
        return True
    return any(requirement.marker.evaluate({**markers, "extra": extra}) for extra in extras)


@dataclass(frozen=True)
class Findings:
    problems: list[str]
    notes: list[str]
    checked: int


def problems(pins: Pins, env: Environment) -> Findings:
    """Problems are drift; notes never fail the check."""

    found: list[str] = []
    notes: list[str] = []
    if env.python != pins.python:
        found.append(
            f"python: running {env.python[0]}.{env.python[1]}, "
            f"uv.lock is compiled for {pins.python[0]}.{pins.python[1]}"
        )

    queue: list[tuple[str, frozenset[str]]] = []
    for requirement in pins.requirements:
        if not _applies(requirement, env.markers, ("",)):
            continue
        root: str = canonicalize_name(requirement.name)
        root_installed = env.version(root)
        root_pinned = pins.lock.get(root)
        # A version off its pin is reported as drift below; this names only a
        # pin-equal (or unpinned) install that requirements.in itself rejects.
        if (
            root_installed is not None
            and (root_pinned is None or Version(root_installed) == Version(root_pinned))
            and not requirement.specifier.contains(root_installed, prereleases=True)
        ):
            found.append(
                f"spec: {root} installed {root_installed}, "
                f"requirements.in wants {requirement.specifier}"
            )
        queue.append((root, frozenset(requirement.extras)))

    checked: set[str] = set()
    expanded: set[tuple[str, str]] = set()
    while queue:
        name, extras = queue.pop()
        installed = env.version(name)
        pinned = pins.lock.get(name)
        if name not in checked:
            checked.add(name)
            if installed is None:
                found.append(f"missing: {name} (pinned {pinned or 'nowhere'})")
            elif pinned is None:
                notes.append(f"unpinned: {name} {installed} is not in uv.lock")
            elif Version(installed) != Version(pinned):
                found.append(f"drift: {name} installed {installed}, pinned {pinned}")
        if installed is None:
            continue
        for extra in ("", *sorted(extras)):
            if (name, extra) in expanded:
                continue
            expanded.add((name, extra))
            for spec in env.requires(name):
                dependency = Requirement(spec)
                if _applies(dependency, env.markers, (extra,)):
                    queue.append((canonicalize_name(dependency.name), frozenset(dependency.extras)))
    return Findings(problems=sorted(set(found)), notes=sorted(set(notes)), checked=len(checked))


def remedy(pins: Pins, repo: Path) -> list[str]:
    python = f"python{pins.python[0]}.{pins.python[1]}"
    venv = '"$HOME/.venvs/mip-deploy"'
    return [
        "fix: build a dedicated deploy interpreter from the pins, outside this checkout,",
        "     then select it (never re-sync a venv other sessions share):",
        f"  {python} -m venv {venv}",
        f'  "$HOME/.venvs/mip-deploy/bin/python" -m pip install -r {repo / "requirements.txt"}',
        '  export MIP_DEPLOY_PYTHON="$HOME/.venvs/mip-deploy/bin/python"',
    ]


def check(repo: Path, env: Environment) -> tuple[int, list[str]]:
    pins = load_pins(repo)
    findings = problems(pins, env)
    if not findings.problems:
        python = f"{pins.python[0]}.{pins.python[1]}"
        summary = f"{findings.checked} installed packages equal their uv.lock pins (Python {python})"
        return EXIT_PINNED, [summary, *findings.notes]
    listed = findings.problems[:MAX_LISTED]
    if len(findings.problems) > MAX_LISTED:
        listed.append(f"... and {len(findings.problems) - MAX_LISTED} more")
    return EXIT_DRIFT, listed + findings.notes + remedy(pins, repo)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    code, lines = check(args.repo_root.resolve(), current_environment())
    for line in lines:
        print(f"  {line}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
