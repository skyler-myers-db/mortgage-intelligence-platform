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
 * The rule:
 *   - an `unreachable` payload says nothing about the actor, UNLESS
 *     `api.health` marked it as an authentication failure (401, a confirmed
 *     sign-in redirect, an ended session's unreadable body, or 403); then the
 *     actor is known to be nobody (key null);
 *   - every other payload is a trusted observation of `actor_cache_key`,
 *     null included: the anonymous `{status, mode}` body a reachable server
 *     returns without a forwarded identity is a trusted "nobody".
 *
 * The mark lives in a module WeakSet, not on the payload, so the payload
 * shape (and `lib/apiTypes`) is unchanged and nothing can forge it from JSON.
 */

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

/** What a raw (not debounced) health payload says about the actor. */
export function healthActorObservation(payload: HealthPayload): HealthActorObservation {
  if (payload.status === 'unreachable') {
    return authFailedPayloads.has(payload) ? { trusted: true, key: null } : { trusted: false };
  }
  return { trusted: true, key: payload.actor_cache_key ?? null };
}
