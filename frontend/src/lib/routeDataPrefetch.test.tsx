// @vitest-environment happy-dom

import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider, type QueryKey } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

/**
 * Route data prefetch (audit delivery-03), proven against the routes it
 * prefetches for:
 *   - parity: every key the prefetch fills is a key the mounted route
 *     registers, read through the same api method with deep-equal arguments;
 *   - join: a prefetch in flight that meets a retryable 503 leaves the mounted
 *     route the same warming loop as a mount without a prefetch (the prefetch
 *     must never pass `retry`);
 *   - audit allowlist: no path ever prefetches anything but the five
 *     non-audited aggregate reads.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

interface ApiCall {
  method: string;
  args: unknown[];
}

const recorder = vi.hoisted(() => ({
  calls: [] as ApiCall[],
  answer: null as null | ((method: string, args: unknown[]) => Promise<unknown> | undefined),
}));

vi.mock('./api', async (importOriginal) => {
  const real = await importOriginal<typeof import('./api')>();
  const api = new Proxy({} as typeof real.api, {
    get: (_target, method) => (...args: unknown[]) => {
      recorder.calls.push({ method: String(method), args });
      return recorder.answer?.(String(method), args) ?? new Promise(() => undefined);
    },
  });
  return { ...real, api };
});

vi.mock('./routePreloaders', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./routePreloaders')>()),
  preloadRouteForPath: vi.fn(),
}));

import { ApiError } from './api';
import { createMipQueryClient } from './queryClient';
import { queryKeys } from './queryKeys';
import { preloadRouteForPath } from './routePreloaders';
import { ROUTE_IDS, ROUTES } from './routeMeta';
import { HOME_CONTACTABLE_PREVIEW_CRITERIA, HOME_PORTFOLIO_PREVIEW_CRITERIA } from './homeQueries';
import { prefetchRouteData } from './routeDataPrefetch';
import { useWarmingUpRetry } from './useWarmingUpRetry';
import { AppProvider } from '../components/AppContext';
import { FootprintProvider } from '../components/FootprintProvider';
import { RouteNav } from '../components/layout/RouteNav';
import Home, { HOME_PORTFOLIO_PREVIEW_CRITERIA as HOME_ROUTE_CRITERIA, requestHomePortfolioPreview } from '../routes/home';
import { HOME_CONTACTABLE_PREVIEW_CRITERIA as HOME_BANNER_CRITERIA } from '../routes/home.approval-banner';
import AnalyticsRoute from '../routes/analytics';

/** The five non-audited aggregate reads a prefetch may ever make. */
const ALLOWED_READS = ['analyticsExecutive', 'analyticsRateWindow', 'homeSummary', 'portfolioPreview', 'stateRollups'];

const SIGNAL = '<signal>';

/** An api call's arguments with the AbortSignal replaced by a marker. */
function withoutSignal(args: unknown[]): unknown[] {
  return args.map((arg) => (arg instanceof AbortSignal ? SIGNAL : arg));
}

/** Run a cached query's own queryFn once and return the api call it makes. */
function callOf(client: QueryClient, queryKey: QueryKey): ApiCall {
  const query = client.getQueryCache().find({ queryKey, exact: true });
  const queryFn = query?.options.queryFn;
  if (typeof queryFn !== 'function') throw new Error(`no queryFn cached for ${JSON.stringify(queryKey)}`);
  const before = recorder.calls.length;
  void (queryFn as (context: unknown) => unknown)({
    queryKey,
    signal: new AbortController().signal,
    meta: undefined,
    client,
  });
  const made = recorder.calls.slice(before);
  expect(made, `${JSON.stringify(queryKey)} makes one api call`).toHaveLength(1);
  return { method: made[0].method, args: withoutSignal(made[0].args) };
}

const keysOf = (client: QueryClient) => client.getQueryCache().getAll().map((query) => query.queryHash);

describe('route data prefetch', () => {
  let container: HTMLDivElement;
  let root: Root;
  let clients: QueryClient[];

  beforeEach(() => {
    recorder.calls = [];
    recorder.answer = null;
    clients = [];
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => undefined)));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    for (const client of clients) client.clear();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.mocked(preloadRouteForPath).mockClear();
  });

  function client(): QueryClient {
    const created = createMipQueryClient();
    clients.push(created);
    return created;
  }

  async function mount(queryClient: QueryClient, path: string, node: ReactNode): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[path]}>
            <AppProvider>
              <FootprintProvider>{node}</FootprintProvider>
            </AppProvider>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  describe('parity with the mounted route', () => {
    it("Home's criteria are the route's own exports", () => {
      expect(HOME_PORTFOLIO_PREVIEW_CRITERIA).toEqual(HOME_ROUTE_CRITERIA);
      expect(HOME_CONTACTABLE_PREVIEW_CRITERIA).toEqual(HOME_BANNER_CRITERIA);
    });

    it.each([
      ['/', '', () => <Home />],
      ['/analytics', '', () => <AnalyticsRoute />],
    ])('%s: every prefetched key is one the route registers, read the same way', async (path, search, render) => {
      const prefetched = client();
      prefetchRouteData(prefetched, path, search);
      const prefetchedKeys = prefetched.getQueryCache().getAll().map((query) => query.queryKey);
      expect(prefetchedKeys.length).toBeGreaterThan(0);

      const mounted = client();
      await mount(mounted, path, render());
      const routeKeys = new Set(keysOf(mounted));

      for (const queryKey of prefetchedKeys) {
        const hash = prefetched.getQueryCache().find({ queryKey, exact: true })?.queryHash as string;
        expect(routeKeys.has(hash), `the route registers ${hash}`).toBe(true);
        expect(callOf(prefetched, queryKey), `${hash} is read the same way`).toEqual(callOf(mounted, queryKey));
      }
    });

    it("prefetches Home's three hero reads and its national state rollups", () => {
      const prefetched = client();
      prefetchRouteData(prefetched, '/', '');
      expect(prefetched.getQueryCache().getAll().map((query) => query.queryKey).sort()).toEqual([
        queryKeys.homeSummary(),
        [...queryKeys.all, 'geo', 'state-rollups', '', 'any', '{}'],
        queryKeys.homePreview(),
        queryKeys.portfolioPreview(['home', 'contactable']),
      ].sort());
      expect(preloadRouteForPath).toHaveBeenCalledWith('/');
    });
  });

  describe('join: a prefetch in flight shares the mounted warming loop', () => {
    const warming = () => new ApiError('Warehouse warming up', {
      path: '/api/v1/portfolio/preview',
      status: 503,
      retryable: true,
      dependency: 'warehouse',
      reason: 'warming_up',
    });

    function PreviewProbe() {
      const { warmingUp, error } = useWarmingUpRetry(requestHomePortfolioPreview, { queryKey: queryKeys.homePreview() });
      return (
        <output data-probe="">
          {JSON.stringify({ attempt: warmingUp?.attempt ?? null, maxAttempts: warmingUp?.maxAttempts ?? null, error: error !== null })}
        </output>
      );
    }

    /** Mount the Home preview probe, fail its in-flight read with a 503, then let one retry interval pass. */
    async function warmingStates(withPrefetch: boolean): Promise<string[]> {
      vi.useFakeTimers();
      const rejects: Array<(err: unknown) => void> = [];
      recorder.answer = (method) => (method === 'portfolioPreview'
        ? new Promise((_resolve, reject) => {
          rejects.push(reject);
        })
        : undefined);
      const queryClient = client();
      if (withPrefetch) prefetchRouteData(queryClient, '/', '');
      await act(async () => {
        root.render(
          <QueryClientProvider client={queryClient}>
            <PreviewProbe />
          </QueryClientProvider>,
        );
      });
      const probe = () => container.querySelector('[data-probe]')?.textContent ?? '';
      const previewReads = () => recorder.calls.filter((call) => call.method === 'portfolioPreview'
        && (call.args[0] as { marketing_eligibility?: string }).marketing_eligibility === 'Any').length;
      expect(previewReads(), 'the mount joined the one in-flight read').toBe(1);
      const states: string[] = [];
      await act(async () => {
        rejects[0](warming());
        await vi.advanceTimersByTimeAsync(0);
      });
      states.push(probe());
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5_000);
      });
      await act(async () => {
        rejects[rejects.length - 1](warming());
        await vi.advanceTimersByTimeAsync(0);
      });
      states.push(probe());
      states.push(`reads=${previewReads()}`);
      return states;
    }

    it('shows the same attempt and maxAttempts as a mount without a prefetch', async () => {
      const withoutPrefetch = await warmingStates(false);
      await act(async () => root.unmount());
      root = createRoot(container);
      recorder.calls = [];
      const withPrefetch = await warmingStates(true);

      expect(withoutPrefetch).toEqual([
        JSON.stringify({ attempt: 2, maxAttempts: 6, error: false }),
        JSON.stringify({ attempt: 3, maxAttempts: 6, error: false }),
        'reads=2',
      ]);
      expect(withPrefetch).toEqual(withoutPrefetch);
    });
  });

  describe('audit allowlist', () => {
    const concretePaths = ROUTE_IDS.map((id) => ROUTES[id].pattern.replace(/:[A-Za-z]+/g, 'B-0OXOBYLW8MNCK'));

    it.each(concretePaths.flatMap((path) => [[path, ''], [path, '?view=geography']]))(
      'prefetchRouteData(%s, %j) reads only non-audited aggregates',
      (path, search) => {
        prefetchRouteData(client(), path, search);
        const methods = [...new Set(recorder.calls.map((call) => call.method))];
        expect(methods.filter((method) => !ALLOWED_READS.includes(method))).toEqual([]);
      },
    );

    /** Hover then focus each RouteNav link; returns the api methods each intent read. */
    async function navIntents(): Promise<Record<string, string[]>> {
      const queryClient = client();
      await mount(queryClient, '/glossary', <RouteNav />);
      const links = [...container.querySelectorAll<HTMLAnchorElement>('a.route-nav__link')];
      expect(links.length).toBeGreaterThan(5);
      const reads: Record<string, string[]> = {};
      for (const link of links) {
        recorder.calls = [];
        await act(async () => {
          link.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, relatedTarget: document.body }));
          link.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
        });
        reads[link.getAttribute('href') ?? ''] = [...new Set(recorder.calls.map((call) => call.method))].sort();
      }
      return reads;
    }

    it('RouteNav hover and focus read only the Analytics aggregates, and nothing for Home, the queue or a dossier', async () => {
      const reads = await navIntents();

      expect(Object.values(reads).flat().filter((method) => !ALLOWED_READS.includes(method))).toEqual([]);
      expect(reads['/analytics']).toEqual(['analyticsExecutive', 'analyticsRateWindow']);
      for (const [href, methods] of Object.entries(reads)) {
        if (href !== '/analytics') expect(methods, `${href} hover reads nothing`).toEqual([]);
      }
      expect(preloadRouteForPath).toHaveBeenCalledWith('/lead-queue');
      expect(preloadRouteForPath).toHaveBeenCalledWith('/analytics');
    });

    it('RouteNav skips the Analytics data prefetch under saveData, but still preloads the chunk', async () => {
      Object.defineProperty(navigator, 'connection', { configurable: true, value: { saveData: true } });
      try {
        const reads = await navIntents();
        expect(reads['/analytics']).toEqual([]);
        expect(preloadRouteForPath).toHaveBeenCalledWith('/analytics');
      } finally {
        Reflect.deleteProperty(navigator, 'connection');
      }
    });

    it('prefetches data only for / and an unfiltered /analytics', () => {
      for (const path of concretePaths) {
        recorder.calls = [];
        prefetchRouteData(client(), path, '');
        const expectData = path === '/' || path === '/analytics';
        expect(recorder.calls.length > 0, path).toBe(expectData);
      }
      recorder.calls = [];
      prefetchRouteData(client(), '/analytics', '?states=TX');
      expect(recorder.calls, 'a filtered /analytics prefetches nothing').toEqual([]);
    });
  });
});
