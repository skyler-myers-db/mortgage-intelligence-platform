# File-size refactor plan

Date: 2026-06-14

The file-size gate is active in CI (`python tools/check_file_sizes.py --warn 500 --fail 900`). The oversized-file allowlist originally expired on 2026-06-21 as a post-Summit forcing function, but the listed-for-sale/HELOC hardening work intentionally prioritized live product credibility and source-evidence correctness over broad refactors.

The allowlist expiry is re-dated to 2026-07-31 with this explicit schedule decision:

1. Split `frontend/src/lib/api.ts` into typed endpoint clients by route group.
2. Split `frontend/src/components/mortgage/LeadTable.tsx` into table shell, row, row preview, bulk approval, and sales-disposition modules.
3. Split `frontend/src/components/mortgage/USChoroplethMap.tsx` into topology loading, drill state, legend/tooltip, and SVG rendering modules.
4. Split `backend/services/repositories/databricks_genie.py` and `backend/services/genie_actions.py` into guardrails, trusted SQL proof, action routing, and response shaping modules.
5. Split `backend/services/resilience.py` into circuit breaker, retry policy, TTL cache, and dependency error modules.
6. Split `backend/services/repositories/databricks_portfolio.py`, `backend/services/audit_store.py`, and `backend/services/sales_state.py` along query-builder, mapper, and persistence boundaries.
7. Split `tools/e2e_borrower_audit.py` into SQL fetchers, recompute model, comparators, and report rendering.
8. Move route-specific CSS out of `frontend/src/design-system/components.css` only where the prototype BEM contract remains preserved.

## 2026-06-17 Genie hardening addendum

The Genie sample-accuracy hardening introduced three additional oversize files:
`backend/services/repositories/databricks_genie_direct.py`,
`backend/services/repositories/databricks_genie_canonical.py`, and
`backend/api/genie.py`. They are temporarily covered by the same 2026-07-31
expiry because the immediate release blocker was answer correctness across
recommended questions, canonical fallbacks, and live stress prompts.

Before 2026-07-31:

1. Split `databricks_genie_direct.py` into population, segment, offer, ZIP/location, and governance-response dispatchers.
2. Split `databricks_genie_canonical.py` into prompt classifiers, SQL templates, and result-shaping helpers.
3. Split `backend/api/genie.py` into public chat routes, proof/asset routes, admin/eval routes, and request/response mappers.
4. Keep the new Genie evaluation fixtures close to the split modules so every extracted path retains direct unit coverage.

No file should receive another expiry extension without either a smaller-file split or a new dated schedule decision in this document.

## 2026-07-13 capability and bootstrap addendum

Two infrastructure modules crossed the 900-line boundary while the agentic
capability proof ledger and idempotent Lakebase migration chain were being
hardened: `backend/services/capabilities.py` and
`backend/services/lakebase_bootstrap.py`. They are covered only through the
existing 2026-07-31 deadline; this addendum does not extend that date.

Before 2026-07-31:

1. Split capability discovery, live workspace probes, and proof-ledger status
   shaping into separate modules while retaining one public snapshot API.
2. Split Lakebase migration SQL, migration-state predicates, and bootstrap
   orchestration so advisory-lock and idempotency behavior remain independently
   testable.
3. Remove both allowlist entries when the extracted modules land.

## 2026-07-23 deploy command-of-record addendum

The release-hardening work exposed that `scripts/deploy.sh` was outside the
file-size gate because neither `scripts/` nor `.sh` sources were inspected.
The gate now covers both. The newly added agent-proxy, verifier-Gateway, and
durable cutover-journal lifecycle functions were extracted to focused sourced
libraries under `scripts/lib/`; every new library remains below the 900-line
hard limit without an allowlist.

The remaining `scripts/deploy.sh` monolith predates this extraction and is
temporarily allowlisted only through 2026-08-15 so the command-of-record can
retain its reviewed ordering while the release completes. Before that date:

1. Extract App rollback, first-install recovery, and failure-compensation
   functions into a sourced App lifecycle library.
2. Extract credential minting and bounded-identity execution functions into a
   sourced identity library without exporting private credentials.
3. Extract Unity Catalog grant and job-refresh functions into a sourced data
   lifecycle library.
4. Keep orchestration order, argument parsing, and top-level traps in
   `scripts/deploy.sh`, then remove its allowlist entry.

Do not extend this exception without a new dated decision and concrete split
evidence. New shell libraries are never covered by this exception.

## 2026-08-05 governed-draft and Genie-lifecycle addendum

The 2026-07-31 expiry lapsed with the `codex/intelligence-trust-ux` branch in
flight, so the gate began failing for every file still listed. This is the new
dated schedule decision required by the rule above; it is not a silent
extension.

**Split completed with this decision.** `backend/api/genie.py` (1349 lines,
891 on `main`) crossed the limit when the async Genie lifecycle
(`/message/submit`, `/message/progress`, `/message/complete`) landed. Plan item
3 above is now partially satisfied: the deterministic guardrail battery —
protected-class, instruction-override, outreach, PII, scope-bypass, source-gap,
off-topic, cross-lender, sales-ops, footprint, plus refusal shaping and the
governed output block — moved verbatim to
`backend/services/genie_deterministic.py`. That is policy, not routing, so it
belongs beside the other Genie services. The router is now 734 lines and its
allowlist entry is **removed**, not re-dated. Call sites in the route bodies
are unchanged, so the audit source-contract test and the OpenAPI baseline still
hold. Remaining for that file: separate proof/asset and admin/eval routes from
the public chat routes.

**Newly oversize.** `backend/api/outreach.py` went 938 -> 2192 lines during the
campaign-treatment and governed-draft work. It is the human-approval and audit
path, so it is listed here rather than split in the same pass. Before the date
below:

1. Extract draft generation, regeneration, and copy-verification into an
   outreach draft service.
2. Extract the approval/rejection commit path (the atomic Lakebase decision +
   audit write) into its own module so the transaction boundary is
   independently testable.
3. Extract campaign-treatment assignment and eligibility gating.
4. Keep only routing, request validation, and response mapping in the router.

**New expiry: 2026-09-15** for the eight files still listed
(`databricks_genie_canonical.py`, `outreach.py`, `databricks_genie_direct.py`,
`databricks_portfolio.py`, `audit_store.py`, `sales_state.py`,
`databricks_genie.py`, `genie_actions.py`) and for the frontend entries carried
from the 2026-06-14 list. `scripts/deploy.sh` keeps its own 2026-08-15 date and
is not extended here.

The ratchet this decision adds: a file may be re-dated at most once more. Any
file still oversize on 2026-09-15 blocks merge until it is split.

### Two thresholds, one allowlist

The gate is enforced at two different limits and the allowlist has to satisfy
both: `tests/unit/test_architecture_boundaries.py` fails backend files over
**1000** lines, while CI runs `tools/check_file_sizes.py --warn 500 --fail 900`
over the whole repo. Files in the 900–1000 band therefore pass the unit test and
fail CI, which is how `backend/services/genie_client.py` (957) and
`tools/databricks/converge_campaign_treatment_access.py` (933, down from 985
after the raw-token probe came out) sat unlisted while `main` went red.

Both are covered through 2026-09-15 by the decision above. Before that date:

1. Split `genie_client.py` into transport/retry, message lifecycle polling, and
   response normalization.
2. Split `converge_campaign_treatment_access.py` into credential minting,
   identity probing, and group convergence, keeping the probe's secret-free
   diagnostics with the probe.

## 2026-08-06 schema validator split addendum

`backend/schemas/_validators.py` grew from 70 lines (architecture-audit
baseline) to 961 during the Genie analyst-brief and governance-hardening
rounds, landing in the same 900–1000 band described above: green in
`test_architecture_boundaries.py`, red in CI. It was never allowlisted, so the
gate failed on `main` from roughly PR #129 onward.

**Split completed with this decision — no allowlist entry added.** The module
is removed and its contents moved verbatim, by responsibility, to five
modules that each sit below the 500-line warning threshold:

- `_validators_tenant.py` — configured lender name + state-footprint
  providers, public lender refs, reviewed geography labels.
- `_validators_protected_class_patterns.py` — the reviewed protected-class /
  health / proxy vocabulary regexes only, so the term lists stay auditable in
  one place.
- `_validators_protected_class.py` — scanning machinery (confusable folding,
  audience-claim grammar, windowed proxy matching, criterion state) and the
  fail-closed protected-class detectors.
- `_validators_person_names.py` — title-case/contextual name-shape detection
  and the reviewed non-person phrase vocabulary.
- `_validators_unsafe_text.py` — prompt-injection, confidential/internal,
  mechanical-PII detectors, and the composite `contains_unsafe_ai_text`.

All 36 import sites now import from the responsibility modules directly;
there is no re-export facade, so the pile cannot silently regrow behind one
import path. Pattern equivalence and detector behavior were verified against
the pre-split module before deletion.

## 2026-09-08 deploy command-of-record slicing addendum

`scripts/deploy.sh` (5,080 lines) was covered only through 2026-08-15, so the
gate has failed on every run since that date (`ALLOWLIST-EXPIRED:
scripts/deploy.sh expired 2026-08-15`; last green `main` run 2026-08-13, first
observed failure PR #232). The size gate is not a required check, which is how
the red streak went unnoticed for three weeks.

**Split completed with this decision — the allowlist entry is removed, not
re-dated.** The 2026-07-23 plan above assumed the monolith was mostly helper
functions. It is not: roughly 1,900 lines are functions and roughly 3,150 lines
are top-level orchestration under the `step` banners, so extracting functions
alone cannot reach the 900-line limit. The entrypoint is therefore sliced,
verbatim and in reviewed order, into sourced files under `scripts/lib/`:

| File | Lines | Original range | Content |
|---|---|---|---|
| `scripts/deploy.sh` | 480 | — | usage, strict mode, credential unexport, argument parsing, `step`/`run` helpers, `on_error` + `trap … ERR`, exact-source gate, interpreter resolution, deploy-wide state, the sourcing index, the completion banner |
| `deploy_app_access_lifecycle.sh` | 166 | 391–551 | App identity pin, foreign-catalog binding remediation, sync-contract restore, treatment/release access convergence |
| `deploy_app_release_lifecycle.sh` | 463 | 566–1022 | signed-blue restore, candidate quiesce/stop, captured App and Gateway ACL convergence, failed-deploy compensation, bootstrap-grant revocation |
| `deploy_first_install_lifecycle.sh` | 339 | 1024–1356 | first-install journal capture/refresh/recovery/cleanup and `restore_rendered_sql_fail_closed` (the EXIT handler; the `trap … EXIT` statement stays in the entrypoint directly after this source line) |
| `deploy_identity_lifecycle.sh` | 572 | 1359–1924 | deployment-control resolution, workspace auth binding, M2M/App token minting, bounded-identity wrappers |
| `deploy_step_preflight_gates.sh` | 487 | 1929–2404 | step 0: preflight and configuration gates |
| `deploy_step_automation_identities.sh` | 390 | 2406–2783 | step 0 (continued): automation credentials, tooling guard, signed lease, bootstrap recovery, journal read, rollback scope |
| `deploy_step_signed_blue_proof.sh` | 219 | 2788–2996 | step 0a: signed-blue proof |
| `deploy_step_bundle_apply.sh` | 428 | 3001–3416 | steps 0a–4: Genie resolution, runtime secrets, render, build, validate/plan/apply, Lakebase role convergence, audits |
| `deploy_step_lakebase_and_uc_grants.sh` | 309 | 3421–3718 | steps 4b–4d: migration, synced-catalog quiesce, reviewed-function grants, treatment DDL, UC grants, secret scopes |
| `deploy_step_app_promotion_and_refresh.sh` | 239 | 3723–3950 | steps 5–10: App promotion and capture, silver/gold refresh, lifecycle sync, KPI snapshot, Genie rebind |
| `deploy_step_agentic_provisioning.sh` | 363 | 3955–4306 | step 10b: sync proof, verifier capture, historical retirement, Supervisor/proxy/Gateway provisioning |
| `deploy_step_agentic_cutover.sh` | 493 | 4307–4788 | step 10b (continued): agent-proxy boundary proof and the signed cutover |
| `deploy_step_agent_eval_and_smoke.sh` | 289 | 4793–5070 | steps 10c–11: Agent Evaluation, redeploy, live smoke, fail-closed restore |

The four function libraries and the nine step files are contiguous line ranges
of the previous file; nothing was reordered. The entrypoint keeps every other
line in place and replaces each range with a `# shellcheck source=` directive
plus the source line, so the reviewed step order is still readable top to
bottom in `scripts/deploy.sh`. Because a sourced file runs in the sourcing
shell, `set -euo pipefail`, `trap on_error ERR`, the EXIT compensation trap,
`$0`, `--help`, the positional arguments, and every variable are the same
objects they were; `./scripts/deploy.sh -t dev` executes the same statements in
the same order in the same process. Two intentional additions: each step file
begins with a guard that exits 2 before running anything when it is executed
directly (a slice run on its own would otherwise start issuing CLI calls
without strict mode, the exact-source gate, or the lease), and the only
observable difference on an internal error path is that bash's own diagnostic
names the step file and its local line.

Proof recorded with this decision:

- Expanding every slice back into the entrypoint (headers and directives
  stripped) reproduces the pre-split file byte for byte:
  `sha256 47bb8285619736b3f5d39a32e0f0ed938bf920c0d9a256eb51a04ac01ff36c13`,
  234,658 bytes, on both sides.
- `bash -n` passes on the entrypoint and all thirteen slices, which also proves
  no cut lands inside a compound statement, heredoc, or continuation.
- `shellcheck --severity=warning $(git ls-files '*.sh')` (the CI gate) passes.
  Standalone analysis treats each file as its own scope, so step files carry a
  file-level `disable=SC2034` (top-level state is read by later step files) and
  two libraries carry the same note for the deploy-wide state their recovery
  functions assign. SC2154, the check the gate exists for, stays active; a
  whole-program `-a` pass was measured and rejected because it already reports
  seven SC2034 findings on the unsplit file.
- `python tools/check_file_sizes.py --warn 500 --fail 900` passes with no entry
  for any of the fourteen files.
- The sandboxed dry run and the deploy contract suite
  (`tests/unit/test_deploy_dev_workflow_contract.py`) run against the sliced
  layout; the contracts that pin orchestration order and counts read the
  command-of-record through `tests/fixtures/deploy_script.py`
  (`deploy_entrypoint_text()`), which expands the slices back into the
  entrypoint so every existing index/count assertion keeps its meaning.

The 2026-07-23 addendum's three named libraries map as follows: the App
lifecycle library became the App-access, App-release, and first-install
libraries; the identity library is `deploy_identity_lifecycle.sh`; the UC
grant and job-refresh functions stayed beside their only call sites in
`deploy_step_lakebase_and_uc_grants.sh` and
`deploy_step_app_promotion_and_refresh.sh` rather than moving ahead of the
orchestration, which keeps the slices verbatim.

**Next forcing function.** All eighteen remaining allowlist entries — including
`frontend/src/design-system/components.css` (6,618 lines) — expire on
2026-09-15, one week after this decision, and the 2026-08-05 ratchet above says
a file still oversize on that date blocks merge until it is split. Nothing in
this addendum extends that date.

## 2026-09-08 allowlist-expiry addendum

All eighteen remaining allowlist entries expire on 2026-09-15 and the
2026-08-05 ratchet above forbids another re-date, so from 2026-09-16 the gate
would fail every pull request with one `ALLOWLIST-EXPIRED` line per file. The
gate is not a required check, which is how a red `main` lands silently. This
addendum records the splits made in the week before the cliff, one commit per
file, each commit removing that file's allowlist entry in the same change.

Every split is proven with `tools/refactor_proof.py`, added with this
addendum: `defset` compares every top-level definition (functions, classes,
assignments; class members when `--flatten-classes`) of the pre-split blob
against the union of the post-split files and passes only when nothing was
removed or textually changed; `branches` proves a branch extraction from a
giant dispatcher function (skeleton verbatim, every extracted body verbatim,
no name left out of scope); `css` proves a stylesheet slicing (byte-identical
`@import` expansion plus an identical class-selector inventory). The tool was
validated against the two earlier criteria splits (d404b423, b7125331: 64 and
72 definitions, none changed) before use. Where a move is not verbatim
(mypy-driven type fixes in code leaving an exempt module, or a component
extraction), the deviation is enumerated under the file.

### tools/e2e_borrower_audit.py (1,526 -> 343)

Plan item 7: SQL fetchers, recompute model, comparators, report rendering.

| File | Lines | Content |
|---|---|---|
| `tools/e2e_borrower_audit.py` | 343 | docstring, sys.path shim, `run_audit`, client construction, CLI |
| `tools/e2e_borrower_audit_model.py` | 71 | gold-threshold constants, `Mismatch`, `ClipAudit` |
| `tools/e2e_borrower_audit_fetch.py` | 370 | sampling, raw-share, gold-row and evidence fetchers |
| `tools/e2e_borrower_audit_recompute.py` | 299 | independent Python re-computation of the gold row |
| `tools/e2e_borrower_audit_compare.py` | 320 | raw-vs-silver, raw-vs-gold, gold-vs-API comparators |
| `tools/e2e_borrower_audit_report.py` | 200 | `_hash_clip`, `render_report` |

Proof: `defset` pre 31 definitions, post 31 across six files, removed none,
added none, text-changed none. `python tools/e2e_borrower_audit.py --help`
resolves the sibling imports through the shim, and the `tools.*` import path
resolves under pytest. `tests/unit/test_next_best_offer.py` now pins
`NBO_PRODUCT_LABELS` in the recompute module, where the offer-label lookup
lives. Entry removed.

### tools/databricks/converge_campaign_treatment_access.py (936 -> 423)

The 2026-08-05 "Two thresholds" item asked for credential minting, identity
probing, and group convergence. The mint is the inner loop of
`target_identity_groups_probe` (one 245-line function: mint an exact
short-lived credential, read the target identity inside the
ambient-credential-free environment with the bounded settle window, restore
on failure), so separating it from the probe would not be a verbatim move.
The split is therefore two-way, with the probe's secret-free diagnostics
staying with the probe as the plan required:

| File | Lines | Content |
|---|---|---|
| `tools/databricks/converge_campaign_treatment_access.py` | 423 | identifier quoting, object presence, effective-privilege assertions, table grant convergence, CLI |
| `tools/databricks/campaign_treatment_identity_probe.py` | 535 | settle window and mint retry policy, ambient-auth isolation, fingerprints and failure diagnostics, `target_identity_groups_probe`, `target_group_membership_probe` |

Proof: `defset` pre 39 definitions, post 39 across two files, removed none,
added none, text-changed none. The deploy command of record still invokes
`python -m tools.databricks.converge_campaign_treatment_access`, whose
entrypoint and `deployment_workspace_client()` call are unchanged (the
deploy-contract pins that name the helper pass). No re-export facade: the
three tools and three tests that imported probe names from the converger
(`ensure_pipeline_namespace`, `foreign_catalog_binding_manifest`,
`audit_agent_runtime_foreign_uc_access`, and their tests plus the
credential-settle-window tests) now import them from the probe module. Entry
removed.

### frontend/src/components/mortgage/LeadTable.tsx (1,261 -> 567)

Plan item 2: table shell, row, row preview, bulk approval, sales disposition.
The row (`LeadTableRow.tsx`) and decision panels already existed; this pass
extracts the remaining responsibilities. It is a hook/component extraction,
not a verbatim move, so the proof is render equivalence plus the suites.

| File | Lines | Content |
|---|---|---|
| `LeadTable.tsx` | 567 | props, campaign binding, sorting, virtualization, table shell, `renderSortHeader`, `exportCsv`, composition |
| `useLeadApprovalActions.ts` | 541 | approve/reject/submit, reject-panel state, selection set, `bulkApprove` with its in-flight latches, bulk toast and its effects, focus restore |
| `useLeadSalesActions.ts` | 223 | sales overrides (and the merged `displayLeads`), assignment, disposition panel state, sales toast |
| `useLeadTableHotkeys.ts` | 40 | latest-handler ref plus the single window keydown listener |
| `LeadTableBulkActions.tsx` | 183 | `.bulk-actions` toolbar and bulk result toast |
| `LeadTableStatusChips.tsx` | 90 | growth-agent proof row, campaign-binding status and provenance rows |

Proof: a throwaway vitest harness (not committed) mounted the table in the
providers the suites use and printed `sha256(container.innerHTML)` for five
states (default list, expanded row with the reject panel, bulk toolbar,
sorted by score, virtualized); all five hashes match a pristine
`git archive` base tree byte for byte, and two base runs agree, so the
harness is deterministic. The eight LeadTable suites (72 tests), the full
frontend suite (1,085 tests), eslint at `--max-warnings 0`, `tsc -b`, the
production build and the bundle budget pass. Deviations, all recorded in the
commit: `approvalError` stays in the shell because both hooks write the one
`.table-error` alert; the sales hook returns `displayLeads` because the
override merge is the only consumer of the overrides; `assignSelected`
takes the selection and clear callback as arguments so the success path's
state-update order is unchanged; the two sales effects now register before
the virtualizer's effects (mutually independent); and two `eslint-disable`
comments were added for `react-hooks/purity` and `react-hooks/refs` findings
that the `useVirtualizer` compiler bailout had masked inside the old
function (the lines themselves are unchanged). Entry removed.

### frontend/src/components/mortgage/USChoroplethMap.tsx (1,044 -> 677)

Plan item 3: topology loading, drill state, legend/tooltip, SVG rendering.
The tooltip (`USChoroplethMapTooltip.tsx`) and utilities already existed.

| File | Lines | Content |
|---|---|---|
| `USChoroplethMap.tsx` | 677 | drill state machine, memoized derivations, real US state paths, breadcrumbs and chips, tooltip portal |
| `useChoroplethLiveFacts.ts` | 222 | lazily imported topology, per-state rollups (re-fetching on segment filter, mode, criteria), ZIP rollups on drill, the assignment overlay |
| `USChoroplethMapZipLevel.tsx` | 212 | the former `renderZipLevel` closure as a component with explicit props, plus `ZIP_TILE_CAP` |
| `USChoroplethMapLegend.tsx` | 110 | the `.map-legend` block |

Proof: the same render-equivalence harness printed hashes for four states
(state level, drilled ZIP level, overlay on, state selected); all four match
the pristine base tree byte for byte with a deterministic base. The three
map suites plus the four route tests that mount the map (48 tests), the full
frontend suite, eslint, `tsc -b`, build and budget pass. Structural notes:
two moved effects reset the tooltip, so the hook receives the component's
`setHover` (a state setter, stable identity); the ZIP component receives
`onSelectZip`/`onOpenStateQueue` callbacks so navigation stays with the
component that owns the drill state. Entry removed.

### backend/services/resilience.py (986 -> 223)

Plan item 5: circuit breaker, retry policy, TTL cache, dependency error.

| File | Lines | Content |
|---|---|---|
| `backend/services/resilience.py` | 223 | `with_retry`, `Resilient`, the type variable, and re-exports with the byte-identical `__all__` |
| `backend/services/resilience_breaker.py` | 373 | `DependencyDownError`, `CircuitBreaker`, the breaker registry (`get_breaker`, `all_breakers`, the test reset) |
| `backend/services/resilience_cache.py` | 456 | `TTLCache`, the stale-while-revalidate executor pair and its `atexit` hook, `StaleWhileRevalidateCache` |

Proof: `defset --allow-changed log`: pre 18 definitions, post 19 across three
files, removed none, added none; the only text change is the module logger,
which each new module now defines for itself. All 46 importers keep importing
from `backend.services.resilience`; the tests that reach the breaker registry
through the module (`_reset_breakers_for_tests`, `get_breaker`,
`CircuitBreaker`) resolve through the re-exports. 154 tests across the
resilience, observability, config-cache, error-sanitizer, load-test,
health-endpoint, treatment-gate, Lakebase-pool and workspace-host files pass.
Entry removed.

### backend/services/lakebase_bootstrap.py (927 -> 489)

The 2026-07-13 addendum: migration SQL, migration-state predicates, bootstrap
orchestration, with advisory-lock and idempotency behavior independently
testable.

| File | Lines | Content |
|---|---|---|
| `backend/services/lakebase_bootstrap.py` | 489 | the `ensure_*` orchestration, the process lock and the five bootstrapped flags (module globals the tests set directly), test hooks, re-exports |
| `backend/services/lakebase_bootstrap_sql.py` | 341 | every DDL tuple, preflight query and advisory-lock key |
| `backend/services/lakebase_bootstrap_state.py` | 165 | the five `_*_already_applied` predicates and both advisory-lock release helpers |

Proof: `defset --allow-changed log`: pre 36 definitions, post 37 across
three files, removed none, added none, only the per-module logger duplicated.
The DDL names the bootstrap tests assert on stay importable from the
orchestration module; the documentation-contract scan of this file (no
unversioned API paths) still passes. 132 tests across the bootstrap,
outreach-reject, sales-manager, documentation-contract, architecture,
typecheck-ratchet, loan-officer and approval-funnel files pass. Entry removed.

### backend/services/capabilities.py (911 -> 460)

The 2026-07-13 addendum: capability discovery, live workspace probes, and
proof-ledger status shaping behind one public snapshot API.

| File | Lines | Content |
|---|---|---|
| `backend/services/capabilities.py` | 460 | discovery (`probe_capabilities`), contract-presence checks, `_status_from_live`, the cached snapshot API, re-exports |
| `backend/services/capabilities_models.py` | 85 | `CapabilityStatus`, `Capability`, `LiveCapabilityStatus`, the live map alias, constants, and the one helper both halves call |
| `backend/services/capabilities_live_probes.py` | 425 | `collect_live_capability_statuses` and every `_probe_*` workspace probe |

Proof: `defset`: pre 36 definitions, post 36 across three files, removed
none, added none, text-changed none. Import direction is models, then live
probes, then discovery, so there is no cycle. The only monkeypatched module
attribute in the capability tests is `get_settings`, whose reader
`probe_capabilities` stayed put. 370 tests across the capabilities,
growth-agent API, admin-operations, health-endpoint, architecture and
documentation-contract files pass. Entry removed.

### backend/services/audit_store.py (1,872 -> 260)

Plan item 6: the policy/persistence boundary. The metadata vocabulary and its
validators are policy; the store, actor resolution and singleton are
persistence.

| File | Lines | Content |
|---|---|---|
| `backend/services/audit_store.py` | 260 | fallback identity counter, `build_safe_audit_metadata`, the `AuditStore` protocol, `resolve_actor`, event-type coercion, the store singleton and proxy, re-exports of every name imported elsewhere |
| `backend/services/audit_metadata_policy.py` | 663 | PII denylist, every metadata key and value vocabulary, the three audit exceptions |
| `backend/services/audit_metadata_validation.py` | 534 | key/PII/allowlist validators, top-level column, portfolio-criteria, result-filter and decision-input value policies, `_sanitize_metadata` |
| `backend/services/audit_metadata_public_values.py` | 583 | `_assert_public_safe_values` alone (507 lines) |

Proof: `defset --allow-changed _metadata_keys_deep`: pre 77 definitions,
post 77 across four files, removed none, added none. The one allowed change
is type-only: the second branch of `_metadata_keys_deep` no longer re-annotates
`out` (`out: set[str] = set()` became `out = set()`), which is the
`[no-redef]` error the module's mypy exemption had been hiding; the first
branch already declares the type for the function scope, so runtime behavior
is unchanged and the validation module type-checks without an exemption.
Layering is policy, then validation, then public values, then the store.
660 tests across the audit-store contract, PII denylist, my-events,
cohort-filter, rate-spread floor, contact-eligibility, Genie actions,
outreach-reject, admin RBAC, household rollup, growth-agent API, API-routes,
typecheck-ratchet, architecture and sales-manager files pass, and the
worker's `mypy backend` run reports no issues in 269 files. Entry removed.
