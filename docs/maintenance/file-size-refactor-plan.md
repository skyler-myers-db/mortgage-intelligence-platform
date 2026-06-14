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

No file should receive another expiry extension without either a smaller-file split or a new dated schedule decision in this document.
