# Re-verification — deployed hardening + scoped partial-readiness claim

> **Internal validation artifact. This is NOT a signoff and is not approved for public release.**
> It is an independent re-audit of the implementation agent's
> deployed hardening and its
> "partial readiness" claim. Full Module 0 readiness is **not** asserted here.

**Date:** 2026-05-22
**Branch/commit:** `hardening/module0-release-readiness` @ `bee6540` (worktree clean)
**Deployment:** `mip-app` deployment `01f1556546f613c7a8701d44660b397a`, live at
`https://mip-app-2543889327043640.aws.databricksapps.com`
**Method:** git/diff review → evidence-file inspection → live Chrome probes (API
+ UI), filters emphasis.

---

## Bottom line

The agent's revised judgment — **scoped partial readiness for Module 0 excluding
live MLS/listing and building-permit triggers; no full signoff** — is
**supported by my independent verification.** The earlier "signoff while live
blockers were open" was correctly retracted; the current artifact and language
are honest. I found **no functional regressions** and the filters/data are
consistent end-to-end. One **cosmetic LOW** (a label inconsistency) is the only
new issue.

---

## Independently verified as TRUE

| Claim | Verification |
| --- | --- |
| Hardening fixes committed + deployed | Worktree clean at `bee6540`; `qualify()` allowlist + `/leads include_suppressed_for_analytics` present in HEAD; live app reflects the new build. |
| Live app healthy | `/api/health` = `ok`, deps `warehouse/lakebase/genie` all `up`. |
| Filters consistent (emphasis) | **Exact additivity** CA+FL+IL == multi for every metric: addressable 3,503,983; in-the-money 84,783; high-opportunity 2,169; offer-recommended 3,025,457; approved 14. UI mirrors API (In the Money **84.78K**, Approved 14, High-Opp 2.17K). Bad input `states=CALIFORNIA` → 422. |
| Resilience/degraded drill | Real Lakebase stop → `/api/health` `degraded {lakebase: down}` + breaker open + UI degraded banner screenshot → restart → recovered to `ok` (9 polls). Evidence: `tools/kill_drill/evidence/signoff_lakebase_real_20260522T000022Z.log`. |
| Authenticated non-admin proof | Code path sound: `require_admin` raises 403; `AdminDep` gates admin/assets/audit/health routers; `/leads` only clears eligibility under the admin-gated flag. Artifact records a real temp SP (`mip-non-admin-proof-…`, CAN_USE only): admin routes 403, suppressed analytics 403, default drilldown 200 with 10/10 `marketing_eligible=true`/`opt_in`, SP deleted. Screenshots in `frontend/test-results/signoff-non-admin/`. |
| Release-readiness artifact honest | `dist/release-readiness.md/json` marks MLS/listing + building-permit `pending` with an explicit "What Cannot Be Claimed" section; no overstated signoff. The prior doc was renamed to `…failed-readiness-audit.md` and re-verdicted. |
| No regression: asset/proof/glossary/evidence | Asset endpoint 200; proof reconciles (88 / 85% signal / 376 bps / 91% equity / 9% LTV); proof drawer opens and **Escape dismisses** (LOW 3 stays closed); evidence drawer + glossary intact. |

## Legitimate data movement (not a regression)

In-the-money totals shifted since my last audit (national 135.5K→**111.9K**;
CA+FL+IL 103.6K→**84.8K**) and the hero borrower's rate spread moved 391→**376
bps** (market par 6.36%→6.51%). This is the product working as designed: live
gold data refreshed, market rates rose, fewer borrowers clear the ≥75 bps
in-the-money threshold. Crucially, **additivity stayed exact**, which proves the
movement is real data, not computational drift.

## New finding

**LOW 1 (cosmetic) — dossier card label inconsistency.** On Borrower 360, the
dossier card chip reads "Borrower dossier" (`borrower-360.tsx:112`) while the
card heading reads "Customer 360 dossier" (`borrower-360.tsx:288`). The heading
was set back to "Customer 360 dossier" in `d81014b` ("close live readiness UI
blockers"), likely to satisfy a Playwright selector. Same surface, two names —
purely cosmetic, no functional impact. Recommend picking one term (the source
material uses "Customer 360" as an industry term, so either is defensible) and
aligning the chip + heading.

## Still open (correctly excluded from the partial claim)

- **MLS/listing live triggers** — Cotality Delta Share pending; honestly false,
  no fabricated evidence.
- **Building-permit / renovation triggers** — same.
- These remain genuine requirement gaps that depend on Cotality data delivery,
  not code; the partial-readiness boundary names them explicitly.

## Verdict

The deployed hardening is **substantive and the live evidence is materially
stronger** than the prior pass: filters and data are consistent, the two new
gates (resilience, non-admin authorization) are genuinely demonstrated, and the
team's discipline in retracting premature signoff language is the right call.

**Scoped readiness:** Module 0 core flow is demo/operationally credible **except
MLS/listing and building-permit live-trigger claims**, which cannot be made until
the Cotality feeds land. One cosmetic LOW to tidy. This document affirms the
**partial** judgment and explicitly does **not** grant full Module 0 signoff.
