---
name: lead-queue-filter-plumbing
description: Adding a Lead Queue portfolio filter means touching several parallel structures in lead-queue.filters.ts; the /api/leads pass-through is generic.
metadata:
  type: project
---

A new Lead Queue portfolio/segment filter is not a single edit — it threads through several parallel structures in `frontend/src/routes/lead-queue.filters.ts` plus the route render. Miss one and the filter silently no-ops, doesn't render a chip, or isn't exported.

**Why:** The filter layer is deliberately split (options / normalize / chip / parse) so the URL stays the source of truth and export stays allowlisted; the pieces are easy to under-wire.

**How to apply:** When adding a filter, wire all of:
1. A `*_FILTER_OPTIONS` const array (the dropdown values).
2. Normalization into `parsePortfolioCriteria` (URL param -> criteria field) and/or a `*DisplayValue` helper for the current-value label.
3. The `portfolioFilterEntries` mapping so an active value renders a hero chip, and `buildLeadQueueExportFilters` allowlisting if it should survive export.
4. The `<FilterSelect>` render + `updateParam(...)` call in `lead-queue.tsx`.

The backend `/api/leads` pass-through via `portfolioCriteria` is **generic** — allowlisted keys flow through without a per-filter backend change. Related: [[feedback-design-contract]].
