"""Markdown report rendering for the borrower E2E audit.

Split out of ``tools/e2e_borrower_audit.py`` (2026-09-08); ``_hash_clip`` and
``render_report`` moved verbatim.
"""

from __future__ import annotations

import hashlib

from tools.e2e_borrower_audit_model import ClipAudit, Mismatch


def _hash_clip(clip: str) -> str:
    """Short, stable hash of a CLIP for audit-only logs -- not PII-leaking.

    Only used internally; the final report writes the raw CLIP in the
    audit appendix where access is restricted. Salt is a constant so
    a reader can cross-reference between runs.
    """
    return hashlib.sha256(("mip_clip_log_v1:" + clip).encode()).hexdigest()[:12]


def render_report(
    audits: list[ClipAudit],
    seed: int,
    sample_size: int,
    coverage_states: list[str],
) -> str:
    """Render the markdown report body."""
    passed = [a for a in audits if a.passed]
    total = len(audits)
    mismatch_count = sum(len(a.mismatches) for a in audits)

    lines: list[str] = []
    lines.append("# Module 0 Borrower End-to-End Accuracy Audit")
    lines.append("")
    lines.append(f"**Pass rate:** {len(passed)}/{total} CLIPs fully match across "
                 "raw -> silver -> gold -> /api.")
    lines.append(f"**Total field-level mismatches:** {mismatch_count}.")
    lines.append(f"**Sample size / seed:** {sample_size} / {seed}.")
    lines.append("**Market rate used:** live MORTGAGE30US `is_latest` "
                 "from `mip.silver.market_rates_weekly`.")
    lines.append("")

    # Methodology
    lines.append("## Methodology")
    lines.append("")
    lines.append(
        f"1. Stratified random sample across {len(coverage_states)} current coverage "
        "states x 3 opportunity buckets"
    )
    lines.append("   (high >= 60, mid 45..59, low < 45 -- tuned to the live")
    lines.append("   `gold.borrower_360` cap of ~68 given the simplified")
    lines.append("   `intent_trigger` column; full treatment lives in")
    lines.append("   `gold.lead_scores`) using `ORDER BY HASH(clip, :seed)` for")
    lines.append("   deterministic reproducibility across runs.")
    lines.append("2. For each CLIP the audit pulls rows from the raw Cotality share")
    lines.append("   (`entrada_eval_voluntary_lien_status_marketing_v2` +")
    lines.append("    `entrada_eval_property_domain_v3`) and the latest FRED")
    lines.append("   `MORTGAGE30US` market rate.")
    lines.append("3. Derived columns are independently recomputed in Python using the")
    lines.append("   canonical scoring primitives in `backend/services/scoring.py`")
    lines.append("   (golden-fixture pinned against the UC SQL functions).")
    lines.append("4. The `/api/borrowers/{borrower_id}` payload is materialised by")
    lines.append("   instantiating `DatabricksBorrowerRepository` directly -- same path")
    lines.append("   the FastAPI router uses, so the post-redaction shape is identical.")
    lines.append("5. Three-way diff: raw-recomputed vs gold vs api. Any non-matching")
    lines.append("   field is recorded with expected/actual values.")
    lines.append("")

    # Sampling distribution
    by_state: dict[str, int] = {}
    by_bucket: dict[str, int] = {}
    for a in audits:
        by_state[a.state] = by_state.get(a.state, 0) + 1
        by_bucket[a.opportunity_score_bucket] = by_bucket.get(a.opportunity_score_bucket, 0) + 1
    lines.append("### Sample distribution")
    lines.append("")
    lines.append("| State | Count |")
    lines.append("|---|---|")
    for s in coverage_states:
        lines.append(f"| {s} | {by_state.get(s, 0)} |")
    lines.append("")
    lines.append("| Opportunity bucket | Count |")
    lines.append("|---|---|")
    for b in ("high", "mid", "low"):
        lines.append(f"| {b} | {by_bucket.get(b, 0)} |")
    lines.append("")

    # Per-CLIP verification tables
    lines.append("## Per-CLIP verification (synthetic borrower_id)")
    lines.append("")
    lines.append("Each row summarises the audit result for one CLIP. The CLIP -> "
                 "borrower_id mapping appears in the audit-only appendix below.")
    lines.append("")
    lines.append("| borrower_id | state | bucket | score | rate_spread_bps | equity_pct | "
                 "ltv | recommended_offer | segments | ITM | status |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for a in audits:
        g = a.gold
        score = g.get("opportunity_score")
        status = "pass" if a.passed else f"FAIL ({len(a.mismatches)})"
        lines.append(
            f"| {a.borrower_id} | {a.state} | {a.opportunity_score_bucket} | {score} | "
            f"{g.get('rate_spread_bps')} | {g.get('equity_pct')} | {g.get('ltv')} | "
            f"{g.get('recommended_offer')} | "
            f"{','.join(g.get('segment_codes') or []) or '-'} | "
            f"{'yes' if g.get('in_the_money') else 'no'} | {status} |"
        )
    lines.append("")

    lines.append("## Structural findings surfaced during this run")
    lines.append("")
    lines.append("This report does not carry static defect assertions. Any active drift")
    lines.append("must appear as a field-level mismatch below, sourced from the sampled")
    lines.append("raw, gold, and API payloads. Gold lookups are keyed by mastered CLIP")
    lines.append("so the audit does not assume any display identifier is globally unique.")
    lines.append("")

    # Field-level issue list
    lines.append("## Field-level issue list (gold arithmetic)")
    lines.append("")
    all_mismatches = [m for a in audits for m in a.mismatches]
    if not all_mismatches:
        lines.append("None. All sampled CLIPs matched across raw-recomputed, gold, and /api payloads.")
        lines.append("")
    else:
        by_field: dict[str, list[Mismatch]] = {}
        for m in all_mismatches:
            by_field.setdefault(m.field, []).append(m)
        lines.append("| field | count | surface_a -> surface_b | representative row |")
        lines.append("|---|---|---|---|")
        for fld, rows in sorted(by_field.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            r0 = rows[0]
            surface_pair = f"{r0.surface_a} -> {r0.surface_b}"
            repr_ = (
                f"{r0.borrower_id}: expected={r0.expected!r}, actual={r0.actual!r}"
            )
            lines.append(f"| `{fld}` | {len(rows)} | {surface_pair} | {repr_} |")
        lines.append("")

    # Raw-vs-silver drift (informational).
    lines.append("## Raw-vs-silver drift (informational)")
    lines.append("")
    lines.append("Cells below are rows where the live raw share value differs from")
    lines.append("the silver snapshot the gold CTAS read. A non-empty table here")
    lines.append("means silver is stale or a coercion is masking a share change --")
    lines.append("NOT that gold arithmetic is wrong.")
    lines.append("")
    rvs = [m for a in audits for m in a.raw_vs_silver]
    if not rvs:
        lines.append("None. Silver matches raw share on every sampled CLIP/column.")
    else:
        by_field: dict[str, list[Mismatch]] = {}
        for m in rvs:
            by_field.setdefault(m.field, []).append(m)
        lines.append("| field | count | representative row |")
        lines.append("|---|---|---|")
        for fld, rows in sorted(by_field.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            r0 = rows[0]
            repr_ = (
                f"{r0.borrower_id}: raw={r0.expected!r}, silver={r0.actual!r}"
                + (f" ({r0.notes})" if r0.notes else "")
            )
            lines.append(f"| `{fld}` | {len(rows)} | {repr_} |")
    lines.append("")

    lines.append("## Remediation / decisions")
    lines.append("")
    if not all_mismatches:
        lines.append("**Verdict:** gold arithmetic and /api projection")
        lines.append("agree with independent Python recomputation on every sampled CLIP.")
        lines.append("`mip.gold.borrower_360` is trustworthy for the columns audited.")
    else:
        lines.append("- See Field-level issue list above; each field bucket is a")
        lines.append("  candidate for code review or a fix commit.")
        lines.append("- For fields that differ in `raw_recomputed -> gold` the source of")
        lines.append("  truth is silver (the CTAS input); the gold CTAS is the suspect.")
        lines.append("- For fields that differ in `gold -> api` the source of truth is")
        lines.append("  gold; the repository / pii_redaction boundary is the suspect.")
    lines.append("")

    # Audit-only appendix
    lines.append("## Appendix: CLIP -> borrower_id (audit-only)")
    lines.append("")
    lines.append("Raw CLIPs live here for traceability. Public prose uses only the")
    lines.append("synthetic `borrower_id`. Do not copy this section into customer")
    lines.append("surfaces.")
    lines.append("")
    lines.append("| borrower_id | state | score | clip | clip_hash12 |")
    lines.append("|---|---|---|---|---|")
    for a in audits:
        lines.append(
            f"| {a.borrower_id} | {a.state} | {a.gold.get('opportunity_score')} "
            f"| `{a.clip}` | `{_hash_clip(a.clip)}` |"
        )
    lines.append("")

    return "\n".join(lines) + "\n"
