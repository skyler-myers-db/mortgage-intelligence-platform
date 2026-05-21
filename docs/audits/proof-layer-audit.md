# Proof Layer audit — Borrower 360 transparency tranche

> **Internal validation artifact — not approved for public release.** Scope:
> the new `GET /api/v1/borrowers/{id}/proof` endpoint + `proof_policy`, the
> Borrower 360 proof drawer (Math / Evidence / Lineage / Reproduce), the
> glossary registry/page/term component, the confidence→signal-strength
> rename, and the data-quality drift detection. Verified by code read + direct
> policy execution + local frontend code review, plus a live no-regression
> check on the deployed app.

**Date:** 2026-05-20
**Worktree:** proof-layer tranche (uncommitted), no `.git/index.lock`.
**Live deployment (unchanged this tranche):** `01f154a31e5b1db2aa46fbd9e4d1158f`
(the v4 multi-select build).

## Headline result

The Proof Layer is **code-complete, well-engineered, and faithfully implements
the three-layer design** we discussed (inline understanding → show the math →
proof drawer), including the two corrections I pushed for: the
confidence→signal-strength rename *with* the "deterministic average, not a
statistical confidence interval" framing, and the fair-lending discipline on
the newly-exposed sub-scores. The "Reproduce SQL" surface — the highest-risk
piece — is protected by a strict fixed-template policy.

**One material caveat, honestly flagged by engineering and confirmed by me:
the Proof Layer is NOT deployed to the live app.** The live Borrower 360 still
shows "85% conf.", zero glossary terms, and no proof drawer. So this tranche is
verified by **code + tests + local walkthrough only** — a live-UC Chrome
walkthrough of the proof layer is impossible until it's deployed. That is the
correct posture given no `DATABRICKS_TOKEN` was available; it just means "safe
to demo" does **not** yet apply to these features.

**Finding set: 0 P0, 0 P1, 0 HIGH, 0 MEDIUM, 2 LOW. Plus 1 deployment-status note.**

## What I verified in code

### Proof endpoint + policy (the security-sensitive surface)

| Property | Evidence |
|---|---|
| Path-validated, 404-safe, audited | `backend/api/borrowers.py:201-231` — `_path_borrower_id`, 404 on miss, writes `VIEW_BORROWER_PROOF` audit row with a **hashed** SQL fingerprint (no raw SQL/PII in the row). |
| Reproduce SQL is fixed-template + validated | `backend/services/proof_policy.py:validate_borrower_proof_sql` enforces: SELECT/WITH only, no `;`/`--`/`/* */`, no `SELECT *`, DML/DDL word denylist, **PII-adjacent column denylist** (`owner_name_hash`, `owner_*_name`, `owner_link_id`, `situs/mailing_*`, `raw_lender`, `current_servicer`, `source_table`, `email`, `phone`), and a **FROM/JOIN relation allowlist** (only `borrower_dossier`, `lead_scores`, `evidence_events`). Every emitted query runs through it (`databricks_borrowers.py:665`). |
| Policy rejections proven | Directly executed the validator: DELETE, `SELECT *`, `owner_name_hash`, `source_table`, unapproved relation, and semicolon are all rejected. (The relation allowlist is catalog-aware via `qualify()`.) |
| `source_table` removed | In the proof column denylist and absent from the reproduce SQL — matches the signoff. |
| Evidence rows reuse redaction | Proof evidence rows go through `_redact_evidence_list`, the same path the dossier drawer uses — no new leak surface. |

### Show-the-math + self-checking drift detection

`_build_borrower_proof` (`databricks_borrowers.py:674+`) doesn't just display
the math — it **recomputes and reconciles**:

- `recomputed_score = lead_score(**components)` compared against the dossier
  score *and* the materialized `lead_scores` score.
- `recomputed_strength = round(sum(sub_scores)/5)` compared the same way.
- `_recomputed_offer_code(...)` compared against the displayed offer.
- Refresh-skew check: dossier vs `lead_scores` refresh timestamps.
- Any mismatch appends to `known_data_gaps`; **`trusted = not gaps`** — the proof
  marks itself untrusted rather than hiding a discrepancy.

This is the strongest possible version of the transparency feature: it proves
the displayed number reconciles, and self-flags when it doesn't.

### Confidence → signal-strength rename + split

- `ConfidenceMeter.tsx:24-25` renders `title="Signal strength is a deterministic
  average of the five scoring sub-scores, not a statistical confidence
  interval."` and `aria-label="Recommendation signal strength {value} percent."`
- The proof SQL aliases the borrower-level `confidence` column to
  `dossier_signal_strength` / `recomputed_signal_strength`; the per-evidence-row
  `confidence` stays named `confidence`, and the proof drawer's Evidence tab says
  it is "separate from borrower signal strength." The two-numbers-one-word
  problem is resolved.

### Glossary system

- `mortgageGlossary.ts` — 28-term registry (AVM, LTV, CLIP, Owner Link, HELOC,
  bps, in-the-money, rate spread, signal strength, etc.).
- `GlossaryTerm.tsx` — accessible term with `aria-label` + `role="tooltip"`.
- `glossary.tsx` route + anchor links.
- `borrower-360.tsx` wires `GlossaryTerm` onto CLIP, Owner Link, AVM, LTV,
  in-the-money, next-best-offer, supporting evidence.

### Fair-lending discipline on exposed sub-scores (the dependency I flagged)

`databricks_borrowers.py:118-131` carries three explicit notes into the proof
payload:

- `_FIT_FAIR_LENDING_NOTE` — CONV/FHA/VA parity-scored, compliance reviews any
  asymmetric change.
- `_RELATIONSHIP_FAIR_LENDING_NOTE` — retention treatment is a marketing signal,
  compliance review required.
- `_INVESTOR_FAIR_LENDING_NOTE` — investor/absentee/corporate attributes are
  property posture, not protected-class identity; exposed for proxy-risk review.

The `fit` sub-score fields are property attributes only
(`is_owner_occupied`, `first_pos_loan_type`, `is_corporate_owner`,
`is_investor`) — no protected-class proxies — and the BL §5.1 framing is wired
in. This directly closes the concern I raised when we discussed the proposal.

### Proof drawer (frontend)

`BorrowerProofDrawer.tsx` — 4 tabs (`Math`, `Evidence`, `Lineage`, `Reproduce`)
as proper ARIA tabs (`role="tablist"`, `role="tab"`, `aria-selected`), lazy
copy-SQL, and the evidence-vs-signal-strength clarification. This matches the
agreed shape (and correctly merged "Meaning" into the glossary popover rather
than a 5th tab — the simplification I suggested).

## Findings

### Deployment-status note (not a defect — a scope boundary)

The Proof Layer is **not on the live deployment**. Live Borrower 360 =
`01f154a31e…` (v4) shows "85% conf.", no glossary, no proof drawer. The
engineering signoff states this honestly (no `DATABRICKS_TOKEN` / no auth
profile, so no real-UC deploy). Consequence: **these features cannot be
demoed on the live app and have not had a live-UC walkthrough.** Before they
can be shown to anyone, the tranche needs a `./scripts/deploy.sh -t dev` run
and a live re-verification. This is the single most important thing to track.

### LOW 1 — `GlossaryTerm` keyboard affordance is lighter than the analytics multi-select

The glossary term exposes `aria-label` + `role="tooltip"`, which covers
hover/focus reveal, but I did not see explicit keyboard-open / focus-ring
handling at the level the analytics multi-select got in v4. For a tooltip-on-
focus pattern this is acceptable, but to match the bar the rest of the app now
sets, confirm the term is focusable (`tabIndex`), reveals the tip on keyboard
focus (not just hover), and that the "open glossary entry" affordance is
reachable by keyboard. Verify against the WCAG pass when this deploys.

### LOW 2 — Reproduce SQL uses a `:borrower_id` bind placeholder in copyable text

The copyable reproduce SQL contains `WHERE borrower_id = :borrower_id`. That's
correct and safe for the app, but a user who pastes it into a Databricks SQL
editor must know to substitute the parameter (and that the demo catalog masks
identity). Make sure the drawer's copy UI includes a one-line "replace
`:borrower_id` with the masked borrower id; runs against governed/masked data"
note so the "reproduce it yourself" promise doesn't trip a first-time user.
Minor copy, not a correctness issue.

## What I could NOT verify (and why)

- **Live proof drawer / glossary rendering, tab interactions, copy-SQL button,
  glossary-page anchors** — not deployed; the live app is v4. Engineering tested
  these against the local in-process app + `happy-dom` DOM tests (217 frontend
  tests, 47 drawer/glossary, 73 proof/backend), which I corroborate via the code
  read and the direct `proof_policy` execution, but I did not run the frontend
  suite in this sandbox (no npm) and could not click the live drawer.
- **Live-UC recomputation parity** (does the Reproduce SQL actually return the
  displayed 88 against the warehouse) — requires a deployed app + warehouse
  session; relayed from engineering's local run.

## No-regression on what IS live

| Surface | Result |
|---|---|
| Analytics `?states=IL` deep-link | In the Money 67.86K, "State IL" chip — still correct. |
| Live Borrower 360 base | Renders cleanly (the v4 build), zero console errors. |
| Console | Zero errors on the live surfaces checked. |

Since the proof tranche is undeployed, the live app is byte-for-byte the v4
build, so no live regression is possible from this changeset.

## Verdict

The Proof Layer is **excellent work** — it implements the three-layer design
faithfully, takes the two refinements from our design discussion (the
deterministic-average framing for signal strength; the fair-lending notes on
exposed sub-scores), and protects the reproduce-SQL surface with a strict,
directly-verified policy. The self-checking drift detection (`trusted = not
gaps`) is a stronger transparency guarantee than the original proposal asked
for.

**Code + policy + local-test signoff: clean (0 P0/P1/HIGH/MEDIUM, 2 LOW).**
**Live signoff: not applicable — the tranche is undeployed.** The honest
engineering caveat is correct and I confirm it: do not treat the proof/glossary
features as demo-ready until a dev deploy + live re-verification lands. The two
LOW items (glossary keyboard affordance, reproduce-SQL copy note) are polish for
that same pass.

---

## v2 independent verification — 2026-05-20 (deployed + polish tranche)

The Proof Layer is now **deployed** (`01f154cbb2ae11adbdff153d3b776879`), so I
ran the live Chrome walkthrough that was impossible in v1, plus verified the
audit-polish fixes and re-confirmed the analytics filters.

### Polish fixes — verified (both my PL v1 LOWs closed)

| Item | Status |
|---|---|
| Nav order: Ask Genie before Glossary | `RouteNav.tsx:27-28` and live nav both confirm. |
| Glossary `aria-describedby` (my PL v1 LOW 1) | `GlossaryTerm.tsx:24` + 28 live terms carry `aria-describedby`. |
| Reproduce-SQL `:borrower_id` copy note (my PL v1 LOW 2) | Live Reproduce tab: "Replace `:borrower_id` with the masked borrower id shown here; the query runs against governed, masked data." |
| Proof drawer via portal | `createPortal(drawer, document.body)`; live tab clicks work (nav no longer intercepts). |
| Tab/Shift+Tab focus trap | `BorrowerProofDrawer.tsx:79-97`, with the modal `role="dialog" aria-modal="true"`. |

### Live walkthrough on `01f154cbb2` — all green except one item

| Check | Result |
|---|---|
| Header label | Reads **"Signal 85%"** — old "conf." is gone (`hasConf:false`). |
| Glossary terms | 28 terms with `aria-describedby` render on Borrower 360. |
| Proof drawer opens | "Show proof" opens the portal dialog "Proof: Borrower B-102FL7THC6Q3L · Copyable, traceable, reproducible scoring", badge "Governed proof ready · 88 score 85% signal". |
| **Math tab arithmetic** | Opportunity score `0.35×100 + 0.30×84 + 0.15×62 + 0.10×100 + 0.10×80 = 87.5 → 88` (banker's rounding, matches `fn_lead_score`); signal strength `(100+84+62+100+80)/5 = 85.2 → 85%`; rate spread `(10.27% − 6.36%)×10000 = 391 bps`; equity `168,163 − 15,000 = 153,163 (91%)`; LTV `15,000/168,163 = 9%`. All recompute and reconcile with displayed values. |
| "What this number means" | "Signal strength is deterministic scoring signal coverage, not a statistical confidence interval or a credit decision probability." |
| Score components + fair-lending notes | Economic 100 / Intent 84 / Fit 62 / Relationship 100 / Evidence 80, with the investor + CONV/FHA/VA parity notes inline. |
| Reproduce tab | 3 fixed-template queries (Score components, Decision inputs, Evidence rows), each Copy SQL + Open SQL editor + sql-hash; recomputes `fn_lead_score`/`fn_next_best_offer`; **no `source_table`**. |
| Close (X button) | Dismisses the drawer (confirmed by screenshot). |
| Console | **Zero errors** across the walkthrough. |

### Analytics filter no-regression on `01f154cbb2`

`?states=CA,FL,IL` → In the Money **103.57K** (CA 16,706 + FL 19,010 + IL 67,858), chip "State 3 selected", zero console errors. Multi-select aggregation unchanged.

### LOW 3 (new) — Escape and backdrop-click do not dismiss the proof drawer live

The proof drawer dismisses **only via the explicit "Close proof drawer" (X)
button**. I tested Escape two ways — a real keypress and a genuine
`window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))` — and the
dialog stayed open (`role=dialog[aria-modal=true]` still present) both times; a
backdrop click at the dimmed area also did not close it. This is despite the
source implementing a `window`-level Escape→`onClose` handler
(`BorrowerProofDrawer.tsx:73-99`).

So the deployed behavior diverges from the source on the Escape/backdrop
dismiss paths. Severity is **LOW**: the drawer is not a dead-end (the X button
works and the dialog is correctly Tab/Shift+Tab focus-trapped while open), and
nothing is broken functionally — but Escape-to-dismiss is a standard WAI-ARIA
modal-dialog expectation, and it's silently non-functional in the shipped build.

Notably, the engineering signoff claimed focus-trap test coverage for
**Tab/Shift+Tab only** — it did not claim an Escape-dismiss test, which is
consistent with this gap going unnoticed. **Recommended fix:** add an e2e/DOM
test that opens the drawer, presses Escape, and asserts it closes (and the same
for backdrop click); then confirm the shipped bundle actually runs the
window-level Escape handler. This is the one item to close before treating the
proof drawer's keyboard story as complete.

### v2 verdict

**Findings: 0 P0, 0 P1, 0 HIGH, 0 MEDIUM, 1 LOW (Escape/backdrop dismiss).**

The Proof Layer is deployed and, on the live app, delivers exactly what the
original transparency feedback asked for: every key number shown with its
literal arithmetic, recomputed and reconciled (88 and 85% both check out
against the gold functions), sourced to the UC function/table, with the
fair-lending framing inline and a fixed-template, PII-safe, copyable Reproduce
SQL. The signal-strength rename, glossary, nav order, portal render, and
focus trap are all live and correct, and the analytics filters did not
regress. The single LOW is that Escape/backdrop don't dismiss the modal live
(X button does) — a keyboard-affordance gap to close with a real e2e test, not
a correctness or safety defect.

Sign-off: the proof layer is **demo-safe and live**, with one LOW keyboard
follow-up. Zero console errors; no prior surface regressed.

---

## v3 re-validation — 2026-05-21 (LOW 3 closure + Asset layer no-regression)

Re-verified after the latest agent updates (the uncommitted Asset Metadata
layer plus a new `BorrowerProofDrawer.test.tsx`). This pass closes the one open
LOW from v2.

### LOW 3 — CLOSED (Escape + backdrop now dismiss live)

On the current deployment, re-tested on hero borrower `B-102FL7THC6Q3L`:

| Dismiss path | v2 (prior) | v3 (now) |
| --- | --- | --- |
| Close (X) button | Worked | Works |
| **Escape key** (real keypress) | Did **not** close | **Closes the drawer** (confirmed by screenshot — drawer gone, page restored) |
| **Backdrop / scrim click** | Did **not** close | **Closes the drawer** (confirmed by screenshot) |

The deployed bundle now runs the `window`-level Escape→`onClose` handler and the
scrim `onClick`, matching `BorrowerProofDrawer.tsx`. The earlier divergence was a
stale-bundle artifact, now resolved by the redeploy.

Test coverage: `BorrowerProofDrawer.test.tsx` adds an explicit Escape-dismiss
assertion (`window.dispatchEvent(KeyboardEvent 'Escape')` → `onClose` called once)
alongside the Tab/Shift+Tab focus-trap test. **Residual (informational, not a
finding):** there is still no explicit unit test for the **backdrop-click**
dismiss path — the scrim wiring is trivial and verified live, but a one-line test
would make the keyboard/pointer dismiss story fully gated.

### Math + reproduce re-confirmed live

Proof drawer Math tab still reconciles: `0.35·100 + 0.30·84 + 0.15·62 +
0.10·100 + 0.10·80 = 87.5 → 88`; signal `(100+84+62+100+80)/5 = 85.2 → 85%`;
rate spread 391 bps; equity 91%; LTV 9%; all source-attributed. Proof endpoint
returns `trusted:true` with 7 governed `source_assets` (dossier, lead_scores,
evidence_events + the four `fn_*` functions).

### v3 verdict

**0 P0 / 0 P1 / 0 HIGH / 0 MEDIUM / 0 LOW.** The single v2 LOW is closed on the
live app and now has Escape-dismiss test coverage. The proof layer is fully
signed off. (Asset Metadata layer reviewed separately in
`asset-metadata-layer-audit.md`.)
