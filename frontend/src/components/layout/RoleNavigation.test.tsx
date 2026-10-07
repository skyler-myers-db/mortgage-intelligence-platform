import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionResponse } from '../../types';

vi.mock('../AppContext', () => ({
  useApp: () => ({ lastBorrowerId: null }),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: { session: vi.fn() },
}));

import { Rail } from './Rail';
import { RouteNav } from './RouteNav';

const SESSION_QUERY_KEY = ['session', 'access'] as const;

function createDeferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function renderNavigation(queryClient: QueryClient): string {
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/']}>
        <Rail />
        <RouteNav />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function expectAdminHidden(html: string): void {
  expect(html).not.toContain('href="/admin-config"');
  expect(html).not.toContain('Admin / settings');
  expect(html).not.toContain('>Admin<');
}

function expectAdminVisible(html: string): void {
  expect(html.match(/href="\/admin-config"/g)).toHaveLength(2);
  expect(html).toContain('Admin / settings');
  expect(html).toContain('>Admin<');
}

/** The ledger's route-nav link ('Audit') and its rail item, counted apart. */
function ledgerLinks(html: string): { nav: number; rail: number } {
  const links = [...html.matchAll(/<a[^>]*href="\/audit-ledger"[^>]*>[\s\S]*?<\/a>/g)].map((match) => match[0]);
  return {
    nav: links.filter((link) => link.includes('route-nav__link') && link.includes('>Audit<')).length,
    rail: links.filter((link) => link.includes('rail__item') && link.includes('aria-label="Audit ledger"')).length,
  };
}

describe('role-aware navigation', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
  });

  it('fails closed before the first successful session authorization', () => {
    const html = renderNavigation(queryClient);
    expectAdminHidden(html);
    expect(ledgerLinks(html)).toEqual({ nav: 0, rail: 0 });
  });

  /**
   * D-audit-reads-c3: a read-only auditor gets the ledger as 'Audit' in the
   * route nav (in Admin's place) and as a rail item; never Admin. An admin
   * keeps Admin in the nav (one more link would wrap the pinned one-line
   * nav) and reaches the ledger from the rail. A plain user gets neither.
   */
  it('shows an auditor the ledger in the nav and the rail, and no Admin anywhere', () => {
    queryClient.setQueryData<SessionResponse>(SESSION_QUERY_KEY, {
      can_access_admin: false,
      can_approve: false,
      can_read_audit: true,
    });
    const html = renderNavigation(queryClient);

    expectAdminHidden(html);
    expect(ledgerLinks(html)).toEqual({ nav: 1, rail: 1 });
  });

  it('shows an admin Admin in the nav and both items in the rail, but no nav "Audit"', () => {
    queryClient.setQueryData<SessionResponse>(SESSION_QUERY_KEY, {
      can_access_admin: true,
      can_approve: true,
      can_read_audit: true,
    });
    const html = renderNavigation(queryClient);

    expectAdminVisible(html);
    expect(ledgerLinks(html)).toEqual({ nav: 0, rail: 1 });
  });

  it('shows a plain workspace user neither the ledger nor Admin', () => {
    queryClient.setQueryData<SessionResponse>(SESSION_QUERY_KEY, {
      can_access_admin: false,
      can_approve: true,
      can_read_audit: false,
    });
    const html = renderNavigation(queryClient);

    expectAdminHidden(html);
    expect(ledgerLinks(html)).toEqual({ nav: 0, rail: 0 });
  });

  it('shows the Admin destination in both navs after an affirmative session response', () => {
    queryClient.setQueryData<SessionResponse>(SESSION_QUERY_KEY, { can_access_admin: true, can_approve: true });
    expectAdminVisible(renderNavigation(queryClient));
  });

  it('preserves the last successful admin decision during a background refetch', async () => {
    const initial = createDeferred<SessionResponse>();
    const initialFetch = queryClient.fetchQuery({
      queryKey: SESSION_QUERY_KEY,
      queryFn: () => initial.promise,
    });

    expectAdminHidden(renderNavigation(queryClient));
    initial.resolve({ can_access_admin: true, can_approve: true });
    await initialFetch;
    expectAdminVisible(renderNavigation(queryClient));

    await queryClient.invalidateQueries({ queryKey: SESSION_QUERY_KEY, refetchType: 'none' });
    const background = createDeferred<SessionResponse>();
    const backgroundFetch = queryClient.fetchQuery({
      queryKey: SESSION_QUERY_KEY,
      queryFn: () => background.promise,
      staleTime: 0,
    });

    expect(queryClient.isFetching({ queryKey: SESSION_QUERY_KEY })).toBe(1);
    expectAdminVisible(renderNavigation(queryClient));

    background.resolve({ can_access_admin: true, can_approve: true });
    await backgroundFetch;
    expectAdminVisible(renderNavigation(queryClient));
  });
});
