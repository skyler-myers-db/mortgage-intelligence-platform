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

Wired into the bundle as the `mip_genie_eval` job (resources/jobs/
mip_genie_eval.yml) so it runs nightly after mip_refresh_scores.
Failures emit a structured WARNING; we don't fail the pipeline on
a single regression because Genie behaviour is non-deterministic
and we'd rather see the trend than block deploys on noise.
"""
from __future__ import annotations

import argparse
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
    latency_s: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.cite_ok and self.forbid_ok and self.require_ok and self.rows_ok


def _load_questions() -> list[dict[str, Any]]:
    with QUESTIONS_PATH.open() as f:
        data = yaml.safe_load(f)
    questions = data.get("questions") or []
    if not isinstance(questions, list):
        raise ValueError(f"{QUESTIONS_PATH} 'questions' must be a list")
    return questions


def _ask(base: str, token: str | None, question: str, timeout_s: int) -> tuple[
    dict[str, Any], float
]:
    """Hit /api/genie/message; return (response_json, elapsed_s)."""
    url = f"{base.rstrip('/')}/api/genie/message"
    body = json.dumps({"question": question}).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 -- internal API
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        elapsed = time.monotonic() - start
        return ({"error": f"HTTP {e.code}: {e.reason}"}, elapsed)
    except urllib.error.URLError as e:
        elapsed = time.monotonic() - start
        return ({"error": f"URLError: {e.reason}"}, elapsed)
    elapsed = time.monotonic() - start
    return payload, elapsed


def _score_question(spec: dict[str, Any], response: dict[str, Any], elapsed_s: float) -> QuestionScore:
    """Apply the four checks. 25 points each → max 100."""
    qid = spec["id"]
    answer = str(response.get("answer") or "")
    table_rows = response.get("table_rows") or []
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

    # 1. must_cite
    must_cite = spec.get("must_cite") or []
    if must_cite:
        cite_hits = [c for c in must_cite if c.lower() in answer_lc]
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

    # Latency is informational, not a fail.
    max_latency = float(spec.get("max_latency_s") or 0)
    if max_latency > 0 and elapsed_s > max_latency:
        score.latency_ok = False
        score.notes.append(
            f"latency {elapsed_s:.1f}s > target {max_latency:.1f}s"
        )

    score.score = 25.0 * sum(
        [score.cite_ok, score.forbid_ok, score.require_ok, score.rows_ok]
    )
    return score


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
    lines.append("| id | category | score | latency | passed |")
    lines.append("|---|---|---:|---:|:---:|")
    for s in scores:
        ok = "✅" if s.passed else "❌"
        lines.append(
            f"| `{s.id}` | {s.category} | {s.score:.0f} | {s.latency_s:.1f}s | {ok} |"
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
    parser.add_argument("--report-dir", default=str(REPORT_DIR))
    parser.add_argument("--update-baseline", action="store_true",
                        help="Treat this run's overall score as the new floor")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    questions = _load_questions()
    log.info("genie_eval: loaded %d questions", len(questions))

    scores: list[QuestionScore] = []
    for q in questions:
        log.info("ask %s: %s", q["id"], q["question"][:60])
        response, elapsed_s = _ask(args.base, args.token, q["question"], args.timeout)
        s = _score_question(q, response, elapsed_s)
        log.info(
            "  → score %.0f (latency %.1fs, %s)",
            s.score, s.latency_s,
            "PASS" if s.passed else "FAIL: " + "; ".join(s.notes),
        )
        scores.append(s)

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
    if regressed and baseline_score is not None:
        log.warning(
            "REGRESSION: overall score %.1f dropped > %.1f below baseline %.1f",
            summary["overall_score"], REGRESSION_THRESHOLD, baseline_score,
        )
        # Exit 0 anyway — Genie is non-deterministic and we'd rather
        # see the trend in the markdown report than fail the bundle
        # job on noise. Operators triage from the report.
    return 0


if __name__ == "__main__":
    sys.exit(main())
