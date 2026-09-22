/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
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
 * 30 s under the production query defaults) and threw again, forever.
 */

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const BORROWER_ID = 'B-0TESTBORROWER';
const ROUTE_KEY = ['mip', 'borrower', BORROWER_ID] as const;
const SHELL_KEY = ['mip', 'health'] as const;
const OTHER_ROUTE_KEY = ['mip', 'leads'] as const;

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

function ShellBody({ read }: { read: () => Promise<string> }) {
  const query = useQuery({ queryKey: SHELL_KEY, queryFn: read });
  return <p data-testid="shell">{query.data ?? 'checking'}</p>;
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

  it('Try again re-reads the failed route data once and renders it; nothing else refetches', async () => {
    let payload: Dossier = { events: null };
    const readRoute = vi.fn(async () => payload);
    const readShell = vi.fn(async () => 'healthy');
    const readOtherRoute = vi.fn(async () => ['warm cache of a route visited earlier']);
    // A route visited earlier left a warm cache entry this visit never touches.
    await queryClient.fetchQuery({ queryKey: OTHER_ROUTE_KEY, queryFn: readOtherRoute });

    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <ShellBody read={readShell} />
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
    expect(readOtherRoute).toHaveBeenCalledTimes(1);
    expect(queryClient.getQueryData(OTHER_ROUTE_KEY), 'an untouched warm cache survives').toEqual([
      'warm cache of a route visited earlier',
    ]);
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
