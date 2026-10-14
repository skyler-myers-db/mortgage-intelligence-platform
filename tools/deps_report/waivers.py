"""Dated waivers: ci.yml review dates, file-size allowlist expiries and hold dates.

- every ``Review date: YYYY-MM-DD`` comment in ci.yml: a past date is the
  gate (``review_date_past``), one within 7 days a warning;
- every tools/file_size_allowlist.json ``expires`` entry: expired or expiring
  within 7 days is the gate (7 days red, 8 green);
- every tools/dependency_holds.json ``review_by``: a passed date is the gate.

The clock is injected (``today``) so nothing here reads the wall clock.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from tools.deps_report import REPO_ROOT, Signal, SourceError, repo_path

CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
ALLOWLIST = REPO_ROOT / "tools" / "file_size_allowlist.json"
WINDOW_DAYS = 7
_REVIEW = re.compile(r"Review date:\s*(\d{4}-\d{2}-\d{2})")


def review_dates(path: Path = CI_WORKFLOW) -> list[tuple[int, date]]:
    found: list[tuple[int, date]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        for match in _REVIEW.finditer(line):
            found.append((number, date.fromisoformat(match.group(1))))
    return found


def allowlist_expiries(path: Path = ALLOWLIST) -> dict[str, date]:
    try:
        expires = json.loads(path.read_text(encoding="utf-8"))["expires"]
        return {str(name): date.fromisoformat(str(when)) for name, when in expires.items()}
    except (OSError, KeyError, AttributeError, ValueError) as exc:
        raise SourceError("file_size_allowlist") from exc


def waiver_signals(
    today: date,
    holds: list[dict[str, object]],
    ci_path: Path = CI_WORKFLOW,
    allowlist_path: Path = ALLOWLIST,
) -> tuple[list[Signal], list[Signal], list[dict[str, str]]]:
    """(gate, warnings, rows) for every dated waiver."""

    gate: list[Signal] = []
    warnings: list[Signal] = []
    rows: list[dict[str, str]] = []
    where = repo_path(ci_path)
    for number, when in review_dates(ci_path):
        subject = f"{where}:{number}"
        if when < today:
            gate.append(Signal("review_date_past", subject, when.isoformat()))
        elif (when - today).days <= WINDOW_DAYS:
            warnings.append(Signal("review_date_soon", subject, when.isoformat()))
        rows.append({"kind": "ci.yml review date", "subject": subject, "date": when.isoformat()})
    for name, when in sorted(allowlist_expiries(allowlist_path).items()):
        if (when - today).days <= WINDOW_DAYS:
            gate.append(Signal("allowlist_expiring", name, when.isoformat()))
        rows.append({"kind": "file-size allowlist", "subject": name, "date": when.isoformat()})
    for hold in holds:
        when = date.fromisoformat(str(hold["review_by"]))
        if when < today:
            gate.append(Signal("hold_review_passed", str(hold["package"]), when.isoformat()))
        rows.append({"kind": f"{hold['ecosystem']} hold", "subject": str(hold["package"]), "date": when.isoformat()})
    return gate, warnings, rows
