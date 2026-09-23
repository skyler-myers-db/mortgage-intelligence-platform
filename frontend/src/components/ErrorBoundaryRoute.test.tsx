/**
 * @vitest-environment happy-dom
 */

import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClientProvider, useQuery, type QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { rootErrorOptions } from '../lib/clientErrorLog';
import { createMipQueryClient } from '../lib/queryClient';
import { RouteErrorBoundary } from './ErrorBoundaryRoute';

/**
 * "Try again" on a route that threw while rendering a malformed payload
 * (audit stack-01 follow-up). Before the fix the boundary only cleared its
 * own state: the route re-mounted onto the cached malformed result (fresh for
 * 30 s under the production query defaults) and threw again, forever. A first
 * fix removed only the queries the cache reported activity for during the
 * current visit, which misses a route that throws on its first render from a
 * cache an earlier visit, or another route, filled: that render never
 * commits, so the cache reports nothing. Both cases are pinned below.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const BORROWER_ID = 'B-0TESTBORROWER';
const ROUTE_KEY = ['mip', 'borrower', BORROWER_ID] as const;
const SHELL_KEY = ['mip', 'health'] as const;
const OTHER_ROUTE_KEY = ['mip', 'leads'] as const;
const SHELL_HELD_KEY = ['mip', 'workspace'] as const;

interface Dossier {
  events: string[] | null;
}

function RouteBody({ read }: { read: () => Promise<Dossier> }) {
  const query = useQuery({ queryKey: ROUTE_KEY, queryFn: read });
  if (!query.data) return <p data-testid="route-loading">loading</p>;
  // `events` is a required array on the wire; a null one throws here, the
  // way Borrower 360 throws mapping over a null `evidence_events`.
  const events = query.data.events as string[];
  return (
    <ul data-testid="route-events">
      {events.map((event) => (
        <li key={event}>{event}</li>
      ))}
    </ul>
  );
}

/** Another route reading the same dossier query without touching `events`. */
function OtherRouteBody({ read }: { read: () => Promise<Dossier> }) {
  const query = useQuery({ queryKey: ROUTE_KEY, queryFn: read });
  return <p data-testid="other-route">{query.data ? 'dossier cached' : 'loading'}</p>;
}

function ShellBody({ read }: { read: () => Promise<string> }) {
  const query = useQuery({ queryKey: SHELL_KEY, queryFn: read });
  return <p data-testid="shell">{query.data ?? 'checking'}</p>;
}

/** A mounted shell component holding a cached query through a disabled observer. */
function ShellHeldBody({ read }: { read: () => Promise<string> }) {
  const query = useQuery({ queryKey: SHELL_HELD_KEY, queryFn: read, enabled: false });
  return <p data-testid="shell-held">{query.data ?? 'none'}</p>;
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

describe('RouteErrorBoundary', () => {
  let root: Root;
  let container: HTMLElement;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container, rootErrorOptions());
    queryClient = createMipQueryClient();
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    queryClient.clear();
    vi.restoreAllMocks();
  });

  const surface = () => container.querySelector('[data-testid="error-surface"]');
  const tryAgain = () =>
    Array.from(container.querySelectorAll('button')).find((b) => b.textContent === 'Try again');
  const routeEvents = () => container.querySelector('[data-testid="route-events"]')?.textContent;

  /**
   * One route visit, the way app.tsx mounts it: a wrapper re-keyed per visit
   * around a RouteErrorBoundary for that pathname.
   */
  async function visit(visitKey: string, pathname: string, body: ReactNode) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <div key={visitKey}>
            <RouteErrorBoundary pathname={pathname}>{body}</RouteErrorBoundary>
          </div>
        </QueryClientProvider>,
      );
    });
    await flush();
  }

  it('Try again re-reads the failed route data once and renders it; nothing else refetches', async () => {
    let payload: Dossier = { events: null };
    const readRoute = vi.fn(async () => payload);
    const readShell = vi.fn(async () => 'healthy');
    const readOtherRoute = vi.fn(async () => ['warm cache of a route visited earlier']);
    const readShellHeld = vi.fn(async () => 'Summit Mortgage');
    // A route visited earlier left a warm cache entry this visit never touches.
    await queryClient.fetchQuery({ queryKey: OTHER_ROUTE_KEY, queryFn: readOtherRoute });
    await queryClient.fetchQuery({ queryKey: SHELL_HELD_KEY, queryFn: readShellHeld });

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <ShellBody read={readShell} />
          <ShellHeldBody read={readShellHeld} />
          <RouteErrorBoundary pathname={`/borrower-360/${BORROWER_ID}`}>
            <RouteBody read={readRoute} />
          </RouteErrorBoundary>
        </QueryClientProvider>,
      );
    });
    await flush();

    expect(surface()?.getAttribute('data-error-kind')).toBe('render');
    expect(surface()?.textContent).toContain('Route · Borrower 360');
    expect(container.innerHTML).not.toContain(BORROWER_ID);
    expect(readRoute).toHaveBeenCalledTimes(1);

    // While the surface is up nothing re-reads by itself.
    await flush();
    await flush();
    expect(readRoute).toHaveBeenCalledTimes(1);

    // The API is fixed; the user clicks Try again.
    payload = { events: ['Current lien rate is 120 bps above par.'] };
    await act(async () => {
      tryAgain()?.click();
    });
    await flush();

    expect(surface()).toBeNull();
    expect(container.querySelector('[data-testid="route-events"]')?.textContent).toBe(
      'Current lien rate is 120 bps above par.',
    );
    expect(readRoute, 'exactly one user-initiated re-read').toHaveBeenCalledTimes(2);
    expect(readShell, 'the shell query is still observed, so it is not re-read').toHaveBeenCalledTimes(1);
    expect(container.querySelector('[data-testid="shell"]')?.textContent).toBe('healthy');
    // Held by a mounted component whose observer is disabled: inactive, but
    // observed, so it stays in the cache.
    expect(queryClient.getQueryData(SHELL_HELD_KEY), 'a disabled-but-mounted observer keeps its query').toBe(
      'Summit Mortgage',
    );
    expect(readShellHeld).toHaveBeenCalledTimes(1);
    // An unobserved cache is discarded by Try again but never re-read: the
    // route that owns it reads it again only when the user navigates there.
    expect(readOtherRoute, 'an unobserved cache is not re-read').toHaveBeenCalledTimes(1);
    expect(queryClient.getQueryData(OTHER_ROUTE_KEY), 'an unobserved cache is discarded').toBeUndefined();
  });

  it('Try again recovers on a revisit that re-throws from the payload an earlier visit cached', async () => {
    let payload: Dossier = { events: null };
    const readRoute = vi.fn(async () => payload);
    const dossierPath = `/borrower-360/${BORROWER_ID}`;

    await visit('v1', dossierPath, <RouteBody read={readRoute} />);
    expect(surface()?.getAttribute('data-error-kind')).toBe('render');
    expect(readRoute).toHaveBeenCalledTimes(1);

    // The user leaves for another route...
    await visit('leads', '/lead-queue', <p data-testid="other-route">Leads</p>);
    expect(surface()).toBeNull();

    // ...and comes back within gcTime (Back, or a Leads-row link). The route
    // throws on its first render straight from the cached payload: a render
    // that throws never commits, so it subscribes no observer and issues no read.
    await visit('v2', dossierPath, <RouteBody read={readRoute} />);
    expect(surface()?.getAttribute('data-error-kind')).toBe('render');
    expect(readRoute, 'the revisit re-threw from the cache without a read').toHaveBeenCalledTimes(1);

    // The API is fixed; the user clicks Try again.
    payload = { events: ['Listed for sale 12 days ago.'] };
    await act(async () => {
      tryAgain()?.click();
    });
    await flush();

    expect(surface()).toBeNull();
    expect(routeEvents()).toBe('Listed for sale 12 days ago.');
    expect(readRoute, 'exactly one user-initiated re-read').toHaveBeenCalledTimes(2);
  });

  it('Try again recovers when the route threw on a payload a different route cached', async () => {
    let payload: Dossier = { events: null };
    const readRoute = vi.fn(async () => payload);

    // Another route reads the same query and renders it fine (it never
    // touches `events`), leaving the malformed payload in the cache.
    await visit('leads', '/lead-queue', <OtherRouteBody read={readRoute} />);
    expect(container.querySelector('[data-testid="other-route"]')?.textContent).toBe('dossier cached');
    expect(readRoute).toHaveBeenCalledTimes(1);
    // One more hop, so the dossier visit does not also see that route's
    // observer leave the cache.
    await visit('home', '/', <p data-testid="home-route">Home</p>);

    // First visit to the dossier: it throws on its first render from that cache.
    await visit('dossier', `/borrower-360/${BORROWER_ID}`, <RouteBody read={readRoute} />);
    expect(surface()?.getAttribute('data-error-kind')).toBe('render');
    expect(readRoute).toHaveBeenCalledTimes(1);

    payload = { events: ['Owner Link shows 3 properties.'] };
    await act(async () => {
      tryAgain()?.click();
    });
    await flush();

    expect(surface()).toBeNull();
    expect(routeEvents()).toBe('Owner Link shows 3 properties.');
    expect(readRoute, 'exactly one user-initiated re-read').toHaveBeenCalledTimes(2);
  });

  it('shows the recovery surface again when the re-read is still malformed', async () => {
    const readRoute = vi.fn(async (): Promise<Dossier> => ({ events: null }));

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <RouteErrorBoundary pathname={`/borrower-360/${BORROWER_ID}`}>
            <RouteBody read={readRoute} />
          </RouteErrorBoundary>
        </QueryClientProvider>,
      );
    });
    await flush();
    expect(surface()).not.toBeNull();

    await act(async () => {
      tryAgain()?.click();
    });
    await flush();

    expect(readRoute).toHaveBeenCalledTimes(2);
    expect(surface()?.getAttribute('data-error-kind')).toBe('render');
    expect(tryAgain()).toBeDefined();
  });
});
