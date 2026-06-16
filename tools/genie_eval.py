#!/usr/bin/env python
"""Genie eval — score live answers against tools/genie_eval_questions.yml.

Hits POST /api/genie/message for each question in the YAML, scores
the response on four checks (must_cite, forbid_keywords,
require_keywords, min_rows), records latency, and emits a markdown
report.

Designed to run BOTH:

  - locally / in CI:  `python tools/genie_eval.py --base http://localhost:8000`
  - against deployed: `python tools/genie_eval.py --base $MIP_APP_URL --token $TOK`

The Databricks Apps OAuth token is required for the deployed path
(the app is gated behind workspace identity). Pass it via --token,
$DATABRICKS_TOKEN, or have ~/.databrickscfg set up so the SDK can
mint one.

Output: writes a markdown report to docs/genie_eval/<UTC-stamp>.md
plus a JSON summary at docs/genie_eval/latest.json that downstream
jobs / dashboards can read. A regression is flagged when the
overall score drops > 10 points from docs/genie_eval/baseline.json
(committed alongside the YAML; bump it intentionally when you
believe the new floor is the new baseline).

Wired into the GitHub nightly real-UC workflow against the deployed
Databricks App. Failures are release-gating by default: any failed
canonical question or baseline regression returns a non-zero exit code.
Use ``--soft`` only for exploratory runs where writing the report is enough.
"""
from __future__ import annotations

import argparse
import email.message
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
QUESTIONS_PATH = REPO_ROOT / "tools" / "genie_eval_questions.yml"
REPORT_DIR = REPO_ROOT / "docs" / "genie_eval"
BASELINE_PATH = REPORT_DIR / "baseline.json"
LATEST_PATH = REPORT_DIR / "latest.json"
REGRESSION_THRESHOLD = 10.0  # points
DEFAULT_CATALOG = "mip"
DEFAULT_ATTEMPTS = int(os.environ.get("MIP_GENIE_EVAL_ATTEMPTS", "4"))
DEFAULT_RETRY_BACKOFF_S = float(os.environ.get("MIP_GENIE_EVAL_RETRY_BACKOFF_S", "20"))
DEFAULT_PACE_S = float(os.environ.get("MIP_GENIE_EVAL_PACE_S", "2"))
_RETRYABLE_HTTP_CODES = {429, 502, 503, 504}

log = logging.getLogger("genie_eval")


@dataclass
class QuestionScore:
    """Per-question result. The four sub-scores are independent so a
    regression report can flag exactly which check started failing.
    """

    id: str
    category: str
    question: str
    answer: str
    score: float = 0.0
    cite_ok: bool = True
    forbid_ok: bool = True
    require_ok: bool = True
    rows_ok: bool = True
    latency_ok: bool = True
    numeric_ok: bool = True
    trusted_ok: bool = True
    sql_ok: bool = True
    freshness_ok: bool = True
    canonical_ok: bool = True
    latency_s: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return (
            self.cite_ok
            and self.forbid_ok
            and self.require_ok
            and self.rows_ok
            and self.numeric_ok
            and self.trusted_ok
            and self.sql_ok
            and self.freshness_ok
            and self.canonical_ok
        )


def _load_questions() -> list[dict[str, Any]]:
    with QUESTIONS_PATH.open() as f:
        data = yaml.safe_load(f)
    questions = data.get("questions") or []
    if not isinstance(questions, list):
        raise ValueError(f"{QUESTIONS_PATH} 'questions' must be a list")
    return _render_catalog_templates(questions, catalog=_configured_catalog())


def _configured_catalog() -> str:
    return (os.environ.get("MIP_DEFAULT_CATALOG") or DEFAULT_CATALOG).strip() or DEFAULT_CATALOG


def _render_catalog_templates(value: Any, *, catalog: str) -> Any:
    if isinstance(value, str):
        rendered = value.replace("{catalog}", catalog)
        if catalog != DEFAULT_CATALOG:
            for schema in ("gold", "semantics", "silver", "ref", "raw"):
                rendered = rendered.replace(
                    f"{DEFAULT_CATALOG}.{schema}",
                    f"{catalog}.{schema}",
                )
        return rendered
    if isinstance(value, list):
        return [_render_catalog_templates(item, catalog=catalog) for item in value]
    if isinstance(value, dict):
        return {key: _render_catalog_templates(item, catalog=catalog) for key, item in value.items()}
    return value


def _request_payload(base: str, token: str | None, question: str) -> urllib.request.Request:
    url = f"{base.rstrip('/')}/api/genie/message"
    body = json.dumps({"question": question}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, data=body, method="POST", headers=headers)


def _http_error_payload(exc: urllib.error.HTTPError) -> dict[str, Any]:
    try:
        raw = exc.read()
    except Exception:  # noqa: BLE001 -- best effort diagnostic only
        raw = b""
    if not raw:
        return {}
    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001 -- non-JSON error bodies are valid
        return {}
    return payload if isinstance(payload, dict) else {}


def _retry_after_s(
    *,
    code: int,
    headers: email.message.Message,
    payload: dict[str, Any],
    attempt: int,
    retry_backoff_s: float,
) -> float:
    retry_after = headers.get("Retry-After")
    if retry_after:
        try:
            return max(0.0, float(retry_after))
        except ValueError:
            pass

    for key in ("retry_after_seconds", "retry_after_s"):
        value = payload.get(key)
        if isinstance(value, int | float):
            return max(0.0, float(value))
    detail = payload.get("detail")
    if isinstance(detail, dict):
        for key in ("retry_after_seconds", "retry_after_s"):
            value = detail.get(key)
            if isinstance(value, int | float):
                return max(0.0, float(value))

    # Genie's app-side breaker cools down after 20s. Respect that by default
    # so the eval can run immediately after the live Genie regression suite.
    multiplier = max(1, attempt)
    if code == 429:
        multiplier += 1
    return max(0.0, retry_backoff_s * multiplier)


def _ask(
    base: str,
    token: str | None,
    question: str,
    timeout_s: int,
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    retry_backoff_s: float = DEFAULT_RETRY_BACKOFF_S,
    sleep: Any = time.sleep,
) -> tuple[dict[str, Any], float]:
    """Hit /api/genie/message; return (response_json, elapsed_s).

    The eval often runs immediately after the live Genie regression suite.
    That can leave the deployed app's Genie breaker half-open or cooling down.
    Retry only clearly transient HTTP failures; policy/refusal failures still
    score normally through the returned response body.
    """
    attempts = max(1, attempts)
    start = time.monotonic()
    last_error: dict[str, Any] | None = None
    for attempt in range(1, attempts + 1):
        req = _request_payload(base, token, question)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 -- internal API
                payload = json.loads(resp.read().decode("utf-8"))
            if payload.get("source") == "degraded" and attempt < attempts:
                delay_s = retry_backoff_s * attempt
                log.warning(
                    "  transient degraded Genie response; retrying in %.1fs (%d/%d)",
                    delay_s,
                    attempt + 1,
                    attempts,
                )
                sleep(delay_s)
                continue
            elapsed = time.monotonic() - start
            return payload, elapsed
        except urllib.error.HTTPError as exc:
            payload = _http_error_payload(exc)
            retryable = exc.code in _RETRYABLE_HTTP_CODES and attempt < attempts
            last_error = {
                "error": f"HTTP {exc.code}: {exc.reason}",
                "http_status": exc.code,
                "attempts": attempt,
            }
            if retryable:
                delay_s = _retry_after_s(
                    code=exc.code,
                    headers=exc.headers,
                    payload=payload,
                    attempt=attempt,
                    retry_backoff_s=retry_backoff_s,
                )
                log.warning(
                    "  transient HTTP %s from Genie eval; retrying in %.1fs (%d/%d)",
                    exc.code,
                    delay_s,
                    attempt + 1,
                    attempts,
                )
                sleep(delay_s)
                continue
            elapsed = time.monotonic() - start
            return last_error, elapsed
        except urllib.error.URLError as exc:
            retryable = attempt < attempts
            last_error = {
                "error": f"URLError: {exc.reason}",
                "attempts": attempt,
            }
            if retryable:
                delay_s = retry_backoff_s * attempt
                log.warning(
                    "  transient URL error from Genie eval; retrying in %.1fs (%d/%d)",
                    delay_s,
                    attempt + 1,
                    attempts,
                )
                sleep(delay_s)
                continue
            elapsed = time.monotonic() - start
            return last_error, elapsed
    elapsed = time.monotonic() - start
    return last_error or {"error": "unknown Genie eval request failure"}, elapsed


def _warehouse_creds() -> tuple[str, str, str] | None:
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get("DATABRICKS_SERVER_HOSTNAME")
    token = os.environ.get("DATABRICKS_TOKEN")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not host or not token or not warehouse_id:
        return None
    if not host.startswith("http"):
        host = "https://" + host
    return host.rstrip("/"), token, warehouse_id


def _run_canonical_sql(statement: str, column: str) -> float | None:
    creds = _warehouse_creds()
    if creds is None:
        return None
    host, token, warehouse_id = creds
    url = f"{host}/api/2.0/sql/statements/"
    body = json.dumps(
        {
            "statement": statement,
            "warehouse_id": warehouse_id,
            "wait_timeout": "50s",
            "on_wait_timeout": "CANCEL",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 -- internal API
        payload = json.loads(resp.read().decode("utf-8"))
    state = (payload.get("status") or {}).get("state")
    if state != "SUCCEEDED":
        err = ((payload.get("status") or {}).get("error") or {}).get("message", "unknown")
        raise RuntimeError(f"canonical SQL failed: state={state!r} err={err!r}")
    columns = [
        c.get("name", "")
        for c in ((payload.get("manifest") or {}).get("schema") or {}).get("columns", [])
    ]
    rows = (payload.get("result") or {}).get("data_array") or []
    if not rows:
        raise RuntimeError("canonical SQL returned zero rows")
    try:
        idx = columns.index(column)
    except ValueError as exc:
        raise RuntimeError(f"canonical SQL missing column {column!r}; columns={columns!r}") from exc
    return float(rows[0][idx])


def _score_question(spec: dict[str, Any], response: dict[str, Any], elapsed_s: float) -> QuestionScore:
    """Apply the four checks. 25 points each → max 100."""
    qid = spec["id"]
    answer = str(response.get("answer") or "")
    table_rows = response.get("table_rows") or []
    proof = response.get("proof") or {}
    sql_query = response.get("sql_query") or proof.get("sql_query")
    score = QuestionScore(
        id=qid,
        category=spec.get("category", "uncategorised"),
        question=spec["question"],
        answer=answer,
        latency_s=round(elapsed_s, 2),
    )
    if "error" in response:
        score.notes.append(f"request error: {response['error']}")
        score.cite_ok = score.forbid_ok = score.require_ok = score.rows_ok = False
        return score

    answer_lc = answer.lower()
    proof_assets = proof.get("source_assets") or []
    if not isinstance(proof_assets, list):
        proof_assets = []
    response_assets = response.get("trusted_assets") or []
    if not isinstance(response_assets, list):
        response_assets = []
    citation_text = " ".join(
        [
            answer,
            str(sql_query or ""),
            *[str(asset) for asset in proof_assets],
            *[str(asset) for asset in response_assets],
        ]
    ).lower()

    # 1. must_cite
    must_cite = spec.get("must_cite") or []
    if must_cite:
        cite_hits = [c for c in must_cite if c.lower() in citation_text]
        score.cite_ok = bool(cite_hits)
        if not score.cite_ok:
            score.notes.append(
                f"missing required citation; expected one of {must_cite!r}"
            )

    # 2. forbid_keywords
    forbid = spec.get("forbid_keywords") or []
    forbidden_hits = [k for k in forbid if k.lower() in answer_lc]
    score.forbid_ok = not forbidden_hits
    if forbidden_hits:
        score.notes.append(f"forbidden phrase present: {forbidden_hits!r}")

    # 3. require_keywords (OR semantics — any one match satisfies)
    require = spec.get("require_keywords") or []
    if require:
        score.require_ok = any(k.lower() in answer_lc for k in require)
        if not score.require_ok:
            score.notes.append(
                f"missing required keyword (need any of): {require!r}"
            )

    # 4. min_rows
    min_rows = int(spec.get("min_rows") or 0)
    if min_rows > 0:
        actual = len(table_rows) if isinstance(table_rows, list) else 0
        score.rows_ok = actual >= min_rows
        if not score.rows_ok:
            score.notes.append(
                f"too few rows: got {actual}, expected >= {min_rows}"
            )

    expected_range = spec.get("expected_numeric_range") or {}
    if expected_range:
        lo = float(expected_range.get("min", float("-inf")))
        hi = float(expected_range.get("max", float("inf")))
        values = _extract_numeric_values(answer, table_rows)
        score.numeric_ok = any(lo <= value <= hi for value in values)
        if not score.numeric_ok:
            score.notes.append(
                f"no numeric value inside expected range [{lo}, {hi}]; saw {values[:10]!r}"
            )

    if spec.get("require_trusted_proof"):
        score.trusted_ok = bool(proof.get("trusted"))
        if not score.trusted_ok:
            score.notes.append("proof.trusted was not true")

    if spec.get("require_select_sql"):
        sql_text = str(sql_query or "").strip().lower()
        score.sql_ok = sql_text.startswith("select") or sql_text.startswith("with")
        if not score.sql_ok:
            score.notes.append("missing SELECT-only generated SQL")
    required_sql = [str(v).lower() for v in spec.get("required_sql_contains") or []]
    if required_sql:
        sql_text = str(sql_query or "").lower()
        missing = [needle for needle in required_sql if needle not in sql_text]
        if missing:
            score.sql_ok = False
            score.notes.append(f"generated SQL missing required text: {missing!r}")

    if spec.get("require_freshness"):
        freshness = proof.get("data_freshness") if isinstance(proof, dict) else None
        score.freshness_ok = any(
            isinstance(row, dict) and row.get("refreshed_at")
            for row in freshness or []
        )
        if not score.freshness_ok:
            score.notes.append("proof.data_freshness did not include refreshed_at")

    canonical_sql = spec.get("canonical_sql")
    canonical_column = spec.get("canonical_column")
    if canonical_sql and canonical_column:
        try:
            expected = _run_canonical_sql(str(canonical_sql), str(canonical_column))
            if expected is None:
                score.canonical_ok = False
                score.notes.append("canonical SQL configured but Databricks warehouse env vars are missing")
            else:
                values = _extract_numeric_values(answer, table_rows)
                score.canonical_ok = any(round(value) == round(expected) for value in values)
                if not score.canonical_ok:
                    score.notes.append(
                        f"canonical mismatch for {canonical_column}: expected {expected:g}; "
                        f"saw {values[:10]!r}"
                    )
        except Exception as exc:  # noqa: BLE001 -- eval should report the failed gate
            score.canonical_ok = False
            score.notes.append(f"canonical SQL check failed: {exc}")

    # Latency is informational, not a fail.
    max_latency = float(spec.get("max_latency_s") or 0)
    if max_latency > 0 and elapsed_s > max_latency:
        score.latency_ok = False
        score.notes.append(
            f"latency {elapsed_s:.1f}s > target {max_latency:.1f}s"
        )

    checks = [
        score.cite_ok,
        score.forbid_ok,
        score.require_ok,
        score.rows_ok,
        score.numeric_ok,
        score.trusted_ok,
        score.sql_ok,
        score.freshness_ok,
        score.canonical_ok,
    ]
    score.score = round(100.0 * sum(checks) / len(checks), 1)
    return score


def _extract_numeric_values(answer: str, table_rows: Any) -> list[float]:
    import re

    values: list[float] = []
    for raw in re.findall(r"(?<![A-Za-z])[-+]?\$?\d[\d,]*(?:\.\d+)?%?", answer):
        cleaned = raw.replace("$", "").replace(",", "").replace("%", "")
        try:
            values.append(float(cleaned))
        except ValueError:
            continue
    if isinstance(table_rows, list):
        for row in table_rows:
            if not isinstance(row, dict):
                continue
            for value in row.values():
                if isinstance(value, int | float):
                    values.append(float(value))
                elif isinstance(value, str):
                    cleaned = value.replace("$", "").replace(",", "").replace("%", "").strip()
                    try:
                        values.append(float(cleaned))
                    except ValueError:
                        continue
    return values


def _emit_markdown(scores: list[QuestionScore], stamp: str, base: str) -> str:
    overall = round(sum(s.score for s in scores) / max(1, len(scores)), 1)
    failed = [s for s in scores if not s.passed]
    by_cat: dict[str, list[QuestionScore]] = {}
    for s in scores:
        by_cat.setdefault(s.category, []).append(s)

    lines: list[str] = []
    lines.append(f"# Genie eval — {stamp}")
    lines.append("")
    lines.append(f"- Base URL: `{base}`")
    lines.append(f"- Overall score: **{overall} / 100**")
    lines.append(f"- Questions: {len(scores)}  ·  passed: {len(scores) - len(failed)}  ·  failed: {len(failed)}")
    lines.append("")
    lines.append("## By category")
    lines.append("")
    lines.append("| category | mean score | passed |")
    lines.append("|---|---:|---:|")
    for cat, items in sorted(by_cat.items()):
        mean = round(sum(s.score for s in items) / len(items), 1)
        passed = sum(1 for s in items if s.passed)
        lines.append(f"| {cat} | {mean} | {passed} / {len(items)} |")
    lines.append("")
    if failed:
        lines.append("## Failures")
        lines.append("")
        for s in failed:
            lines.append(f"### {s.id}  (score {s.score:.0f})")
            lines.append(f"**Q:** {s.question}")
            lines.append("")
            lines.append("**Notes:**")
            for n in s.notes:
                lines.append(f"- {n}")
            lines.append("")
            lines.append("**Answer (truncated to 400 chars):**")
            lines.append("")
            lines.append("> " + (s.answer[:400] or "(empty)").replace("\n", " "))
            lines.append("")
    lines.append("## Per-question detail")
    lines.append("")
    lines.append("| id | category | score | latency | reconcile | proof | passed |")
    lines.append("|---|---|---:|---:|:---:|:---:|:---:|")
    for s in scores:
        ok = "✅" if s.passed else "❌"
        canonical = "✅" if s.canonical_ok else "❌"
        proof = "✅" if all([s.trusted_ok, s.sql_ok, s.freshness_ok]) else "❌"
        lines.append(
            f"| `{s.id}` | {s.category} | {s.score:.0f} | {s.latency_s:.1f}s | {canonical} | {proof} | {ok} |"
        )
    return "\n".join(lines) + "\n"


def _write_json_summary(scores: list[QuestionScore], stamp: str, base: str) -> dict[str, Any]:
    overall = round(sum(s.score for s in scores) / max(1, len(scores)), 1)
    summary = {
        "stamp": stamp,
        "base": base,
        "overall_score": overall,
        "passed": sum(1 for s in scores if s.passed),
        "total": len(scores),
        "questions": [
            {
                "id": s.id,
                "category": s.category,
                "score": s.score,
                "passed": s.passed,
                "latency_s": s.latency_s,
                "checks": {
                    "citations": s.cite_ok,
                    "forbidden_terms": s.forbid_ok,
                    "required_terms": s.require_ok,
                    "rows": s.rows_ok,
                    "numeric": s.numeric_ok,
                    "trusted_assets": s.trusted_ok,
                    "sql": s.sql_ok,
                    "freshness": s.freshness_ok,
                    "canonical_sql": s.canonical_ok,
                },
                "notes": s.notes,
            }
            for s in scores
        ],
    }
    return summary


def _check_regression(latest_overall: float) -> tuple[bool, float | None]:
    """Compare against the committed baseline. True → regression."""
    if not BASELINE_PATH.exists():
        return False, None
    try:
        with BASELINE_PATH.open() as f:
            baseline = json.load(f)
        baseline_overall = float(baseline.get("overall_score", 0))
    except Exception:  # noqa: BLE001 -- bad baseline = no regression check, not a failure
        return False, None
    if latest_overall < baseline_overall - REGRESSION_THRESHOLD:
        return True, baseline_overall
    return False, baseline_overall


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="Base URL for the MIP API")
    parser.add_argument("--token", default=os.environ.get("DATABRICKS_TOKEN"))
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    parser.add_argument("--retry-backoff-s", type=float, default=DEFAULT_RETRY_BACKOFF_S)
    parser.add_argument("--pace-s", type=float, default=DEFAULT_PACE_S)
    parser.add_argument("--report-dir", default=str(REPORT_DIR))
    parser.add_argument("--update-baseline", action="store_true",
                        help="Treat this run's overall score as the new floor")
    parser.add_argument(
        "--soft",
        action="store_true",
        help="Write reports but return 0 even when questions fail or the baseline regresses.",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    questions = _load_questions()
    log.info("genie_eval: loaded %d questions", len(questions))

    scores: list[QuestionScore] = []
    for idx, q in enumerate(questions):
        log.info("ask %s: %s", q["id"], q["question"][:60])
        response, elapsed_s = _ask(
            args.base,
            args.token,
            q["question"],
            args.timeout,
            attempts=args.attempts,
            retry_backoff_s=args.retry_backoff_s,
        )
        s = _score_question(q, response, elapsed_s)
        log.info(
            "  → score %.0f (latency %.1fs, %s)",
            s.score, s.latency_s,
            "PASS" if s.passed else "FAIL: " + "; ".join(s.notes),
        )
        scores.append(s)
        if args.pace_s > 0 and idx < len(questions) - 1:
            time.sleep(args.pace_s)

    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
    Path(args.report_dir).mkdir(parents=True, exist_ok=True)
    md = _emit_markdown(scores, stamp, args.base)
    md_path = Path(args.report_dir) / f"{stamp}.md"
    md_path.write_text(md)
    summary = _write_json_summary(scores, stamp, args.base)
    LATEST_PATH.write_text(json.dumps(summary, indent=2) + "\n")
    log.info("report: %s", md_path)
    log.info("summary: %s", LATEST_PATH)

    if args.update_baseline:
        BASELINE_PATH.write_text(json.dumps(summary, indent=2) + "\n")
        log.info("baseline updated: %s", BASELINE_PATH)
        return 0

    regressed, baseline_score = _check_regression(summary["overall_score"])
    failed = [s for s in scores if not s.passed]
    if regressed and baseline_score is not None:
        log.warning(
            "REGRESSION: overall score %.1f dropped > %.1f below baseline %.1f",
            summary["overall_score"], REGRESSION_THRESHOLD, baseline_score,
        )
    if args.soft:
        return 0
    if failed:
        log.error("FAIL: %d Genie eval question(s) failed", len(failed))
        return 10
    if regressed and baseline_score is not None:
        return 11
    return 0


if __name__ == "__main__":
    sys.exit(main())
