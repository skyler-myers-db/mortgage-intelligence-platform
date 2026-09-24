import type { ReactNode } from 'react';
import { createBrowserRouter, useRouteError, type RouteObject } from 'react-router';
import App from './app';

/**
 * The app's data router (2026-09-21 audit states-05).
 *
 * One catch-all route renders `<App/>`, and App keeps its own `<Routes>` tree
 * exactly as it was under `<BrowserRouter>`: route matching, lazy routes, the
 * per-route ErrorBoundary and the pathname-keyed transition are untouched.
 * The data router exists for one reason: `useBlocker`, which the unsaved-
 * changes guard (hooks/useUnsavedGuard) needs and which a declarative router
 * cannot provide. This is deliberately NOT the loader / action migration: no
 * loaders, no actions, no nested route objects.
 */

/**
 * A data router puts React Router's own error boundary around its root
 * route, so a shell render throw would render React Router's developer page
 * instead of reaching main.tsx's root ErrorBoundary. This element re-throws
 * the caught error so the product's recovery surface (and its message-free
 * client error report) still owns every shell failure.
 */
export function RethrowToRootBoundary(): never {
  throw useRouteError();
}

/** The route table: the catch-all that hands every URL to `element`. */
export function appRouteObjects(element: ReactNode): RouteObject[] {
  return [{ path: '*', element, ErrorBoundary: RethrowToRootBoundary }];
}

export function createAppRouter() {
  return createBrowserRouter(appRouteObjects(<App />));
}
