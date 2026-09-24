/**
 * @vitest-environment happy-dom
 *
 * Dependency contracts: the handful of third-party exports the app reaches
 * into beyond a package's documented API, pinned so a dependency bump that
 * moves them fails here, by name, instead of in a rendered route.
 *
 * react-router (wave-1c follow-up #18). UnsavedChangesGuard reads
 * `UNSAFE_DataRouterContext` to tell a data router (main.tsx's
 * createBrowserRouter, where `useBlocker` works) from a declarative one
 * (every route unit test's MemoryRouter, where `useBlocker` throws). The
 * export is `UNSAFE_` because it is an integration point, not an app API, so
 * react-router may rename or drop it in a minor. The guard only reads its
 * presence; this file pins exactly that: it is a React context object, it is
 * null under a declarative router and set under a data router. It also pins
 * the data-router entry points the shell mounts (createBrowserRouter,
 * useBlocker, and RouterProvider from react-router/dom).
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

  it('the data-router entry points the shell mounts are exported', () => {
    expect(typeof createBrowserRouter).toBe('function');
    expect(typeof useBlocker).toBe('function');
    expect(typeof RouterProvider).toBe('function');
  });
});
