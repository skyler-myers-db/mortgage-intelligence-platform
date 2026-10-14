/**
 * @vitest-environment happy-dom
 *
 * W5c integration (w5-refusal-capture-sales x w5-lead-queue-paging): the
 * Refusal reports list is a recorded read, so a dependency recovery must not
 * re-read it. The list's page queries carry `meta: { auditedPages: true }`,
 * the predicate paging's HealthProvider recovery skips (hasAuditedPages).
 * FamilyCounts observes the SAME page-0 query key with no meta, and an
 * observer's options overwrite the shared query's, so this is proven on the
 * mounted panel, not on the predicate alone: a real HealthProvider, a real
 * QueryClient and a scripted Lakebase down -> up edge. A plain Lakebase
 * panel beside it is re-fired by the same edge (non-vacuity); the reader's
 * Retry is the only re-read.
 */
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../lib/api';
import type { HealthPayload } from '../../lib/apiTypes';
import { HealthProvider } from '../HealthProvider';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ list: vi.fn(), question: vi.fn() }));
vi.mock('../../lib/apiClients/refusalReports', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/apiClients/refusalReports')>()),
  refusalReportsApi: api,
}));

import { RefusalReportsPanel } from './RefusalReportsPanel';

function lakebaseDown(path: string): ApiError {
  return new ApiError('lakebase is warming up', {
    path,
    status: 503,
    retryable: true,
    dependency: 'lakebase',
    reason: 'warming_up',
  });
}

const health = (lakebase: 'up' | 'down'): HealthPayload => ({
  status: lakebase === 'up' ? 'ok' : 'degraded',
  mode: 'live',
  dependencies: { warehouse: 'up', lakebase, genie: 'up' },
});

describe('a Lakebase recovery never re-reads the recorded Refusal reports list', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.useFakeTimers();
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false } },
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    api.list.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    Reflect.deleteProperty(document, 'visibilityState');
    vi.useRealTimers();
  });

  async function advance(ms: number): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  function PlainLakebasePanel({ queryFn }: { queryFn: () => Promise<string> }) {
    const query = useQuery({ queryKey: ['plain-lakebase-panel'], queryFn });
    return <div data-panel="plain">{query.isError ? 'error' : (query.data ?? 'loading')}</div>;
  }

  it('re-fires the plain panel on the down -> up edge and leaves the list to its Retry', async () => {
    let lakebaseUp = false;
    const fetchHealth = vi.fn(async () => health(lakebaseUp ? 'up' : 'down'));
    api.list.mockImplementation(async () => {
      throw lakebaseDown('/api/v1/admin/refusal-reports');
    });
    const plain = vi.fn(async () => {
      if (!lakebaseUp) throw lakebaseDown('/api/v1/admin/plain');
      return 'plain loaded';
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <HealthProvider pollIntervalDegradedMs={3000} debounceUpMs={0} fetchHealth={fetchHealth}>
            <PlainLakebasePanel queryFn={plain} />
            <RefusalReportsPanel />
          </HealthProvider>
        </QueryClientProvider>,
      );
    });
    await advance(0);
    expect(container.textContent).toContain('The refusal reports could not be read.');
    expect(api.list).toHaveBeenCalledTimes(1);
    expect(plain).toHaveBeenCalledTimes(1);

    lakebaseUp = true;
    await advance(3000);
    await advance(50);

    expect(plain, 'the recovery edge fired').toHaveBeenCalledTimes(2);
    expect(container.querySelector('[data-panel="plain"]')?.textContent).toBe('plain loaded');
    expect(api.list, 'a recovery never re-reads the recorded list').toHaveBeenCalledTimes(1);
    expect(container.textContent).toContain('The refusal reports could not be read.');

    await advance(8000);
    expect(api.list).toHaveBeenCalledTimes(1);

    const retry = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Retry');
    expect(retry).toBeDefined();
    await act(async () => {
      retry?.click();
    });
    await advance(0);
    expect(api.list, "the reader's Retry is the one re-read").toHaveBeenCalledTimes(2);
  });
});
