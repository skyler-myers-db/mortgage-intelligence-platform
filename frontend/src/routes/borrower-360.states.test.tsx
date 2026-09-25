/**
 * @vitest-environment happy-dom
 *
 * Borrower 360's failure states (audit 2026-09-21 states-04 slice 2 and
 * states-03 part a), rendered through the real route: under a banner that
 * already names the outage the dossier waits calmly; any other failure speaks
 * the shared vocabulary and never the transport message; a 404 is unchanged.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../lib/apiTransport';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const state = vi.hoisted(() => ({
  borrower: { data: null as unknown, warmingUp: null as unknown, error: null as unknown, manualRetry: vi.fn() },
  health: null as null | { health: { dependencies: Record<string, string> }; connection: string },
}));

const apiMocks = vi.hoisted(() => ({
  borrowerLifecycle: vi.fn(() => new Promise(() => undefined)),
}));

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: apiMocks,
}));
vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: () => state.borrower,
}));
vi.mock('../components/HealthProvider', () => ({
  useOptionalHealth: () => state.health,
}));
vi.mock('../components/AppContext', () => ({
  useApp: () => ({ lastBorrowerId: null, setLastBorrowerId: vi.fn(), saveLead: vi.fn(), isLeadSaved: () => false }),
}));

import Borrower360 from './borrower-360';

const ID = 'B-0123456789ABC';
const SENTINEL = 'SENTINEL SQL warehouse unavailable 500 Internal Server Error';
const WAREHOUSE_DOWN = { health: { dependencies: { warehouse: 'down', lakebase: 'up', genie: 'up' } }, connection: 'online' };

function outage(): ApiError {
  return new ApiError(SENTINEL, { path: `/api/v1/borrowers/${ID}`, status: 503, retryable: true, dependency: 'warehouse', reason: 'retries_exhausted' });
}

let root: Root;
let queryClient: QueryClient;

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  state.health = null;
  state.borrower = { data: null, warmingUp: null, error: null, manualRetry: vi.fn() };
});

afterEach(() => {
  act(() => root.unmount());
  queryClient.clear();
  document.body.innerHTML = '';
});

async function mount() {
  await act(async () => {
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
  // A failed read's page is its own chunk, loaded on the failure.
  await act(async () => {
    await vi.dynamicImportSettled();
  });
}

const text = () => document.body.textContent ?? '';
const retry = () => document.querySelector<HTMLButtonElement>(`button[aria-label="Retry loading borrower ${ID}"]`);

describe('Borrower 360 failure states', () => {
  it('waits calmly under a banner that names the outage, with a secondary Retry', async () => {
    state.health = WAREHOUSE_DOWN;
    state.borrower.error = outage();
    await mount();

    expect(document.querySelector('h1')?.textContent).toBe(`Loading ${ID}…`);
    expect(text()).toContain('This dossier reloads when the analytics warehouse reconnects.');
    expect(text()).toContain('Reconnecting');
    expect(text()).not.toContain('Backend unavailable');
    expect(text()).not.toContain('SENTINEL');
    expect(document.querySelector('[role="alert"]')).toBeNull();
    act(() => retry()?.click());
    expect(state.borrower.manualRetry).toHaveBeenCalledTimes(1);
  });

  it('names the same outage in the shared vocabulary when no banner does', async () => {
    state.borrower.error = outage();
    await mount();

    expect(document.querySelector('h1')?.textContent).toBe('The analytics warehouse is unavailable');
    expect(text()).toContain('The app already retried; try again shortly.');
    expect(text()).toContain('Unavailable');
    expect(text()).not.toContain('SENTINEL');
    expect(retry()).not.toBeNull();
  });

  it('never prints a 500\'s transport message', async () => {
    state.health = WAREHOUSE_DOWN;
    state.borrower.error = new ApiError(SENTINEL, { path: `/api/v1/borrowers/${ID}`, status: 500 });
    await mount();

    expect(document.querySelector('h1')?.textContent).toBe(`Couldn't load borrower ${ID}`);
    expect(text()).toContain('The server hit an unexpected error.');
    expect(text()).not.toContain('SENTINEL');
    expect(text()).not.toContain('reloads when');
  });

  it('offers no Retry for a permission failure', async () => {
    state.borrower.error = new ApiError(SENTINEL, { path: `/api/v1/borrowers/${ID}`, status: 403 });
    await mount();

    expect(document.querySelector('h1')?.textContent).toBe(`Your role can't open borrower ${ID}`);
    expect(text()).toContain('Access required');
    expect(retry()).toBeNull();
  });

  it('keeps the 404 copy', async () => {
    state.borrower.error = new ApiError('not found', { path: `/api/v1/borrowers/${ID}`, status: 404 });
    await mount();

    expect(document.querySelector('h1')?.textContent).toBe(`Borrower ${ID} not found`);
    expect(text()).toContain('Check the ID, use search, or return to the lead queue.');
    expect(retry()).toBeNull();
  });
});
