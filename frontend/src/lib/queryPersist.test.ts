/**
 * The actor-scoped persisted aggregate cache (audit delivery-05): what may
 * persist (a default-deny allow-list and a masked-id screen), when it is
 * restored (only after the actor gate first opens), how it ages (the true
 * dataUpdatedAt, a 24 h maxAge, the build buster) and how the gate owns it
 * (an actor change removes it, a closed gate drops writes). Node environment
 * with a stubbed `window` (two Map-backed storages): no DOM needed.
 */
import { QueryClient, type QueryKey } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ACTOR_A, ACTOR_B } from '../test/actorKeys';
import { NOBODY, _resetActorScopeForTests, observeActor } from './actorScope';
import { geoQueryKeys, rollupCohort } from './geoQueryKeys';
import { homeQueries } from './homeQueries';
import { installQueryPersistence } from './queryClient';
import {
  PERSISTABLE_EXACT,
  PERSISTABLE_PREFIXES,
  QUERY_CACHE_KEY,
  QUERY_CACHE_MAX_AGE_MS,
  QUERY_CACHE_SAVE_THROTTLE_MS,
  _resetQueryPersistenceForTests,
  isPersistableQueryKey,
  queryCacheBuster,
  startQueryPersistence,
} from './queryPersist';
import { queryKeys } from './queryKeys';

const BUSTER = 'index-AbCd1234.js';
const T0 = Date.parse('2026-09-30T08:00:00Z');

let session: Map<string, string>;
let sessionWrites: number;

beforeEach(() => {
  session = new Map();
  sessionWrites = 0;
  const local = new Map<string, string>();
  const storage = (values: Map<string, string>, onWrite?: () => void) => ({
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => {
      onWrite?.();
      values.set(key, value);
    },
    removeItem: (key: string) => {
      values.delete(key);
    },
  });
  vi.stubGlobal('window', {
    localStorage: storage(local),
    sessionStorage: storage(session, () => {
      sessionWrites += 1;
    }),
  });
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
const stored = () => {
  const raw = session.get(QUERY_CACHE_KEY);
  return raw ? (JSON.parse(raw) as { buster: string; clientState: { queries: Array<{ queryKey: QueryKey }>; mutations: unknown[] } }) : null;
};
const storedKeys = () => stored()?.clientState.queries.map((query) => query.queryKey) ?? null;

/** Start persistence on `qc`, then let one throttled save run. */
async function persisting(qc: QueryClient, buster = BUSTER): Promise<void> {
  await startQueryPersistence(qc, buster);
}

async function saveNow(): Promise<void> {
  await vi.advanceTimersByTimeAsync(QUERY_CACHE_SAVE_THROTTLE_MS);
}

describe('what may persist: default deny', () => {
  it('pins the allow-list exactly', () => {
    expect(PERSISTABLE_EXACT).toEqual([
      ['mip', 'portfolio', 'preview', 'home'],
      ['mip', 'portfolio', 'preview', 'home', 'contactable'],
      ['mip', 'home', 'summary'],
      ['mip', 'geo', 'rate-sensitivity'],
      ['mip', 'config', 'options'],
    ]);
    expect(PERSISTABLE_PREFIXES).toEqual([
      ['mip', 'geo', 'state-rollups'],
      ['mip', 'segments'],
      ['mip', 'analytics', 'executive'],
      ['mip', 'analytics', 'geography'],
      ['mip', 'analytics', 'segments'],
      ['mip', 'analytics', 'rate-window'],
    ]);
  });

  it.each<[string, QueryKey]>([
    ['Home preview', homeQueries.homePreview.queryKey()],
    ['Home contactable preview', homeQueries.homeContactablePreview.queryKey()],
    ['Home summary', homeQueries.homeSummary.queryKey()],
    ['national state rollups', geoQueryKeys.stateRollups(rollupCohort(null, 'any'))],
    ['filtered state rollups', geoQueryKeys.stateRollups(rollupCohort(['itm'], 'all', { state: 'IL' }))],
    ['rate sensitivity', geoQueryKeys.rateSensitivity()],
    ['segment combinations (whole-book counts, by design)', queryKeys.segments(['combinations'])],
    ['segments', queryKeys.segments([{ state: 'IL' }])],
    ['executive analytics', queryKeys.analytics('executive', [{}])],
    ['geography analytics', queryKeys.analytics('geography')],
    ['segment analytics', queryKeys.analytics('segments', ['x'])],
    ['rate-window analytics', queryKeys.analytics('rate-window')],
    ['config options', queryKeys.configOptions()],
  ])('persists %s', (_label, key) => {
    expect(isPersistableQueryKey(key)).toBe(true);
  });

  // A non-exhaustive sample: anything not on the allow-list is denied, so new
  // keys (another lane's) need no edit here.
  it.each<[string, QueryKey]>([
    ['leads', queryKeys.leads([])],
    ['a borrower dossier', queryKeys.borrower('B-0OXOBYLW8MNCK')],
    ['borrower proof', queryKeys.borrowerProof('B-0OXOBYLW8MNCK')],
    ['borrower lifecycle', queryKeys.borrowerLifecycle('B-0OXOBYLW8MNCK')],
    ['the borrower decision history', queryKeys.borrowerDecisions('B-0OXOBYLW8MNCK')],
    ['an offer snapshot', queryKeys.offerSnapshot('B-0OXOBYLW8MNCK', null)],
    ['an outreach draft', queryKeys.outreachDraft('B-0OXOBYLW8MNCK', 'email', null)],
    ['audit events', queryKeys.auditEvents([])],
    ['audit rollups', queryKeys.auditRollups('7d')],
    ['an audit receipt', queryKeys.auditReceipt('evt-1')],
    ['my recent activity', ['audit', 'my-events', 20]],
    ['Genie start', queryKeys.genieStart()],
    ['growth agent', queryKeys.growthAgent()],
    ['growth agent capabilities', queryKeys.growthAgentCapabilities()],
    ['workspace', queryKeys.workspace()],
    ['sales roster', queryKeys.salesRoster()],
    ['sales ops', queryKeys.salesOps()],
    ['campaigns', queryKeys.campaigns()],
    ['activation summary', queryKeys.activationSummary()],
    ['admin rules', queryKeys.adminRules()],
    ['data estate', queryKeys.dataEstate()],
    ['an asset', queryKeys.assetMetadata('mip.gold.borrower_360')],
    ['lineage', queryKeys.lineageManifest()],
    ['footprint', queryKeys.footprint()],
    ['the session', ['session', 'access']],
    ['a Portfolio Builder preview', queryKeys.portfolioPreview([{ state: 'IL' }])],
    ['a longer Home preview key', [...queryKeys.homePreview(), 'x']],
    ['ZIP rollups (sample_borrower_id)', geoQueryKeys.zipRollups('IL', rollupCohort(null, 'any'))],
    ['county ZIP rollups', queryKeys.geoCountyZipRollups('17031', [])],
    ['the assignment overlay', geoQueryKeys.assignmentOverlay('state', null)],
    ['economics (top_borrowers)', queryKeys.analytics('economics')],
    ['economics points (borrower_id)', queryKeys.analytics('economics-points')],
    ['signals (evidence_examples.borrower_id)', queryKeys.analytics('signals')],
    ['funnel', queryKeys.analytics('funnel')],
    ['funnel by loan officer', queryKeys.analytics('funnel-loan-officer')],
  ])('never persists %s', (_label, key) => {
    expect(isPersistableQueryKey(key)).toBe(false);
  });
});

describe('the masked-id screen', () => {
  it('drops an allow-listed query whose data carries a masked borrower id', async () => {
    const qc = client();
    await persisting(qc);
    qc.setQueryData(queryKeys.homeSummary(), { total: 12_480, spotlight: 'B-0OXOBYLW8MNCK' });
    qc.setQueryData(queryKeys.configOptions(), { lender_name: 'Summit Mortgage' });
    await saveNow();
    expect(storedKeys()).toEqual([queryKeys.configOptions()]);
    expect(session.get(QUERY_CACHE_KEY)).not.toMatch(/B-[0-9A-Z]{13}/);
  });
});

describe('restore', () => {
  it('round-trips an aggregate with its true dataUpdatedAt', async () => {
    const first = client();
    await persisting(first);
    first.setQueryData(queryKeys.homeSummary(), { total: 12_480 });
    await saveNow();
    expect(storedKeys()).toEqual([queryKeys.homeSummary()]);

    vi.setSystemTime(T0 + 60 * 60 * 1000);
    _resetQueryPersistenceForTests();
    const reloaded = client();
    await persisting(reloaded);
    expect(reloaded.getQueryData(queryKeys.homeSummary())).toEqual({ total: 12_480 });
    expect(reloaded.getQueryState(queryKeys.homeSummary())?.dataUpdatedAt).toBe(T0);
  });

  async function restoredHomeSummary(): Promise<QueryClient> {
    const first = client();
    await persisting(first);
    first.setQueryData(queryKeys.homeSummary(), { total: 12_480 });
    await saveNow();
    vi.setSystemTime(T0 + 60_000);
    _resetQueryPersistenceForTests();
    const reloaded = client();
    await persisting(reloaded);
    expect(reloaded.getQueryData(queryKeys.homeSummary()), 'precondition: restored').toEqual({ total: 12_480 });
    return reloaded;
  }

  it('a restored value whose first refresh fails is reset, so the surface shows its real state (fail visibly)', async () => {
    const reloaded = await restoredHomeSummary();
    await reloaded
      .fetchQuery({ queryKey: queryKeys.homeSummary(), queryFn: () => Promise.reject(new Error('warehouse warming up')) })
      .catch(() => undefined);
    await vi.advanceTimersByTimeAsync(0);
    expect(reloaded.getQueryData(queryKeys.homeSummary()), 'the snapshot no longer masks the failure').toBeUndefined();
  });

  it('a restored value whose first refresh succeeds is settled: a later failure keeps the fresh data', async () => {
    const reloaded = await restoredHomeSummary();
    await reloaded.fetchQuery({ queryKey: queryKeys.homeSummary(), queryFn: () => Promise.resolve({ total: 12_481 }) });
    await reloaded
      .fetchQuery({ queryKey: queryKeys.homeSummary(), queryFn: () => Promise.reject(new Error('flap')), staleTime: 0 })
      .catch(() => undefined);
    await vi.advanceTimersByTimeAsync(0);
    expect(reloaded.getQueryData(queryKeys.homeSummary())).toEqual({ total: 12_481 });
  });

  it('a newer read wins over the restored snapshot (hydrate is newer-wins)', async () => {
    const first = client();
    await persisting(first);
    first.setQueryData(queryKeys.homeSummary(), { total: 1 });
    await saveNow();
    vi.setSystemTime(T0 + 5_000);
    _resetQueryPersistenceForTests();
    const reloaded = client();
    reloaded.setQueryData(queryKeys.homeSummary(), { total: 2 });
    await persisting(reloaded);
    expect(reloaded.getQueryData(queryKeys.homeSummary())).toEqual({ total: 2 });
  });

  it.each([
    ['another build (buster)', 0, 'index-ZzZz9876.js'],
    ['older than 24 h', QUERY_CACHE_MAX_AGE_MS + 1, BUSTER],
  ])('discards and removes a snapshot from %s', async (_label, age, buster) => {
    const first = client();
    await persisting(first);
    first.setQueryData(queryKeys.homeSummary(), { total: 12_480 });
    await saveNow();
    vi.setSystemTime(Date.now() + age);
    _resetQueryPersistenceForTests();
    const reloaded = client();
    await persisting(reloaded, buster);
    expect(reloaded.getQueryData(queryKeys.homeSummary())).toBeUndefined();
    expect(session.has(QUERY_CACHE_KEY)).toBe(false);
  });

  it('a malformed snapshot is removed, not restored', async () => {
    session.set(QUERY_CACHE_KEY, '{"not":"a client"}');
    const qc = client();
    await persisting(qc);
    expect(session.has(QUERY_CACHE_KEY)).toBe(false);
  });

  it('restores only what this build would persist', async () => {
    session.set(QUERY_CACHE_KEY, JSON.stringify({
      buster: BUSTER,
      timestamp: T0,
      clientState: {
        mutations: [],
        queries: [
          { queryKey: queryKeys.leads([]), queryHash: '["mip","leads"]', state: { data: [1], dataUpdatedAt: T0, status: 'success' } },
          { queryKey: queryKeys.homeSummary(), queryHash: '["mip","home","summary"]', state: { data: { total: 3 }, dataUpdatedAt: T0, status: 'success' } },
        ],
      },
    }));
    const qc = client();
    await persisting(qc);
    expect(qc.getQueryData(queryKeys.leads([]))).toBeUndefined();
    expect(qc.getQueryData(queryKeys.homeSummary())).toEqual({ total: 3 });
  });
});

describe('the actor gate owns the snapshot', () => {
  it('nothing is imported or restored while the gate is pending; once, when it first opens', async () => {
    _resetActorScopeForTests({ status: 'pending', owner: ACTOR_A });
    const load = vi.fn(async () => ({ startQueryPersistence: vi.fn(async () => undefined) }));
    installQueryPersistence(client(), load);
    expect(load).not.toHaveBeenCalled();
    observeActor({ key: ACTOR_A });
    observeActor({ key: ACTOR_A });
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('an already open gate starts at once', () => {
    const load = vi.fn(async () => ({ startQueryPersistence: vi.fn(async () => undefined) }));
    installQueryPersistence(client(), load);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('an actor change removes the snapshot; a closed gate drops writes', async () => {
    _resetActorScopeForTests({ status: 'pending', owner: ACTOR_A });
    observeActor({ key: ACTOR_A });
    const qc = client();
    await persisting(qc);
    qc.setQueryData(queryKeys.homeSummary(), { total: 1 });
    await saveNow();
    expect(session.has(QUERY_CACHE_KEY)).toBe(true);

    // The change, as a NEW document (a mid-session change resets the tab,
    // D-identity-review-a3): pending over A's stamps, then B first.
    _resetActorScopeForTests({ status: 'pending', owner: ACTOR_A });
    observeActor({ key: ACTOR_B });
    expect(session.has(QUERY_CACHE_KEY), 'rows 1/3/4 remove it').toBe(false);

    qc.setQueryData(queryKeys.homeSummary(), { total: 2 });
    await saveNow();
    const bSnapshot = session.get(QUERY_CACHE_KEY);
    expect(bSnapshot).toBeDefined();
    observeActor({ key: null });
    qc.setQueryData(queryKeys.homeSummary(), { total: 3 });
    await saveNow();
    expect(session.get(QUERY_CACHE_KEY), 'closed: the write is dropped').toBe(bSnapshot);
  });
});

describe('saving', () => {
  it('is a 1 s trailing throttle over the query cache', async () => {
    const qc = client();
    await persisting(qc);
    const before = sessionWrites;
    qc.setQueryData(queryKeys.homeSummary(), { total: 1 });
    qc.setQueryData(queryKeys.configOptions(), { lender_name: 'Summit Mortgage' });
    await vi.advanceTimersByTimeAsync(QUERY_CACHE_SAVE_THROTTLE_MS - 1);
    expect(sessionWrites - before, 'nothing before the window closes').toBe(0);
    qc.setQueryData(queryKeys.homeSummary(), { total: 2 });
    await vi.advanceTimersByTimeAsync(1);
    expect(sessionWrites - before).toBe(1);
    expect(storedKeys()).toHaveLength(2);
  });

  it('saves reads that settled before persistence started (the lazy chunk loads after the gate opens)', async () => {
    const qc = client();
    qc.setQueryData(queryKeys.homeSummary(), { total: 1 });
    await persisting(qc);
    expect(session.has(QUERY_CACHE_KEY)).toBe(false);
    await saveNow();
    expect(storedKeys()).toEqual([queryKeys.homeSummary()]);
  });

  it('skips a snapshot over 256 KiB and removes the stale one', async () => {
    const qc = client();
    await persisting(qc);
    qc.setQueryData(queryKeys.homeSummary(), { total: 1 });
    await saveNow();
    expect(session.has(QUERY_CACHE_KEY)).toBe(true);
    qc.setQueryData(queryKeys.configOptions(), { blob: 'x'.repeat(262_144) });
    await saveNow();
    expect(session.has(QUERY_CACHE_KEY)).toBe(false);
  });

  it('never persists a mutation', async () => {
    const qc = client();
    await persisting(qc);
    await qc.getMutationCache().build(qc, { mutationFn: async () => 'ok' }).execute(undefined);
    qc.setQueryData(queryKeys.homeSummary(), { total: 1 });
    await saveNow();
    expect(stored()?.clientState.mutations).toEqual([]);
  });
});

describe('the buster', () => {
  const doc = (...srcs: string[]) => ({
    querySelectorAll: () => srcs.map((src) => ({ getAttribute: () => src })) as unknown as NodeListOf<Element>,
  });

  it('is the hashed entry file name, not the boot module before it', () => {
    expect(queryCacheBuster(doc('/assets/boot-FF4k56kb.js', '/assets/index-Bl8TACrT.js'))).toBe('index-Bl8TACrT.js');
  });

  it("is 'dev' for an unhashed entry", () => {
    expect(queryCacheBuster(doc('/src/main.tsx'))).toBe('dev');
    expect(queryCacheBuster(doc())).toBe('dev');
  });
});
