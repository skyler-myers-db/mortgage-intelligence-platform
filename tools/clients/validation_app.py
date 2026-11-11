"""The deployed App's own answers that nightly.yml checks (M1-PR8).

Two read-only calls to the App API with a per-run bearer, each a step of
``python -m tools.clients.validation_session``:

* ``app-sha --token-env NAME --git-sha SHA``: the authenticated
  ``/api/v1/health`` must name the run's commit, which is what lets a green run
  certify its own sha (tools/live_validation_gate.py).
* ``install-class --token-env NAME --github-output PATH``: the App's
  ``/api/v1/admin/health`` ``app_env`` decides whether the App API mutation
  suites may run. Their own guard refuses any App that is not ``dev`` or
  ``sandbox``, so on a ``prod`` App they are skipped (a gap
  docs/deployment.md lists) rather than run to a certain failure; any other
  answer refuses, so an unknown install class never certifies a run.

Output is codes and public names, never a value.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

Fetch = Callable[[str, str], tuple[int, object]]
# app_env values whose App runs the App API mutation suites, and the one that
# skips them. The suites' own guard (``app_env in {"dev", "sandbox"}``) is the
# same set: tests/unit/test_nightly_workflow_contract.py holds them equal.
MUTATION_APP_ENVS = frozenset({"dev", "sandbox"})
SKIP_APP_ENVS = frozenset({"prod"})
MUTATION_OUTPUT = "app_api_mutation"


def _get_json(url: str, token: str) -> tuple[int, object]:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - the App's https origin
            return int(response.status), json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return int(exc.code), None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


def _health(url: str, token: str) -> tuple[int, object]:
    return _get_json(f"{url}/api/v1/health", token)


def _admin_health(url: str, token: str) -> tuple[int, object]:
    return _get_json(f"{url}/api/v1/admin/health", token)


def _answer(
    label: str,
    args: argparse.Namespace,
    environ: Mapping[str, str],
    fetch: Fetch,
    sleep: Callable[[float], None],
) -> tuple[int, dict[str, object] | None]:
    """The App's 200 JSON answer after the warm-up retries, or (last status, None)."""

    url = environ.get("MIP_APP_URL", "").strip().rstrip("/")
    token = environ.get(args.token_env, "").strip()
    if not url.startswith("https://") or not token:
        print(f"::error::{label} needs MIP_APP_URL and {args.token_env}")
        return -1, None
    status, body = 0, None
    for attempt in range(args.attempts):
        status, body = fetch(url, token)
        if status == 200:
            break
        if attempt + 1 < args.attempts:
            sleep(args.interval)
    if status != 200 or not isinstance(body, dict):
        return status, None
    return status, body


def app_sha(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    health: Fetch = _health,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    status, body = _answer("app-sha", args, environ, health, sleep)
    if status == -1:
        return 2
    if body is None:
        print(f"::error::the deployed App's authenticated health did not answer 200 (last status {status})")
        return 2
    if body.get("git_sha") != args.git_sha:
        print("::error::the deployed App does not run this run's commit; dispatch nightly.yml on the deployed sha")
        return 2
    print(f"deployed App git sha matches {args.git_sha} (identity: {args.token_env})")
    return 0


def install_class(
    args: argparse.Namespace,
    environ: Mapping[str, str],
    admin_health: Fetch = _admin_health,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    status, body = _answer("install-class", args, environ, admin_health, sleep)
    if status == -1:
        return 2
    if body is None:
        print(f"::error::the App's admin health did not answer 200 (last status {status}); the install class is unknown")
        return 2
    app_env = body.get("app_env")
    if app_env in MUTATION_APP_ENVS:
        decision = "run"
        print(f"app_env={app_env}: the App API mutation suites run")
    elif app_env in SKIP_APP_ENVS:
        decision = "skip"
        print(
            f"::notice::app_env={app_env}: the App API mutation suites are skipped on this install "
            "(docs/deployment.md, 'Live validation per client', gaps)"
        )
    else:
        print("::error::the App reports no known app_env (dev, sandbox or prod); refusing to decide the mutation suites")
        return 2
    with Path(args.github_output).open("a", encoding="utf-8") as handle:
        handle.write(f"{MUTATION_OUTPUT}={decision}\n")
    return 0
