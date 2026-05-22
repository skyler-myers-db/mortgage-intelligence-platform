# Archived re-verification — consolidated `main` + partial-readiness snapshot

> **Internal validation artifact. This is NOT a signoff and is not approved for public release.**
> Independent re-audit of the consolidated `main` and its
> partial-readiness claim at `a85e730`. Full Module 0 readiness is **not**
> asserted. This is a historical snapshot; current evidence of record is the
> latest green CI/nightly run and deployed app state.

**Date:** 2026-05-22
**Commit:** `main` @ `a85e730` (worktree clean; `bee6540` is an ancestor)
**Deployment:** `mip-app` deployment `01f155c1f4fe1900a50b15f346fe3db6`, live at
`https://mip-app-2543889327043640.aws.databricksapps.com`
**Method:** git topology + diff review → CI/gate inspection → live Chrome probes
(API + UI), filters emphasis.

---

## Bottom line

The agent's consolidation claim checked out at the time of this snapshot. `main`
was the single clean source of truth, the hardening branch was merged (`080cd10`),
and the ~28 follow-on commits were CI/test stabilization plus genuine improvements
(lead-queue a11y, automated non-admin auth gate, Genie strategy-board refactor).
Live re-audit showed **healthy deployment, exact filter consistency, and zero
regressions**. The partial-readiness boundary (MLS/listing + building permits
excluded) was unchanged and correct. No new blockers were found.

## Independently verified

| Area | Result |
| --- | --- |
| Git topology | `main == a85e730`, worktree clean, only `main`/`origin/main`, hardening branch merged. ✔ |
| Deployment health | `/api/health` = `ok`; warehouse/lakebase/genie all `up`. ✔ |
| **Filter additivity (emphasis)** | CA+FL+IL == multi **exact for every metric**: addressable 3,503,983; in-the-money 84,783; high-opportunity 2,169; offer-recommended 3,025,457; approved 15. ✔ |
| Bad input guard | `states=CALIFORNIA` → 422. ✔ |
| Cross-surface consistency | Hero borrower B-102FL7THC6Q3L shows **+376 bps** identically on the lead table, proof drawer, and Borrower 360; proof reconciles (88 opportunity / 85% signal / 376 bps / 91% equity / 9% LTV). ✔ |
| No regression | Asset endpoint 200; segments 200; proof 200/trusted; lead queue renders (masked IDs, competitor chips, segments, score/signal, "PII suppressed", "500 of 3,901 · capped at 500"); a11y commits didn't break rendering. ✔ |
| Non-admin auth gate | Now an **automated nightly CI gate**: mints an M2M OAuth bearer for an intentionally non-admin SP and runs an "Authenticated non-admin smoke" step (`nightly.yml`) — upgrade from the prior one-off SP. ✔ |

## Legitimate live-data movement (not a regression)
National in-the-money holds at **111,885** (stable vs the prior re-audit); approved
ticked 14→**15** (one more Lakebase approval). Consistent with a live, refreshing
data plane; additivity staying exact confirms no computational drift.

## Still open / unchanged at snapshot time
- **LOW 1 (cosmetic, later cleaned up):** dossier card chip "Borrower dossier" vs
  heading "Customer 360 dossier" (`borrower-360.tsx:112` vs `:288`) — purely
  cosmetic. This snapshot recorded the finding before the later cleanup aligned
  the heading to "Borrower dossier."
- **MLS/listing + building-permit live triggers** — external Cotality
  pending dependencies; correctly excluded from the partial claim.
- Real-infra credential-kill drill and deep Genie fuzz are skipped by workflow
  design (simulated drill + standard fuzz pass) — a known, declared boundary.
- GitHub Actions Node 20 deprecation — maintenance item, not a failure.

## Verdict
The consolidated `main` is in its strongest state yet: clean Git, green CI/nightly
with the non-admin proof now automated, healthy deployment, arithmetically exact
filters, and verified cross-surface data consistency. This affirms **partial
readiness** for the Module 0 core flow under the agreed boundary (no live MLS/
listing or building-permit trigger claims) and explicitly does **not** grant full
Module 0 signoff.
