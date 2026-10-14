"""The weekly dependency and release-signal report (D-platform-process-a).

Audit quality-v2 / stack-10, W5e lane w5-deps-report. Standard library only.
``python -m tools.deps_report --json OUT --markdown OUT`` reads the exact pins
(frontend/package.json, requirements.in + uv.lock), the advisory feeds (npm
audit over the lock, pip-audit over uv.lock), the literal tool pins in
.github/workflows/*.yml, the dated waivers (ci.yml review dates, the file-size
allowlist, tools/dependency_holds.json) and, informationally, the live
release gate. It exits 1 only on a gate signal, 2 when a source could not be
read, and always writes both outputs (docs/audits/supply-chain-audit.md, W5e
addendum).

This module holds what the sections share: the gate/warning record, version
classification and the dated holds.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
HOLDS_FILE = REPO_ROOT / "tools" / "dependency_holds.json"
HOLD_KEYS = frozenset({"ecosystem", "package", "below", "literals", "reason", "review_by"})
ECOSYSTEMS = ("npm", "pypi", "tool-literal")
_VERSION = re.compile(r"v?(\d+(?:\.\d+)*)")


class SourceError(RuntimeError):
    """A registry, API or tool read failed; the report exits 2 naming the source."""

    def __init__(self, source: str) -> None:
        super().__init__(source)
        self.source = source


@dataclass(frozen=True)
class Signal:
    """One gate (red) or warning line. Every field is rendered as a code span."""

    code: str
    subject: str
    detail: str = ""


def repo_path(path: Path) -> str:
    """A repository-relative POSIX path (the bare name outside the repository)."""

    resolved = path.resolve()
    return resolved.relative_to(REPO_ROOT).as_posix() if resolved.is_relative_to(REPO_ROOT) else resolved.name


def version_tuple(value: str) -> tuple[int, ...] | None:
    """The leading numeric release of a semver / PEP 440 string, else None."""

    match = _VERSION.match(value.strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def classify_gap(pinned: str, latest: str) -> str:
    """current | patch | minor | major | unknown; for a 0.y.z pin a y change is major."""

    have, want = version_tuple(pinned), version_tuple(latest)
    if have is None or want is None:
        return "unknown"
    have, want = have + (0,) * (3 - len(have)), want + (0,) * (3 - len(want))
    if want <= have:
        return "current"
    if want[0] != have[0] or (have[0] == 0 and want[1] != have[1]):
        return "major"
    return "minor" if want[1] != have[1] else "patch"


def load_holds(path: Path = HOLDS_FILE) -> list[dict[str, object]]:
    """tools/dependency_holds.json, schema-checked (a malformed file is a SourceError)."""

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        holds = document["holds"]
        if set(document) != {"holds"} or not isinstance(holds, list):
            raise SourceError("dependency_holds")
        for hold in holds:
            bound = "literals" if hold.get("ecosystem") == "tool-literal" else "below"
            if set(hold) != {"ecosystem", "package", bound, "reason", "review_by"} or hold["ecosystem"] not in ECOSYSTEMS:
                raise SourceError("dependency_holds")
            date.fromisoformat(str(hold["review_by"]))
    except (OSError, KeyError, TypeError, AttributeError, ValueError) as exc:
        raise SourceError("dependency_holds") from exc
    return holds


def hold_for(holds: list[dict[str, object]], ecosystem: str, package: str) -> dict[str, object] | None:
    return next((h for h in holds if h["ecosystem"] == ecosystem and h["package"] == package), None)


def drift_row(name: str, pinned: str, latest: str, published: str, hold: dict[str, object] | None) -> dict[str, str]:
    """One package row: held when a hold names it, else current or pending (drift never reds)."""

    gap = classify_gap(pinned, latest)
    status = "held" if hold is not None else ("current" if gap == "current" else "pending")
    return {
        "package": name,
        "pinned": pinned,
        "latest": latest,
        "published": published,
        "gap": gap,
        "status": status,
        "hold_reason": str(hold["reason"]) if hold else "",
        "hold_review_by": str(hold["review_by"]) if hold else "",
    }
