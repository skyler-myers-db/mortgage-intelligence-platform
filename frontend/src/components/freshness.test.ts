/**
 * Freshness buckets and the backend timestamp parse (2026-09-21 audit
 * responsive-07). The evidence chips' dot and label read an evidence
 * source's `updatedAt`, which arrives naive ("2026-04-20 06:12"), with a UTC
 * suffix ("2026-04-20 06:12 UTC") or as ISO-8601. All three mean UTC. Run in
 * a zone west of UTC, where a naive string read as LOCAL time moves the
 * instant by hours and flips a bucket at its edge.
 */
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { freshnessBucket } from './freshness';
import { calendarDate, parseBackendTimestamp } from '../lib/timeParse';
import { parseBackendTimestamp as reexported } from '../lib/time';

declare const process: { env: Record<string, string | undefined> };

const originalTz = process.env.TZ;

beforeAll(() => {
  process.env.TZ = 'America/Los_Angeles';
});

afterAll(() => {
  process.env.TZ = originalTz;
});

describe('parseBackendTimestamp', () => {
  it('runs in a zone west of UTC (the precondition that makes the next cases bite)', () => {
    expect(new Date(2026, 3, 20, 6, 12).getTimezoneOffset()).toBeGreaterThan(0);
  });

  it('pins the naive, the " UTC"-suffixed and the ISO forms to the same UTC instant', () => {
    const expected = Date.UTC(2026, 3, 20, 6, 12);
    for (const wire of ['2026-04-20 06:12', '2026-04-20 06:12 UTC', '2026-04-20 06:12 utc', '2026-04-20T06:12:00Z', '2026-04-20T06:12']) {
      expect(parseBackendTimestamp(wire)?.getTime(), wire).toBe(expected);
    }
    expect(parseBackendTimestamp('2026-04-20 06:12:30.5 UTC')?.getTime()).toBe(Date.UTC(2026, 3, 20, 6, 12, 30, 500));
  });

  it('returns null for what does not parse, never a guessed instant', () => {
    for (const wire of ['', '   ', 'not a date', 'Apr 20 UTC', ' UTC', null, undefined]) {
      expect(parseBackendTimestamp(wire), String(wire)).toBeNull();
    }
    expect(parseBackendTimestamp(Number.NaN)).toBeNull();
  });

  it('keeps a calendar date a calendar date, and lib/time re-exports the same parser', () => {
    expect(calendarDate('2026-07-14')?.getTime()).toBe(Date.UTC(2026, 6, 14));
    expect(calendarDate('2026-07-14 00:00')).toBeNull();
    expect(reexported).toBe(parseBackendTimestamp);
  });
});

describe('freshnessBucket', () => {
  const at = (iso: string) => new Date(iso);

  it('buckets a naive UTC timestamp by its UTC instant, not the viewer clock', () => {
    // 7 days and 1 minute after 06:12 UTC: aging. Read as Los Angeles local
    // time the same string is 13:12 UTC, only 6 days 17 hours ago: fresh.
    expect(freshnessBucket('2026-04-20 06:12', at('2026-04-27T06:13:00Z'))).toBe('aging');
    expect(freshnessBucket('2026-04-20 06:12 UTC', at('2026-04-27T06:13:00Z'))).toBe('aging');
    expect(freshnessBucket('2026-04-20T06:12:00Z', at('2026-04-27T06:13:00Z'))).toBe('aging');
  });

  it('keeps the thresholds: 7 days fresh, 30 days aging, older stale', () => {
    const now = at('2026-05-30T12:00:00Z');
    expect(freshnessBucket('2026-05-23 12:00 UTC', now)).toBe('fresh');
    expect(freshnessBucket('2026-05-23 11:59 UTC', now)).toBe('aging');
    expect(freshnessBucket('2026-04-30 12:00 UTC', now)).toBe('aging');
    expect(freshnessBucket('2026-04-30 11:59 UTC', now)).toBe('stale');
  });

  it('renders no bucket for a missing or unparseable timestamp', () => {
    expect(freshnessBucket(undefined)).toBeNull();
    expect(freshnessBucket('')).toBeNull();
    expect(freshnessBucket('sometime last spring')).toBeNull();
  });
});
