/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../lib/apiTransport';
import type { WarmingUpState } from '../../lib/useWarmingUpRetry';
import { AsyncState, AsyncStatus, preloadAsyncFailure, type AsyncQuery } from './AsyncState';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const healthMocks = vi.hoisted(() => ({
  ctx: null as null | { health: { dependencies?: Record<string, string>; circuit_breakers?: Record<string, string> }; connection: string },
}));
vi.mock('../HealthProvider', () => ({ useOptionalHealth: () => healthMocks.ctx }));
const toastMocks = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('../../lib/toast', () => ({ toast: toastMocks }));

/**
 * The shared loading / warming / error / empty surface (audit states-04
 * slice 2, states-03 a, states-v2, states-08 part 3), rendered for real.
 */

const T0 = new Date('2026-09-25T12:00:00Z').getTime();
const WAREHOUSE_DOWN = { health: { dependencies: { warehouse: 'down', lakebase: 'up', genie: 'up' } }, connection: 'online' };
const SENTINEL = 'SENTINEL server detail 500 Internal Server Error';

function query<T>(overrides: Partial<AsyncQuery<T>> = {}): AsyncQuery<T> {
  return {
    data: null,
    warmingUp: null,
    error: null,
    manualRetry: vi.fn(),
    isFetching: false,
    isPlaceholderData: false,
    errorUpdatedAt: null,
    ...overrides,
  };
}

function warming(): WarmingUpState {
  return { dependency: 'warehouse', label: 'Warehouse warming up', attempt: 1, maxAttempts: 6, correlationId: null, intervalMs: 5_000 };
}

function outage(reason = 'retries_exhausted'): ApiError {
  return new ApiError(SENTINEL, { path: '/api/v1/leads', status: 503, retryable: true, dependency: 'warehouse', reason, correlationId: 'corr-7' });
}

let root: Root;

// The red callout is its own chunk (loaded on the first failure); load it up
// front so every render below is synchronous.
beforeAll(async () => {
  await preloadAsyncFailure();
});

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(T0);
  healthMocks.ctx = null;
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
});

afterEach(() => {
  act(() => root.unmount());
  document.body.innerHTML = '';
  vi.useRealTimers();
  vi.clearAllMocks();
});

const text = () => document.body.textContent ?? '';
const alerts = () => document.querySelectorAll('[role="alert"]');
const retry = () => document.querySelector<HTMLButtonElement>('button[aria-label="Retry loading ranked borrowers"]');

function renderState(q: AsyncQuery<string[]>, onClearFilters?: () => void) {
  act(() => {
    root.render(
      <AsyncState
        query={q}
        subject="Ranked borrowers"
        loading={<div data-testid="loading" />}
        isEmpty={(rows) => rows.length === 0}
        empty={<div data-testid="empty" />}
        onClearFilters={onClearFilters}
      >
        {(rows) => <div data-testid="rows">{rows.join(',')}</div>}
      </AsyncState>,
    );
  });
}

const has = (testId: string) => document.querySelector(`[data-testid="${testId}"]`) !== null;

describe('AsyncState precedence', () => {
  it('renders the rows with an error callout above them (a failed refetch keeps its rows)', () => {
    renderState(query({ data: ['a', 'b'], error: new ApiError(SENTINEL, { path: '/x', status: 500 }) }));
    expect(has('rows')).toBe(true);
    expect(alerts()).toHaveLength(1);
    expect(alerts()[0].compareDocumentPosition(document.querySelector('[data-testid="rows"]') as Node) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('shows the empty state ONLY for a settled, measured zero', () => {
    renderState(query({ data: [] }));
    expect(has('empty')).toBe(true);
    expect(has('loading')).toBe(false);
  });

  it.each<[string, Partial<AsyncQuery<string[]>>, string | null]>([
    ['warming', { data: [], warmingUp: warming() }, 'loading'],
    ['erroring', { data: [], error: new ApiError(SENTINEL, { path: '/x', status: 500 }) }, null],
    ['a placeholder zero (the previous filters)', { data: [], isPlaceholderData: true }, 'loading'],
  ])('never shows the empty state while %s', (_label, overrides, slot) => {
    renderState(query<string[]>(overrides));
    expect(has('empty')).toBe(false);
    expect(has('rows')).toBe(false);
    if (slot) expect(has(slot)).toBe(true);
  });

  it('keeps the skeleton under the warming block with no payload yet', () => {
    renderState(query({ warmingUp: warming() }));
    expect(document.querySelector('[data-testid="warming-up-block"]')?.textContent).toContain('Ranked borrowers loading');
    expect(has('loading')).toBe(true);
  });

  it('keeps the skeleton when the banner suppresses the warming block', () => {
    healthMocks.ctx = WAREHOUSE_DOWN;
    renderState(query({ warmingUp: warming() }));
    expect(document.querySelector('[data-testid="warming-up-block"]')).toBeNull();
    expect(has('loading')).toBe(true);
  });

  it('shows only the error surface when the first load failed', () => {
    renderState(query({ error: new ApiError(SENTINEL, { path: '/x', status: 500 }) }));
    expect(alerts()).toHaveLength(1);
    expect(has('loading')).toBe(false);
  });

  it('shows the skeleton on a first load', () => {
    renderState(query());
    expect(has('loading')).toBe(true);
    expect(text()).toBe('');
  });
});

describe('AsyncStatus: bannered vs red', () => {
  it('a matching outage under the banner is a calm status, never an alert, with a secondary Retry', () => {
    healthMocks.ctx = WAREHOUSE_DOWN;
    const q = query({ error: outage() });
    act(() => root.render(<AsyncStatus query={q} subject="Ranked borrowers" />));
    const status = document.querySelector('[data-async-status="bannered"]');
    expect(status?.getAttribute('role')).toBe('status');
    expect(status?.textContent).toContain('Ranked borrowers This panel reloads when the analytics warehouse reconnects.');
    expect(alerts()).toHaveLength(0);
    act(() => retry()?.click());
    expect(q.manualRetry).toHaveBeenCalledTimes(1);
  });

  it('a 403 under the same banner stays red with the forbidden copy', () => {
    healthMocks.ctx = WAREHOUSE_DOWN;
    act(() => root.render(<AsyncStatus query={query({ error: new ApiError(SENTINEL, { path: '/x', status: 403, dependency: 'warehouse' }) })} subject="Ranked borrowers" />));
    expect(alerts()).toHaveLength(1);
    expect(alerts()[0].textContent).toContain("Your role can't open ranked borrowers.");
    expect(retry()).toBeNull();
  });

  it('the same outage with no banner is red, with Retry and the copyable reference', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    const q = query({ error: outage() });
    act(() => root.render(<AsyncStatus query={q} subject="Ranked borrowers" />));
    expect(alerts()[0].textContent).toContain('The analytics warehouse is unavailable.');
    expect(document.querySelector('[data-testid="async-status-reference"]')?.textContent).toBe('corr-7');
    const copy = [...document.querySelectorAll('button')].find((button) => button.textContent === 'Copy');
    await act(async () => {
      copy?.click();
      await Promise.resolve();
    });
    expect(writeText).toHaveBeenCalledWith('corr-7');
    expect(toastMocks.success).toHaveBeenCalledWith('Reference copied', { detail: null });
  });

  it('names Retry with the subject as a sentence reads it, keeping an acronym', () => {
    act(() => root.render(<AsyncStatus query={query({ error: new ApiError(SENTINEL, { path: '/x', status: 500 }) })} subject="Portfolio KPIs" />));
    expect(document.querySelector('button[aria-label="Retry loading portfolio KPIs"]')).not.toBeNull();
  });

  it('never renders the server message', () => {
    act(() => root.render(<AsyncStatus query={query({ error: new ApiError(SENTINEL, { path: '/x', status: 500 }) })} subject="Ranked borrowers" />));
    expect(text()).not.toContain('SENTINEL');
    expect(text()).not.toMatch(/Internal Server Error/);
  });

  it('renders nothing for an aborted request', () => {
    act(() => root.render(<AsyncStatus query={query({ error: new ApiError('request aborted', { path: '/x', aborted: true }) })} subject="Ranked borrowers" />));
    expect(text()).toBe('');
  });

  it('offers Clear filters for a 422 when the route can clear them', () => {
    const clear = vi.fn();
    act(() => root.render(<AsyncStatus query={query({ error: new ApiError(SENTINEL, { path: '/x', status: 422 }) })} subject="Ranked borrowers" onClearFilters={clear} />));
    const button = document.querySelector<HTMLButtonElement>('button[aria-label="Clear the invalid filters"]');
    act(() => button?.click());
    expect(clear).toHaveBeenCalledTimes(1);
  });
});

describe('AsyncStatus: a 429 counts its wait down', () => {
  it('keeps Retry aria-disabled (not native disabled) for the whole wait, then one click = one retry', async () => {
    const q = query({
      error: new ApiError(SENTINEL, { path: '/x', status: 429, retryable: true, reason: 'rate_limited', retryAfterMs: 12_000 }),
      errorUpdatedAt: T0,
    });
    act(() => root.render(<AsyncStatus query={q} subject="Ranked borrowers" />));
    const visible = () => (document.querySelector('.async-status__wait [aria-hidden="true"]')?.textContent ?? '');
    expect(text()).toContain('Try again in');
    expect(visible()).toBe('12 s');
    expect(retry()?.getAttribute('aria-disabled')).toBe('true');
    expect(retry()?.disabled).toBe(false);
    act(() => retry()?.click());
    expect(q.manualRetry).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(11_000);
    });
    expect(visible()).toBe('1 s');
    act(() => retry()?.click());
    expect(q.manualRetry).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(retry()?.hasAttribute('aria-disabled')).toBe(false);
    expect(text()).not.toContain('Try again in');
    act(() => retry()?.click());
    expect(q.manualRetry).toHaveBeenCalledTimes(1);
  });
});
