/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AsyncQuery } from './AsyncState';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// The DegradedBanner already names the warehouse outage.
vi.mock('../HealthProvider', () => ({
  useOptionalHealth: () => ({ health: { dependencies: { warehouse: 'down', lakebase: 'up', genie: 'up' } }, connection: 'online' }),
}));
vi.mock('../../lib/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

/**
 * AsyncStatus before its failure chunk has loaded, and when it cannot load:
 * a neutral role=status line holds the slot, never an alert under a banner
 * and never the server's text. (AsyncState.test preloads the chunk; this
 * file must not.)
 */

const SENTINEL = 'SENTINEL server detail 503 Service Unavailable';

/**
 * A fresh AsyncStatus and a warehouse outage built from the SAME module graph
 * (describeApiError and the banner rule check `instanceof ApiError`).
 */
async function freshOutage() {
  const [{ AsyncStatus }, { ApiError }] = await Promise.all([import('./AsyncState'), import('../../lib/apiTransport')]);
  const query: AsyncQuery<unknown> = {
    data: null,
    warmingUp: null,
    error: new ApiError(SENTINEL, { path: '/api/v1/leads', status: 503, retryable: true, dependency: 'warehouse', reason: 'retries_exhausted' }),
    manualRetry: vi.fn(),
    isFetching: false,
    isPlaceholderData: false,
    errorUpdatedAt: null,
  };
  return { AsyncStatus, query };
}

let root: Root;

// Transform the failure chunk's module graph once, up front (a loaded machine
// can take many seconds), then drop the instances so every test below starts
// with AsyncState's loader empty.
beforeAll(async () => {
  await import('./AsyncFailure');
  vi.resetModules();
}, 60_000);

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
});

afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = '';
  vi.doUnmock('./AsyncFailure');
  vi.resetModules();
});

const pending = () => document.querySelector('[data-async-status="pending"]');
const text = () => document.body.textContent ?? '';

async function flush(): Promise<void> {
  await act(async () => {
    await vi.dynamicImportSettled();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

describe('AsyncStatus while its failure chunk is not loaded', { timeout: 30_000 }, () => {
  it('holds the slot with a neutral status, then renders the calm bannered status', async () => {
    const { AsyncStatus, query } = await freshOutage();
    act(() => root.render(<AsyncStatus query={query} subject="Ranked borrowers" />));

    expect(pending()?.getAttribute('role')).toBe('status');
    expect(pending()?.textContent).toBe('Ranked borrowers could not load.');
    expect(document.querySelectorAll('[role="alert"]')).toHaveLength(0);
    expect(text()).not.toContain('SENTINEL');

    await flush();
    expect(pending()).toBeNull();
    expect(document.querySelector('[data-async-status="bannered"]')?.textContent).toContain(
      'This panel reloads when the analytics warehouse reconnects.',
    );
    expect(document.querySelectorAll('[role="alert"]')).toHaveLength(0);
  });

  it('keeps the neutral status when the chunk cannot load (a stale deploy)', async () => {
    vi.resetModules();
    vi.doMock('./AsyncFailure', () => {
      throw new Error('Failed to fetch dynamically imported module');
    });
    const { AsyncStatus, query } = await freshOutage();
    act(() => root.render(<AsyncStatus query={query} subject="Ranked borrowers" />));
    await flush();

    expect(pending()?.getAttribute('role')).toBe('status');
    expect(pending()?.textContent).toBe('Ranked borrowers could not load.');
    expect(document.querySelectorAll('[role="alert"]')).toHaveLength(0);
    expect(text()).not.toContain('SENTINEL');
    expect(text()).not.toContain('Failed to fetch');
  });
});
