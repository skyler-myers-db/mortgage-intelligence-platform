/**
 * @vitest-environment happy-dom
 */

import { act, useEffect, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, useLocation, useNavigate, type Location, type NavigateFunction } from 'react-router';
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

const recorder = vi.hoisted(() => ({ renders: [] as Recorded[], mounts: 0, nextInstance: 0, real: false }));

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
    // `real`: React's own ViewTransition underneath, to count startViewTransition calls.
    return recorder.real
      ? actual.createElement(actual.ViewTransition, props, children)
      : actual.createElement(actual.Fragment, null, children);
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
    // The keep-alive slot's loader preloads the route chunk beside it.
    LeadQueueRoute: Object.assign(Ok('lead-queue'), { preload: () => Promise.resolve() }),
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

type MediaListener = (event: { matches: boolean }) => void;

/** A matchMedia whose reduced-motion answer can flip later, notifying its listeners. */
function stubReducedMotion(reduce: boolean): { flip: (next: boolean) => void } {
  let current = reduce;
  const listeners = new Set<MediaListener>();
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      media: query,
      get matches() {
        return current && query.includes('prefers-reduced-motion: reduce');
      },
      addEventListener: (_type: string, listener: MediaListener) => listeners.add(listener),
      removeEventListener: (_type: string, listener: MediaListener) => listeners.delete(listener),
      addListener: (listener: MediaListener) => listeners.add(listener),
      removeListener: (listener: MediaListener) => listeners.delete(listener),
    }),
  });
  return {
    flip: (next) => {
      current = next;
      for (const listener of [...listeners]) listener({ matches: next });
    },
  };
}

describe('App route View Transition boundary', () => {
  let root: Root;
  let container: HTMLElement;
  let navigate: NavigateFunction = () => undefined;
  let current: Location | null = null;
  const originalMatchMedia = window.matchMedia;

  function NavigateProbe() {
    const routerNavigate = useNavigate();
    const location = useLocation();
    useEffect(() => {
      navigate = routerNavigate;
      current = location;
    }, [routerNavigate, location]);
    return null;
  }

  beforeEach(() => {
    recorder.renders = [];
    recorder.mounts = 0;
    recorder.real = false;
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
    // The Lead Queue keep-alive slot is a lazy module (routes/lead-queue.keepAlive).
    await act(async () => {
      await vi.dynamicImportSettled();
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

  async function go(to: string | number): Promise<void> {
    await act(async () => {
      void (typeof to === 'number' ? navigate(to) : navigate(to));
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

  it('keeps the painted route mounted when the OS reduced-motion setting flips mid-session', async () => {
    const media = stubReducedMotion(false);
    await renderAt('/');
    const painted = container.querySelector('[data-testid="route-ok"]');
    expect(painted?.textContent).toBe('home');
    const instancesBefore = instances().size;

    // The OS switches to reduce (a change event, as a browser sends): the
    // element type around the route must not change, or the route remounts
    // and loses its state (a decision receipt vanished this way).
    await act(async () => media.flip(true));
    await flush();
    expect(container.querySelector('[data-testid="route-ok"]'), 'the same route node, not a remount').toBe(painted);
    expect(instances().size).toBe(instancesBefore);
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

  it('keeps ONE boundary and the same route node across Genie conversation links (audit shell-03)', async () => {
    stubReducedMotion(false);
    // Runs after the fallback test above, which needs the chunk unresolved.
    await act(async () => {
      chunks.genie.resolve();
      await chunks.genie.promise;
    });
    await renderAt('/ask-genie');
    const painted = container.querySelector('[data-testid="route-ok"]');
    expect(painted?.textContent).toBe('genie');
    const mounted = recorder.mounts;
    const boundaries = instances().size;

    for (const path of [
      '/ask-genie/0123456789abcdef0123456789abcdef',
      '/ask-genie/01234567-89ab-cdef-0123-456789abcdef',
      '/ask-genie/not-an-id',
      '/ask-genie',
    ]) {
      await go(path);
      expect(recorder.mounts, `${path} mounts no new boundary`).toBe(mounted);
      expect(instances().size, `${path} keeps the boundary`).toBe(boundaries);
      expect(container.querySelector('[data-testid="route-ok"]'), `${path} keeps the route mounted`).toBe(painted);
      // The harness contract: the marker names the raw pathname.
      expect(markers().map((node) => node.getAttribute('data-route-path'))).toEqual([path]);
    }

    await go('/glossary');
    expect(recorder.mounts, 'leaving the page still re-keys').toBe(mounted + 1);
  });

  describe('the Lead Queue keep-alive slot (W5c runtime-08)', () => {
    const DOSSIER = '/borrower-360/B-0TESTBORROWER0';
    const queueNode = () => Array.from(container.querySelectorAll('[data-testid="route-ok"]'))
      .find((node) => node.textContent === 'lead-queue') ?? null;
    const paths = () => markers().map((node) => node.getAttribute('data-route-path'));
    const hidden = (node: Element | null) =>
      (node?.closest('.route-transition') as HTMLElement | null)?.style.display === 'none';

    it('hides the queue on a dossier and reveals the same node on Back, with one marker throughout', async () => {
      stubReducedMotion(false);
      await renderAt('/lead-queue?state=IL&row=B-0TESTBORROWER0');
      const queue = queueNode();
      expect(queue).not.toBeNull();
      expect(paths()).toEqual(['/lead-queue']);

      await go(DOSSIER);
      expect(queueNode(), 'hidden under Activity, not unmounted').toBe(queue);
      expect(hidden(queue)).toBe(true);
      expect(queue?.closest('.route-transition')?.hasAttribute('data-route-path'), 'the hidden slot names no route').toBe(false);
      expect(paths()).toEqual([DOSSIER]);

      await go(-1);
      expect(queueNode()).toBe(queue);
      expect(hidden(queue)).toBe(false);
      expect(paths()).toEqual(['/lead-queue']);
      expect(current?.search).toBe('?state=IL&row=B-0TESTBORROWER0');
    });

    it('reveals the kept queue for the bare Leads link and puts its filters back in the URL', async () => {
      stubReducedMotion(false);
      await renderAt('/lead-queue?state=IL');
      const queue = queueNode();
      await go(DOSSIER);
      await go('/lead-queue');
      expect(queueNode()).toBe(queue);
      expect(hidden(queue)).toBe(false);
      expect(current?.pathname).toBe('/lead-queue');
      expect(current?.search, 'the bare link is replaced with the kept view').toBe('?state=IL');
      expect(paths()).toEqual(['/lead-queue']);
    });

    it('a return differing only in ?row= is the kept queue; other filters or another page are not', async () => {
      stubReducedMotion(false);
      await renderAt('/lead-queue?state=IL&row=B-0TESTBORROWER0');
      const queue = queueNode();
      await go(DOSSIER);
      await go('/lead-queue?state=IL');
      expect(queueNode(), 'row is not a filter').toBe(queue);

      await go(DOSSIER);
      await go('/lead-queue?state=TX');
      const fresh = queueNode();
      expect(fresh, 'other filters start a new queue').not.toBe(queue);
      expect(fresh).not.toBeNull();

      await go('/glossary');
      expect(queueNode(), 'any other destination unmounts the slot').toBeNull();
      expect(paths()).toEqual(['/glossary']);
    });

    it('starts one View Transition per navigation into, out of and back to the slot', async () => {
      stubReducedMotion(false);
      recorder.real = true;
      const original = (document as unknown as { startViewTransition?: unknown }).startViewTransition;
      const calls: string[] = [];
      // happy-dom has no FontFaceSet or getAnimations; React reads both around the update.
      Object.defineProperty(document, 'fonts', {
        configurable: true,
        value: { status: 'loaded', ready: Promise.resolve() },
      });
      Object.defineProperty(document.documentElement, 'getAnimations', { configurable: true, value: () => [] });
      Object.defineProperty(document, 'startViewTransition', {
        configurable: true,
        writable: true,
        value: (arg: { update: () => void } | (() => void)) => {
          calls.push(current?.pathname ?? '');
          const update = typeof arg === 'function' ? arg : arg.update;
          const done = Promise.resolve().then(update);
          return { ready: done, finished: done, updateCallbackDone: done, skipTransition: () => undefined };
        },
      });
      try {
        await renderAt('/glossary');
        const cold = calls.length;
        await go('/lead-queue?state=IL');
        expect(calls.length - cold, 'glossary -> queue').toBe(1);
        const queue = queueNode();
        expect(paths()).toEqual(['/lead-queue']);
        await go('/lead-queue?state=TX');
        expect(calls.length - cold, 'a filter change animates nothing').toBe(1);
        await go(DOSSIER);
        expect(calls.length - cold, 'queue -> dossier (the queue hides)').toBe(2);
        expect(hidden(queue)).toBe(true);
        expect(paths()).toEqual([DOSSIER]);
        await go('/lead-queue');
        expect(calls.length - cold, 'dossier -> kept queue (a reveal), its URL replace adds none').toBe(3);
        expect(current?.search).toBe('?state=TX');
        expect(queueNode()).toBe(queue);
        expect(hidden(queue)).toBe(false);
        expect(paths()).toEqual(['/lead-queue']);
      } finally {
        Object.defineProperty(document, 'startViewTransition', { configurable: true, writable: true, value: original });
        Reflect.deleteProperty(document, 'fonts');
        Reflect.deleteProperty(document.documentElement, 'getAnimations');
      }
    });
  });
});
