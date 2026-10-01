#!/usr/bin/env python3
"""Release gate on the last green live-validation run (audit quality-03 item 3).

Decision D-platform-process-b. A release may claim the live checks only when a
completed ``live validation (on demand)`` run (nightly.yml, dispatched on
main) certifies code that is runtime-equivalent to the release commit:

- R is the NEWEST completed run (success, failure, timed_out or
  startup_failure; cancelled and skipped runs are ignored) whose head_sha has
  an empty runtime diff to the release sha. Selecting the newest such run,
  not the newest green one, means a later red run on the same code is never
  shopped around.
- R must be green, at most ``max_age_days`` old (exactly 14.0 days passes),
  and its three release jobs must each have concluded ``success``. nightly.yml
  runs ``verify_deployed_app_contract --git-sha`` twice, so a green run really
  certifies the deployed commit it names.
- "Runtime" is deny-by-default: every path is runtime except the explicit
  NON_RUNTIME list below. genie/sample_questions.md is read at runtime, so
  ``**/*.md`` is deliberately NOT in the list; only repo-root ``*.md`` is.

Exit codes: 0 PASS, 1 FAIL, 2 API error (every caller treats 2 as FAIL).
Output carries only URLs, shas, ages, job conclusions, the reason and paths.
Standard library only; subprocesses use a fixed argv and urllib talks https
only. scripts/deploy.sh and scripts/package_source.sh never call this tool.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_FILE = "nightly.yml"
API_ROOT = "https://api.github.com"
MAX_PAGES = 5
PER_PAGE = 100
LOOKBACK_DAYS = 120
DEFAULT_MAX_AGE_DAYS = 14.0
MAX_LISTED_PATHS = 20

# The jobs a green run must carry (nightly.yml job names; pinned by
# tests/unit/test_nightly_workflow_contract.py).
PARITY_JOB = "SQL<->Python + Lakebase + Genie live"
PLAYWRIGHT_JOB = "Playwright (real-UC golden path)"
DRILL_JOB = "Credential-kill drill (simulated targets)"
REQUIRED_JOBS = (PARITY_JOB, PLAYWRIGHT_JOB, DRILL_JOB)

GREEN = frozenset({"success"})
RED = frozenset({"failure", "timed_out", "startup_failure"})

# Everything else is runtime (deny by default).
NON_RUNTIME_PREFIXES = (
    "docs/",
    ".claude/",
    "design_files/",
    ".github/",
    "tests/",
    "frontend/tests/",
    "frontend/src/test/",
)
NON_RUNTIME_SUFFIXES = (".test.ts", ".test.tsx")

FIX_HINT = "dispatch deploy-dev.yml on {sha}, then nightly.yml on the same sha"


class GateApiError(RuntimeError):
    """An HTTP, auth or parse failure talking to GitHub (exit 2, treated as FAIL)."""


@dataclass(frozen=True)
class Run:
    run_id: int
    head_sha: str
    conclusion: str
    updated_at: datetime
    html_url: str

    @property
    def green(self) -> bool:
        return self.conclusion in GREEN


@dataclass
class Verdict:
    status: str  # PASS | FAIL | API_ERROR
    reason: str | None  # no_green_run | runtime_changed | later_failure | stale | job_not_success | api_error
    release_sha: str
    run_url: str | None = None
    run_sha: str | None = None
    age_days: float | None = None
    jobs: dict[str, str] = field(default_factory=dict)
    paths: list[str] = field(default_factory=list)
    fix: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def exit_code(self) -> int:
        return {"PASS": 0, "FAIL": 1}.get(self.status, 2)


def is_non_runtime(path: str) -> bool:
    """True only for the explicit NON_RUNTIME list; every other path is runtime."""

    if path.startswith(NON_RUNTIME_PREFIXES):
        return True
    if "/" not in path and path.endswith(".md"):
        return True
    return path.startswith("frontend/src/") and path.endswith(NON_RUNTIME_SUFFIXES)


def _git(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False)


def runtime_changed_paths(a: str, b: str, git: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = _git) -> list[str] | None:
    """Runtime paths changed between two commits; None when a sha is missing from local history.

    ``--no-renames`` is load-bearing: with git's default rename detection a
    rename prints only its destination, so moving runtime code into a
    NON_RUNTIME path (backend/x.py -> tests/x.py) would read as an empty
    runtime diff. Without it a rename lists its deleted source and its added
    destination, and the source stays runtime.
    """

    for sha in (a, b):
        if git(["cat-file", "-e", f"{sha}^{{commit}}"]).returncode != 0:
            return None
    diff = git(["diff", "--name-only", "--no-renames", a, b])
    if diff.returncode != 0:
        return None
    return [path for path in diff.stdout.splitlines() if path and not is_non_runtime(path)]


def resolve_sha(ref: str, git: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] = _git) -> str:
    result = git(["rev-parse", "--verify", f"{ref}^{{commit}}"])
    if result.returncode != 0:
        raise SystemExit(f"cannot resolve release sha {ref!r} in local history")
    return result.stdout.strip()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no UTC offset")
    return parsed


def _oldest_updated(raw: list[object]) -> datetime | None:
    """The page's oldest updated_at over every raw entry, filtered-out runs included."""

    try:
        return min(
            (_parse_time(item["updated_at"]) for item in raw if isinstance(item, dict) and "updated_at" in item),
            default=None,
        )
    except (AttributeError, TypeError, ValueError) as exc:
        raise GateApiError(f"malformed run entry: {type(exc).__name__}") from exc


def parse_runs(payload: object) -> list[Run]:
    """Completed main workflow_dispatch runs with a green or red conclusion."""

    if not isinstance(payload, dict) or not isinstance(payload.get("workflow_runs"), list):
        raise GateApiError("runs payload has no workflow_runs list")
    runs: list[Run] = []
    for item in payload["workflow_runs"]:
        try:
            if item["head_branch"] != "main" or item["event"] != "workflow_dispatch":
                continue
            conclusion = item.get("conclusion") or ""
            if conclusion not in GREEN | RED:
                continue
            runs.append(
                Run(int(item["id"]), str(item["head_sha"]), conclusion, _parse_time(item["updated_at"]), str(item["html_url"]))
            )
        except (AttributeError, KeyError, TypeError, ValueError) as exc:
            raise GateApiError(f"malformed run entry: {type(exc).__name__}") from exc
    return runs


def parse_jobs(payload: object) -> dict[str, str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
        raise GateApiError("jobs payload has no jobs list")
    jobs: dict[str, str] = {}
    for item in payload["jobs"]:
        try:
            jobs[str(item["name"])] = str(item.get("conclusion") or item.get("status") or "unknown")
        except (KeyError, TypeError) as exc:
            raise GateApiError(f"malformed job entry: {type(exc).__name__}") from exc
    return jobs


Fetch = Callable[[str], object]


def list_runs(fetch: Fetch, now: datetime) -> list[Run]:
    """Paginate the workflow's completed main dispatch runs, at most 5 pages or 120 days back."""

    cutoff = now - timedelta(days=LOOKBACK_DAYS)
    runs: list[Run] = []
    for page in range(1, MAX_PAGES + 1):
        query = urllib.parse.urlencode(
            {"branch": "main", "event": "workflow_dispatch", "status": "completed", "per_page": PER_PAGE, "page": page}
        )
        payload = fetch(f"actions/workflows/{WORKFLOW_FILE}/runs?{query}")
        page_runs = parse_runs(payload)
        raw = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
        runs += [run for run in page_runs if run.updated_at >= cutoff]
        oldest = _oldest_updated(raw)
        if len(raw) < PER_PAGE or oldest is None or oldest < cutoff:
            break
    return runs


def verdict(
    runs: Sequence[Run],
    release_sha: str,
    now: datetime,
    max_age_days: float = DEFAULT_MAX_AGE_DAYS,
    diff: Callable[[str, str], list[str] | None] = runtime_changed_paths,
    jobs: Callable[[int], dict[str, str]] | None = None,
) -> Verdict:
    ordered = sorted(runs, key=lambda run: run.updated_at, reverse=True)
    diffs: dict[str, list[str] | None] = {}

    def changed(sha: str) -> list[str] | None:
        if sha not in diffs:
            diffs[sha] = diff(sha, release_sha)
        return diffs[sha]

    fix = FIX_HINT.format(sha=release_sha)
    chosen = next((run for run in ordered if changed(run.head_sha) == []), None)
    if chosen is None:
        green = next((run for run in ordered if run.green), None)
        if green is None:
            return Verdict("FAIL", "no_green_run", release_sha, fix=fix)
        paths = changed(green.head_sha)
        listed = paths[:MAX_LISTED_PATHS] if paths is not None else [f"{green.head_sha} is missing from local history"]
        return Verdict(
            "FAIL",
            "runtime_changed",
            release_sha,
            run_url=green.html_url,
            run_sha=green.head_sha,
            age_days=_age(green, now),
            paths=listed,
            fix=fix,
        )
    base = Verdict("FAIL", None, release_sha, run_url=chosen.html_url, run_sha=chosen.head_sha, age_days=_age(chosen, now))
    if not chosen.green:
        base.reason, base.fix = "later_failure", fix
        return base
    if base.age_days is not None and base.age_days > max_age_days:
        base.reason, base.fix = "stale", fix
        return base
    if jobs is None:
        raise GateApiError("no jobs reader")
    base.jobs = {name: conclusion for name, conclusion in jobs(chosen.run_id).items() if name in REQUIRED_JOBS}
    missing = {name: base.jobs.get(name, "absent") for name in REQUIRED_JOBS if base.jobs.get(name) != "success"}
    if missing:
        base.jobs = {**base.jobs, **missing}
        base.reason, base.fix = "job_not_success", fix
        return base
    base.status = "PASS"
    return base


def _age(run: Run, now: datetime) -> float:
    return round((now - run.updated_at).total_seconds() / 86400, 3)


def _https_fetch(repo: str, token: str) -> Fetch:
    def fetch(path: str) -> object:
        url = f"{API_ROOT}/repos/{repo}/{path}"
        if urllib.parse.urlparse(url).scheme != "https":
            raise GateApiError("only https is allowed")
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "mip-live-validation-gate",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise GateApiError(f"GitHub API request failed: {type(exc).__name__}") from exc

    return fetch


def _gh_fetch(path: str) -> object:
    result = subprocess.run(
        ["gh", "api", "-H", "Accept: application/vnd.github+json", f"repos/{{owner}}/{{repo}}/{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise GateApiError("gh api request failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GateApiError("gh api returned non-JSON") from exc


def github_fetch(env: dict[str, str] | None = None) -> Fetch:
    """urllib with GITHUB_TOKEN/GH_TOKEN and GITHUB_REPOSITORY, else the `gh api` fallback."""

    env = dict(os.environ if env is None else env)
    token = env.get("GITHUB_TOKEN") or env.get("GH_TOKEN")
    repo = env.get("GITHUB_REPOSITORY")
    if token and repo:
        return _https_fetch(repo, token)
    return _gh_fetch


def evaluate(
    release_sha: str,
    max_age_days: float = DEFAULT_MAX_AGE_DAYS,
    fetch: Fetch | None = None,
    now: datetime | None = None,
    diff: Callable[[str, str], list[str] | None] = runtime_changed_paths,
) -> Verdict:
    """The gate's verdict; an HTTP, auth or parse failure is API_ERROR, never PASS."""

    fetch = fetch or github_fetch()
    now = now or datetime.now(UTC)
    try:
        runs = list_runs(fetch, now)
        return verdict(
            runs,
            release_sha,
            now,
            max_age_days,
            diff=diff,
            jobs=lambda run_id: parse_jobs(fetch(f"actions/runs/{run_id}/jobs?per_page=100")),
        )
    except GateApiError as exc:
        return Verdict("API_ERROR", "api_error", release_sha, fix=f"{exc}; re-run once the GitHub API answers")


def render(result: Verdict) -> str:
    lines = [f"live-validation gate: {result.status}" + (f" ({result.reason})" if result.reason else "")]
    lines.append(f"  release sha: {result.release_sha}")
    if result.run_url:
        lines.append(f"  run: {result.run_url} on {result.run_sha}, {result.age_days} days old")
    for name, conclusion in result.jobs.items():
        lines.append(f"  job {name}: {conclusion}")
    for path in result.paths:
        lines.append(f"  runtime path changed: {path}")
    if result.fix:
        lines.append(f"  fix: {result.fix}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--release-sha", required=True, help="the release commit (a sha or HEAD)")
    parser.add_argument("--max-age-days", type=float, default=DEFAULT_MAX_AGE_DAYS)
    parser.add_argument("--json", dest="json_out", default=None, help="also write the verdict as JSON here")
    args = parser.parse_args(argv)
    result = evaluate(resolve_sha(args.release_sha), args.max_age_days)
    print(render(result))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(result.as_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
