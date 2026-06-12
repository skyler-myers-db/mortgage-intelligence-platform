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
 * Parses the DRAWER_SOURCES timestamp format ("YYYY-MM-DD HH:MM UTC"):
 * Date.parse handles the ISO-ish form on modern engines; the fallback
 * coerces the " UTC" suffix to ISO.
 */
export type FreshnessBucket = 'fresh' | 'aging' | 'stale';

export function freshnessBucket(updatedAt?: string, now: Date = new Date()): FreshnessBucket | null {
  if (!updatedAt) return null;
  let ms = Date.parse(updatedAt);
  if (Number.isNaN(ms)) {
    // "2026-04-20 06:12 UTC" → "2026-04-20T06:12:00Z"
    const normalized = updatedAt.replace(' ', 'T').replace(/\s*UTC\s*$/i, 'Z');
    ms = Date.parse(normalized);
  }
  if (Number.isNaN(ms)) return null;
  const days = (now.getTime() - ms) / (1000 * 60 * 60 * 24);
  if (days <= 7) return 'fresh';
  if (days <= 30) return 'aging';
  return 'stale';
}

export const FRESHNESS_LABEL: Record<FreshnessBucket, string> = {
  fresh: 'Fresh',
  aging: 'Aging',
  stale: 'Stale',
};
