import type { HealthPayload } from './apiTypes';

/**
 * Which health payloads may say who the actor is (Genie residual #3, audit
 * genie-02 item 1 and bundle-04 item 4).
 *
 * `api.health` never throws on a failed probe: it returns the same
 * `{status: 'unreachable'}` snapshot for a 502, a dropped connection, an
 * unreadable body and an ended session, so the topbar and the banner render
 * one "unknown" state. Those payloads carry no `actor_cache_key`, and the
 * shell used to read that absence as "the actor changed to nobody", wiping
 * the Genie transcript, the in-flight turn record, pins and the last
 * borrower on a network blip or a post-deploy 502.
 *
 * The rule (D-identity-review-a1):
 *   - an `unreachable` payload says nothing about the actor, UNLESS
 *     `api.health` marked it as an authentication failure (401, a confirmed
 *     sign-in redirect, an ended session's unreadable body, or 403); then the
 *     actor is known to be nobody (key null);
 *   - any other payload is trusted only when it is a REAL health body:
 *     `mode` is 'live', `status` is 'ok' or 'degraded', and
 *     `actor_cache_key` is absent, null or `actor_` plus 16 lowercase hex
 *     characters (ACTOR_CACHE_KEY_RE). Then it is a trusted observation of
 *     that key, null included: the anonymous `{status, mode}` body a
 *     reachable server returns without a forwarded identity is a trusted
 *     "nobody";
 *   - everything else ({}, a proxy's JSON, a malformed key) is untrusted and
 *     says nothing.
 *
 * The mark lives in a module WeakSet, not on the payload, so the payload
 * shape (and `lib/apiTypes`) is unchanged and nothing can forge it from JSON.
 *
 * `sessionActorObservation` applies the same key rule to the /api/session
 * body, which seeds the actor gate before the first probe (HealthProvider).
 */

/** The server's opaque per-actor key: `actor_` + 16 lowercase hex. */
export const ACTOR_CACHE_KEY_RE = /^actor_[0-9a-f]{16}$/;

export function isActorCacheKey(value: unknown): value is string {
  return typeof value === 'string' && ACTOR_CACHE_KEY_RE.test(value);
}

const authFailedPayloads = new WeakSet<HealthPayload>();

/** Record that `api.health` got this payload from an authentication failure. */
export function markAuthFailedHealth(payload: HealthPayload): HealthPayload {
  authFailedPayloads.add(payload);
  return payload;
}

export type HealthActorObservation = { trusted: false } | { trusted: true; key: string | null };

/** The last trusted actor observation HealthProvider publishes. */
export interface ActorIdentity {
  key: string | null;
}

/** Absent, null or a well-formed key: the only values a real body carries. */
function isKeyOrNothing(value: unknown): value is string | null | undefined {
  return value === undefined || value === null || isActorCacheKey(value);
}

/** What a raw (not debounced) health payload says about the actor. */
export function healthActorObservation(payload: HealthPayload): HealthActorObservation {
  if (payload.status === 'unreachable') {
    return authFailedPayloads.has(payload) ? { trusted: true, key: null } : { trusted: false };
  }
  const real =
    payload.mode === 'live' &&
    (payload.status === 'ok' || payload.status === 'degraded') &&
    isKeyOrNothing(payload.actor_cache_key);
  return real ? { trusted: true, key: payload.actor_cache_key ?? null } : { trusted: false };
}

/**
 * What an /api/session body says about the actor. Trusted only for a real
 * session body: both capability flags are booleans and `actor_cache_key` is
 * an OWN property holding null or a well-formed key (an older backend that
 * omits it, a `{}` or a malformed key seeds nothing).
 */
export function sessionActorObservation(body: unknown): HealthActorObservation {
  if (!body || typeof body !== 'object') return { trusted: false };
  const session = body as Record<string, unknown>;
  if (typeof session.can_access_admin !== 'boolean' || typeof session.can_approve !== 'boolean') {
    return { trusted: false };
  }
  if (!Object.prototype.hasOwnProperty.call(session, 'actor_cache_key')) return { trusted: false };
  const key = session.actor_cache_key;
  if (key === null) return { trusted: true, key: null };
  return isActorCacheKey(key) ? { trusted: true, key } : { trusted: false };
}
