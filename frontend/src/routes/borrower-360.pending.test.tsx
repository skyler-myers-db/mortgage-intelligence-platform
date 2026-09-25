/**
 * @vitest-environment happy-dom
 *
 * Borrower 360 while its failure page (DossierFailure, its own chunk) has not
 * loaded, and when that chunk cannot load (a stale deploy): the read DID fail,
 * so the page says so neutrally ("Borrower … could not load."), never
 * "Loading borrower …" forever, never an alert, never the transport text; once
 * the chunk has failed, Retry (one explicit re-read) and Back to lead queue.
 * (borrower-360.states.test covers the page once the chunk is there.)
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, type ComponentType } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const state = vi.hoisted(() => ({
  borrower: { data: null as unknown, warmingUp: null as unknown, error: null as unknown, manualRetry: vi.fn() },
}));

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { borrowerLifecycle: vi.fn(() => new Promise(() => undefined)) },
}));
vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: () => state.borrower,
}));
vi.mock('../components/HealthProvider', () => ({
  useOptionalHealth: () => null,
}));
vi.mock('../components/AppContext', () => ({
  useApp: () => ({ lastBorrowerId: null, setLastBorrowerId: vi.fn(), saveLead: vi.fn(), isLeadSaved: () => false }),
}));

const ID = 'B-0123456789ABC';
const SENTINEL = 'SENTINEL SQL warehouse unavailable 500 Internal Server Error';

let root: Root;
let queryClient: QueryClient;

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

afterEach(() => {
  act(() => root.unmount());
  queryClient.clear();
  document.body.innerHTML = '';
  vi.doUnmock('../components/mortgage/DossierFailure');
  vi.resetModules();
});

/** The route and an error built from the SAME fresh module graph (instanceof ApiError). */
async function freshRoute() {
  const [{ default: Borrower360 }, { ApiError }] = await Promise.all([import('./borrower-360'), import('../lib/apiTransport')]);
  state.borrower = {
    data: null,
    warmingUp: null,
    error: new ApiError(SENTINEL, { path: `/api/v1/borrowers/${ID}`, status: 500 }),
    manualRetry: vi.fn(),
  };
  return Borrower360;
}

function render(Borrower360: ComponentType) {
  act(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/borrower-360/${ID}`]}>
          <Routes>
            <Route path="/borrower-360/:id" element={<Borrower360 />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

async function flush() {
  await act(async () => {
    await vi.dynamicImportSettled();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

const text = () => document.body.textContent ?? '';
const retry = () => document.querySelector<HTMLButtonElement>(`button[aria-label="Retry loading borrower ${ID}"]`);

describe('Borrower 360 before its failure page has loaded', { timeout: 30_000 }, () => {
  it('says the borrower could not load, never "Loading", while the chunk loads', async () => {
    vi.resetModules();
    const Borrower360 = await freshRoute();
    render(Borrower360);

    expect(text()).toContain(`Borrower ${ID} could not load.`);
    expect(text()).not.toContain('Loading borrower');
    expect(document.querySelector('[role="alert"]')).toBeNull();
    expect(text()).not.toContain('SENTINEL');
    // Retry waits for the failure page (it carries the right action).
    expect(retry()).toBeNull();

    await flush();
    expect(text()).not.toContain('could not load.');
    expect(text()).toContain("Couldn't load");
  });

  it('keeps the neutral line and offers Retry and the way back when the chunk cannot load', async () => {
    vi.resetModules();
    vi.doMock('../components/mortgage/DossierFailure', () => {
      throw new Error('Failed to fetch dynamically imported module');
    });
    const Borrower360 = await freshRoute();
    render(Borrower360);
    await flush();

    expect(text()).toContain(`Borrower ${ID} could not load.`);
    expect(text()).not.toContain('Loading borrower');
    expect(text()).not.toContain('SENTINEL');
    expect(text()).not.toContain('Failed to fetch');
    expect(document.querySelector('[role="alert"]')).toBeNull();
    expect(document.querySelector('a[href="/lead-queue"]')?.textContent).toContain('Back to lead queue');
    act(() => retry()?.click());
    expect(state.borrower.manualRetry).toHaveBeenCalledTimes(1);
  });
});
