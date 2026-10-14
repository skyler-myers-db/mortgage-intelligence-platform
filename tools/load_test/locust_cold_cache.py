"""Cold-cache Locust profile (audit delivery-09): does a cold App exhaust its slots?

Operator-only, like ``locustfile.py``: ``pip install locust`` on the workstation;
never a runtime dependency. Run against a deployed App:

    MIP_API_URL=https://<app>.databricksapps.com MIP_BEARER_TOKEN=... \\
      locust -f tools/load_test/locust_cold_cache.py --headless \\
      -u 30 -r 30 --run-time 5m --host "$MIP_API_URL"

Each of the 30 users fires the first-load sequence once, back to back (the
shell and Home a browser opens on a cold process), then runs weighted tasks at
between(1, 3) s. The DEFAULT profile is audit-free reads only:

* GET /health, /session, /home/summary, /segments, /geo/state-rollups,
  /geo/rate-sensitivity, /config/options, /analytics/rate-window;
* POST /portfolio/preview with ``{}`` and POST /genie/start with ``{}`` (both
  read-only; /genie/start reads Lakebase and static files only).

/leads is opt-in through ``MIP_COLD_CACHE_INCLUDE_AUDITED=1`` because each
/leads page writes a VIEW_LEADS audit row. /borrowers/{id} is never called.
Every request is named by its route template, so no id reaches the stats.
The bearer comes from ``MIP_BEARER_TOKEN`` and is never printed or logged.

On quit it writes ``tools/load_test/results/<UTC>-cold-cache.json`` and prints
the summary and the verdict (``cold_cache_analysis``).
"""
from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from locust import HttpUser, between, events

try:
    from tools.load_test.cold_cache_analysis import Sample, summarize
except ImportError:  # Locust puts the locustfile's own directory on sys.path.
    from cold_cache_analysis import Sample, summarize  # type: ignore[no-redef]

API_PREFIX = "/" + (os.environ.get("MIP_API_PREFIX", "/api/v1").strip() or "/api/v1").strip("/")
INCLUDE_AUDITED = os.environ.get("MIP_COLD_CACHE_INCLUDE_AUDITED", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
RESULTS_DIR = Path(__file__).resolve().parent / "results"

# (method, route template), in the order a cold browser open issues them.
FIRST_LOAD: tuple[tuple[str, str], ...] = (
    ("GET", "health"),
    ("GET", "session"),
    ("GET", "config/options"),
    ("GET", "home/summary"),
    ("POST", "portfolio/preview"),
    ("GET", "geo/state-rollups"),
    ("GET", "geo/rate-sensitivity"),
    ("GET", "segments"),
    ("GET", "analytics/rate-window"),
    ("POST", "genie/start"),
)
# The only POSTs the profile may send: both are read-only.
READ_ONLY_POSTS = frozenset({"portfolio/preview", "genie/start"})

_SAMPLES: list[Sample] = []
_RUN_START: list[float] = []


def _route(path: str) -> str:
    return f"{API_PREFIX}/{path}"


@events.test_start.add_listener
def _on_test_start(**_kwargs: Any) -> None:
    _SAMPLES.clear()
    _RUN_START[:] = [time.time()]


@events.request.add_listener
def _on_request(
    request_type: str,
    name: str,
    response_time: float,
    response_length: int,
    response: Any = None,
    exception: BaseException | None = None,
    start_time: float | None = None,
    **_kwargs: Any,
) -> None:
    started = (start_time or time.time()) - (_RUN_START[0] if _RUN_START else time.time())
    status = int(getattr(response, "status_code", 0) or 0)
    body: dict[str, Any] | None = None
    if status in (429, 503) and response is not None:
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
        body = parsed if isinstance(parsed, dict) else None
    _SAMPLES.append(
        Sample(name=f"{request_type} {name}", started_s=started, elapsed_ms=response_time, status=status, body=body)
    )


@events.quitting.add_listener
def _on_quitting(**_kwargs: Any) -> None:
    summary = summarize(
        _SAMPLES,
        stall_routes=(f"GET {_route('health')}", f"GET {_route('session')}"),
    )
    summary["profile"] = {
        "users": "30 at spawn rate 30 (-u 30 -r 30)",
        "include_audited": INCLUDE_AUDITED,
        "first_load": [f"{method} {_route(path)}" for method, path in FIRST_LOAD],
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    target = RESULTS_DIR / f"{stamp}-cold-cache.json"
    target.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"cold-cache verdict: {summary['verdict']} (written to {target.name})")


def _get(path: str) -> Any:
    def run(user: ColdCacheUser) -> None:
        user._call("GET", path)

    run.__name__ = f"get_{path.replace('/', '_').replace('-', '_')}"
    return run


def _post(path: str) -> Any:
    def run(user: ColdCacheUser) -> None:
        user._call("POST", path)

    run.__name__ = f"post_{path.replace('/', '_').replace('-', '_')}"
    return run


_TASKS: dict[Any, int] = {
    _get("health"): 2,
    _get("session"): 2,
    _get("home/summary"): 3,
    _get("segments"): 2,
    _get("geo/state-rollups"): 2,
    _get("geo/rate-sensitivity"): 1,
    _get("config/options"): 1,
    _get("analytics/rate-window"): 1,
    _post("portfolio/preview"): 3,
    _post("genie/start"): 1,
}
if INCLUDE_AUDITED:
    # Each page writes a VIEW_LEADS audit row: opt-in only.
    _TASKS[_get("leads")] = 3


class ColdCacheUser(HttpUser):
    """One cold browser: the first-load burst, then audit-free reads."""

    wait_time = between(1, 3)

    def on_start(self) -> None:
        bearer = os.environ.get("MIP_BEARER_TOKEN", "").strip()
        if bearer:
            self.client.headers.update({"Authorization": f"Bearer {bearer}"})
        for method, path in FIRST_LOAD:
            self._call(method, path)

    def _call(self, method: str, path: str) -> None:
        url = _route(path)
        if method == "POST":
            if path not in READ_ONLY_POSTS:
                raise ValueError("the cold-cache profile sends only read-only POSTs")
            self.client.post(url, json={}, name=url)
        else:
            self.client.get(url, name=url)

    tasks = _TASKS
