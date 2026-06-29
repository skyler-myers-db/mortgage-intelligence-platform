#!/usr/bin/env python3
"""Run live Mortgage Growth Agent golden eval and log it to Databricks MLflow.

This is an operator validation tool, not an app runtime fallback. It exercises
the deployed `/api/growth-agent/agent/run` endpoint with the same JSON content
type a browser client sends, scores the returned governed workflow payloads
with `tests/eval/scorers.py`, and records the result in a Databricks experiment
that the app capability probe can later verify.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tests.eval.scorers import load_cases, score_batch  # noqa: E402


def _run(args: list[str], *, input_json: dict[str, Any] | None = None) -> dict[str, Any]:
    cmd = ["databricks", *args, "-o", "json"]
    if input_json is not None:
        cmd = ["databricks", *args, "--json", json.dumps(input_json), "-o", "json"]
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return json.loads(proc.stdout or "{}")


def _run_no_output(args: list[str], *, input_json: dict[str, Any] | None = None) -> None:
    cmd = ["databricks", *args]
    if input_json is not None:
        cmd = ["databricks", *args, "--json", json.dumps(input_json)]
    proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: {proc.stderr.strip() or proc.stdout.strip()}")


def _experiment_id(name: str) -> str:
    try:
        existing = _run(["experiments", "get-by-name", name])
    except RuntimeError:
        created = _run(["experiments", "create-experiment", name])
        return str(created.get("experiment_id") or created.get("experiment", {}).get("experiment_id") or "")
    experiment = existing.get("experiment") or existing
    return str(experiment.get("experiment_id") or "")


def _git_sha() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO,
        text=True,
        capture_output=True,
        check=True,
    )
    return proc.stdout.strip()


def _call_growth_agent(
    *,
    app_url: str,
    token: str,
    case: dict[str, Any],
    timeout_s: float,
) -> dict[str, Any]:
    payload = {
        "prompt": str(case["prompt"]),
        "request_id": f"eval-{uuid.uuid4()}",
        "save_monitor": False,
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    url = f"{app_url.rstrip('/')}/api/growth-agent/agent/run"
    with httpx.Client(timeout=timeout_s, follow_redirects=False) as client:
        response = client.post(url, headers=headers, json=payload)
    try:
        body = response.json()
    except json.JSONDecodeError:
        body = {"detail": response.text[:500]}
    if response.status_code >= 400:
        if isinstance(body, dict):
            return {"error": str(body.get("detail") or body)}
        return {"error": str(body)}
    if not isinstance(body, dict):
        return {"error": f"expected JSON object, got {type(body).__name__}"}
    return body


def _log_eval_run(
    *,
    experiment_name: str,
    app_url: str,
    summary: dict[str, Any],
    responses_by_case_id: dict[str, dict[str, Any]],
) -> str:
    experiment_id = _experiment_id(experiment_name)
    if not experiment_id:
        raise RuntimeError(f"Could not create or resolve experiment {experiment_name}")
    now_ms = int(time.time() * 1000)
    run = _run(
        ["experiments", "create-run"],
        input_json={
            "experiment_id": experiment_id,
            "run_name": f"mip-growth-agent-golden-{now_ms}",
            "start_time": now_ms,
        },
    )
    run_info = run.get("run", {}).get("info") or run.get("info") or run
    run_id = str(run_info.get("run_id") or "")
    if not run_id:
        raise RuntimeError(f"Experiment run response missing run_id: {run}")
    git_sha = _git_sha()
    _run_no_output(
        ["experiments", "log-batch", "--run-id", run_id],
        input_json={
            "metrics": [
                {"key": "score", "value": float(summary["score"]), "timestamp": now_ms, "step": 0},
                {"key": "passed", "value": float(summary["passed"]), "timestamp": now_ms, "step": 0},
                {"key": "total", "value": float(summary["total"]), "timestamp": now_ms, "step": 0},
            ],
            "params": [
                {"key": "git_sha", "value": git_sha[:250]},
                {"key": "app_url", "value": app_url[:250]},
                {"key": "case_count", "value": str(summary["total"])},
            ],
            "tags": [
                {"key": "mip_eval_type", "value": "growth_agent_golden"},
                {"key": "mip_git_sha", "value": git_sha[:250]},
            ],
        },
    )
    # Keep the full payload local and concise in MLflow. The app capability
    # probe only needs the metrics and params above to prove the latest run.
    failures = [row for row in summary["results"] if not row["passed"]]
    _run_no_output(
        ["experiments", "set-tag", "--run-id", run_id, "--key", "mip_eval_failures", "--value", json.dumps(failures)[:250]],
    )
    _run_no_output(
        ["experiments", "update-run", "--run-id", run_id, "--status", "FINISHED", "--end-time", str(int(time.time() * 1000))],
    )
    _ = responses_by_case_id
    return run_id


def _write_env(path: Path, *, experiment_name: str, run_id: str) -> None:
    def assignment(key: str, value: str) -> str:
        return f"{key}={shlex.quote(value)}"

    path.write_text(
        "\n".join(
            [
                assignment("MIP_AGENT_EVAL_EXPERIMENT", experiment_name),
                assignment("MIP_AGENT_EVAL_RUN_ID", run_id),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"[agent-eval] wrote env file: {path}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-url", default=os.environ.get("MIP_APP_URL", ""))
    parser.add_argument("--token", default=os.environ.get("MIP_BEARER_TOKEN", ""))
    parser.add_argument("--experiment", default=os.environ.get("MIP_AGENT_EVAL_EXPERIMENT", "/Shared/mip-agent-eval"))
    parser.add_argument("--cases", type=Path, default=REPO / "tests" / "eval" / "golden_agent_cases.jsonl")
    parser.add_argument("--timeout-s", type=float, default=60.0)
    parser.add_argument("--out-env", type=Path)
    parser.add_argument("--out-json", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.app_url:
        raise ValueError("--app-url or MIP_APP_URL is required")
    if not args.token:
        raise ValueError("--token or MIP_BEARER_TOKEN is required")
    cases = load_cases(args.cases)
    responses: dict[str, dict[str, Any]] = {}
    for case in cases:
        case_id = str(case["id"])
        print(f"[agent-eval] running {case_id}: {case['prompt']}")
        responses[case_id] = _call_growth_agent(
            app_url=args.app_url,
            token=args.token,
            case=case,
            timeout_s=args.timeout_s,
        )
    summary = score_batch(responses, cases)
    run_id = _log_eval_run(
        experiment_name=args.experiment,
        app_url=args.app_url,
        summary=summary,
        responses_by_case_id=responses,
    )
    result = {"run_id": run_id, "experiment": args.experiment, **summary}
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.out_json:
        args.out_json.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.out_env:
        _write_env(args.out_env, experiment_name=args.experiment, run_id=run_id)
    if summary["passed"] != summary["total"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
