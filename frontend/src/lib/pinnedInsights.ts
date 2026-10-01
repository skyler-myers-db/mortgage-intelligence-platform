import { useSyncExternalStore } from 'react';
import { readActorScoped, subscribeActorScope, updateActorScoped } from './actorScope';

/**
 * Pinned insights (re-audit Buyer-Wow #9) — "pin to Home" closes the loop
 * from question → insight → standing artifact. A pin is a PERSONAL bookmark
 * of a Genie answer, kept client-side in actor-scoped localStorage: a
 * PRIVATE_LOCAL key of lib/actorScope, read and written only through its
 * gate, so nothing renders before the actor is known and another actor's
 * pins are removed, never adopted. It is deliberately NOT a governed
 * mutation: it changes no borrower data, sends no outreach, and needs no
 * audit row — so it stays booth-safe and backend-free. The Home "Pinned
 * insights" card and the answer's pin button share this external store via
 * useSyncExternalStore so they stay in sync within the SPA (no
 * cross-tab-only staleness).
 */

export interface PinnedInsight {
  /** Stable id (question hash / message id / hashed question). */
  id: string;
  question: string;
  /** One-line summary: the metric or the cleaned answer, truncated. */
  summary: string;
  source: string | null;
  pinnedAt: string; // ISO
}

export const PINNED_INSIGHTS_KEY = 'mip.pinnedInsights';
const MAX_PINS = 6;

/**
 * Degraded/non-trusted Genie answer sources — the app's single trust boundary.
 * These are caveats/refusals, never persistable artifacts: they must not be
 * pinned to Home, nor persisted as conversation history. Trust is a DENYLIST
 * (anything not in this set is a genuine, source-cited answer — `genie`,
 * `trusted_sql`, `sales_ops`, …), NOT an allowlist of one source. Mirrored by
 * `shouldPersistConversation` in GenieChat, which imports this same set.
 */
export const NON_PERSISTABLE_SOURCES = new Set([
  'degraded',
  'policy_blocked',
  'refused',
  'data_gap',
  'out_of_footprint',
]);

/** True when an answer's source is a genuine, trusted, source-cited result
 *  (not a degraded/policy-blocked caveat) — the boundary for pin/persist. */
export function isTrustedGenieSource(source: string | null | undefined): boolean {
  const s = String(source ?? '').trim();
  return s.length > 0 && !NON_PERSISTABLE_SOURCES.has(s);
}

function parsePins(raw: string | null): PinnedInsight[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (p): p is PinnedInsight =>
        p && typeof p.id === 'string' && typeof p.question === 'string' && typeof p.summary === 'string',
    );
  } catch {
    return [];
  }
}

// External store: a lazily read snapshot + subscriber set, so same-tab
// consumers re-render on pin/unpin (localStorage 'storage' events only fire
// cross-tab). The snapshot is dropped on every write and every actor-gate
// event, so hidden pins disappear and restored ones come back.
let cache: PinnedInsight[] | null = null;
const subscribers = new Set<() => void>();

function invalidate(): void {
  cache = null;
  subscribers.forEach((fn) => fn());
}

/** Every write is an updater over the raw stored list (see lib/actorScope). */
function update(next: (pins: PinnedInsight[]) => PinnedInsight[] | null): void {
  updateActorScoped('local', PINNED_INSIGHTS_KEY, (raw) => {
    const pins = next(parsePins(raw));
    return pins === null ? null : JSON.stringify(pins);
  });
  invalidate();
}

subscribeActorScope(invalidate);

function onStorageEvent(e: StorageEvent) {
  if (e.key === PINNED_INSIGHTS_KEY) invalidate();
}

function subscribe(cb: () => void): () => void {
  if (subscribers.size === 0 && typeof window !== 'undefined') {
    window.addEventListener('storage', onStorageEvent);
  }
  subscribers.add(cb);
  return () => {
    subscribers.delete(cb);
    if (subscribers.size === 0 && typeof window !== 'undefined') {
      window.removeEventListener('storage', onStorageEvent);
    }
  };
}

function getSnapshot(): PinnedInsight[] {
  if (cache === null) cache = parsePins(readActorScoped('local', PINNED_INSIGHTS_KEY));
  return cache;
}

export function pinInsight(insight: PinnedInsight): void {
  update((pins) => [insight, ...pins.filter((p) => p.id !== insight.id)].slice(0, MAX_PINS));
}

export function unpinInsight(id: string): void {
  update((pins) => pins.filter((p) => p.id !== id));
}

/** Remove every pin (tests). Production never calls it: the actor gate
 *  removes the key itself on an actor change. */
export function clearPinnedInsights(): void {
  update(() => null);
}

export function usePinnedInsights() {
  const pins = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return { pins, pin: pinInsight, unpin: unpinInsight };
}
