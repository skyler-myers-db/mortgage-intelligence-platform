---
name: component-test-context-mock
description: How to mount routes/components in happy-dom vitest tests — which modules to mock and how to run the suite.
metadata:
  type: project
---

Mounting a route or context-dependent component in a happy-dom vitest test needs a specific set of `vi.mock`s, or it explodes on network/context.

**Why:** Routes call live hooks (react-query, warming-up retry, footprint, app context) that have no provider in a bare `createRoot` render; unmocked they throw or hang.

**How to apply:**
- Mock `../lib/api` with a `vi.hoisted(() => ({ ...fns }))` object exposed as `{ api: apiMocks }`. Set `mockResolvedValue` in `beforeEach`.
- Mock `../components/AppContext` `useApp` when the component reads `setDrawer` / `showEvidence` / `showConfidence`.
- For a full route mount also mock `../lib/useWarmingUpRetry` to an idle shape `{ data: null, warmingUp: null, error: null, isFetching: false, manualRetry: vi.fn() }`, plus `../components/FootprintProvider` (`useFootprint`) and `../lib/configOptionsQuery` (`useConfigOptionsQuery`) with stable objects.
- Wrap in `<QueryClientProvider>` (retry:false) + `<MemoryRouter initialEntries={[path]}>`; drive async with an `act`-based `settle()` (flush microtasks + a 0ms timer) or a `waitUntil(cond)` loop.
- **Run the suite via `npm --prefix frontend run test`** (not `npx`/repo-root) so the babel/rolldown + vitest config resolves from `frontend/`.
- **Gotcha:** a *pending* "Checking"/loading placeholder can share a CSS class with the *resolved* rows (e.g. `.growth-agent-capability`). A `waitUntil(() => querySelector('.that-class'))` will fire on the placeholder before data lands — pin the resolved row TEXT instead. Related: [[project-warming-up-pattern]].
