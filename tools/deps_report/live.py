"""The informational live-validation section (D-platform-process-b step 4).

Shows tools/live_validation_gate.py's verdict for ``origin/main`` (the report
job checks out with fetch-depth 0): the reason, the deciding run's age, sha
and URL, and the count of changed runtime paths plus the first 10. An age of
7 days or more is a warning line; a FAIL states what a release from this sha
would hit. Any exception renders 'live gate unavailable: <class name>'.

It NEVER contributes to the report's exit status: the release gate itself
(deploy-prod.yml, tools/release_readiness.py) is the enforcement point, and a
weekly report red on every stale week would train people to ignore it.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from tools import live_validation_gate as gate

WARN_AGE_DAYS = 7.0
LISTED_PATHS = 10
Evaluate = Callable[..., Any]


def live_section(
    release_sha: str | None = None,
    evaluate: Evaluate | None = None,
    now: datetime | None = None,
    fetch: gate.Fetch | None = None,
) -> dict[str, object]:
    """The verdict as plain data; never raises."""

    sha = release_sha or ""
    try:
        sha = sha or gate.resolve_sha("origin/main")
        counts: dict[str, int | None] = {}

        def diff(a: str, b: str) -> list[str] | None:
            paths = gate.runtime_changed_paths(a, b)
            counts[a] = None if paths is None else len(paths)
            return paths

        verdict = (evaluate or gate.evaluate)(sha, gate.DEFAULT_MAX_AGE_DAYS, fetch=fetch, now=now, diff=diff)
        data = verdict.as_dict()
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - informational: any failure is shown, never raised
        return {"available": False, "error": type(exc).__name__, "release_sha": sha}
    age = data.get("age_days")
    run_sha = data.get("run_sha")
    paths = [str(path) for path in data.get("paths") or []]
    changed = counts.get(str(run_sha)) if run_sha else None
    return {
        "available": True,
        "status": str(data.get("status")),
        "reason": str(data.get("reason") or ""),
        "release_sha": str(data.get("release_sha") or sha),
        "run_url": str(data.get("run_url") or ""),
        "run_sha": str(run_sha or ""),
        "age_days": age,
        "changed_runtime_paths": changed if changed is not None else len(paths),
        "paths": paths[:LISTED_PATHS],
        "stale_warning": isinstance(age, int | float) and age >= WARN_AGE_DAYS,
    }
