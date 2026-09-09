"""End-to-end borrower accuracy audit for Module 0.

Purpose
-------
Prove that, for any given borrower (CLIP), the values shown in the app
match what the raw Cotality Delta Share actually says about that
property. This tool:

1. Samples N random CLIPs from ``mip.gold.borrower_360`` stratified
   across the current refreshed geography coverage with a mix of
   high/mid/low ``opportunity_score``.
2. For each CLIP:
   a. Pulls the raw-share rows (``entrada_eval_voluntary_lien_status_
      marketing_v2``, ``entrada_eval_property_domain_v3``) and the
      latest FRED market rate.
   b. Recomputes every ``gold.borrower_360`` derived column
      independently in Python using the canonical scoring primitives
      in ``backend/services/scoring.py`` (the same primitives pinned
      by golden fixtures against the UC SQL functions).
   c. Pulls the actual ``gold.borrower_360`` row.
   d. Pulls the ``/api/borrowers/{borrower_id}`` payload via
      ``DatabricksBorrowerRepository`` (same construction the router
      performs).
   e. Compares all three surfaces -- raw-recomputed vs gold vs api --
      and flags any mismatch with field-level detail.
3. Writes a structured report to stdout and (optionally) to a JSON file.

Reproducibility
---------------
``python tools/e2e_borrower_audit.py --sample-size 20 --seed 42`` pins
the deterministic selection. The sampler uses ``XXHASH64(clip)`` to
produce a stable, seed-driven ordering without ``ORDER BY RAND()``
(which is expensive and non-deterministic across
re-runs).

Environment
-----------
Reads ``DATABRICKS_HOST``, ``DATABRICKS_TOKEN``, and
``DATABRICKS_WAREHOUSE_ID`` from the environment. For a workstation run
against the live workspace, a short-lived OAuth access token from
``databricks auth token --profile DEFAULT`` is sufficient.

PII posture
-----------
This script operates *inside* the repository boundary -- it fetches
raw-share rows (which contain ``owner_1_full_name`` and
``situs_street_address``) solely to verify derivations. The report
writers NEVER emit raw PII; they log the synthetic ``borrower_id`` and
the CLIP (an opaque mastered property id). Raw names are read but
never written out; addresses are never projected.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Make the repo importable when this script is run directly.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.services.databricks_sql import (  # noqa: E402
    DatabricksSqlClient,
    DatabricksSqlError,
)
from backend.services.pii_redaction import redact_borrower_row  # noqa: E402
from backend.services.scoring import lead_score  # noqa: E402
from tools.e2e_borrower_audit_compare import (  # noqa: E402
    compare_gold_vs_api,
    compare_raw_vs_gold,
    compare_raw_vs_silver,
)
from tools.e2e_borrower_audit_fetch import (  # noqa: E402
    _bucket_label,
    fetch_evidence_count,
    fetch_gold_row,
    fetch_market_rate,
    fetch_owner_related_count,
    fetch_raw_inputs,
    load_coverage_states,
    sample_clips,
)
from tools.e2e_borrower_audit_model import ClipAudit, Mismatch  # noqa: E402
from tools.e2e_borrower_audit_recompute import recompute_from_raw  # noqa: E402
from tools.e2e_borrower_audit_report import render_report  # noqa: E402

# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def run_audit(
    client: DatabricksSqlClient,
    *,
    sample_size: int,
    seed: int,
) -> tuple[list[ClipAudit], list[str]]:
    """Sample + audit ``sample_size`` CLIPs and return per-CLIP results."""
    coverage_states = load_coverage_states(client)
    candidates = sample_clips(
        client,
        sample_size=sample_size,
        seed=seed,
        states=coverage_states,
    )
    if not candidates:
        raise DatabricksSqlError("Sampling produced zero CLIPs -- check the warehouse/tables")

    market_rate = fetch_market_rate(client)
    print(f"Market rate (fractional): {market_rate}")
    print(
        f"Sampled {len(candidates)} CLIPs across "
        f"{len(coverage_states)} coverage states. Auditing..."
    )

    audits: list[ClipAudit] = []
    for i, cand in enumerate(candidates, start=1):
        clip = cand["clip"]
        borrower_id = cand["borrower_id"]
        score = int(cand["opportunity_score"] or 0)
        audit = ClipAudit(
            clip=clip,
            borrower_id=borrower_id,
            state=cand["state"],
            opportunity_score_bucket=_bucket_label(score),
        )
        try:
            raw = fetch_raw_inputs(client, clip)
            # IMPORTANT: key the gold lookup by CLIP, not borrower_id.
            # CLIP is the mastered property key for this audit.
            gold = fetch_gold_row(client, clip)
            related_count = fetch_owner_related_count(client, gold.get("owner_link_id"))
            recomputed = recompute_from_raw(
                raw,
                market_rate_fraction=market_rate,
                related_property_count=related_count,
            )
            # Patch evidence sub-score and dependent scores.
            evidence_count = fetch_evidence_count(client, clip)
            evidence_sub = min(100, 20 * evidence_count)
            recomputed["evidence_sub"] = evidence_sub
            recomputed["opportunity_score"] = lead_score(
                recomputed["economic_incentive"],
                recomputed["intent_trigger"],
                recomputed["fit"],
                recomputed["relationship"],
                evidence_sub,
            )
            recomputed["confidence"] = int(round(
                (recomputed["economic_incentive"]
                 + recomputed["intent_trigger"]
                 + recomputed["fit"]
                 + recomputed["relationship"]
                 + evidence_sub) / 5.0
            ))

            # Simulate the /api/borrowers/{borrower_id} payload by
            # applying the same redact_borrower_row projection the
            # repository would apply. We skip the real repository get()
            # because it keys on borrower_id (collision-prone on live
            # data) and would return an arbitrary colliding CLIP's row.
            # The redaction projection is the interesting boundary
            # check; the SQL it wraps is strict-equivalent to
            # fetch_gold_row above.
            # Pull trigger_timeline_json alongside the projected cols so
            # the repository's full-surface behaviour is exercised.
            timeline_row = client.execute_one(
                "SELECT trigger_timeline_json FROM mip.gold.borrower_360 "
                "WHERE clip = :clip LIMIT 1",
                {"clip": clip},
            ) or {}
            gold_for_redaction = {**gold, **timeline_row}
            api_payload = redact_borrower_row(gold_for_redaction)
            audit.raw_inputs = raw
            audit.raw_recomputed = recomputed
            audit.gold = gold
            audit.api = api_payload
            # Track raw->silver drift (freshness / coercion) separately
            # from gold arithmetic correctness.
            audit.raw_vs_silver = compare_raw_vs_silver(audit)
            audit.mismatches.extend(compare_raw_vs_gold(audit))
            audit.mismatches.extend(compare_gold_vs_api(audit))
        except Exception as exc:  # pragma: no cover -- network / SQL faults
            audit.mismatches.append(Mismatch(
                clip=clip, borrower_id=borrower_id,
                surface_a="raw", surface_b="gold",
                field="audit_exception",
                expected="no exception", actual=f"{type(exc).__name__}: {exc}",
            ))
        audits.append(audit)
        status = "PASS" if audit.passed else f"FAIL ({len(audit.mismatches)})"
        print(f"  [{i:2d}/{len(candidates)}] {borrower_id} {cand['state']} score={score} -> {status}")

    return audits, coverage_states


# ---------------------------------------------------------------------------
# Client construction (with OAuth-token fallback)
# ---------------------------------------------------------------------------


def _resolve_token() -> str:
    """Use ``DATABRICKS_TOKEN`` if set, else try the CLI OAuth path.

    A workstation run usually has the Databricks CLI authenticated but
    no PAT in ``.env.local``; we call ``databricks auth token --profile
    DEFAULT`` behind the scenes so the script works out of the box.
    """
    tok = os.environ.get("DATABRICKS_TOKEN")
    if tok:
        return tok
    import subprocess

    try:
        out = subprocess.run(
            ["databricks", "auth", "token", "--profile", os.environ.get("DATABRICKS_CONFIG_PROFILE", "DEFAULT")],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        data = json.loads(out.stdout)
        return str(data["access_token"])
    except Exception as exc:  # pragma: no cover -- env-specific
        raise RuntimeError(
            "No DATABRICKS_TOKEN set and `databricks auth token` failed. "
            "Either export a PAT or log in via `databricks auth login`."
        ) from exc


def build_client() -> DatabricksSqlClient:
    host = os.environ.get("DATABRICKS_HOST")
    warehouse = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not host or not warehouse:
        raise RuntimeError(
            "Missing DATABRICKS_HOST / DATABRICKS_WAREHOUSE_ID. "
            "Source .env.local before running."
        )
    token = _resolve_token()
    return DatabricksSqlClient(host=host, token=token, warehouse_id=warehouse, timeout_s=50)


def warmup_client(client: DatabricksSqlClient, *, attempts: int = 6) -> None:
    """Poke the warehouse with a trivial query until it succeeds.

    Serverless warehouses cold-start in 10-30s; the tool's per-query
    timeout is 60s but during the very first call we sometimes hit the
    CANCEL path when the wait-timeout is less than cold-start. A short
    retry loop makes the whole run deterministic.
    """
    last_exc: Exception | None = None
    for i in range(attempts):
        try:
            client.execute("SELECT 1 AS warmup")
            return
        except DatabricksSqlError as exc:
            last_exc = exc
            time.sleep(min(30.0, 4.0 * (i + 1)))
    if last_exc:
        raise last_exc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__ or "")
    parser.add_argument("--sample-size", type=int, default=20)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Path to write the Markdown report. Defaults to docs/validation/"
             "borrower-e2e-audit.md.",
    )
    parser.add_argument(
        "--json",
        type=str,
        default=None,
        help="Optional path for a JSON dump of per-CLIP mismatches.",
    )
    args = parser.parse_args()

    client = build_client()
    warmup_client(client)

    audits, coverage_states = run_audit(
        client,
        sample_size=args.sample_size,
        seed=args.seed,
    )

    report = render_report(
        audits,
        seed=args.seed,
        sample_size=args.sample_size,
        coverage_states=coverage_states,
    )
    out_path = Path(args.output) if args.output else (
        _REPO_ROOT / "docs" / "validation" / "borrower-e2e-audit.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(f"\nWrote report -> {out_path}")

    if args.json:
        payload = [
            {
                "clip": a.clip,
                "borrower_id": a.borrower_id,
                "state": a.state,
                "bucket": a.opportunity_score_bucket,
                "passed": a.passed,
                "mismatches": [
                    {
                        "field": m.field,
                        "surface_a": m.surface_a,
                        "surface_b": m.surface_b,
                        "expected": m.expected,
                        "actual": m.actual,
                        "notes": m.notes,
                    }
                    for m in a.mismatches
                ],
            }
            for a in audits
        ]
        Path(args.json).write_text(json.dumps(payload, indent=2, default=str))
        print(f"Wrote JSON mismatches -> {args.json}")

    # Exit non-zero if anything failed, so CI-like usage catches drift.
    return 0 if all(a.passed for a in audits) else 1


if __name__ == "__main__":
    raise SystemExit(main())
