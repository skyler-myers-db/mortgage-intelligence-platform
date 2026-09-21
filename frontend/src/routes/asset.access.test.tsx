/**
 * @vitest-environment happy-dom
 *
 * 2026-09-21 audit critic-03. The buyer personas are not admins, and the
 * asset-detail read is AdminDep-gated. Their 403 used to read "Admin access
 * required" with "Return home" as the only exit. The state must name the
 * required role and lead back to where the actor came from, else Home.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from '../lib/api';
import AssetRoute from './asset';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

describe('Asset detail — denied actor', () => {
  let root: Root;
  let container: HTMLElement;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await act(async () => root.unmount());
    queryClient.clear();
    container.remove();
  });

  async function renderAt(entries: string[]): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={entries}>
            <Routes>
              <Route path="/lead-queue" element={<div>Lead queue destination</div>} />
              <Route path="/" element={<div>Home destination</div>} />
              <Route path="/data-estate/assets/:assetKey" element={<AssetRoute />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await settle();
  }

  const denied = (): HTMLElement | null =>
    container.querySelector<HTMLElement>('[data-testid="asset-access-denied"]');
  const backButton = (): HTMLButtonElement | undefined =>
    [...(denied()?.querySelectorAll<HTMLButtonElement>('button') ?? [])].find((button) =>
      button.textContent?.includes('Back to previous page'),
    );
  const homeLink = (): HTMLAnchorElement | undefined =>
    [...(denied()?.querySelectorAll<HTMLAnchorElement>('a') ?? [])].find((link) =>
      link.textContent?.includes('Go to Home'),
    );

  function deny(): void {
    vi.spyOn(api, 'assetMetadata').mockRejectedValue(
      new ApiError('forbidden', { path: '/api/admin/assets/lead_population/metadata', status: 403 }),
    );
  }

  it('names the required role and returns the actor to the page they came from', async () => {
    deny();
    await renderAt(['/lead-queue', '/data-estate/assets/lead_population']);

    expect(denied()).not.toBeNull();
    expect(denied()?.querySelector('h2')?.textContent).toBe('Administrator access required');
    expect(denied()?.querySelector('[data-testid="access-denied-role"]')?.textContent)
      .toContain('Required role: Administrator');
    expect(denied()?.textContent).toContain('403');
    // Not the generic failure copy: a denied actor is not told the asset is broken.
    expect(container.textContent).not.toContain('Asset unavailable');

    expect(backButton()).toBeTruthy();
    expect(homeLink()?.getAttribute('href')).toBe('/');

    await act(async () => {
      backButton()?.click();
    });
    await settle();
    expect(container.textContent).toContain('Lead queue destination');
    expect(denied()).toBeNull();
  });

  it('offers Home as the single primary exit on a cold deep link with no in-app history', async () => {
    deny();
    await renderAt(['/data-estate/assets/lead_population']);

    expect(denied()).not.toBeNull();
    // No Back button that would walk the actor out of the product.
    expect(backButton()).toBeUndefined();
    expect(homeLink()?.className).toContain('btn--primary');

    await act(async () => {
      homeLink()?.click();
    });
    await settle();
    expect(container.textContent).toContain('Home destination');
  });

  it('keeps the generic unavailable state for non-403 failures', async () => {
    vi.spyOn(api, 'assetMetadata').mockRejectedValue(
      new ApiError('not found', { path: '/api/admin/assets/nope/metadata', status: 404 }),
    );
    await renderAt(['/lead-queue', '/data-estate/assets/nope']);

    expect(denied()).toBeNull();
    expect(container.textContent).toContain('Asset unavailable');
    expect(container.textContent).not.toContain('Required role');
  });
});
