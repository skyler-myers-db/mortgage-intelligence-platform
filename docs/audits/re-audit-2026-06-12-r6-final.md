# Module 0 — Re-Audit #6: r5-Remediation Adjudication (final)

> **Internal validation artifact — not approved for public release.**
> Scope: adversarial verification of the r5 remediation (range `129ab34..e8b0656`, 5 commits)
> — code + tests + live probes (Jun 12, ~3:30 AM EDT) + full regression scan. No code modified.

## Verdict

**Accepted. All six fixes are real, tested, and (where probeable) live — with one adjudication overturned and one recurring signoff hygiene defect.**

### Confirmed (code + tests, live where noted)

- **D1 pin markdown/truncation** — `pinnedInsights.ts:133-151`: strips bold/code/bullets, word-boundary truncation with >50% guard, trims dangling `([{,;:-` etc. Tests include the *exact* live artifact shape ("Illinois (\*\*IL\*\*)…55,037") and the e2e asserts no `**`/backticks ever render in the pinned card. My independent end-to-end re-pin tonight failed on *session mechanics* (panel toggle state in automation), not on the feature — localStorage and card text from the prior pin cycle show no raw markdown; the signoff's 5/5 live battery covered D1 directly. Accepted on code + their live evidence + my partial probe.
- **D2 refusal follow-ups** — synthesized fallback now gated on the same trust denylist; explicit *backend-provided* follow-ups still render on untrusted answers (deliberate, commented, tested both directions). Note: the e2e refusal test asserts zero chips, which couples to the backend returning none on `policy_blocked` — true today; the unit tests carry the real boundary.
- **D5 entrance replay** — `KpiCard` keyed on label only; `useFirstAppearance` freezes first-ness at mount; remount-with-new-value test asserts no replay. **The self-caught Sankey twin** (keyed on volatile counts) is real, fixed (keyed on stage structure), and its previously-vacuous once-only test now remounts with changed counts. No opposite bug introduced: never-replay-within-page-load is the documented semantic. Nit: same-label KPIs now share an animation key across routes.
- **D6a/b/c** — direction-only headline when delta is non-finite + finite-mover preference (tested incl. null); `useCountUp.ts` deleted (zero refs); ROI rejects >$100M balance / >$100K cost-per-lead consistently with `clampPct`, trillion tier added, boundary-tested.
- **D8 hover adjudication HOLDS** — every Supporting-evidence chip gets a non-optional descriptor with unknown-source fallback (`drawerSources.ts:111`); 110 ms open delay; live hover regression added to the e2e spec. My 2:30 AM non-appearance doesn't reproduce in their battery; withdrawn.
- **Regression scan** — diff confined to stated files; GenieChat/home untouched; no weakened tests; `nonNegMoney` signature change has no other callers; vitest static count = exactly **387** (+11, matching the claim); budget bump 83→87 is within the file's ~5% policy with the baseline preserved (not rewritten).

### Overturned: the Sankey "0.0%" adjudication

The signoff classed the funnel artifact as by-design because "conversion % is already suppressed for grown stages." That suppression (a) predates this range and (b) only covers **grown** stages (`ratio > 1.0001 → null`). The r5 artifact is a **shrunk** stage: Approved 8 / ≥16K → 0.013% → `toFixed(1)` → **"0.0%"** — and it renders live right now ("8 · 0.0%" on the funnel, re-confirmed tonight). Fix is one line in `formatConversionPct` (`analytics.lib.ts:400-404`): floor tiny ratios to "<0.1%" or suppress below threshold. The *balloon* adjudication (mixed denominators are honest data; talk-track item) I accept — heights are true and the grown-stage % is suppressed; but "0.0%" is a rendering artifact, not honesty.

### Recurring signoff hygiene defect (third occurrence)

"5 commits await your push" — **origin/main is at `e8b0656` == HEAD; 0 ahead.** Same stale-ahead-count pattern as rounds 3 and 4 (the bash snippet in the signoff was captured pre-push and never re-checked). Nothing awaits you. Recommendation for the agent's loop: re-run `git status -sb` *immediately before* writing the signoff, or stop reporting push state entirely.

### New/residual items (small)

1. **Pin-to-Home is panel-only** — the `/ask-genie` deep-dive route renders trusted answers *without* the pin affordance (discovered tonight while re-testing D1 there). Booth Q&A lives on the deep-dive. Either add the pin to the route's answer surface or record panel-only as deliberate.
2. **`initialJsBytes` budget headroom is 0.7%** (270 vs actual 268.16) — violates the file's own ~5% policy in the tight direction; the next tiny dependency bump fails CI in the middle of demo week. Bump to ~282.
3. Briefing still (correctly per D6a's scope) leads with the purge artifact "down 74.2%" — the r5 *operational* item stands: re-seed a handful of approvals before Sunday or the morning-briefing opener is a shrinkage story.
4. Sankey "0.0%" (above) — one-line fix.

### Session notes

Zero console errors across tonight's session (sixth consecutive clean sweep). Live probes this round: briefing headline copy, Sankey "0.0%" reproduction, deep-dive trusted answer (by-state question re-run), pin-affordance absence on `/ask-genie`, D1 storage/card inspection. Backend suites remain CI-attested (sandbox Python floor); the 387 count was verified by independent static count.

## Close

Six rounds, ~60 findings raised, every one now fixed, withdrawn-with-method-notes, or adjudicated-with-rationale — and the implementation caught two real bugs the audits missed (the budget-422 behind the freeze, and its own Sankey replay). What remains before Sunday is deliberately small: the "0.0%" floor, the budget headroom bump, the approval re-seed, and a decision on deep-dive pinning. The product is in the best shape it has been at any point in this audit cycle.
