/**
 * @vitest-environment happy-dom
 */

import { act, useEffect, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useNavigate, type NavigateFunction } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './app';

/**
 * The route View Transition boundary (2026-09-21 audit stack-04 / motion-03 /
 * runtime-10 / css-10 / shell-10, phase 1), asserted on the real `App` route
 * table with React's `ViewTransition` replaced by a recorder. The browser
 * behaviour (one document.startViewTransition per route change, none for a
 * ?query change or under reduced motion) is proven in
 * tests/e2e/fixture/motion-nav.fixture.spec.ts; this pins the props and the
 * keying that produce it, and the harness contract on the painted wrapper.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

interface Recorded {
  instance: number;
  props: Record<string, unknown>;
}

const recorder = vi.hoisted(() => ({ renders: [] as Recorded[], mounts: 0, nextInstance: 0 }));

vi.mock('react', async (importOriginal) => {
  const actual = await importOriginal<typeof import('react')>();
  function RecordingViewTransition({ children, ...props }: { children?: ReactNode } & Record<string, unknown>) {
    const [instance] = actual.useState(() => {
      recorder.nextInstance += 1;
      return recorder.nextInstance;
    });
    recorder.renders.push({ instance, props });
    actual.useEffect(() => {
      recorder.mounts += 1;
    }, []);
    return actual.createElement(actual.Fragment, null, children);
  }
  return { ...actual, ViewTransition: RecordingViewTransition };
});

vi.mock('./components/layout/AppShell', () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <main id="main-content" data-testid="app-shell">{children}</main>
  ),
}));

vi.mock('./components/layout/RouteNav', () => ({ RouteNav: () => <nav data-testid="route-nav" /> }));

const chunks = vi.hoisted(() => {
  let resolve: () => void = () => undefined;
  const promise = new Promise<void>((res) => {
    resolve = res;
  });
  return { genie: { promise, resolve } };
});

vi.mock('./lib/routePreloaders', async () => {
  const { lazy } = await import('react');
  const Ok = (label: string) => () => <div data-testid="route-ok">{label}</div>;
  return {
    HomeRoute: Ok('home'),
    AnalyticsRoute: Ok('analytics'),
    LeadQueueRoute: Ok('lead-queue'),
    GlossaryRoute: Ok('glossary'),
    AssetRoute: Ok('asset'),
    PortfolioBuilderRoute: Ok('portfolio'),
    SegmentIntelligenceRoute: Ok('segments'),
    Borrower360Route: Ok('borrower'),
    NotFoundRoute: Ok('not-found'),
    OfferOrchestratorRoute: Ok('offer'),
    AskGenieRoute: lazy(() => chunks.genie.promise.then(() => ({ default: Ok('genie') }))),
    AdminConfigRoute: Ok('admin'),
    preloadLikelyNextRoutes: () => () => undefined,
  };
});

function stubReducedMotion(reduce: boolean): void {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      media: query,
      matches: reduce && query.includes('prefers-reduced-motion: reduce'),
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
    }),
  });
}

describe('App route View Transition boundary', () => {
  let root: Root;
  let container: HTMLElement;
  let navigate: NavigateFunction = () => undefined;
  const originalMatchMedia = window.matchMedia;

  function NavigateProbe() {
    const routerNavigate = useNavigate();
    useEffect(() => {
      navigate = routerNavigate;
    }, [routerNavigate]);
    return null;
  }

  beforeEach(() => {
    recorder.renders = [];
    recorder.mounts = 0;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: originalMatchMedia });
  });

  async function flush(): Promise<void> {
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
  }

  async function renderAt(path: string): Promise<void> {
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
    await flush();
  }

  async function go(to: string): Promise<void> {
    await act(async () => {
      void navigate(to);
    });
    await flush();
  }

  const markers = () => Array.from(container.querySelectorAll('#main-content .route-transition[data-route-path]'));
  const lastProps = () => recorder.renders[recorder.renders.length - 1]?.props;
  const instances = () => new Set(recorder.renders.map((render) => render.instance));

  it('names the route enter / exit classes and turns update and default off when motion is allowed', async () => {
    stubReducedMotion(false);
    await renderAt('/');
    expect(lastProps()).toEqual({
      enter: 'mip-route-enter',
      exit: 'mip-route-exit',
      update: 'none',
      default: 'none',
    });
  });

  it('renders no ViewTransition at all under reduced motion, so React never starts one', async () => {
    stubReducedMotion(true);
    await renderAt('/');
    expect(recorder.renders).toEqual([]);
    expect(markers().map((node) => node.getAttribute('data-route-path'))).toEqual(['/']);
    await go('/glossary');
    expect(recorder.renders).toEqual([]);
    expect(container.querySelector('[data-testid="route-ok"]')?.textContent).toBe('glossary');
  });

  it('re-keys the boundary on a pathname change but not on a search change', async () => {
    stubReducedMotion(false);
    await renderAt('/lead-queue');
    expect(instances().size).toBe(1);
    expect(recorder.mounts).toBe(1);

    await go('/lead-queue?state=IL');
    expect(instances().size, 'a ?query change keeps the same boundary').toBe(1);
    expect(recorder.mounts).toBe(1);

    await go('/glossary');
    expect(instances().size, 'a new pathname mounts a new boundary').toBe(2);
    expect(recorder.mounts).toBe(2);

    await go('/glossary#terms');
    expect(recorder.mounts).toBe(2);
  });

  it('keeps exactly one painted marker, with the route content as its direct child', async () => {
    stubReducedMotion(false);
    await renderAt('/');
    await go('/analytics');
    expect(markers()).toHaveLength(1);
    expect(markers()[0].getAttribute('data-route-path')).toBe('/analytics');
    expect(container.querySelector('.route-transition[data-route-path] > [data-testid="route-ok"]')?.textContent).toBe('analytics');
  });

  it('wraps the first-render fallback in the --fallback modifier with no route marker', async () => {
    stubReducedMotion(false);
    await renderAt('/ask-genie');
    const fallback = container.querySelector('.route-transition > [data-route-fallback]')?.parentElement;
    expect(fallback?.className).toBe('route-transition route-transition--fallback');
    expect(fallback?.hasAttribute('data-route-path')).toBe(false);
    expect(markers()).toEqual([]);

    await act(async () => {
      chunks.genie.resolve();
      await chunks.genie.promise;
    });
    await flush();
    expect(container.querySelector('.route-transition--fallback')).toBeNull();
    expect(markers().map((node) => node.getAttribute('data-route-path'))).toEqual(['/ask-genie']);
  });
});
