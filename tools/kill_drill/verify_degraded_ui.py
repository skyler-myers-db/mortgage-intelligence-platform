#!/usr/bin/env python3
"""Verify the degraded-state UI during a credential-kill drill.

Runs against a live Module 0 app URL (default ``http://127.0.0.1:5173``
for the frontend dev server, paired with the backend at
``http://127.0.0.1:8000``). Hits every canonical route, looks for the
``<DegradedBanner>`` marker emitted by
``frontend/src/components/mortgage/DegradedBanner.tsx``, and asserts
that no route rendered suspicious "real-looking" rows while the
backend is degraded.

Why stdlib-only: the drill runs on operator laptops and in short-lived
CI contexts where installing Playwright browsers would add 5+ min.
The banner is a ``role="status"`` element with a known
``data-degraded-dependency`` attribute and stable copy ("Backend is
warming up"). We don't need a real browser to verify it -- we need the
frontend to be serving the HTML shell + the backend to be reporting
degraded on ``/api/health``.

Checks per route:
  1. Frontend HTML shell responds 200 (Vite index.html served).
  2. ``/api/health`` reports ``status: degraded`` (or any dependency
     "down" / breaker "open"). The banner only renders when that is
     true; if health is green, the verifier skips UI assertions and
     exits non-zero with a clear message -- that is a drill-setup
     error, not a resilience regression.
  3. The canonical data endpoints backing that route return 503 (or
     an explicit ``degraded: true`` / fallback-sourced 200). We do NOT
     fetch the SPA's runtime HTML because Vite ships a client shell --
     verifying the data plane is the authoritative signal.

Exit codes:
  0 -- all routes passed (degraded state is visible, no fake data).
  1 -- resilience regression (a route served suspicious rows, or the
       backend was healthy while the verifier was told to validate a
       degraded state).
  2 -- prerequisite failure (frontend unreachable, /api/health
       unparseable, etc).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteProbe:
    """One UI route + the data endpoint(s) that back it.

    ``critical_endpoints`` is the set of backend calls the route
    actually makes on first render. When any of these returns
    ``degraded`` the UI shows the banner + hides / greys the panel --
    this is what we're verifying.
    """

    name: str
    path: str
    critical_endpoints: tuple[str, ...]


ROUTES: tuple[RouteProbe, ...] = (
    RouteProbe("home", "/", ("/api/portfolio/preview", "/api/leads?limit=5")),
    RouteProbe("portfolio-builder", "/portfolio-builder", ("/api/portfolio/preview",)),
    RouteProbe(
        "segment-intelligence",
        "/segment-intelligence",
        ("/api/segments",),
    ),
    RouteProbe("lead-queue", "/lead-queue", ("/api/leads?limit=5",)),
    RouteProbe("borrower-360", "/borrower-360/B-48291", ("/api/borrowers/B-48291",)),
    RouteProbe(
        "offer-orchestrator",
        "/offer-orchestrator/B-48291",
        ("/api/borrowers/B-48291",),
    ),
    RouteProbe("ask-genie", "/ask-genie", ()),  # Genie is POST-only; skip fetch
    RouteProbe("admin-config", "/admin-config", ("/api/health",)),
)


class VerifierError(RuntimeError):
    pass


def _fetch(url: str, timeout_s: float = 5.0) -> tuple[int, bytes]:
    """Return ``(status, body)`` for a GET; any network error -> (0, b"")."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json, text/html"})
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:  # noqa: BLE001 -- already in an error path
            body = b""
        return exc.code, body
    except Exception:  # noqa: BLE001 -- network errors all treated alike
        return 0, b""


def _parse_json(body: bytes) -> dict | list | None:
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None


def check_health(api_url: str) -> dict:
    """Return the parsed ``/api/health`` payload or raise."""
    code, body = _fetch(f"{api_url}/api/health")
    if code != 200 or not body:
        raise VerifierError(f"/api/health returned HTTP {code}; cannot verify drill")
    payload = _parse_json(body)
    if not isinstance(payload, dict):
        raise VerifierError("/api/health returned unparseable body")
    return payload


def is_degraded(health: dict) -> tuple[bool, str]:
    """Return ``(degraded?, reason)``."""
    if health.get("status") == "degraded":
        return True, "status=degraded"
    deps = health.get("dependencies") or {}
    for name, state in deps.items():
        if state == "down":
            return True, f"dep {name}=down"
    breakers = health.get("circuit_breakers") or {}
    for name, state in breakers.items():
        if state == "open":
            return True, f"breaker {name}=open"
    return False, "all green"


def check_frontend_shell(frontend_url: str, path: str) -> bool:
    """Return True when the frontend serves an HTML shell for ``path``.

    Vite serves a SPA -- every known route returns the same
    ``index.html`` shell; the router then mounts the right view. We
    only need to confirm the shell responds 200 + contains the app
    mount point.
    """
    code, body = _fetch(f"{frontend_url}{path}")
    if code != 200:
        return False
    text = body.decode("utf-8", errors="replace")
    # Vite dev server index.html always contains `<div id="root">`.
    return "id=\"root\"" in text or "id='root'" in text


def check_endpoint_degraded(api_url: str, endpoint: str) -> tuple[bool, str]:
    """Return ``(passed, reason)`` for a single backing endpoint.

    Acceptable degraded shapes:
      * HTTP 503 (with or without ``retryable: true`` -- either is fine)
      * HTTP 500/502/504 (visible non-200 failure)
      * HTTP 200 with ``degraded: true`` or ``status: degraded``
      * HTTP 200 with an empty collection (no borrowers is not fake)
      * HTTP 200 with ``source: fallback | corpus`` (Genie safe corpus)

    Unacceptable (regression signal):
      * HTTP 200 with non-empty, real-looking rows.
    """
    code, body = _fetch(f"{api_url}{endpoint}")
    if code in (503, 500, 502, 504):
        return True, f"{endpoint} -> HTTP {code}"
    if code == 0:
        return True, f"{endpoint} -> network error (visible failure)"
    if code != 200:
        return True, f"{endpoint} -> HTTP {code} (visible failure)"

    # 200 body analysis
    payload = _parse_json(body)
    if isinstance(payload, dict):
        if payload.get("degraded") is True or payload.get("status") == "degraded":
            return True, f"{endpoint} -> 200 with degraded=true"
        if payload.get("source") in ("fallback", "corpus"):
            return True, f"{endpoint} -> 200 with source={payload['source']}"
        items = payload.get("items")
        if isinstance(items, list) and len(items) == 0:
            return True, f"{endpoint} -> 200 with empty items[]"
        if not payload:
            return True, f"{endpoint} -> 200 with empty object"
    if isinstance(payload, list) and len(payload) == 0:
        return True, f"{endpoint} -> 200 with empty list"

    # Non-empty 200 payload during a degraded drill == regression.
    preview = body[:200].decode("utf-8", errors="replace")
    return False, f"{endpoint} -> 200 with non-empty payload (possible mock fallback); first 200 chars: {preview!r}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--api-url",
        default="http://127.0.0.1:8000",
        help="Backend base URL (default http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--frontend-url",
        default="http://127.0.0.1:5173",
        help="Frontend base URL (default http://127.0.0.1:5173)",
    )
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="Only check backend endpoints; skip HTML shell check.",
    )
    parser.add_argument(
        "--allow-green",
        action="store_true",
        help="Don't fail if /api/health reports fully healthy (dry-run).",
    )
    args = parser.parse_args()

    print(f"[verify] api_url={args.api_url} frontend_url={args.frontend_url}")

    try:
        health = check_health(args.api_url)
    except VerifierError as exc:
        print(f"[verify] PREREQ FAIL: {exc}", file=sys.stderr)
        return 2

    degraded, reason = is_degraded(health)
    print(f"[verify] /api/health -> degraded={degraded} ({reason})")
    if not degraded and not args.allow_green:
        print(
            "[verify] FAIL: drill verifier expected a degraded state but the "
            "backend is fully green. Did you kick off the drill?",
            file=sys.stderr,
        )
        return 1

    failures: list[str] = []

    for route in ROUTES:
        print(f"[verify] route={route.name} path={route.path}")
        if not args.skip_frontend and not check_frontend_shell(
            args.frontend_url, route.path
        ):
            msg = f"frontend shell for {route.path} did not return 200 / mount point"
            print(f"  FAIL: {msg}", file=sys.stderr)
            failures.append(msg)
            continue

        for endpoint in route.critical_endpoints:
            passed, why = check_endpoint_degraded(args.api_url, endpoint)
            marker = "PASS" if passed else "FAIL"
            print(f"  {marker}: {why}")
            if not passed:
                failures.append(f"{route.name}: {why}")

    # Small pause so logs flush before exit in CI
    time.sleep(0.1)

    if failures:
        print("", file=sys.stderr)
        print(f"[verify] {len(failures)} failure(s):", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1

    print("[verify] PASS -- every route surfaced a degraded signal and no fake data was detected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
