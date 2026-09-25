/**
 * @vitest-environment happy-dom
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { QUEUE_VERSION_SERVER_TTL_MS } from '../lib/queueVersion';
import { useLeadQueueFreshness } from './lead-queue.freshness';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * The Lead Queue's freshness (audit states-09), driven through a real
 * QueryClient and the real queue-version poll with `fetch` stubbed: the
 * baseline, another actor's change, a reader's own write, and that a version
 * change NEVER re-reads the leads by itself (a VIEW_LEADS audit row).
 *
 * The stub replays the backend's cache (workspace_queue_version.py: a
 * process-local TTLCache): a read answers the ledgers as of the last fill
 * until QUEUE_VERSION_SERVER_TTL_MS have passed since that fill, so a read
 * right after a write can be the PRE-write version, as it is in production.
 */

const V1 = '1'.repeat(32);
const V2 = '2'.repeat(32);
const V3 = '3'.repeat(32);
const T0 = new Date('2026-09-25T12:00:00Z').getTime();

let root: Root;
let queryClient: QueryClient;
/** The decision ledgers' current version (a write moves it before it returns). */
let ledger = V1;
/** The server cache: the version as of its last fill, and when that fill was. */
let served: { version: string; filledAt: number } | null = null;
let versionReads = 0;
/** When (ms after T0) each version read reached the server. */
let readTimes: number[] = [];
let otherPaths: string[] = [];

/** One read of the server: a fill when the cache is empty or a full TTL old (TTLCache: now >= expires_at). */
function serverRead(): string {
  const now = Date.now();
  if (served === null || now >= served.filledAt + QUEUE_VERSION_SERVER_TTL_MS) {
    served = { version: ledger, filledAt: now };
  }
  return served.version;
}

function Harness(props: { enabled: boolean; dataUpdatedAt: number | null; onRefresh: () => void }) {
  const node = useLeadQueueFreshness({ ...props, isFetching: false });
  return <div data-testid="slot">{node}</div>;
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(T0);
  ledger = V1;
  served = null;
  versionReads = 0;
  readTimes = [];
  otherPaths = [];
  vi.stubGlobal('fetch', async (path: string) => {
    if (path === '/api/v1/workspace/queue-version') {
      versionReads += 1;
      readTimes.push(Date.now() - T0);
      return new Response(JSON.stringify({ version: serverRead() }), { status: 200, headers: { 'Content-Type': 'application/json' } });
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

/** Advance the fake clock to `T0 + at` ms. */
async function advanceTo(at: number) {
  await advance(Math.max(0, T0 + at - Date.now()));
}

const pill = () => document.querySelector('[data-testid="queue-updated"]');
const fetchedAt = () => document.querySelector('[data-testid="fetched-at"]');
const refresh = () => document.querySelector<HTMLButtonElement>('button[aria-label="Refresh ranked borrowers"]');
const announcement = () => document.querySelector('[role="status"]')?.textContent ?? '';

/** A successful approve from this tab: the write commits (the ledger moves) before it returns. */
async function ownApprove() {
  ledger = ledger === V1 ? V2 : V3;
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
    ledger = V2;
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
    ledger = V2;
    await advance(60_000);
    act(() => refresh()?.click());
    expect(onRefresh).toHaveBeenCalledTimes(1);
    // The leads query settles anew (a new dataUpdatedAt): the pill goes.
    await render({ dataUpdatedAt: T0 + 61_000, onRefresh });
    expect(pill()).toBeNull();
    expect(fetchedAt()).not.toBeNull();
  });

  // The server cache was filled by the poll at t=0; an own approve at each
  // offset. Across the next two polls the reader's own write never reads as
  // a change, and the write is re-read ONCE, a server TTL (+1 s) after it.
  it.each([0, 10_000, 29_000, 45_000])('never shows the pill for an own approve %i ms after a poll, across two polls', async (offset) => {
    const onRefresh = await render();
    expect(readTimes).toEqual([0]);
    await advanceTo(offset);
    await ownApprove();
    const writeAt = Date.now() - T0;
    await advance(1);
    expect(readTimes, 'no immediate re-read: the server would answer from its pre-write cache').toEqual([0]);
    for (const at of [60_001, 90_000, 120_001, 150_000, 180_001]) {
      await advanceTo(at);
      expect(pill(), `no pill at t=${at} ms`).toBeNull();
    }
    // The one own-write re-read is due a server TTL (+1 s) after the write;
    // nothing but the minute poll (t=60 s) reads before it.
    expect(readTimes).toContain(writeAt + 31_000);
    expect(readTimes.filter((t) => t > writeAt && t < writeAt + 31_000 && t !== 60_000)).toEqual([]);
    expect(readTimes.filter((t) => t > writeAt).length, 'two polls and the re-read at least').toBeGreaterThanOrEqual(3);
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it('never adopts a pre-write answer a warm server cache gives a poll right after the write', async () => {
    // Another reader (another tab, another worker's traffic) fills the cache
    // at t=40 s; this tab approves at t=45 s; its poll at t=60 s is answered
    // from that fill (the PRE-write version) although it was sent after the
    // write. Only a read asked for a full TTL after the write may absorb it.
    await render();
    await advanceTo(40_000);
    served = null;
    serverRead();
    await advanceTo(45_000);
    await ownApprove();
    for (const at of [60_001, 90_000, 120_001, 150_000, 180_001]) {
      await advanceTo(at);
      expect(pill(), `no pill at t=${at} ms`).toBeNull();
    }
  });

  it('a Refresh while an own write is pending keeps it pending', async () => {
    const onRefresh = await render();
    await advanceTo(10_000);
    await ownApprove();
    await advanceTo(15_000);
    // The rows settle anew while the version on hand is still pre-write.
    await render({ dataUpdatedAt: T0 + 15_000, onRefresh });
    for (const at of [60_001, 120_001]) {
      await advanceTo(at);
      expect(pill(), `no pill at t=${at} ms`).toBeNull();
    }
  });

  it('a bulk run of own writes re-reads the version once, a TTL after the last', async () => {
    await render();
    await advanceTo(5_000);
    await ownApprove();
    await advanceTo(8_000);
    await ownApprove();
    await advanceTo(12_000);
    await ownApprove();
    await advanceTo(12_000 + 31_000 - 1);
    expect(versionReads).toBe(1);
    await advance(2);
    expect(versionReads).toBe(2);
    await advanceTo(60_001);
    expect(pill()).toBeNull();
  });

  it('counts a queue write made just before the queue mounted as its own', async () => {
    // An approve on Borrower 360, then back to the queue: the server cache
    // (filled at t=-5 s) still answers the pre-write version at mount.
    vi.setSystemTime(T0 - 5_000);
    serverRead();
    vi.setSystemTime(T0 - 3_000);
    await ownApprove();
    vi.setSystemTime(T0);
    await render();
    for (const at of [60_001, 120_001]) {
      await advanceTo(at);
      expect(pill(), `no pill at t=${at} ms`).toBeNull();
    }
  });

  it('still shows another actor\'s later change after an own write', async () => {
    await render();
    await ownApprove();
    await advanceTo(45_000);
    ledger = V3;
    await advanceTo(120_001);
    expect(pill()).not.toBeNull();
  });

  it('polls nothing while the rows are not settled', async () => {
    await render({ enabled: false });
    await advance(180_000);
    expect(versionReads).toBe(0);
    expect(pill()).toBeNull();
  });
});
