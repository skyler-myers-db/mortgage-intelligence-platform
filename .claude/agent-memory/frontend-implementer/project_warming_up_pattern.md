---
name: Warming-up retry pattern
description: How to render cold-start 503s as a per-tile warming-up block instead of a red error banner
type: project
---

The backend's `_dependency_down_handler` returns HTTP 503 with
`{detail, retryable: true, dependency, correlation_id}` when a
dependency's circuit is open or warehouse is cold-starting. The
frontend's `api.ts` `_fetchWithRetry` retries 3× with jittered backoff,
but on a genuinely cold warehouse that's not enough.

Why: Databricks SQL warehouses auto-suspend after idle and take
~30–60s to warm. The first nav on a cold boot surfaces a 503. Without
explicit UX, the user sees a red "Backend unavailable" banner.

How to apply:
- `ApiError` exposes `status`, `retryable`, `dependency`,
  `correlationId`, `aborted`. Use `isWarmingUpError(err)` instead of
  string-matching `err.message`.
- For single-fetch routes, call `useWarmingUpRetry(fetcher, deps,
  opts)` from `frontend/src/lib/useWarmingUpRetry.ts` — returns
  `{ data, warmingUp, error, manualRetry }` and handles 6×/5s retry.
- For multi-fetch routes (Offer Orchestrator's
  `Promise.all([borrower, recommend])`), inline the attempt loop —
  see `offer-orchestrator.tsx` for the pattern. Track `warmingUp`
  state alongside `loadError`.
- Render the shared `WarmingUpBlock` presentational component
  (`components/ui/WarmingUpBlock.tsx`). Standard copy:
  "Databricks SQL warehouses auto-suspend when idle. It takes ~30
  seconds to warm up. Retrying automatically…" with
  "(attempt N of 6)" counter.
- On Home, the KPI row is hidden while warming (`!previewWarming`)
  so warming + KPI don't double-render, but the map + activity log
  stay live — per-tile independence.
