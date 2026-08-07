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
