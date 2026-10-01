/**
 * The persisted aggregate cache (lib/queryPersist, audit delivery-05) never
 * brings back a restored value over a read that has already failed: the W5b
 * R1 ruling (a refresh that fails over restored data shows the failure, or a
 * marked stale value, never the snapshot as if it were current).
 *
 * The restore is lazy (it waits for the actor gate and its own chunk), so a
 * hero read can fail BEFORE it lands. Hydrate is newer-wins and a failed
 * first read has no data, so without a screen the restored success replaced
 * the error and no later refresh ever settled it. Node environment with a
 * stubbed `window` (a Map-backed sessionStorage), as queryPersist.test.ts.
 */
import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ACTOR_A } from '../test/actorKeys';
import { NOBODY, _resetActorScopeForTests } from './actorScope';
import {
  QUERY_CACHE_KEY,
  QUERY_CACHE_SAVE_THROTTLE_MS,
  _resetQueryPersistenceForTests,
  startQueryPersistence,
} from './queryPersist';
import { queryKeys } from './queryKeys';

const BUSTER = 'index-AbCd1234.js';
const T0 = Date.parse('2026-09-30T08:00:00Z');
const SNAPSHOT = { total: 12_480 };

let session: Map<string, string>;
/** Called when the restore reads the snapshot (the moment it starts landing). */
let onSnapshotRead: (() => void) | null;

beforeEach(() => {
  session = new Map();
  onSnapshotRead = null;
  const storage = (values: Map<string, string>) => ({
    getItem: (key: string) => {
      if (key.endsWith(QUERY_CACHE_KEY)) onSnapshotRead?.();
      return values.get(key) ?? null;
    },
    setItem: (key: string, value: string) => {
      values.set(key, value);
    },
    removeItem: (key: string) => {
      values.delete(key);
    },
  });
  vi.stubGlobal('window', { localStorage: storage(new Map()), sessionStorage: storage(session) });
  _resetActorScopeForTests({ status: 'open', owner: ACTOR_A });
  _resetQueryPersistenceForTests();
  vi.useFakeTimers();
  vi.setSystemTime(T0);
});

afterEach(() => {
  vi.useRealTimers();
  _resetActorScopeForTests({ status: 'open', owner: NOBODY });
  vi.unstubAllGlobals();
});

const client = () => new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
const failing = () => Promise.reject(new Error('warehouse warming up'));

/** A tab that read the Home summary and saved it; then a reload, one minute later. */
async function savedThenReloaded(): Promise<QueryClient> {
  const first = client();
  await startQueryPersistence(first, BUSTER);
  first.setQueryData(queryKeys.homeSummary(), SNAPSHOT);
  await vi.advanceTimersByTimeAsync(QUERY_CACHE_SAVE_THROTTLE_MS);
  expect(session.size, 'precondition: a snapshot was saved').toBeGreaterThan(0);
  vi.setSystemTime(T0 + 60_000);
  _resetQueryPersistenceForTests();
  return client();
}

describe('a read that failed before the snapshot restore landed', () => {
  it('keeps its error: the restored figures never come back as a success', async () => {
    const reloaded = await savedThenReloaded();
    await reloaded.fetchQuery({ queryKey: queryKeys.homeSummary(), queryFn: failing }).catch(() => undefined);
    expect(reloaded.getQueryState(queryKeys.homeSummary())?.status, 'precondition: failed first').toBe('error');

    await startQueryPersistence(reloaded, BUSTER);
    await vi.advanceTimersByTimeAsync(0);

    const state = reloaded.getQueryState(queryKeys.homeSummary());
    expect(state?.status).toBe('error');
    expect(state?.data, 'the snapshot never masks the failure').toBeUndefined();
  });

  it('a read whose retry is still in flight after a failed attempt is not bridged by the snapshot', async () => {
    const reloaded = await savedThenReloaded();
    const retrying = reloaded
      .fetchQuery({ queryKey: queryKeys.homeSummary(), queryFn: failing, retry: 1, retryDelay: 10_000 })
      .catch(() => undefined);
    await vi.advanceTimersByTimeAsync(0);
    expect(reloaded.getQueryState(queryKeys.homeSummary())?.fetchFailureCount, 'precondition: one attempt failed').toBe(1);

    await startQueryPersistence(reloaded, BUSTER);
    expect(reloaded.getQueryData(queryKeys.homeSummary()), 'the warming state stays').toBeUndefined();

    await vi.advanceTimersByTimeAsync(10_000);
    await retrying;
    expect(reloaded.getQueryState(queryKeys.homeSummary())?.status).toBe('error');
    expect(reloaded.getQueryData(queryKeys.homeSummary())).toBeUndefined();
  });

  it('a read that fails while the snapshot is landing never keeps the restored figures', async () => {
    const reloaded = await savedThenReloaded();
    // The first read starts as the restore reads the snapshot and fails a few
    // microtasks later: after the screen, around the hydrate.
    onSnapshotRead = () => {
      onSnapshotRead = null;
      void reloaded.fetchQuery({ queryKey: queryKeys.homeSummary(), queryFn: failing }).catch(() => undefined);
    };
    await startQueryPersistence(reloaded, BUSTER);
    await vi.advanceTimersByTimeAsync(0);
    expect(reloaded.getQueryData(queryKeys.homeSummary())).toBeUndefined();
  });

  it('control: a read still on its first attempt is bridged by the snapshot, which then settles', async () => {
    const reloaded = await savedThenReloaded();
    let resolve: (value: { total: number }) => void = () => undefined;
    const pending = reloaded.fetchQuery({
      queryKey: queryKeys.homeSummary(),
      queryFn: () => new Promise<{ total: number }>((done) => {
        resolve = done;
      }),
    });
    await startQueryPersistence(reloaded, BUSTER);
    expect(reloaded.getQueryData(queryKeys.homeSummary()), 'the snapshot bridges the load').toEqual(SNAPSHOT);
    expect(reloaded.getQueryState(queryKeys.homeSummary())?.dataUpdatedAt, 'with its true age').toBe(T0);
    resolve({ total: 12_481 });
    await pending;
    expect(reloaded.getQueryData(queryKeys.homeSummary())).toEqual({ total: 12_481 });
  });
});
