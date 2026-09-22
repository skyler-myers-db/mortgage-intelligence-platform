/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
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

vi.mock('./lib/routePreloaders', async () => {
  const { lazy } = await import('react');
  const Ok = (label: string) => () => <div data-testid="route-ok">{label}</div>;
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
    PortfolioBuilderRoute: Ok('portfolio'),
    SegmentIntelligenceRoute: Ok('segments'),
    Borrower360Route: Ok('borrower'),
    NotFoundRoute: Ok('not-found'),
    OfferOrchestratorRoute: Ok('offer'),
    AskGenieRoute: Ok('genie'),
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

  async function renderAt(path: string) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter initialEntries={[path]}>
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
});
