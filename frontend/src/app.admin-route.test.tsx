/**
 * @vitest-environment happy-dom
 *
 * AdminRouteGate. Backend AdminDep checks are the security boundary; the gate
 * keeps a denied deep link from mounting an operator console full of 403
 * panels. Until the 2026-09-21 audit (shell-06) it did that with a silent
 * `<Navigate to="/" replace />`, and this file pinned that redirect. A denied
 * actor now gets a 403 surface that names the required role and offers a way
 * back; the console still never mounts.
 */

import { act, Suspense } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from './lib/api';

vi.mock('./lib/routePreloaders', async (importOriginal) => ({
  ...(await importOriginal<typeof import('./lib/routePreloaders')>()),
  AdminConfigRoute: () => <div data-testid="admin-console">Rules, data sources, and audit</div>,
}));

import { AdminRouteGate } from './app';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// Bounded well inside the per-test timeout, so a gate that never settles
// fails on its assertion instead of timing out mid-act and poisoning the
// next test's render.
const SETTLE_ATTEMPTS = 300;
const TEST_TIMEOUT_MS = 15_000;

describe('AdminRouteGate', { timeout: TEST_TIMEOUT_MS }, () => {
  let root: Root;
  let container: HTMLElement;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await act(async () => root.unmount());
    container.remove();
  });

  async function renderAt(entries: string[]): Promise<void> {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={entries}>
            <Suspense fallback={<div>Loading route</div>}>
              <Routes>
                <Route path="/admin-config" element={<AdminRouteGate />} />
                <Route path="/lead-queue" element={<div>Lead queue destination</div>} />
                <Route path="/" element={<div>Home destination</div>} />
              </Routes>
            </Suspense>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    // The gate first waits on the session, then the denied page arrives as a
    // lazy chunk: wait for a settled outcome rather than for one tick.
    const settled = () =>
      denied() !== null || container.querySelector('[data-testid="admin-console"]') !== null;
    for (let attempt = 0; attempt < SETTLE_ATTEMPTS && !settled(); attempt += 1) {
      await act(async () => {
        await new Promise((resolve) => window.setTimeout(resolve, 10));
      });
    }
  }

  const denied = (): HTMLElement | null =>
    container.querySelector<HTMLElement>('[data-testid="admin-access-denied"]');
  const buttonNamed = (name: string): HTMLButtonElement | undefined =>
    [...container.querySelectorAll<HTMLButtonElement>('button')].find((button) =>
      button.textContent?.includes(name),
    );

  it('shows a denied deep link a 403 surface naming the role, without redirecting or mounting the console', async () => {
    vi.spyOn(api, 'session').mockResolvedValue({ can_access_admin: false, can_approve: false, actor_email: null });

    await renderAt(['/admin-config']);

    expect(denied()).not.toBeNull();
    expect(container.querySelector('h1')?.textContent).toBe('Administrator access required');
    expect(denied()?.querySelector('[data-testid="access-denied-role"]')?.textContent)
      .toContain('Required role: Administrator');
    expect(denied()?.textContent).toContain('403');
    // Not bounced, and the operator console never mounted.
    expect(container.textContent).not.toContain('Home destination');
    expect(container.querySelector('[data-testid="admin-console"]')).toBeNull();

    // Cold deep link: Home is the one primary exit; no Back out of the product.
    expect(buttonNamed('Back to previous page')).toBeUndefined();
    const home = [...(denied()?.querySelectorAll<HTMLAnchorElement>('a') ?? [])].find((link) =>
      link.textContent?.includes('Go to Home'),
    );
    expect(home?.getAttribute('href')).toBe('/');
    expect(home?.className).toContain('btn--primary');
  });

  it('offers the way back to the page the actor came from', async () => {
    vi.spyOn(api, 'session').mockResolvedValue({ can_access_admin: false, can_approve: false, actor_email: null });

    await renderAt(['/lead-queue', '/admin-config']);
    expect(denied()).not.toBeNull();

    await act(async () => {
      buttonNamed('Back to previous page')?.click();
    });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });

    expect(container.textContent).toContain('Lead queue destination');
    expect(denied()).toBeNull();
  });

  it('stays closed when the session check fails, without claiming the actor lacks the role', async () => {
    const session = vi.spyOn(api, 'session').mockRejectedValue(new Error('network down'));

    await renderAt(['/admin-config']);

    expect(denied()).not.toBeNull();
    expect(container.querySelector('[data-testid="admin-console"]')).toBeNull();
    expect(denied()?.textContent).toContain('Access not verified');
    expect(denied()?.textContent).toContain('could not be checked');
    expect(denied()?.textContent).not.toContain('does not have it');
    expect(denied()?.textContent).not.toContain('403');

    const callsBefore = session.mock.calls.length;
    await act(async () => {
      buttonNamed('Check access again')?.click();
    });
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
    expect(session.mock.calls.length).toBeGreaterThan(callsBefore);
  });

  it('mounts the console for an administrator', async () => {
    vi.spyOn(api, 'session').mockResolvedValue({ can_access_admin: true, can_approve: true, actor_email: null });

    await renderAt(['/admin-config']);

    expect(container.querySelector('[data-testid="admin-console"]')).not.toBeNull();
    expect(denied()).toBeNull();
  });
});
