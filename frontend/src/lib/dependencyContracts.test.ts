/**
 * @vitest-environment happy-dom
 *
 * Dependency contracts: the handful of third-party exports the app reaches
 * into beyond a package's documented API, pinned so a dependency bump that
 * moves them fails here, by name, instead of in a rendered route.
 *
 * react-router (wave-1c follow-up #18). Three readers reach into
 * `UNSAFE_DataRouterContext`. UnsavedChangesGuard reads its presence to tell
 * a data router (main.tsx's createBrowserRouter, where `useBlocker` works)
 * from a declarative one (every route unit test's MemoryRouter, where
 * `useBlocker` throws). hooks/useRoutePending reads its `router` and
 * subscribes to `router.state.location`. hooks/useLiveSearchParams reads
 * `router.state.location` at event time, and relies on a BEHAVIOUR as well
 * as the export: with no loader on the route (appRouter.tsx), navigate()
 * moves `router.state.location` before it returns, while React commits the
 * new location later, in a transition. The export is `UNSAFE_` because it is
 * an integration point, not an app API, so react-router may rename or drop
 * it, or change that timing, in a minor. This file pins all of it: it is a
 * React context object, it is null under a declarative router and set under
 * a data router, and its router's location moves inside navigate() ahead of
 * the committed render. It also pins the data-router entry points the shell
 * mounts (createBrowserRouter, useBlocker, and RouterProvider from
 * react-router/dom).
 *
 * The upgrade checklist in docs/audits/supply-chain-audit.md
 * ("Dependency upgrade checklist") points here.
 */
import { act, createElement, useContext, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import {
  MemoryRouter,
  UNSAFE_DataRouterContext,
  createBrowserRouter,
  createMemoryRouter,
  useBlocker,
  useLocation,
} from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { afterEach, describe, expect, it } from 'vitest';

const REACT_CONTEXT = Symbol.for('react.context');

let root: Root | null = null;
let host: HTMLDivElement | null = null;

function render(tree: ReactNode): void {
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
  act(() => root?.render(tree));
}

afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  root = null;
  host = null;
});

/** Records what `useContext(UNSAFE_DataRouterContext)` returns where it renders. */
function probe(seen: unknown[]) {
  return function DataRouterProbe() {
    seen.push(useContext(UNSAFE_DataRouterContext));
    return null;
  };
}

describe('react-router exports the app integrates with', () => {
  it('UNSAFE_DataRouterContext is a React context object with a Provider', () => {
    expect(UNSAFE_DataRouterContext, 'react-router no longer exports UNSAFE_DataRouterContext').toBeDefined();
    const context = UNSAFE_DataRouterContext as unknown as { $$typeof?: symbol; Provider?: unknown };
    expect(context.$$typeof).toBe(REACT_CONTEXT);
    expect(context.Provider).toBeDefined();
  });

  it('UNSAFE_DataRouterContext reads null under a declarative router', () => {
    const seen: unknown[] = [];
    render(createElement(MemoryRouter, null, createElement(probe(seen))));
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((value) => value === null)).toBe(true);
  });

  it('UNSAFE_DataRouterContext is set under a data router', () => {
    const seen: unknown[] = [];
    const router = createMemoryRouter([{ path: '/', element: createElement(probe(seen)) }]);
    render(createElement(RouterProvider, { router }));
    expect(seen.length).toBeGreaterThan(0);
    expect(seen.every((value) => value !== null && typeof value === 'object')).toBe(true);
    router.dispose();
  });

  it("a loader-less data router's location moves inside navigate(), before React commits it", async () => {
    // useLiveSearchParams builds a URL write on this: the router's location
    // is the live one while the committed render still paints the previous.
    const committed: string[] = [];
    let contextRouter: { state: { location: { search: string } } } | null = null;
    function LocationProbe() {
      committed.push(useLocation().search);
      contextRouter = useContext(UNSAFE_DataRouterContext)?.router ?? null;
      return null;
    }
    const router = createMemoryRouter([{ path: '*', element: createElement(LocationProbe) }], {
      initialEntries: ['/lead-queue?state=TX'],
    });
    render(createElement(RouterProvider, { router }));
    // Settles a route that has to load first (none here), so only navigate()'s own timing is under test.
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    expect(contextRouter, 'the context carries the router itself').toBe(router);
    const seen = { live: '', committed: '' };
    act(() => {
      void router.navigate('/lead-queue?state=TX&hide=segments');
      // Read as an event handler reads: through the context's router, before act() lets React commit.
      seen.live = contextRouter?.state.location.search ?? '';
      seen.committed = committed[committed.length - 1] ?? '';
    });
    expect(seen).toEqual({ live: '?state=TX&hide=segments', committed: '?state=TX' });
    expect(committed[committed.length - 1]).toBe('?state=TX&hide=segments');
    router.dispose();
  });

  it('the data-router entry points the shell mounts are exported', () => {
    expect(typeof createBrowserRouter).toBe('function');
    expect(typeof useBlocker).toBe('function');
    expect(typeof RouterProvider).toBe('function');
  });
});
