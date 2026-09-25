// @vitest-environment happy-dom

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../lib/apiTransport';
import type { HealthPayload } from '../lib/apiTypes';
import { HealthProvider } from './HealthProvider';
import {
  blockDefersToBanner,
  degradedDependency,
  friendlyDependencyName,
  isBanneredOutage,
  isRecoverableQueryError,
  recoveredDependencies,
  type DependencyHealth,
} from './healthRecovery';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Recovery refetch (audit 2026-09-21 `states-03`).
 *
 * The defect: the DegradedBanner promised automatic recovery that only a
 * Home-only effect delivered, so panels on every other route stayed red after
 * the dependency came back. The fix lives in HealthProvider, so the contract
 * is proven there: a real provider, a real QueryClient, real mounted queries,
 * and a scripted health poll crossing down → up.
 */

function dependencyDown(dependency: string, reason = 'warming_up'): ApiError {
  return new ApiError(`${dependency} is warming up`, {
    path: '/api/v1/leads',
    status: 503,
    retryable: true,
    dependency,
    reason,
  });
}

describe('isRecoverableQueryError', () => {
  const warehouse = new Set(['warehouse']);

  it('accepts a retryable 503 that names the recovered dependency', () => {
    expect(isRecoverableQueryError(dependencyDown('warehouse'), warehouse)).toBe(true);
    // The backend spent its own retry budget while the dependency was down:
    // exactly the panel that must come back when the dependency does.
    expect(isRecoverableQueryError(dependencyDown('warehouse', 'retries_exhausted'), warehouse)).toBe(true);
    expect(isRecoverableQueryError(dependencyDown('Warehouse'), warehouse)).toBe(true);
  });

  it('rejects an outage of a dependency that did not recover', () => {
    expect(isRecoverableQueryError(dependencyDown('genie'), warehouse)).toBe(false);
    expect(isRecoverableQueryError(dependencyDown('lakebase'), warehouse)).toBe(false);
  });

  it('rejects authorization, validation and not-found failures', () => {
    for (const status of [401, 403, 404, 409, 422, 500]) {
      const error = new ApiError('nope', { path: '/api/v1/leads', status, dependency: 'warehouse' });
      expect(isRecoverableQueryError(error, warehouse)).toBe(false);
    }
  });

  it('rejects backpressure, aborts, dependency-less 503s and non-ApiErrors', () => {
    const rateLimited = new ApiError('slow down', {
      path: '/x', status: 429, retryable: true, dependency: 'warehouse', reason: 'rate_limited',
    });
    const aborted = new ApiError('aborted', {
      path: '/x', status: 503, retryable: true, dependency: 'warehouse', aborted: true,
    });
    const anonymous = new ApiError('503', { path: '/x', status: 503, retryable: true });
    expect(isRecoverableQueryError(rateLimited, warehouse)).toBe(false);
    expect(isRecoverableQueryError(aborted, warehouse)).toBe(false);
    expect(isRecoverableQueryError(anonymous, warehouse)).toBe(false);
    expect(isRecoverableQueryError(new Error('boom'), warehouse)).toBe(false);
    expect(isRecoverableQueryError(null, warehouse)).toBe(false);
  });
});

/**
 * The bannered-outage rule (audit states-03 part a): a panel shows the calm
 * "reloads when … reconnects" status instead of a red alert exactly when its
 * error is the outage the DegradedBanner already names, which is exactly the
 * set refetchRecoveredQueries re-fetches when it ends.
 */
describe('isBanneredOutage and blockDefersToBanner', () => {
  const UP = { warehouse: 'up', lakebase: 'up', genie: 'up' };
  const WAREHOUSE_DOWN: DependencyHealth = { dependencies: { ...UP, warehouse: 'down' } };
  const BREAKER_OPEN: DependencyHealth = { dependencies: UP, circuit_breakers: { warehouse: 'closed', lakebase: 'open' } };
  const HEALTHY: DependencyHealth = { dependencies: UP, circuit_breakers: {} };

  it('names the bannered dependency the way the banner does', () => {
    expect(degradedDependency(WAREHOUSE_DOWN)).toBe('warehouse');
    expect(degradedDependency(BREAKER_OPEN)).toBe('lakebase');
    expect(degradedDependency(HEALTHY)).toBeNull();
    expect(degradedDependency(null)).toBeNull();
    expect(friendlyDependencyName('warehouse')).toBe('analytics warehouse');
    expect(friendlyDependencyName('lakebase')).toBe('operational database');
    expect(friendlyDependencyName('genie')).toBe('AI assistant');
  });

  it('is true for the matching retryable 503 while online (retries_exhausted included)', () => {
    expect(isBanneredOutage(dependencyDown('warehouse', 'retries_exhausted'), WAREHOUSE_DOWN, 'online')).toBe(true);
    expect(isBanneredOutage(dependencyDown('warehouse'), WAREHOUSE_DOWN, 'online')).toBe(true);
    // An open breaker is bannered by its name.
    expect(isBanneredOutage(dependencyDown('lakebase', 'breaker_open'), BREAKER_OPEN, 'online')).toBe(true);
  });

  it.each(['offline', 'unreachable', 'session_expired'] as const)('is false while the connection is %s', (connection) => {
    expect(isBanneredOutage(dependencyDown('warehouse'), WAREHOUSE_DOWN, connection)).toBe(false);
    expect(blockDefersToBanner('warehouse', WAREHOUSE_DOWN, connection)).toBe(false);
  });

  it('is false for a non-matching dependency and with no banner', () => {
    expect(isBanneredOutage(dependencyDown('genie'), WAREHOUSE_DOWN, 'online')).toBe(false);
    expect(isBanneredOutage(dependencyDown('warehouse'), HEALTHY, 'online')).toBe(false);
    expect(isBanneredOutage(dependencyDown('warehouse'), null, 'online')).toBe(false);
  });

  it.each([
    ['a 403', new ApiError('x', { path: '/x', status: 403, dependency: 'warehouse' })],
    ['a 422', new ApiError('x', { path: '/x', status: 422, dependency: 'warehouse' })],
    ['a 429', new ApiError('x', { path: '/x', status: 429, retryable: true, dependency: 'warehouse', reason: 'rate_limited' })],
    ['a 500', new ApiError('x', { path: '/x', status: 500, dependency: 'warehouse' })],
    ['a permission_denied 503', new ApiError('x', { path: '/x', status: 503, retryable: false, dependency: 'warehouse', reason: 'permission_denied' })],
    ['an aborted request', new ApiError('x', { path: '/x', status: 503, retryable: true, dependency: 'warehouse', aborted: true })],
    ['a plain Error', new Error('warehouse down')],
  ])('keeps %s red under the matching banner', (_label, error) => {
    expect(isBanneredOutage(error, WAREHOUSE_DOWN, 'online')).toBe(false);
  });

  it('defers a block for the bannered dependency, or one that names none', () => {
    expect(blockDefersToBanner('warehouse', WAREHOUSE_DOWN, 'online')).toBe(true);
    expect(blockDefersToBanner('Warehouse', WAREHOUSE_DOWN, 'online')).toBe(true);
    expect(blockDefersToBanner(null, WAREHOUSE_DOWN, 'online')).toBe(true);
    expect(blockDefersToBanner('lakebase', WAREHOUSE_DOWN, 'online')).toBe(false);
    expect(blockDefersToBanner('lakebase', BREAKER_OPEN, 'online')).toBe(true);
    expect(blockDefersToBanner(null, HEALTHY, 'online')).toBe(false);
  });
});

describe('recoveredDependencies', () => {
  const state = (filtered: 'up' | 'down' | 'resuming' | 'unknown') => ({ filtered });

  it('counts a finished resume (resuming → up) as a recovery', () => {
    expect(
      recoveredDependencies({ warehouse: state('resuming') }, { warehouse: state('up') }, undefined, undefined),
    ).toEqual(['warehouse']);
    expect(
      recoveredDependencies({ warehouse: state('up') }, { warehouse: state('resuming') }, undefined, undefined),
    ).toEqual([]);
  });

  it('reports only the down → up edge', () => {
    expect(
      recoveredDependencies(
        { warehouse: state('down'), lakebase: state('up'), genie: state('down') },
        { warehouse: state('up'), lakebase: state('up'), genie: state('down') },
        undefined,
        undefined,
      ),
    ).toEqual(['warehouse']);
  });

  it('does not treat the first observation as a recovery', () => {
    expect(recoveredDependencies({}, { warehouse: state('up') }, undefined, undefined)).toEqual([]);
    expect(
      recoveredDependencies({ warehouse: state('unknown') }, { warehouse: state('up') }, undefined, undefined),
    ).toEqual([]);
  });

  it('counts a breaker closing, but not one that is still probing', () => {
    expect(recoveredDependencies({}, {}, { genie: 'open' }, { genie: 'closed' })).toEqual(['genie']);
    expect(recoveredDependencies({}, {}, { genie: 'open' }, { genie: 'half_open' })).toEqual([]);
    expect(recoveredDependencies({}, {}, { genie: 'closed' }, { genie: 'closed' })).toEqual([]);
  });
});

describe('HealthProvider refetches failed panels when their dependency recovers', () => {
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
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    queryClient.clear();
    Reflect.deleteProperty(document, 'visibilityState');
    vi.useRealTimers();
  });

  function Panel({ name, queryFn }: { name: string; queryFn: () => Promise<string> }) {
    const query = useQuery({ queryKey: ['panel', name], queryFn });
    return <div data-panel={name}>{query.isError ? 'error' : (query.data ?? 'loading')}</div>;
  }

  const health = (warehouse: 'up' | 'down'): HealthPayload => ({
    status: warehouse === 'up' ? 'ok' : 'degraded',
    mode: 'live',
    dependencies: { warehouse, lakebase: 'up', genie: 'up' },
  });

  const panelText = (name: string) => container.querySelector(`[data-panel="${name}"]`)?.textContent;

  async function advance(ms: number): Promise<void> {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  it('re-fires the mounted warehouse 503, and nothing else', async () => {
    let warehouseUp = false;
    const fetchHealth = vi.fn(async () => health(warehouseUp ? 'up' : 'down'));

    // A Lead Queue panel that fails while the warehouse is cold, then loads.
    const leads = vi.fn(async () => {
      if (!warehouseUp) throw dependencyDown('warehouse');
      return 'leads loaded';
    });
    // A panel the actor is not allowed to read: recovery must not retry it.
    const forbidden = vi.fn(async (): Promise<string> => {
      throw new ApiError('Forbidden', { path: '/api/v1/admin', status: 403 });
    });
    // A Genie outage: a different dependency, which has NOT recovered.
    const genie = vi.fn(async (): Promise<string> => {
      throw dependencyDown('genie');
    });
    // An audited read from a page the user already left (no observer).
    const leftBehind = vi.fn(async (): Promise<string> => {
      throw dependencyDown('warehouse');
    });
    await queryClient.fetchQuery({ queryKey: ['panel', 'left-behind'], queryFn: leftBehind }).catch(() => undefined);

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <HealthProvider pollIntervalDegradedMs={3000} debounceUpMs={0} fetchHealth={fetchHealth}>
            <Panel name="leads" queryFn={leads} />
            <Panel name="forbidden" queryFn={forbidden} />
            <Panel name="genie" queryFn={genie} />
          </HealthProvider>
        </QueryClientProvider>,
      );
    });
    await advance(0);
    expect(panelText('leads')).toBe('error');
    expect(panelText('forbidden')).toBe('error');
    expect([leads, forbidden, genie, leftBehind].map((fn) => fn.mock.calls.length)).toEqual([1, 1, 1, 1]);

    // Still down on the next poll: nothing is re-fired.
    await advance(3000);
    expect(leads).toHaveBeenCalledTimes(1);

    // The warehouse comes back; the next poll sees the down → up edge.
    warehouseUp = true;
    await advance(3000);
    await advance(50); // the refetched query notifies its observer on a later tick

    expect(leads).toHaveBeenCalledTimes(2);
    expect(panelText('leads')).toBe('leads loaded');
    expect(forbidden).toHaveBeenCalledTimes(1);
    expect(genie).toHaveBeenCalledTimes(1);
    expect(leftBehind).toHaveBeenCalledTimes(1);

    // The edge fires once: a later healthy poll does not refetch again.
    await advance(8000);
    expect(leads).toHaveBeenCalledTimes(2);
  });

  it('waits out the down → up debounce before refetching', async () => {
    let warehouseUp = false;
    const fetchHealth = vi.fn(async () => health(warehouseUp ? 'up' : 'down'));
    const leads = vi.fn(async () => {
      if (!warehouseUp) throw dependencyDown('warehouse');
      return 'leads loaded';
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <HealthProvider pollIntervalDegradedMs={3000} debounceUpMs={5000} fetchHealth={fetchHealth}>
            <Panel name="leads" queryFn={leads} />
          </HealthProvider>
        </QueryClientProvider>,
      );
    });
    await advance(0);
    warehouseUp = true;

    await advance(3000); // first "up" probe only starts the debounce window
    expect(leads).toHaveBeenCalledTimes(1);
    await advance(6000); // window elapsed: the banner clears and panels reload
    await advance(50);
    expect(leads).toHaveBeenCalledTimes(2);
    expect(panelText('leads')).toBe('leads loaded');
  });

  it('re-fires a warehouse 503 when a resume finishes (resuming → up), with no debounce', async () => {
    let warehouse: 'resuming' | 'up' = 'resuming';
    const fetchHealth = vi.fn(async (): Promise<HealthPayload> => ({
      status: 'ok',
      mode: 'live',
      dependencies: { warehouse, lakebase: 'up', genie: 'up' },
    }));
    const leads = vi.fn(async () => {
      if (warehouse !== 'up') throw dependencyDown('warehouse');
      return 'leads loaded';
    });

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <HealthProvider pollIntervalDegradedMs={3000} debounceUpMs={5000} fetchHealth={fetchHealth}>
            <Panel name="leads" queryFn={leads} />
          </HealthProvider>
        </QueryClientProvider>,
      );
    });
    await advance(0);
    expect(panelText('leads')).toBe('error');

    warehouse = 'up';
    await advance(3000); // resuming polls at the fast cadence
    await advance(50);

    expect(leads).toHaveBeenCalledTimes(2);
    expect(panelText('leads')).toBe('leads loaded');
  });

  it('still polls and renders without a QueryClientProvider (isolated mounts)', async () => {
    const fetchHealth = vi.fn(async () => health('up'));
    await act(async () => {
      root.render(
        <HealthProvider fetchHealth={fetchHealth}>
          <span data-panel="bare">ready</span>
        </HealthProvider>,
      );
    });
    await advance(0);
    expect(panelText('bare')).toBe('ready');
    expect(fetchHealth).toHaveBeenCalledTimes(1);
  });
});
