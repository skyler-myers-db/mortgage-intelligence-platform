import { useSyncExternalStore } from 'react';

/**
 * Pinned insights (re-audit Buyer-Wow #9) — "pin to Home" closes the loop
 * from question → insight → standing artifact. A pin is a PERSONAL bookmark
 * of a Genie answer, kept client-side (actor-scoped localStorage, cleared on
 * actor change like the other browser state). It is deliberately NOT a
 * governed mutation: it changes no borrower data, sends no outreach, and
 * needs no audit row — so it stays booth-safe and backend-free. The Home
 * "Pinned insights" card and the answer's pin button share this external
 * store via useSyncExternalStore so they stay in sync within the SPA (no
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

function readStore(): PinnedInsight[] {
  if (typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(PINNED_INSIGHTS_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (p): p is PinnedInsight =>
        p && typeof p.id === 'string' && typeof p.question === 'string' && typeof p.summary === 'string',
    );
  } catch {
    return [];
  }
}

// External store: a cached snapshot + subscriber set, so same-tab consumers
// re-render on pin/unpin (localStorage 'storage' events only fire cross-tab).
let cache: PinnedInsight[] = readStore();
const subscribers = new Set<() => void>();

function emit(next: PinnedInsight[]) {
  cache = next;
  try {
    window.localStorage.setItem(PINNED_INSIGHTS_KEY, JSON.stringify(next));
  } catch {
    // Storage may be unavailable (private mode); the in-memory cache still works.
  }
  subscribers.forEach((fn) => fn());
}

function onStorageEvent(e: StorageEvent) {
  if (e.key === PINNED_INSIGHTS_KEY) {
    cache = readStore();
    subscribers.forEach((fn) => fn());
  }
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
  return cache;
}

export function pinInsight(insight: PinnedInsight): void {
  emit([insight, ...cache.filter((p) => p.id !== insight.id)].slice(0, MAX_PINS));
}

export function unpinInsight(id: string): void {
  emit(cache.filter((p) => p.id !== id));
}

/** Used by the actor-change reset to clear personal pins. */
export function clearPinnedInsights(): void {
  emit([]);
}

export function usePinnedInsights() {
  const pins = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return { pins, pin: pinInsight, unpin: unpinInsight };
}
