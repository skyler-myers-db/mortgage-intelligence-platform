/**
 * Evidence-source freshness buckets. Extracted from Primitives.tsx (re-audit
 * #4) so both EvidenceChip and the EvidenceHoverCard can import it without a
 * circular module dependency.
 *
 * Buckets from an evidence source's `updatedAt`:
 *   - fresh: updated within 7 days
 *   - aging: updated 7–30 days ago
 *   - stale: updated > 30 days ago
 *   - null:  no timestamp; render no dot (not a grey placeholder)
 *
 * Parses through lib/timeParse (2026-09-21 audit responsive-07): the
 * DRAWER_SOURCES form ("YYYY-MM-DD HH:MM UTC"), a naive backend timestamp
 * and ISO-8601 all mean UTC. `Date.parse` read the naive form as the
 * viewer's LOCAL time, which moved a chip across a bucket edge by hours.
 */
import { parseBackendTimestamp } from '../lib/timeParse';

export type FreshnessBucket = 'fresh' | 'aging' | 'stale';

export function freshnessBucket(updatedAt?: string, now: Date = new Date()): FreshnessBucket | null {
  const instant = parseBackendTimestamp(updatedAt);
  if (!instant) return null;
  const days = (now.getTime() - instant.getTime()) / (1000 * 60 * 60 * 24);
  if (days <= 7) return 'fresh';
  if (days <= 30) return 'aging';
  return 'stale';
}

export const FRESHNESS_LABEL: Record<FreshnessBucket, string> = {
  fresh: 'Fresh',
  aging: 'Aging',
  stale: 'Stale',
};
