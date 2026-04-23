import { describe, expect, it } from 'vitest';
import { freshnessBucket } from './Primitives';

/**
 * Freshness bucketing drives the visible dot beside every evidence chip.
 * These tests pin the 7-day / 30-day thresholds so a refactor can't
 * silently drift the "Fresh" / "Aging" / "Stale" semantics operators
 * read on the Lead Queue and Borrower 360 pages.
 */
describe('freshnessBucket', () => {
  const now = new Date('2026-04-22T12:00:00Z');

  it('returns null when updatedAt is missing', () => {
    expect(freshnessBucket(undefined, now)).toBeNull();
    expect(freshnessBucket('', now)).toBeNull();
  });

  it('returns null for unparseable timestamps (no grey placeholder)', () => {
    expect(freshnessBucket('not a date', now)).toBeNull();
  });

  it('treats <= 7 days as fresh', () => {
    expect(freshnessBucket('2026-04-20 06:12 UTC', now)).toBe('fresh');
    expect(freshnessBucket('2026-04-22T00:00:00Z', now)).toBe('fresh');
  });

  it('treats 7–30 days as aging', () => {
    // 14 days ago
    expect(freshnessBucket('2026-04-08T12:00:00Z', now)).toBe('aging');
    // exactly 30 days ago (boundary — still aging)
    expect(freshnessBucket('2026-03-23T12:00:00Z', now)).toBe('aging');
  });

  it('treats > 30 days as stale', () => {
    expect(freshnessBucket('2026-03-01T12:00:00Z', now)).toBe('stale');
    expect(freshnessBucket('2025-12-01T12:00:00Z', now)).toBe('stale');
  });
});
