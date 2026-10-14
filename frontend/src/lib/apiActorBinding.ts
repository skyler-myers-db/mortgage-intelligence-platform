/**
 * The per-request actor binding, client half (D-identity-review-a4; audit
 * genie-02, bundle-04 item 4). docs/security/identity-boundary.md §6 is the
 * prose version.
 *
 * Every app request from an opened document carries the document owner's
 * opaque key (lib/actorScope documentOwnerKey: sticky in memory, never
 * cleared by a close). The server (backend/services/actor_binding.py) answers
 * 409 `actor_changed` when that key no longer names the signed-in user, so a
 * request sent between a cookie swap and the health probe that proves it is
 * refused instead of running under the new actor.
 *
 * Rules: no header before the document first opened for a real actor (or
 * while only nobody was observed); none on health or session, which are how
 * a tab learns who it is now; the transport computes the bound init ONCE per
 * logical request, so a retry or a redirect re-issue sends the same value
 * even when the owner is re-stamped to an alias in between. The raw boot
 * primes, the footprint and banner probes, the session probe and the RUM
 * beacon stay header-less (apiTransport.rawFetchGate.test.ts). The header
 * can only refuse, never grant.
 */
import { documentOwnerKey } from './actorScope';
import { requestHealthRecheck } from './healthRecheck';

export const ACTOR_KEY_HEADER = 'X-MIP-Actor-Key';
export const ACTOR_CHANGED_REASON = 'actor_changed';
/** What a refused request means for the reader (describeApiError, the workspace copy). */
export const ACTOR_CHANGED_COPY = 'Nothing was recorded. This tab resets for the new user.';

/** Route paths (apiTransport's `_routePath` form) that never carry the key. */
const UNBOUND_ROUTES: readonly string[] = ['/api/health', '/api/session'];

/**
 * `init` with the owner key merged into its headers, or `init` unchanged.
 * The transport only ever builds plain-record headers (apiTransport
 * _jsonBody: Content-Type, Idempotency-Key and extra headers), so a record
 * spread keeps every one of them.
 */
export function withActorKey(routePath: string, init: RequestInit | undefined): RequestInit | undefined {
  const key = documentOwnerKey();
  if (key === null || UNBOUND_ROUTES.includes(routePath)) return init;
  return { ...init, headers: { ...(init?.headers as Record<string, string> | undefined), [ACTOR_KEY_HEADER]: key } };
}

/**
 * A 409's string `reason`, else null. `actor_changed` also asks the health
 * poll for one more trusted probe, which proves the change and resets the
 * document (lib/actorScope): one request per refused response.
 */
export async function actorRefusalReason(res: Response): Promise<string | null> {
  if (res.status !== 409) return null;
  const reason: unknown = await res
    .clone()
    .json()
    .then((body: { reason?: unknown } | null) => body?.reason, () => null);
  if (typeof reason !== 'string') return null;
  if (reason === ACTOR_CHANGED_REASON) requestHealthRecheck();
  return reason;
}
