/**
 * @vitest-environment happy-dom
 *
 * The asset detail route's failures in the shared buyer-safe vocabulary
 * (audit 2026-09-21 states-04 / states-03): an outage or a missing asset
 * renders AsyncStatus (the describeApiError title and body, one Retry where
 * retrying helps, the copyable correlation id), never the transport's error
 * text; the hand-rolled "Asset unavailable" card is gone. A 403 still renders
 * the AccessDenied page and nothing else.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from '../lib/api';
import { preloadAsyncFailure } from '../components/ui/AsyncState';
import AssetRoute from './asset';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const LEAK = 'statement_id=01ef PERMISSION_DENIED dbfs:/must-not-render';

async function settle(): Promise<void> {
  for (let i = 0; i < 4; i += 1) {
    await act(async () => {
      await Promise.resolve();
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
  }
}

beforeAll(async () => {
  await preloadAsyncFailure();
}, 60_000);

describe('Asset detail failure surface', () => {
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

  async function render(): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
          <MemoryRouter initialEntries={['/data-estate/assets/lead_population']}>
            <Routes>
              <Route path="/data-estate/assets/:assetKey" element={<AssetRoute />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await settle();
  }

  const alert = () => container.querySelector<HTMLElement>('[role="alert"]');

  it('reads an outage in the shared vocabulary with one Retry and the support reference', async () => {
    const read = vi.spyOn(api, 'assetMetadata').mockRejectedValue(
      new ApiError(LEAK, {
        path: '/api/admin/assets/lead_population/metadata',
        status: 503,
        dependency: 'warehouse',
        correlationId: 'corr-asset-0001',
      }),
    );
    await render();

    expect(alert()).not.toBeNull();
    expect(alert()?.getAttribute('data-async-status')).toBe('server_error');
    expect(alert()?.textContent).toContain("Couldn't load asset detail.");
    expect(container.textContent).not.toContain('statement_id');
    expect(container.textContent).not.toContain('dbfs:/');
    expect(container.textContent).not.toContain('Asset unavailable');
    expect(container.textContent).toContain('corr-asset-0001');
    const retry = [...(alert()?.querySelectorAll<HTMLButtonElement>('button') ?? [])].find((button) =>
      button.textContent?.includes('Retry'),
    );
    expect(retry).toBeTruthy();
    expect(read).toHaveBeenCalledTimes(1);
    await act(async () => retry?.click());
    await settle();
    expect(read).toHaveBeenCalledTimes(2);
  });

  it('says a missing asset was not found, never the transport text', async () => {
    vi.spyOn(api, 'assetMetadata').mockRejectedValue(
      new ApiError(LEAK, { path: '/api/admin/assets/nope/metadata', status: 404 }),
    );
    await render();

    expect(alert()?.textContent).toContain("Asset detail wasn't found.");
    expect(alert()?.textContent).toContain('It may have been removed, or the link is out of date.');
    expect(container.textContent).not.toContain('statement_id');
    expect(container.querySelector('[data-testid="asset-access-denied"]')).toBeNull();
  });

  it('keeps the AccessDenied page for a 403, with no failure callout', async () => {
    vi.spyOn(api, 'assetMetadata').mockRejectedValue(
      new ApiError(LEAK, { path: '/api/admin/assets/lead_population/metadata', status: 403 }),
    );
    await render();

    expect(container.querySelector('[data-testid="asset-access-denied"]')).not.toBeNull();
    expect(alert()).toBeNull();
    expect(container.querySelector('[data-async-status]')).toBeNull();
    expect(container.textContent).not.toContain('statement_id');
  });
});
