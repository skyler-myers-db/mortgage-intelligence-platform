/**
 * @vitest-environment happy-dom
 */

import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useNavigate, type NavigateFunction } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './app';

/**
 * Route-level crash containment, asserted on the rendered DOM of the real
 * `App` route table (2026-09-21 audit: stack-01 / shell-01 / states-01 /
 * quality-01 / bundle-01). Before the boundary existed, either failure below
 * unmounted the React root: `#root` was left with zero children.
 *
 * The shell and the route modules are stubbed so the test needs no providers
 * or network; the route table, the Suspense and the ErrorBoundary are real.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('./components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <div data-testid="app-shell">{children}</div>
  ),
}));

vi.mock('./components/layout/RouteNav', async () => {
  const { Link } = await import('react-router');
  return {
    RouteNav: () => (
      <nav data-testid="route-nav">
        <Link to="/glossary">Glossary</Link>
        <Link to="/">Home</Link>
      </nav>
    ),
  };
});

/** Route chunks a test resolves or rejects by hand (the route hold, shell-05). */
const chunks = vi.hoisted(() => {
  function gate() {
    let resolve: () => void = () => undefined;
    let reject: (error: unknown) => void = () => undefined;
    const promise = new Promise<void>((res, rej) => {
      resolve = res;
      reject = rej;
    });
    return { promise, resolve, reject };
  }
  return { portfolio: gate(), segments: gate(), genie: gate() };
});

vi.mock('./lib/routePreloaders', async () => {
  const { lazy } = await import('react');
  const Ok = (label: string) => () => <div data-testid="route-ok">{label}</div>;
  const held = (chunk: { promise: Promise<void> }, label: string) =>
    lazy(() => chunk.promise.then(() => ({ default: Ok(label) })));
  const Throwing = () => {
    throw new Error('Cannot read score of borrower B-0TESTBORROWER');
  };
  const StaleChunk = lazy(() =>
    Promise.reject(
      new TypeError('Failed to fetch dynamically imported module: /assets/analytics-0ld5ta1e.js'),
    ),
  );
  return {
    HomeRoute: Ok('home'),
    AnalyticsRoute: StaleChunk,
    LeadQueueRoute: Throwing,
    GlossaryRoute: Ok('glossary'),
    AssetRoute: Ok('asset'),
    PortfolioBuilderRoute: held(chunks.portfolio, 'portfolio'),
    SegmentIntelligenceRoute: held(chunks.segments, 'segments'),
    Borrower360Route: Ok('borrower'),
    NotFoundRoute: Ok('not-found'),
    OfferOrchestratorRoute: Ok('offer'),
    AskGenieRoute: held(chunks.genie, 'genie'),
    AdminConfigRoute: Ok('admin'),
    preloadLikelyNextRoutes: () => () => undefined,
  };
});

describe('App route error boundary', () => {
  let root: Root;
  let container: HTMLElement;

  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  let navigate: NavigateFunction = () => undefined;
  function NavigateProbe() {
    const routerNavigate = useNavigate();
    useEffect(() => {
      navigate = routerNavigate;
    }, [routerNavigate]);
    return null;
  }

  async function renderAt(path: string) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter initialEntries={[path]}>
            <NavigateProbe />
            <App />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
  }

  const surface = () => container.querySelector('[data-testid="error-surface"]');
  const buttonLabels = () =>
    Array.from(surface()?.querySelectorAll('button') ?? []).map((b) => b.textContent);

  it('contains a throw inside a route: the shell and nav stay mounted', async () => {
    await renderAt('/lead-queue');

    expect(container.childElementCount).toBeGreaterThan(0);
    expect(container.querySelector('[data-testid="app-shell"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="route-nav"]')).not.toBeNull();
    expect(surface()?.getAttribute('data-error-boundary')).toBe('route');
    expect(surface()?.getAttribute('data-error-kind')).toBe('render');
    expect(surface()?.textContent).toContain('Lead Queue hit an unexpected error');
    expect(buttonLabels()).toEqual(['Try again', 'Reload']);
    expect(container.innerHTML).not.toContain('B-0TESTBORROWER');
  });

  it('contains a stale lazy chunk and offers Reload only', async () => {
    await renderAt('/analytics');

    expect(container.querySelector('[data-testid="app-shell"]')).not.toBeNull();
    expect(surface()?.getAttribute('data-error-kind')).toBe('chunk');
    expect(surface()?.textContent).toContain('A new version is available');
    expect(buttonLabels()).toEqual(['Reload']);
    expect(container.innerHTML).not.toContain('analytics-0ld5ta1e');
  });

  it('recovers by navigating away from the broken route', async () => {
    await renderAt('/lead-queue');
    expect(surface()).not.toBeNull();

    await act(async () => {
      Array.from(container.querySelectorAll<HTMLAnchorElement>('[data-testid="route-nav"] a'))
        .find((a) => a.textContent === 'Glossary')
        ?.click();
    });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    expect(surface()).toBeNull();
    expect(container.querySelector('[data-testid="route-ok"]')?.textContent).toBe('glossary');
  });

  describe('route hold (shell-05)', () => {
    const fallbacks = () => container.querySelectorAll('[data-route-fallback]');

    /** Counts every [data-route-fallback] insertion from now on, however briefly it mounts. */
    function countFallbackMounts(): () => number {
      let mounts = 0;
      const count = (records: MutationRecord[]) => {
        for (const record of records) {
          for (const node of record.addedNodes) {
            if (!(node instanceof Element)) continue;
            if (node.matches('[data-route-fallback]')) mounts += 1;
            mounts += node.querySelectorAll('[data-route-fallback]').length;
          }
        }
      };
      const observer = new MutationObserver(count);
      observer.observe(container, { childList: true, subtree: true });
      return () => {
        count(observer.takeRecords());
        return mounts;
      };
    }

    async function flushChunk(): Promise<void> {
      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 0));
      });
    }

    it('keeps the painted route while the next route\'s chunk loads; no fallback mounts until it resolves', async () => {
      await renderAt('/');
      expect(container.querySelector('[data-testid="route-ok"]')?.textContent).toBe('home');
      const mounts = countFallbackMounts();

      await act(async () => {
        void navigate('/portfolio-builder');
      });
      await flushChunk();
      // Held: the previous route is still the painted DOM, and nothing fell back.
      expect(container.querySelector('[data-testid="route-ok"]')?.textContent).toBe('home');
      expect(fallbacks()).toHaveLength(0);
      expect(mounts()).toBe(0);

      await act(async () => {
        chunks.portfolio.resolve();
        await chunks.portfolio.promise;
      });
      await flushChunk();
      expect(container.querySelector('[data-testid="route-ok"]')?.textContent).toBe('portfolio');
      expect(container.querySelector('.route-transition > [data-testid="route-ok"]')).not.toBeNull();
      expect(mounts()).toBe(0);
    });

    it('shows the page-shaped fallback on the first render of a route whose chunk is pending', async () => {
      await renderAt('/ask-genie');
      expect(container.querySelector('.route-transition > [data-route-fallback]')).not.toBeNull();
      expect(container.querySelector('[data-testid="route-ok"]')).toBeNull();

      await act(async () => {
        chunks.genie.resolve();
        await chunks.genie.promise;
      });
      await flushChunk();
      expect(fallbacks()).toHaveLength(0);
      expect(container.querySelector('[data-testid="route-ok"]')?.textContent).toBe('genie');
    });

    it('a chunk that rejects during a held navigation reaches the route boundary, which offers Reload', async () => {
      await renderAt('/');
      await act(async () => {
        void navigate('/segment-intelligence');
      });
      await flushChunk();
      expect(container.querySelector('[data-testid="route-ok"]')?.textContent).toBe('home');

      await act(async () => {
        chunks.segments.reject(
          new TypeError('Failed to fetch dynamically imported module: /assets/segment-intelligence-0ld.js'),
        );
        await chunks.segments.promise.catch(() => undefined);
      });
      await flushChunk();
      expect(surface()?.getAttribute('data-error-boundary')).toBe('route');
      expect(surface()?.getAttribute('data-error-kind')).toBe('chunk');
      expect(buttonLabels()).toEqual(['Reload']);
      expect(container.innerHTML).not.toContain('segment-intelligence-0ld');
    });
  });
});
