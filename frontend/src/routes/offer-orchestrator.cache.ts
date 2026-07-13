import type { Borrower360 as Borrower360Type, OfferRecommendation } from '../types';
import { registerActorScopedMemoryCache } from '../lib/actorScopedMemoryCaches';
import type { OutreachChannel } from './offer-orchestrator.constants';

/**
 * Module-scoped stale-while-revalidate cache for the three per-borrower
 * fetches (`api.borrower`, `api.recommendOffer`, `api.draftOutreach`).
 *
 * The backend's resilience layer already caches the portfolio preview,
 * but the per-borrower dossier path was unbuffered — navigating
 * back/forward between borrowers re-fired three API calls each trip.
 * This cache keeps a 5-minute TTL snapshot in memory so the user sees
 * instant hydration on revisit; the effect still re-fetches in the
 * background when the token increments so the data stays live.
 *
 * Hole-finder finding #23, 2026-04-23.
 */
export interface BorrowerCacheEntry {
  borrower: Borrower360Type;
  recommendation: OfferRecommendation;
  draftSubject: string | null;
  draftBody: string | null;
  draftChannel: OutreachChannel | null;
  fetched: number;
}

export const BORROWER_CACHE = new Map<string, BorrowerCacheEntry>();

const BORROWER_CACHE_TTL_MS = 5 * 60 * 1000;

export function readBorrowerCache(id: string): BorrowerCacheEntry | null {
  const hit = BORROWER_CACHE.get(id);
  if (!hit) return null;
  if (Date.now() - hit.fetched > BORROWER_CACHE_TTL_MS) {
    BORROWER_CACHE.delete(id);
    return null;
  }
  return hit;
}

export function clearBorrowerCache(id?: string | null): void {
  if (id) BORROWER_CACHE.delete(id);
  else BORROWER_CACHE.clear();
}

registerActorScopedMemoryCache(() => clearBorrowerCache());
