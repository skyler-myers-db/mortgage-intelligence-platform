"""Poll a deployed MIP app until live dependencies and breakers are ready.

The public health endpoint intentionally returns HTTP 200 even for degraded
state so Databricks Apps load balancers do not evict a warm container. Live
release gates need a stricter contract before expensive Genie/Playwright work:
dependencies must be up and selected circuit breakers must be closed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

DEFAULT_DEPS = ("warehouse", "lakebase", "genie")


def _request_json(
    base: str,
    path: str,
    token: str | None,
    timeout_s: int,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{base.rstrip('/')}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 -- internal app URL
            raw = resp.read().decode("utf-8")
            return int(resp.status), json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {"detail": raw}
        return int(exc.code), body if isinstance(body, dict) else {"detail": body}


def _health_payload(base: str, token: str | None, request_timeout_s: int) -> tuple[str, int, dict[str, Any]]:
    """Prefer admin diagnostics; fall back to authenticated browser health."""

    status, body = _request_json(base, "/api/admin/health", token, request_timeout_s)
    if status == 200:
        return "/api/admin/health", status, body
    if status not in {401, 403, 404}:
        return "/api/admin/health", status, body
    path, status, body = "/api/health", *_request_json(base, "/api/health", token, request_timeout_s)
    return path, status, body


def _genie_probe(
    base: str,
    token: str | None,
    request_timeout_s: int,
    question: str,
) -> tuple[int, dict[str, Any]]:
    return _request_json(
        base,
        "/api/genie/message",
        token,
        request_timeout_s,
        method="POST",
        payload={"question": question},
    )


def _readiness_errors(body: dict[str, Any], *, deps: tuple[str, ...], breakers: tuple[str, ...]) -> list[str]:
    errors: list[str] = []
    status = body.get("status")
    if status != "ok":
        errors.append(f"status={status!r}")

    dependencies = body.get("dependencies")
    if not isinstance(dependencies, dict):
        errors.append("dependencies missing")
    else:
        for dep in deps:
            if dependencies.get(dep) != "up":
                errors.append(f"dependencies.{dep}={dependencies.get(dep)!r}")

    circuit_breakers = body.get("circuit_breakers")
    if not isinstance(circuit_breakers, dict):
        if breakers:
            errors.append("circuit_breakers missing")
    else:
        for breaker in breakers:
            if circuit_breakers.get(breaker) != "closed":
                errors.append(f"circuit_breakers.{breaker}={circuit_breakers.get(breaker)!r}")
    return errors


def wait_ready(
    *,
    base: str,
    token: str | None,
    timeout_s: int,
    interval_s: float,
    request_timeout_s: int,
    deps: tuple[str, ...] = DEFAULT_DEPS,
    breakers: tuple[str, ...] = DEFAULT_DEPS,
    genie_probe_question: str | None = None,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    attempt = 0
    last: tuple[str, int, dict[str, Any], list[str]] | None = None
    while True:
        attempt += 1
        path, status, body = _health_payload(base, token, request_timeout_s)
        if status == 200:
            errors = _readiness_errors(body, deps=deps, breakers=breakers)
        else:
            errors = [f"{path} HTTP {status}"]
        if not errors:
            print(f"[wait-app-ready] ready after {attempt} attempt(s) via {path}")
            return body
        last = (path, status, body, errors)
        if (
            genie_probe_question
            and "genie" in breakers
            and isinstance(body.get("dependencies"), dict)
            and body["dependencies"].get("genie") == "up"
            and isinstance(body.get("circuit_breakers"), dict)
            and body["circuit_breakers"].get("genie") in {"open", "half_open"}
        ):
            probe_status, probe_body = _genie_probe(
                base,
                token,
                request_timeout_s,
                genie_probe_question,
            )
            source = probe_body.get("source") if isinstance(probe_body, dict) else None
            print(
                "[wait-app-ready] genie warm-up probe "
                f"status={probe_status} source={source!r}",
                flush=True,
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            path, status, body, errors = last
            raise TimeoutError(
                "app did not become ready within "
                f"{timeout_s}s; last={path} HTTP {status}; errors={'; '.join(errors)}; "
                f"body={json.dumps(body, sort_keys=True)[:4000]}"
            )
        print(
            "[wait-app-ready] not ready "
            f"attempt={attempt} path={path} status={status} errors={'; '.join(errors)}",
            flush=True,
        )
        sleep(min(interval_s, max(0.0, remaining)))


def _csv(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Deployed app base URL")
    parser.add_argument("--token", default=os.environ.get("MIP_BEARER_TOKEN"))
    parser.add_argument("--timeout-s", type=int, default=300)
    parser.add_argument("--interval-s", type=float, default=10.0)
    parser.add_argument("--request-timeout-s", type=int, default=30)
    parser.add_argument("--deps", default=",".join(DEFAULT_DEPS))
    parser.add_argument("--breakers", default=",".join(DEFAULT_DEPS))
    parser.add_argument(
        "--genie-probe-question",
        default="",
        help=(
            "Optional governed /api/genie/message prompt used to close a "
            "half-open Genie breaker before semantic scoring."
        ),
    )
    args = parser.parse_args(argv)

    try:
        wait_ready(
            base=args.base,
            token=args.token,
            timeout_s=args.timeout_s,
            interval_s=args.interval_s,
            request_timeout_s=args.request_timeout_s,
            deps=_csv(args.deps),
            breakers=_csv(args.breakers),
            genie_probe_question=args.genie_probe_question.strip() or None,
        )
    except Exception as exc:  # noqa: BLE001 -- CLI diagnostic boundary
        print(f"[wait-app-ready] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
