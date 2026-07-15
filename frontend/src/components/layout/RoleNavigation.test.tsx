import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionResponse } from '../../types';

vi.mock('../AppContext', () => ({
  useApp: () => ({ lastBorrowerId: null }),
}));

vi.mock('../../lib/api', () => ({
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

describe('role-aware navigation', () => {
  let queryClient: QueryClient;

  beforeEach(() => {
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    });
  });

  it('fails closed before the first successful session authorization', () => {
    expectAdminHidden(renderNavigation(queryClient));
  });

  it('shows the Admin destination in both navs after an affirmative session response', () => {
    queryClient.setQueryData<SessionResponse>(SESSION_QUERY_KEY, { can_access_admin: true });
    expectAdminVisible(renderNavigation(queryClient));
  });

  it('preserves the last successful admin decision during a background refetch', async () => {
    const initial = createDeferred<SessionResponse>();
    const initialFetch = queryClient.fetchQuery({
      queryKey: SESSION_QUERY_KEY,
      queryFn: () => initial.promise,
    });

    expectAdminHidden(renderNavigation(queryClient));
    initial.resolve({ can_access_admin: true });
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

    background.resolve({ can_access_admin: true });
    await backgroundFetch;
    expectAdminVisible(renderNavigation(queryClient));
  });
});
