// @vitest-environment happy-dom
/**
 * The presenter roadmap chunk fails soft (W5c integration, the
 * w5-shell-nav-followups review's Rail.tsx guard): when the stale-chunk
 * listener cancels vite:preloadError, Vite's preload helper resolves the
 * import to undefined instead of rejecting. The mock below stands in for that
 * resolution (a module whose default is undefined); the rail must still
 * render M0 alone, with no thrown render. The one-step `.catch` this
 * replaced handed React an undefined component.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { SessionResponse } from '../../types';

const apiMocks = vi.hoisted(() => ({ session: vi.fn() }));
vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: apiMocks,
}));
vi.mock('./RailRoadmap', () => ({ default: undefined }));

import { Rail } from './Rail';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PRESENTER: SessionResponse = { can_access_admin: true, can_approve: true, can_read_audit: true, presenter_mode: true };

describe('the presenter roadmap chunk resolving to undefined', () => {
  let root: Root;
  let queryClient: QueryClient;
  const errors: unknown[] = [];
  const onError = (event: ErrorEvent) => errors.push(event.error);

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement, {
      onUncaughtError: (error) => errors.push(error),
      onCaughtError: (error) => errors.push(error),
    });
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
    queryClient.setQueryData(['session', 'access'], PRESENTER);
    apiMocks.session.mockResolvedValue(PRESENTER);
    errors.length = 0;
    window.addEventListener('error', onError);
  });

  afterEach(() => {
    window.removeEventListener('error', onError);
    act(() => root.unmount());
    queryClient.clear();
    document.body.innerHTML = '';
  });

  it('renders M0 alone and throws nothing', async () => {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>
            <Rail />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    for (let tick = 0; tick < 20; tick += 1) {
      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 5));
      });
    }
    const rail = document.querySelector<HTMLElement>('nav.rail');
    expect(rail, 'the rail still renders').not.toBeNull();
    const modules = [...(rail?.querySelectorAll('.rail__item .mod') ?? [])].map((mod) => mod.textContent);
    expect(modules).toEqual(['M0']);
    expect(rail?.querySelectorAll('button.rail__item--disabled')).toHaveLength(0);
    expect(errors, 'no render error reached a boundary').toEqual([]);
  });
});
