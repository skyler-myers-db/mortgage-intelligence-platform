import type { QueryClient } from '@tanstack/react-query';
import type { BootPrime, BootRead, BootReadName } from '../boot/primeBoot';
import { defaultFetchFootprint, footprintQueryOptions } from '../components/FootprintProvider';
import { api } from './api';
import type { HealthPayload } from './apiTypes';
import { configOptionsQueryOptions } from './configOptionsQuery';
import { sessionQueryOptions } from './sessionQuery';

/**
 * The app side of the boot module (src/boot/primeBoot, audit bundle-02).
 *
 * The boot module starts the four non-audited boot reads beside the entry
 * chunk and leaves the Responses on `window.__MIP_BOOT__`. This module hands
 * each one, once, to the code that would otherwise have made the request:
 *
 *   - `seedBootQueries` (main.tsx, before render) prefetches the session,
 *     config options and footprint queries with each provider's OWN options
 *     factory and a queryFn that consumes the prime, so every observer
 *     (AppContext, RouteNav, IdentityMenu, app.tsx, FootprintProvider) joins
 *     the one in-flight fetch;
 *   - `takeHealthPrime` gives HealthProvider's first probe the primed health
 *     read instead of a second request.
 *
 * A primed Response is used ONLY when it is a 2xx, not an opaque redirect, and
 * its body parses as JSON. A rejection, a non-2xx, a redirect, a parse failure
 * or a missing global calls `fallback()` exactly once: the real request through
 * lib/api (or the provider's own fetcher), so the error mapping (session
 * expiry, warming-up 503s, offline) is identical to a boot with no prime. It
 * never substitutes data. The accepted cost: a failed prime is re-requested
 * once, at most one extra request per failing boot endpoint per page load.
 */

/** The literal primeBoot.ts writes (it imports nothing, so it cannot share a constant). */
const BOOT_GLOBAL = '__MIP_BOOT__';

/** A health prime older than this when the first probe runs is discarded. */
export const HEALTH_PRIME_MAX_AGE_MS = 10_000;

const taken = new Set<BootReadName>();

function bootPrime(): BootPrime | undefined {
  try {
    return (window as Window & { [BOOT_GLOBAL]?: BootPrime })[BOOT_GLOBAL];
  } catch {
    return undefined;
  }
}

/** The primed read for `name`, once per page load; null when absent or already taken. */
export function takeBootRead(name: BootReadName): BootRead | null {
  if (taken.has(name)) return null;
  taken.add(name);
  return bootPrime()?.reads[name] ?? null;
}

/** The prime's parsed body when it is usable, else null. Never throws. */
async function usableJson<T>(read: BootRead): Promise<{ value: T } | null> {
  try {
    const res = await read.response;
    if (!res.ok || res.type === 'opaqueredirect') return null;
    return { value: (await res.json()) as T };
  } catch {
    return null;
  }
}

/**
 * The primed JSON for `name` when usable; otherwise `fallback()`, called once.
 * A query aborted while the prime was in flight also takes the fallback, whose
 * real request then rejects with the abort, as it would have without a prime.
 */
export async function readPrimedJson<T>(
  name: BootReadName,
  signal: AbortSignal | undefined,
  fallback: () => Promise<T>,
): Promise<T> {
  const read = takeBootRead(name);
  const primed = read ? await usableJson<T>(read) : null;
  return primed && !signal?.aborted ? primed.value : fallback();
}

/**
 * The health prime for HealthProvider's first probe: null when absent, taken,
 * or settled more than HEALTH_PRIME_MAX_AGE_MS ago (then it is discarded and
 * the provider probes normally). A read still in flight is fresh.
 */
export function takeHealthPrime(nowMs: number = performance.now()): BootRead | null {
  const read = takeBootRead('health');
  if (!read) return null;
  if (read.settledAt !== null && nowMs - read.settledAt > HEALTH_PRIME_MAX_AGE_MS) return null;
  return read;
}

/**
 * The first probe's payload from the health prime, or from `fallback()` when
 * the prime is unusable. `probeMs` is the boot request's own duration when
 * the prime was used, else null (the caller times its fallback).
 */
export async function readHealthPrime(
  read: BootRead,
  fallback: () => Promise<HealthPayload>,
): Promise<{ payload: HealthPayload; probeMs: number | null }> {
  const primed = await usableJson<HealthPayload>(read);
  if (!primed) return { payload: await fallback(), probeMs: null };
  const settledAt = read.settledAt ?? performance.now();
  return { payload: primed.value, probeMs: Math.round(settledAt - read.startedAt) };
}

/**
 * Called by main.tsx right after createMipQueryClient and before render:
 * starts the three shell queries now, each consuming its boot prime.
 */
export function seedBootQueries(queryClient: QueryClient): void {
  void queryClient.prefetchQuery({
    ...sessionQueryOptions(),
    queryFn: ({ signal }) => readPrimedJson('session', signal, () => api.session(signal)),
  });
  void queryClient.prefetchQuery({
    ...configOptionsQueryOptions(),
    queryFn: ({ signal }) => readPrimedJson('options', signal, () => api.configOptions(signal)),
  });
  void queryClient.prefetchQuery(
    footprintQueryOptions((signal) => readPrimedJson('footprint', signal, () => defaultFetchFootprint(signal))),
  );
}

/** Test seam: forget which reads were taken. */
export function _resetBootPrimeForTests(): void {
  taken.clear();
}
