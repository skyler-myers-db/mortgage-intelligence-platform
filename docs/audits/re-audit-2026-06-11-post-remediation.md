# Module 0 — Re-Audit of the Remediation Signoff (post-`6c9ae6f`)

> **Internal validation artifact — not approved for public release.**
> Adversarial verification of the 2026-06-11 remediation signoff: three parallel code/diff
> audits over `99e55d6..6c9ae6f`, plus an independent live session (Chrome + Mac desktop,
> authenticated) against https://mip-app-2543889327043640.aws.databricksapps.com.
> Companion to `full-stack-audit-2026-06-11.md` and `docs/modernization-todo.md`. No code modified.

**Re-audit date:** 2026-06-11, ~12:15 PM EDT · **HEAD verified:** `6c9ae6f` (merge, 24 commits ahead of `origin/main` — **`git push` still pending, owner: Skyler**) · gold refreshed 2026-06-11T15:30Z (post-remediation refresh confirmed via live Genie freshness marker)

---

## Verdict on the signoff

**Substantially accurate and honestly framed — the remediation is real, deployed, and live-verified.** Of the remediation claims I could adjudicate from code + live behavior: **15 CONFIRMED, 4 PARTIAL, 1 signoff claim REFUTED.** Of its five refutations of the original audit: **4 sustained** (the original audit was wrong or imprecise on those), **1 overturned** (Genie FAB). No new release blockers found. The app is in strictly better shape than the audit baseline on every axis measured.

### Independent live re-verification (this session)

| Claim | Signoff figure | Independently measured now |
|---|---|---|
| `/v1/leads` | 144–159 ms repeats | **291 ms repeat**, 2,593 ms first-hit-after-idle (was 3.6–5.4 s every load). Magnitude confirmed; see note 1 |
| Proof integrity gaps | 25-borrower sweep: 0 | **14-borrower API sweep: 0 gaps, 0 failures** (and the sweep itself landed in the audit log as `view_borrower_proof` events — API-level governance works) |
| Narrative trio | IL 70 / TX 69 / IL 88 | **Bit-exact:** `B-0CPWBTJMAPFY2` IL 70 · `B-1IB0UGBTFYM20` TX 69 · `B-102FL7THC6Q3L` IL 88, all HTTP 200 |
| "average loan age" guardrail | answered, source=genie | **Confirmed live** — executed against the space and returned a sourced, tabular answer (see note 2) |
| FRED readiness | "283 rows" | **"FRED Market Rates — 283 rows"** live on Admin |
| Data-estate + Console open | 3 word-boundary lines, no shred | **Confirmed** — clean wrapping with Console rail open at 1456 px; pending-feed chips wrap under names |
| Hero CTA | 0 reloads (SPA) | **Confirmed** — click → `/ask-genie` with zero new navigation entries |
| `th[aria-sort]` | 7 | **7, live in DOM** |
| Console errors | — | **Zero across the entire re-audit session** |
| Git state | merge `6c9ae6f`, 24 ahead, clean | **Confirmed exactly** |

Note 1 — the 144–159 ms claim holds for warm repeats; after hours idle the first hit was 2.6 s (cache bucket rollover / app restart after deploys). Still a ~15× improvement at p50; the boot-warm + refresh-ahead keeps steady-state browsing fast. Watch one thing: the warm-ahead docstring claims "zero added staleness," but layered TTLs can compound to ~2× TTL worst-case (`databricks_leads.py:698-723`) — make the comment honest.

Note 2 — the unblocked "loan age" answer was **semantically wrong in an instructive way**: Genie computed average loan age from the *data-refresh date* (0.00 years) rather than loan origination. Guardrail fix verified; Genie-space curation gap opened (add origination-date/loan-age semantics or a named metric). Don't ask this exact question on stage yet.

## Where the signoff was right that the original audit was wrong

Conceded, with evidence verified: **audit-write idempotency** (ON CONFLICT on `request_id` + same-transaction gating; the four cited tests exist at `test_outreach_reject.py:458/632/706/764` — original P2-6 withdrawn for the production path); **TTLCache falsy** (`is not None` semantics cache `[]`/`0`/`""` fine — original P3 claim wrong except literal `None`); **Genie `/start` cost** (pure local read, no remote conversation created — duplicate starts are benign); **prod `run_as`** (sustained narrowly: it is a checklist comment block, though checklist item 3 still presupposes an SP the config never sets — the deploy-prod-from-CI-as-SP recommendation stands); **aria-sort placement** (the audit's "button" suggestion was invalid ARIA; `th[aria-sort]` is correct).

## Where the signoff overstates (corrections for the record)

1. **Genie FAB "prototype parity" — REFUTED.** The committed prototype *does* render a fixed bottom-right `.genie__fab` at desktop width (`design_files/Module 0 Prototype.html:773-785`; no media query hides it; same in `index.html` and `Design System.html`). The app's desktop `display:none` is a *deliberate, documented, test-pinned deviation* (`components.css:1678-1702`, `components.test.ts:116-121`) — defensible UX, but "parity" is factually wrong. Reclassify as deviation-by-choice.
2. **mypy ratchet is honor-system, and leakier than stated.** Nothing enforces "shrink-only" (no test pins the override list — adding an exemption passes CI silently); the list has 22 entries, not 21, and the `backend.api.*` **wildcard auto-exempts every future module in `backend/api/`** (~42 modules effectively exempt of 106). New errors in checked modules do fail CI. Cheap fix: a unit test pinning the override list.
3. **Claimed test coverage that isn't committed:** no aria-sort assertion exists anywhere (live probe only); the "Hypothesis property test" is actually a 200k-point seeded sweep + exhaustive half-boundary lattice (arguably better for CI, but not Hypothesis); the "programmatic DDL parity check" from the CTAS slice was a dev-time one-off on the *superseded* syntax — nothing now guards the 269 `COMMENT ON COLUMN` statements against drift.
4. **P1-4 sub-claim unmet:** `.env.example` still doesn't document the PII-salt scope/key (deploy automation arguably supersedes it, but the audit's prescribed fix said document it — and stale *contradictory* salt docs were left behind, below).

## New findings from this re-audit (none release-blocking)

1. **P2-8 residual — lifecycle table loses its metadata anyway:** `jobs/sync_lifecycle_state.py:280` is a bare `CREATE OR REPLACE TABLE` with no CLUSTER BY/TBLPROPERTIES/COMMENTs, and deploy step 9 runs it right after step 8 restores them. The CTAS fix doesn't hold for `gold.borrower_lifecycle_state`.
2. **P2-8 residual — `demo_first_party_feeds.sql` (5 tables)** drops DDL column comments on every refresh (same defect class, demo-gated).
3. **Stale agent memory teaches the known-broken syntax:** `.claude/agent-memory/data-modeler/project_gold_ctas_redeclares_ddl_metadata.md:12-23` still presents the typeless CTAS column list — the exact construct `bccb5b2` proved is a live `PARSE_SYNTAX_ERROR` — as "the working syntax." High regression risk for the next data-modeling agent task. Update or delete the memory file.
4. **Contradictory salt docs left in place:** `silver_property_master.sql:17-23` header still describes the removed fallback and calls rotation "acceptable" (body now says the opposite); same stale claim at `docs/data-contract-module0.md:638`.
5. **Equity/LTV can render 101% combined:** the P2-11 fix uses `ROUND(100−cltv)` + `ROUND(cltv)` (both half-up) — an exact-.5 CLTV yields equity+LTV=101 in the dossier; no complementarity test pins it, and the new column comment says CAST where the code ROUNDs.
6. **Grants step timing nit:** `deploy.sh:419` uses `wait_timeout: 50s / CANCEL` — a cold/queued warehouse fails step 4c with a misleading "grant failed" (serverless makes this unlikely).
7. **Hermeticity guardrail scan roots omit `jobs/` and `tests/`** — `jobs/` has importable Python; a module-scope `load_dotenv` there would evade the AST guard (currently clean).
8. **Admin timestamps still zone-naive** (`admin-config.tsx:772-780` + 3 call sites) — contradicts the "every clock time through `lib/time`" slice claim; instants correct, labels unzoned.
9. **Genie semantic gap:** loan-age questions resolve against refresh dates (note 2) — Genie-space curation item.
10. Cosmetic: EvidenceDrawer's new loading/error freshness states reuse the `--unavailable` style (no `--loading/--error` modifiers); CLAUDE.md now says "Twelve routes" — there are 11 route modules + 1 redirect.

## Open items / ownership

- **`git push origin main`** — 24 commits, yours (push was denied to the agent 4+ times, correctly).
- **Design share link** — `design_files/` ruled canonical per CLAUDE.md (decision recorded in the tracker); updating the share link before the John West / Movement sit-downs is on Skyler.
- **Deferred with forcing function** (sound): router-layer refactors + `set-state-in-effect` removal pinned to the 2026-06-21 allowlist expiry (`tools/file_size_allowlist.json:_policy`, hard-fails CI when due); dependency bumps post-Summit.
- Cotality MLS / Building Permits shares remain the two "Awaiting feed" segments (external; honest pending UI verified again live).

**Bottom line:** signoff accepted with the corrections above. Demo posture for June 15 is strong: hot paths are sub-second warm, the proof surface shows zero false integrity gaps across two independent sweeps, the narrative trio resolves to real dossiers, approvals/audit are live end-to-end, and the remaining risks are documented, owned, and dated.
