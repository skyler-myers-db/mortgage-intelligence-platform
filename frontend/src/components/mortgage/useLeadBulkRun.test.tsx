/**
 * @vitest-environment happy-dom
 *
 * useLeadBulkRun (audit tables-07, states-08 bulk half, #8): the loop that
 * used to show only "Approving…". Pinned here with a controllable `decide`
 * and fake timers:
 *
 *  - Stop is cooperative: it never aborts a POST on the wire, and the rows
 *    after that batch are reported not started (never sent).
 *  - A row that met an ended session stops the run and marks the partly
 *    recorded run for the session dialog.
 *  - A duplicate is skipped, not failed.
 *  - The ETA is the budget floor until a chunk has settled, then the slower
 *    of the floor and the observed pace.
 */
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { _resetSessionStatusForTests, getSessionStatus, markSessionExpired } from '../../lib/sessionStatus';
import {
  bulkRunMinutesLeft,
  formatMinutesLeft,
  useLeadBulkRun,
  type BulkRowReport,
  type BulkRunResult,
} from './useLeadBulkRun';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type Run = ReturnType<typeof useLeadBulkRun>;
let run: Run | null = null;

function Harness() {
  const current = useLeadBulkRun();
  useEffect(() => {
    run = current;
  });
  return null;
}

interface Pending {
  borrowerId: string;
  signal: AbortSignal;
  settle: (report: BulkRowReport) => void;
}

const IDS = Array.from({ length: 9 }, (_, index) => `B-AAAAAAAAAAAA${index + 1}`);

describe('useLeadBulkRun', () => {
  let container: HTMLDivElement;
  let root: Root;
  let pending: Pending[];
  const decide = vi.fn((borrowerId: string, signal: AbortSignal) => new Promise<BulkRowReport>((resolve) => {
    pending.push({ borrowerId, signal, settle: resolve });
  }));

  beforeEach(() => {
    vi.useFakeTimers({ now: new Date('2026-09-24T12:00:00Z') });
    _resetSessionStatusForTests();
    window.sessionStorage.clear();
    pending = [];
    decide.mockClear();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root.render(<Harness />));
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    run = null;
    _resetSessionStatusForTests();
    vi.useRealTimers();
  });

  function start(posts: 1 | 2 = 2): Promise<BulkRunResult | null> {
    let promise: Promise<BulkRunResult | null> = Promise.resolve(null);
    act(() => {
      promise = run!.start({
        kind: 'approve',
        rows: IDS.map((borrowerId) => ({ borrowerId, posts })),
        decide,
      });
    });
    return promise;
  }

  /** Settle the rows on the wire, in order, with these outcomes (default ok). */
  async function settleWire(outcomes: Array<BulkRowReport['outcome']> = []) {
    const wire = pending.splice(0, pending.length);
    await act(async () => {
      wire.forEach((row, index) => row.settle({ outcome: outcomes[index] ?? 'ok', message: null }));
      await Promise.resolve();
      await Promise.resolve();
    });
  }

  it('sends 3 at a time, shows k of N as each POST settles, and reports every row', async () => {
    const done = start();
    expect(decide).toHaveBeenCalledTimes(3);
    expect(run!.progress).toMatchObject({ total: 9, settled: 0, stopRequested: false });
    expect(run!.announcement).toBe('Approving 9 borrowers.');

    await act(async () => {
      pending.shift()!.settle({ outcome: 'ok', message: null });
      await Promise.resolve();
    });
    expect(run!.progress?.settled).toBe(1);
    expect(decide).toHaveBeenCalledTimes(3);

    await settleWire();
    await settleWire();
    await settleWire();
    const result = await done;

    expect(decide).toHaveBeenCalledTimes(9);
    expect(result).toMatchObject({ ok: 9, failed: [], skipped: [], notStarted: [], stopped: false });
    expect(run!.progress).toBeNull();
    expect(run!.announcement).toBe('9 of 9 approved.');
  });

  it('Stop after this batch never aborts a POST on the wire; the rest are reported not started', async () => {
    const done = start();
    const firstBatch = [...pending];
    act(() => run!.requestStop());

    expect(run!.progress?.stopRequested).toBe(true);
    expect(run!.announcement).toBe('Stop requested. The rows already sent will finish; the rest will not start.');
    expect(firstBatch.every((row) => !row.signal.aborted)).toBe(true);

    await settleWire();
    const result = await done;

    expect(decide).toHaveBeenCalledTimes(3);
    expect(firstBatch.every((row) => !row.signal.aborted)).toBe(true);
    expect(result).toMatchObject({ ok: 3, stopped: true, sessionEnded: false });
    expect(result?.notStarted).toEqual(IDS.slice(3));
    expect(run!.announcement).toBe('3 of 9 approved, 6 not started. Stopped.');
  });

  it('a row that met an ended session stops the run and marks a partly recorded bulk approval', async () => {
    markSessionExpired({ method: 'POST', path: '/api/v1/outreach/approve' });
    const done = start();
    await settleWire(['ok', 'session_expired', 'ok']);
    const result = await done;

    expect(decide).toHaveBeenCalledTimes(3);
    expect(result?.sessionEnded).toBe(true);
    expect(result?.failed).toEqual([{ borrowerId: IDS[1], outcome: 'session_expired', message: null }]);
    expect(result?.notStarted).toEqual(IDS.slice(3));
    expect(getSessionStatus().unrecorded).toBe('bulk_approval');
  });

  it('reports a duplicate as skipped, not failed, and lists failures with their reasons', async () => {
    const done = start();
    await act(async () => {
      pending[0].settle({ outcome: 'duplicate', message: null });
      pending[1].settle({ outcome: 'backend', message: 'Draft proof is stale.' });
      pending[2].settle({ outcome: 'network', message: 'Failed to fetch' });
      pending.splice(0, 3);
      await Promise.resolve();
      await Promise.resolve();
    });
    await settleWire();
    await settleWire();
    const result = await done;

    expect(result?.skipped).toEqual([{ borrowerId: IDS[0], outcome: 'duplicate', message: null }]);
    expect(result?.failed).toEqual([
      { borrowerId: IDS[1], outcome: 'backend', message: 'Draft proof is stale.' },
      { borrowerId: IDS[2], outcome: 'network', message: 'Failed to fetch' },
    ]);
    expect(result?.ok).toBe(6);
  });

  it('holds the synchronous latch: a second start while one runs sends nothing', async () => {
    const done = start();
    let second: BulkRunResult | null | undefined;
    await act(async () => {
      second = await run!.start({ kind: 'approve', rows: [{ borrowerId: 'B-ZZZZZZZZZZZZZ', posts: 2 }], decide });
    });
    expect(second).toBeNull();
    expect(decide).toHaveBeenCalledTimes(3);
    await settleWire();
    await settleWire();
    await settleWire();
    await done;
  });

  it('ETA: the budget floor first, then the slower of the floor and the observed pace', async () => {
    const done = start(2);
    // 9 unsampled approves = 18 POSTs over a 120/min budget.
    expect(run!.progress?.minutesLeft).toBeCloseTo(18 / 120);
    expect(formatMinutesLeft(run!.progress!.minutesLeft!)).toBe('about 1 min left');

    // The first batch takes a minute: 6 rows left at 20 s a row = 2 min.
    act(() => vi.advanceTimersByTime(60_000));
    await settleWire();
    expect(run!.progress?.minutesLeft).toBeCloseTo(2);
    expect(formatMinutesLeft(run!.progress!.minutesLeft!)).toBe('about 2 min left');
    await settleWire();
    await settleWire();
    await done;
  });

  it('never promises faster than the mutation budget, and counts a sampled row as one POST', () => {
    expect(bulkRunMinutesLeft([{ borrowerId: 'a', posts: 2 }], 3, 30)).toBeCloseTo(2 / 120);
    expect(bulkRunMinutesLeft(Array.from({ length: 240 }, (_, i) => ({ borrowerId: `${i}`, posts: 1 as const })), 0, 0)).toBe(2);
    expect(bulkRunMinutesLeft([], 3, 30)).toBeNull();
  });

  it('clears a clean result after a few seconds but keeps one with unsent rows until dismissed', async () => {
    let done = start();
    await settleWire();
    await settleWire();
    await settleWire();
    await done;
    expect(run!.result).not.toBeNull();
    act(() => vi.advanceTimersByTime(4000));
    expect(run!.result).toBeNull();

    done = start();
    act(() => run!.requestStop());
    await settleWire();
    await done;
    act(() => vi.advanceTimersByTime(10_000));
    expect(run!.result?.notStarted).toHaveLength(6);
    act(() => run!.dismissResult());
    expect(run!.result).toBeNull();
  });

  it('unmount aborts the POSTs on the wire and stashes the run for the next mount (R5-21)', async () => {
    const done = start();
    const firstBatch = [...pending];
    act(() => root.unmount());
    expect(firstBatch.every((row) => row.signal.aborted)).toBe(true);
    await act(async () => {
      firstBatch.forEach((row) => row.settle({ outcome: 'aborted', message: null }));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(await done).toBeNull();
    expect(JSON.parse(window.sessionStorage.getItem('mip.bulkApprove.lastCancelled') ?? '{}'))
      .toEqual(expect.objectContaining({ ok: 0, aborted: 9 }));
    root = createRoot(container);
    act(() => root.render(<Harness />));
  });
});
