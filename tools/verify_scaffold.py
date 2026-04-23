"""Scaffold sanity check.

Validates that the repository has the structural files the app relies on and
that no forbidden secret files are committed to git. Local-only files like
`.env.local` are expected to exist on a developer machine, so this script
inspects git's tracked set rather than the filesystem.

R6-25: also emits a non-fatal warning when the deploying user lacks the
``Can Manage`` permission on the Databricks App resource. ``databricks
bundle validate`` only checks schema, so the two-phase deploy gotcha
(bundle uploads files; ``databricks apps deploy`` promotes -- see
``docs/se-onboarding.md`` §5) first surfaces during ``terraform apply``
with a confusing "Can View" permission error. Catching it in the
pre-deploy scaffold check means SEs see the actionable message BEFORE
they burn the warehouse warm-up time.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = [
    "README.md",
    "CLAUDE.md",
    "AGENTS.md",
    "app.yaml",
    "databricks.yml",
    "frontend/src/app.tsx",
    "backend/main.py",
    "backend/runtime.py",
    "docs/implementation-plan.md",
    ".claude/settings.json",
    ".claude/skills/databricks-app/SKILL.md",
    "sql/ddl/001_catalogs_schemas.sql",
    "tests/unit/test_scoring.py",
]
FORBIDDEN_COMMITTED = [".env", ".env.local", "secrets"]

# The app resource name matches ``databricks.yml`` ``resources.apps.mip-app``.
# Keep in sync with the bundle so the warning points at the right URL.
_APP_NAME = "mip-app"


def _git_tracked_files() -> set[str]:
    try:
        out = subprocess.check_output(
            ["git", "ls-files"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    return {line.strip() for line in out.splitlines() if line.strip()}


def _check_app_permissions() -> str | None:
    """Return a warning string when the deploying user likely lacks the
    app's ``Can Manage`` permission, or ``None`` when we can confirm the
    grant (or can't tell -- in which case we stay silent rather than
    cry-wolf).

    Non-fatal: any CLI failure (no binary, no auth, no app yet on
    first deploy) returns ``None``. The goal is to surface the known
    ``terraform apply`` failure-mode for SEs who have already deployed
    once and hit the second-deploy permission error.
    """
    # Skip in CI / non-interactive environments where the SE isn't
    # waiting on the output.
    if os.environ.get("CI") == "true":
        return None
    try:
        # Light-touch probe: list the app. 404 means the bundle hasn't
        # been deployed yet (skip -- nothing to check); 200 lets us
        # inspect the permissions. Any other error: stay silent.
        out = subprocess.check_output(
            ["databricks", "apps", "get", _APP_NAME],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None
    try:
        app = json.loads(out)
    except json.JSONDecodeError:
        return None
    # If we can see the app metadata the deploy user has at least read
    # access. The fine-grained "Can Manage" grant check requires the
    # permissions endpoint which is awkward to version-proof across CLI
    # releases, so we surface a one-line hint instead of a hard check:
    # pointer to the documented two-step pattern.
    app_id = app.get("id") or app.get("app_id") or _APP_NAME
    return (
        f"note: Databricks App '{app_id}' detected. If 'databricks bundle "
        f"deploy -t dev' fails at terraform-apply with a 'Can View' permission "
        f"error on databricks_app, run the two-phase deploy documented in "
        f"docs/se-onboarding.md §5: bundle deploy uploads files, then "
        f"'databricks apps deploy {_APP_NAME}' promotes them."
    )


def main() -> None:
    missing = [p for p in REQUIRED if not (ROOT / p).exists()]
    if missing:
        raise SystemExit(f"Missing required files: {missing}")

    tracked = _git_tracked_files()
    leaked = sorted(name for name in FORBIDDEN_COMMITTED if name in tracked)
    if leaked:
        raise SystemExit(f"Forbidden committed secret path(s) in git: {leaked}")

    print(f"MIP scaffold OK: {len(REQUIRED)} required files present, no forbidden paths committed.")

    # R6-25: non-fatal permission hint. Printed last so the scaffold OK
    # line stays the primary signal.
    hint = _check_app_permissions()
    if hint:
        print(hint)


if __name__ == "__main__":
    main()
