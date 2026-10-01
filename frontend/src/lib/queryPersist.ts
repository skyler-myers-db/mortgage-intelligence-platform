import type { Query, QueryClient, QueryKey } from '@tanstack/react-query';
import {
  persistQueryClientRestore,
  persistQueryClientSave,
  type PersistedClient,
  type Persister,
} from '@tanstack/react-query-persist-client';
import { readActorScoped, removeActorScoped, writeActorScoped } from './actorScope';

/**
 * The actor-scoped persisted aggregate cache (audit delivery-05): a reload,
 * a restored or duplicated tab repaints Home and the analytics aggregates
 * from the last snapshot instead of empty skeletons, while the fresh reads
 * run. Lazy: lib/queryClient.ts installQueryPersistence imports this module
 * only once the actor gate (lib/actorScope) first opens, so nothing is
 * restored before the actor is known and none of this is initial JS.
 *
 *   - Storage: ONE PRIVATE_SESSION key of lib/actorScope, 'mip.queryCache.v1'
 *     (sessionStorage: per tab, never across a browser restart). Only the
 *     gate touches it, so an actor change removes it (rows 1/3/4) and
 *     writes are dropped while the gate is closed. No localStorage, no
 *     next-morning restore: that stays an owner decision.
 *   - What: DEFAULT-DENY. Only the successful queries whose key is on the
 *     exact allow-list below (non-PII tenant aggregates) and whose data
 *     carries no masked borrower id (`B-` + 13) are dehydrated. Never leads,
 *     a borrower, lifecycle, audit, Genie, outreach, workspace or admin
 *     reads; never a mutation. geo's `segments(['combinations'])` is
 *     persisted by design: whole-book counts with no ids.
 *   - Freshness: restored queries keep their dehydrated `dataUpdatedAt`, so
 *     a FetchedAt label shows the true age; hydrate is newer-wins, so a
 *     fresher read always replaces restored data. `maxAge` 24 h; the
 *     buster is the hashed entry file name, which every chunk change
 *     re-hashes, so a deploy discards the snapshot.
 *   - Size: a snapshot over 256 KiB of JSON is not written (and the stale
 *     one is removed). Saves are a 1 s trailing throttle over the query
 *     cache's events.
 *
 * Nothing here reads an audited endpoint or writes an audit row: it only
 * serialises reads the page already made.
 */

export const QUERY_CACHE_KEY = 'mip.queryCache.v1';
export const QUERY_CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000;
export const QUERY_CACHE_MAX_CHARS = 262_144;
export const QUERY_CACHE_SAVE_THROTTLE_MS = 1_000;

const MASKED_BORROWER_ID = /\bB-[0-9A-Z]{13}\b/;

/** Exact keys that may persist. */
export const PERSISTABLE_EXACT: readonly (readonly string[])[] = [
  ['mip', 'portfolio', 'preview', 'home'],
  ['mip', 'portfolio', 'preview', 'home', 'contactable'],
  ['mip', 'home', 'summary'],
  ['mip', 'geo', 'rate-sensitivity'],
  ['mip', 'config', 'options'],
];

/** Key prefixes that may persist, whatever criteria follow. */
export const PERSISTABLE_PREFIXES: readonly (readonly string[])[] = [
  ['mip', 'geo', 'state-rollups'],
  ['mip', 'segments'],
  ['mip', 'analytics', 'executive'],
  ['mip', 'analytics', 'geography'],
  ['mip', 'analytics', 'segments'],
  ['mip', 'analytics', 'rate-window'],
];

function startsWith(key: QueryKey, prefix: readonly string[]): boolean {
  return prefix.every((part, index) => key[index] === part);
}

/** The allow-list; everything else is denied. */
export function isPersistableQueryKey(key: QueryKey): boolean {
  return (
    PERSISTABLE_EXACT.some((exact) => exact.length === key.length && startsWith(key, exact)) ||
    PERSISTABLE_PREFIXES.some((prefix) => startsWith(key, prefix))
  );
}

function carriesMaskedId(data: unknown): boolean {
  try {
    return MASKED_BORROWER_ID.test(JSON.stringify(data) ?? '');
  } catch {
    return true;
  }
}

const screened = new WeakMap<Query, { at: number; ok: boolean }>();

/** Dehydrate filter: allow-listed, successful, and free of masked ids
 *  (screened once per query per `dataUpdatedAt`). */
export function shouldPersistQuery(query: Query): boolean {
  if (query.state.status !== 'success' || !isPersistableQueryKey(query.queryKey)) return false;
  const at = query.state.dataUpdatedAt;
  const memo = screened.get(query);
  if (memo && memo.at === at) return memo.ok;
  const ok = !carriesMaskedId(query.state.data);
  screened.set(query, { at, ok });
  return ok;
}

/** The hashed entry file name (`index-<hash>.js`), or 'dev' when unhashed. */
export function queryCacheBuster(doc: Pick<Document, 'querySelectorAll'> = document): string {
  for (const script of Array.from(doc.querySelectorAll('script[type="module"][src]'))) {
    const name = (script.getAttribute('src') ?? '').split('?')[0].split('/').pop() ?? '';
    if (/^index-[\w-]{8}\.js$/.test(name)) return name;
  }
  return 'dev';
}

function isPersistedClient(value: unknown): value is PersistedClient {
  if (!value || typeof value !== 'object') return false;
  const client = value as Partial<PersistedClient>;
  const state = client.clientState as Partial<PersistedClient['clientState']> | undefined;
  return (
    typeof client.timestamp === 'number' &&
    typeof client.buster === 'string' &&
    !!state &&
    Array.isArray(state.queries) &&
    Array.isArray(state.mutations)
  );
}

/** A Persister over the actor gate: the only way this key is read or written. */
export const actorScopedPersister: Persister = {
  persistClient(client) {
    const json = JSON.stringify(client);
    if (json.length > QUERY_CACHE_MAX_CHARS) removeActorScoped('session', QUERY_CACHE_KEY);
    else writeActorScoped('session', QUERY_CACHE_KEY, json);
  },
  restoreClient() {
    const raw = readActorScoped('session', QUERY_CACHE_KEY);
    if (!raw) return undefined;
    let parsed: unknown = null;
    try {
      parsed = JSON.parse(raw);
    } catch {
      // Malformed: removed below.
    }
    if (!isPersistedClient(parsed)) {
      removeActorScoped('session', QUERY_CACHE_KEY);
      return undefined;
    }
    // Defence in depth: restore only what this build would persist.
    const queries = parsed.clientState.queries.filter(
      (query) => isPersistableQueryKey(query.queryKey) && !carriesMaskedId(query.state.data),
    );
    return { ...parsed, clientState: { mutations: [], queries } };
  },
  removeClient() {
    removeActorScoped('session', QUERY_CACHE_KEY);
  },
};

let started = false;

/**
 * Restore once, then save on a trailing throttle. Called by
 * installQueryPersistence after the gate first opens; once per document.
 */
export async function startQueryPersistence(queryClient: QueryClient, buster = queryCacheBuster()): Promise<void> {
  if (started) return;
  started = true;
  const options = { queryClient, persister: actorScopedPersister, buster };
  try {
    await persistQueryClientRestore({ ...options, maxAge: QUERY_CACHE_MAX_AGE_MS });
  } catch {
    // The restore already removed the unreadable snapshot.
  }
  let timer: ReturnType<typeof setTimeout> | null = null;
  const save = () => {
    timer = null;
    void persistQueryClientSave({
      ...options,
      dehydrateOptions: { shouldDehydrateQuery: shouldPersistQuery, shouldDehydrateMutation: () => false },
    });
  };
  const schedule = () => {
    timer ??= setTimeout(save, QUERY_CACHE_SAVE_THROTTLE_MS);
  };
  queryClient.getQueryCache().subscribe((event) => {
    if (event.type === 'added' || event.type === 'removed' || event.type === 'updated') schedule();
  });
  // Reads that settled before this lazy module loaded raised no event here.
  schedule();
}

/** Test seam: allow another start in the same module instance. */
export function _resetQueryPersistenceForTests(): void {
  started = false;
}
