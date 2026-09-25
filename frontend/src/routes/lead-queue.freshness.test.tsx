/**
 * @vitest-environment happy-dom
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useLeadQueueFreshness } from './lead-queue.freshness';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * The Lead Queue's freshness (audit states-09), driven through a real
 * QueryClient and the real queue-version poll with `fetch` stubbed: the
 * baseline, another actor's change, a reader's own write, and that a version
 * change NEVER re-reads the leads by itself (a VIEW_LEADS audit row).
 */

const V1 = '1'.repeat(32);
const V2 = '2'.repeat(32);
const T0 = new Date('2026-09-25T12:00:00Z').getTime();

let root: Root;
let queryClient: QueryClient;
let serverVersion = V1;
let versionReads = 0;
let otherPaths: string[] = [];

function Harness(props: { enabled: boolean; dataUpdatedAt: number | null; onRefresh: () => void }) {
  const node = useLeadQueueFreshness({ ...props, isFetching: false });
  return <div data-testid="slot">{node}</div>;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(T0);
  serverVersion = V1;
  versionReads = 0;
  otherPaths = [];
  vi.stubGlobal('fetch', async (path: string) => {
    if (path === '/api/v1/workspace/queue-version') {
      versionReads += 1;
      return new Response(JSON.stringify({ version: serverVersion }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    }
    otherPaths.push(path);
    throw new Error(`unexpected request ${path}`);
  });
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
  queryClient = new QueryClient();
});

afterEach(() => {
  act(() => root.unmount());
  queryClient.clear();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  document.body.innerHTML = '';
});

async function render(props: { enabled?: boolean; dataUpdatedAt?: number | null; onRefresh?: () => void } = {}) {
  const full = { enabled: true, dataUpdatedAt: T0, onRefresh: vi.fn(), ...props };
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <Harness {...full} />
      </QueryClientProvider>,
    );
  });
  await advance(1);
  return full.onRefresh;
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

const pill = () => document.querySelector('[data-testid="queue-updated"]');
const fetchedAt = () => document.querySelector('[data-testid="fetched-at"]');
const refresh = () => document.querySelector<HTMLButtonElement>('button[aria-label="Refresh ranked borrowers"]');
const announcement = () => document.querySelector('[role="status"]')?.textContent ?? '';

async function ownApprove() {
  await act(async () => {
    await queryClient.getMutationCache()
      .build(queryClient, { mutationKey: ['mip', 'outreach', 'approve'], mutationFn: async () => ({ approved: true }) })
      .execute(undefined);
  });
}

describe('useLeadQueueFreshness', () => {
  it('shows when the rows were fetched, with Refresh, while the version is unchanged', async () => {
    await render();
    expect(versionReads).toBe(1);
    expect(fetchedAt()?.textContent).toContain('Fetched');
    expect(pill()).toBeNull();
    await advance(60_000);
    expect(versionReads).toBe(2);
    expect(pill()).toBeNull();
  });

  it('shows "Queue updated · Refresh" (a status, never an alert) when another actor changes the queue', async () => {
    const onRefresh = await render();
    serverVersion = V2;
    await advance(60_000);
    expect(pill()?.textContent).toContain('Queue updated');
    expect(fetchedAt()).toBeNull();
    expect(announcement()).toBe('The queue was updated. Refresh to read the current queue.');
    expect(document.querySelector('[role="alert"]')).toBeNull();
    // A version change NEVER re-reads the leads by itself.
    expect(onRefresh).not.toHaveBeenCalled();
    expect(otherPaths).toEqual([]);
  });

  it('Refresh is the explicit re-read: one call, and the settled rows re-baseline', async () => {
    const onRefresh = await render();
    serverVersion = V2;
    await advance(60_000);
    act(() => refresh()?.click());
    expect(onRefresh).toHaveBeenCalledTimes(1);
    // The leads query settles anew (a new dataUpdatedAt): the pill goes.
    await render({ dataUpdatedAt: T0 + 61_000, onRefresh });
    expect(pill()).toBeNull();
    expect(fetchedAt()).not.toBeNull();
  });

  it('never shows the pill for the reader\'s own approval in this tab', async () => {
    const onRefresh = await render();
    expect(versionReads).toBe(1);
    serverVersion = V2; // the approval moved the version
    await ownApprove();
    await advance(1);
    // The own write triggers one more version read, which becomes the baseline.
    expect(versionReads).toBe(2);
    await advance(60_000);
    expect(pill()).toBeNull();
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it('still shows another actor\'s later change after an own write', async () => {
    await render();
    serverVersion = V2;
    await ownApprove();
    await advance(1);
    serverVersion = '3'.repeat(32);
    await advance(60_000);
    expect(pill()).not.toBeNull();
  });

  it('polls nothing while the rows are not settled', async () => {
    await render({ enabled: false });
    await advance(180_000);
    expect(versionReads).toBe(0);
    expect(pill()).toBeNull();
  });
});
