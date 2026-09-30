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
 *  - The polite announcement says the start, each quarter once (the highest
 *    one a batch crossed) and the result.
 *  - Canary (D-approval-flow-a1 / -d): one row at a time until one comes back
 *    ok, then 3 at a time; a refusal before the first ok stops the run there
 *    and sends nothing else.
 *  - The run's kind names every line (approve / reject), the session dialog's
 *    mark and the unmount stash.
 */
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { _resetSessionStatusForTests, getSessionStatus, markSessionExpired } from '../../lib/sessionStatus';
import { readCancelledBulk } from './bulkApproveStash';
import {
  bulkCanaryNotice,
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

  function start(posts: 1 | 2 = 2, kind: 'approve' | 'reject' = 'approve'): Promise<BulkRunResult | null> {
    let promise: Promise<BulkRunResult | null> = Promise.resolve(null);
    act(() => {
      promise = run!.start({
        kind,
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

  /** Settle every row as it goes on the wire until the run is over. */
  async function drain(done: Promise<BulkRunResult | null>): Promise<BulkRunResult | null> {
    for (let round = 0; round < 12 && pending.length > 0; round += 1) await settleWire();
    return done;
  }

  it('sends one canary row, then 3 at a time once it came back ok, and reports every row', async () => {
    const done = start();
    expect(decide).toHaveBeenCalledTimes(1);
    expect(run!.progress).toMatchObject({ total: 9, settled: 0, stopRequested: false });
    expect(run!.announcement).toBe('Approving 9 borrowers.');

    await settleWire();
    expect(run!.progress?.settled).toBe(1);
    expect(decide).toHaveBeenCalledTimes(4);

    await act(async () => {
      pending.shift()!.settle({ outcome: 'ok', message: null });
      await Promise.resolve();
    });
    expect(run!.progress?.settled).toBe(2);
    expect(decide, 'the next chunk waits for this one').toHaveBeenCalledTimes(4);

    const result = await drain(done);

    expect(decide).toHaveBeenCalledTimes(9);
    expect(result).toMatchObject({ ok: 9, failed: [], skipped: [], notStarted: [], stopped: false, canary: null });
    expect(run!.progress).toBeNull();
    expect(run!.announcement).toBe('9 of 9 approved.');
  });

  it('a canary refusal stops the run: nothing else is sent, the rest are not started', async () => {
    const done = start();
    await act(async () => {
      pending.shift()!.settle({ outcome: 'backend', message: 'bulk_rationale failed the governed text policy' });
      await Promise.resolve();
      await Promise.resolve();
    });
    const result = await done;

    expect(decide).toHaveBeenCalledTimes(1);
    expect(result?.canary).toEqual({ borrowerId: IDS[0], message: 'bulk_rationale failed the governed text policy' });
    expect(result?.failed).toEqual([
      { borrowerId: IDS[0], outcome: 'backend', message: 'bulk_rationale failed the governed text policy' },
    ]);
    expect(result?.notStarted).toEqual(IDS.slice(1));
    expect(run!.announcement).toBe('0 of 9 approved, 1 failed, 8 not started. Nothing else was sent.');
    expect(bulkCanaryNotice(result!.canary!)).toBe(
      `Nothing else was sent: ${IDS[0]} was refused: bulk_rationale failed the governed text policy`,
    );
  });

  it('a duplicate canary is skipped and the next row becomes the canary', async () => {
    const done = start();
    await settleWire(['duplicate']);
    expect(decide).toHaveBeenCalledTimes(2);
    await settleWire(['ok']);
    expect(decide).toHaveBeenCalledTimes(5);
    const result = await drain(done);

    expect(result?.skipped).toEqual([{ borrowerId: IDS[0], outcome: 'duplicate', message: null }]);
    expect(result?.ok).toBe(8);
    expect(result?.canary).toBeNull();
  });

  it('Stop after this batch never aborts a POST on the wire; the rest are reported not started', async () => {
    const done = start();
    await settleWire();
    const wire = [...pending];
    expect(wire).toHaveLength(3);
    act(() => run!.requestStop());

    expect(run!.progress?.stopRequested).toBe(true);
    expect(run!.announcement).toBe('Stop requested. The rows already sent will finish; the rest will not start.');
    expect(wire.every((row) => !row.signal.aborted)).toBe(true);

    await settleWire();
    const result = await done;

    expect(decide).toHaveBeenCalledTimes(4);
    expect(wire.every((row) => !row.signal.aborted)).toBe(true);
    expect(result).toMatchObject({ ok: 4, stopped: true, sessionEnded: false });
    expect(result?.notStarted).toEqual(IDS.slice(4));
    expect(run!.announcement).toBe('4 of 9 approved, 5 not started. Stopped.');
  });

  it('a row that met an ended session stops the run and marks a partly recorded bulk approval', async () => {
    markSessionExpired({ method: 'POST', path: '/api/v1/outreach/approve' });
    const done = start();
    await settleWire(['ok']);
    await settleWire(['session_expired', 'ok', 'ok']);
    const result = await done;

    expect(decide).toHaveBeenCalledTimes(4);
    expect(result?.sessionEnded).toBe(true);
    expect(result?.failed).toEqual([{ borrowerId: IDS[1], outcome: 'session_expired', message: null }]);
    expect(result?.notStarted).toEqual(IDS.slice(4));
    expect(getSessionStatus().unrecorded).toBe('bulk_approval');
  });

  it('reports a duplicate as skipped, not failed, and lists failures with their reasons', async () => {
    const done = start();
    await settleWire(['ok']);
    await act(async () => {
      pending[0].settle({ outcome: 'duplicate', message: null });
      pending[1].settle({ outcome: 'backend', message: 'Draft proof is stale.' });
      pending[2].settle({ outcome: 'network', message: 'Failed to fetch' });
      pending.splice(0, 3);
      await Promise.resolve();
      await Promise.resolve();
    });
    const result = await drain(done);

    expect(result?.skipped).toEqual([{ borrowerId: IDS[1], outcome: 'duplicate', message: null }]);
    expect(result?.failed).toEqual([
      { borrowerId: IDS[2], outcome: 'backend', message: 'Draft proof is stale.' },
      { borrowerId: IDS[3], outcome: 'network', message: 'Failed to fetch' },
    ]);
    expect(result?.ok).toBe(6);
    expect(result?.canary, 'refusals after the first ok are not a canary stop').toBeNull();
  });

  it('announces the start, each quarter once, and the result last', async () => {
    const twelve = Array.from({ length: 12 }, (_, index) => `B-QUARTER${String(index + 1).padStart(6, '0')}`);
    let done: Promise<BulkRunResult | null> = Promise.resolve(null);
    act(() => {
      done = run!.start({ kind: 'approve', rows: twelve.map((borrowerId) => ({ borrowerId, posts: 2 })), decide });
    });
    expect(run!.announcement).toBe('Approving 12 borrowers.');

    await settleWire();
    expect(run!.announcement, 'the canary alone crosses no quarter').toBe('Approving 12 borrowers.');
    await settleWire();
    expect(run!.announcement).toBe('25% done: 4 of 12.');
    await settleWire();
    expect(run!.announcement).toBe('50% done: 7 of 12.');
    await settleWire();
    expect(run!.announcement).toBe('75% done: 10 of 12.');
    await settleWire();
    await done;
    // 100% is the result itself, not a fourth quarter.
    expect(run!.announcement).toBe('12 of 12 approved.');

    // A batch that crosses several quarters at once says only the highest
    // one (4 of 5 is past 25%, 50% and 75%).
    const five = twelve.slice(0, 5);
    act(() => {
      done = run!.start({ kind: 'approve', rows: five.map((borrowerId) => ({ borrowerId, posts: 2 })), decide });
    });
    await settleWire();
    await settleWire();
    expect(run!.announcement).toBe('75% done: 4 of 5.');
    await settleWire();
    await done;
    expect(run!.announcement).toBe('5 of 5 approved.');
  });

  it('a reject run says Rejecting and rejected, costs one POST a row, and marks a partly recorded bulk rejection', async () => {
    markSessionExpired({ method: 'POST', path: '/api/v1/outreach/reject' });
    const done = start(1, 'reject');
    expect(run!.announcement).toBe('Rejecting 9 borrowers.');
    expect(run!.progress).toMatchObject({ kind: 'reject' });
    // 9 rejects = 9 POSTs over a 120/min budget.
    expect(run!.progress?.minutesLeft).toBeCloseTo(9 / 120);
    await settleWire(['ok']);
    await settleWire(['ok', 'session_expired', 'ok']);
    const result = await done;

    expect(result?.kind).toBe('reject');
    expect(run!.announcement).toBe('3 of 9 rejected, 1 failed, 5 not started. The session ended.');
    expect(getSessionStatus().unrecorded).toBe('bulk_rejection');
  });

  it('holds the synchronous latch: a second start while one runs sends nothing', async () => {
    const done = start();
    let second: BulkRunResult | null | undefined;
    await act(async () => {
      second = await run!.start({ kind: 'approve', rows: [{ borrowerId: 'B-ZZZZZZZZZZZZZ', posts: 2 }], decide });
    });
    expect(second).toBeNull();
    expect(decide).toHaveBeenCalledTimes(1);
    await drain(done);
  });

  it('ETA: the budget floor first, then the slower of the floor and the observed pace', async () => {
    const done = start(2);
    // 9 unsampled approves = 18 POSTs over a 120/min budget.
    expect(run!.progress?.minutesLeft).toBeCloseTo(18 / 120);
    expect(formatMinutesLeft(run!.progress!.minutesLeft!)).toBe('about 1 min left');

    // The canary takes 15 s: 8 rows left at 15 s a row = 2 min.
    act(() => vi.advanceTimersByTime(15_000));
    await settleWire();
    expect(run!.progress?.minutesLeft).toBeCloseTo(2);
    expect(formatMinutesLeft(run!.progress!.minutesLeft!)).toBe('about 2 min left');
    await drain(done);
  });

  it('never promises faster than the mutation budget, and counts a sampled row as one POST', () => {
    expect(bulkRunMinutesLeft([{ borrowerId: 'a', posts: 2 }], 3, 30)).toBeCloseTo(2 / 120);
    expect(bulkRunMinutesLeft(Array.from({ length: 240 }, (_, i) => ({ borrowerId: `${i}`, posts: 1 as const })), 0, 0)).toBe(2);
    expect(bulkRunMinutesLeft([], 3, 30)).toBeNull();
  });

  it('clears a clean result after a few seconds but keeps one with unsent rows until dismissed', async () => {
    let done = start();
    await drain(done);
    expect(run!.result).not.toBeNull();
    act(() => vi.advanceTimersByTime(4000));
    expect(run!.result).toBeNull();

    done = start();
    act(() => run!.requestStop());
    await settleWire();
    await done;
    act(() => vi.advanceTimersByTime(10_000));
    expect(run!.result?.notStarted).toHaveLength(8);
    act(() => run!.dismissResult());
    expect(run!.result).toBeNull();
  });

  it('unmount aborts the POSTs on the wire and stashes the run and its kind for the next mount (R5-21)', async () => {
    const done = start(1, 'reject');
    const wire = [...pending];
    act(() => root.unmount());
    expect(wire.every((row) => row.signal.aborted)).toBe(true);
    await act(async () => {
      wire.forEach((row) => row.settle({ outcome: 'aborted', message: null }));
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(await done).toBeNull();
    expect(JSON.parse(window.sessionStorage.getItem('mip.bulkApprove.lastCancelled') ?? '{}'))
      .toEqual(expect.objectContaining({ ok: 0, aborted: 9, kind: 'reject' }));
    expect(readCancelledBulk()).toEqual({ ok: 0, aborted: 9, kind: 'reject' });
    root = createRoot(container);
    act(() => root.render(<Harness />));
  });

  it('reads a stash written before bulk Reject existed as an approve run', () => {
    window.sessionStorage.setItem('mip.bulkApprove.lastCancelled', JSON.stringify({ ok: 2, aborted: 1, ts: Date.now() }));
    expect(readCancelledBulk()).toEqual({ ok: 2, aborted: 1, kind: 'approve' });
  });
});
