// @vitest-environment happy-dom

import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes, useNavigate, type NavigateFunction } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppShell } from './AppShell';
import { PageShell } from './PageShell';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * Route continuity, proven on the REAL AppShell (audit 2026-09-21 `shell-03`,
 * `stack-03`, `motion-v1`, `critic-v1`, `a11y-03`).
 *
 * `useMainScroll` and `useRouteAnnouncer` have their own suites against a
 * minimal harness. This one guards the wiring: the shell's actual
 * `<main id="main-content">` is the element that resets and restores, and the
 * shell's actual live region is the one that announces. Deleting either hook
 * call from AppShell fails here.
 *
 * The backend is unreachable (every fetch 404s), which is also the honest
 * default for a unit test: there is no mock data path to fall back to.
 */

let navigate: NavigateFunction;

function NavigateProbe() {
  const routerNavigate = useNavigate();
  useEffect(() => {
    navigate = routerNavigate;
  }, [routerNavigate]);
  return null;
}

describe('AppShell route continuity', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    window.sessionStorage.clear();
    document.title = 'Mortgage Intelligence Platform';
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"not found"}', { status: 404 })));
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    await act(async () => root.unmount());
    container.remove();
    queryClient.clear();
    window.sessionStorage.clear();
    vi.unstubAllGlobals();
  });

  async function mount(initialEntry: string): Promise<HTMLElement> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[initialEntry]}>
            <NavigateProbe />
            <AppShell>
              <Routes>
                <Route path="/lead-queue" element={<PageShell title="Ranked borrowers">queue</PageShell>} />
                <Route path="/borrower-360/:id" element={<PageShell title="Borrower">dossier</PageShell>} />
              </Routes>
            </AppShell>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    const main = container.querySelector('main#main-content') as HTMLElement;
    // happy-dom has no layout: give the scroller a tall document.
    Object.defineProperty(main, 'scrollHeight', { configurable: true, get: () => 4000 });
    Object.defineProperty(main, 'clientHeight', { configurable: true, get: () => 800 });
    return main;
  }

  async function go(to: string | number): Promise<void> {
    await act(async () => {
      if (typeof to === 'number') await navigate(to);
      else await navigate(to);
    });
  }

  it('resets the shell scroller on navigation and restores it on Back', async () => {
    const main = await mount('/lead-queue');
    await act(async () => {
      main.scrollTop = 770;
      main.dispatchEvent(new Event('scroll'));
    });

    await go('/borrower-360/B-0123456789ABC');
    expect(main.scrollTop).toBe(0);

    await go(-1);
    expect(main.scrollTop).toBe(770);
  });

  it('titles, announces and focuses through the one shell live region', async () => {
    await mount('/lead-queue');
    const regions = container.querySelectorAll('[data-route-announcer]');
    expect(regions).toHaveLength(1);
    const region = regions[0] as HTMLElement;
    expect(region.getAttribute('aria-live')).toBe('polite');
    expect(region.className).toBe('sr-only');
    expect(region.closest('main')).toBeNull();
    expect(document.title).toBe('Lead Queue · Mortgage Intelligence Platform');
    expect(region.textContent).toBe('');

    await go('/borrower-360/B-0123456789ABC');

    expect(document.title).toBe('Borrower 360 · B-0123456789ABC · Mortgage Intelligence Platform');
    expect(region.textContent).toBe('Borrower 360 · B-0123456789ABC');
    expect(document.activeElement).toBe(container.querySelector('main h1'));
  });
});
