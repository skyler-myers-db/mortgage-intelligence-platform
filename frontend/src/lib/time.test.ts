import { describe, expect, it, vi } from 'vitest';

import {
  DATE_UNKNOWN,
  formatDate,
  formatDateTimeShort,
  formatRelative,
  formatTimeOfDay,
  formatTimestamp,
  formatUtcTitle,
  isoDateTimeAttr,
  parseBackendTimestamp,
  TIMESTAMP_UNAVAILABLE,
} from './time';

// All assertions pass explicit timeZone/locale so CI machines in any zone
// produce identical strings.
const UTC = { timeZone: 'UTC', locale: 'en-US' } as const;

describe('parseBackendTimestamp', () => {
  it('pins naive SQL timestamps to UTC instead of viewer-local time', () => {
    const parsed = parseBackendTimestamp('2026-06-11 03:32:18.767056');
    expect(parsed?.toISOString()).toBe('2026-06-11T03:32:18.767Z');
  });

  it('parses ISO-8601 Z strings to the same instant', () => {
    const parsed = parseBackendTimestamp('2026-06-11T03:32:18.767Z');
    expect(parsed?.toISOString()).toBe('2026-06-11T03:32:18.767Z');
  });

  it('parses seconds-less naive timestamps', () => {
    const parsed = parseBackendTimestamp('2026-06-11 03:32');
    expect(parsed?.toISOString()).toBe('2026-06-11T03:32:00.000Z');
  });

  it('accepts epoch milliseconds and Date instances', () => {
    const epoch = Date.UTC(2026, 5, 11, 3, 32, 18);
    expect(parseBackendTimestamp(epoch)?.toISOString()).toBe(
      '2026-06-11T03:32:18.000Z',
    );
    expect(parseBackendTimestamp(new Date(epoch))?.getTime()).toBe(epoch);
  });

  it('returns null for empty, null, and garbage inputs', () => {
    expect(parseBackendTimestamp(null)).toBeNull();
    expect(parseBackendTimestamp(undefined)).toBeNull();
    expect(parseBackendTimestamp('')).toBeNull();
    expect(parseBackendTimestamp('not a time')).toBeNull();
  });
});

describe('formatTimestamp', () => {
  it('renders date, time, and an explicit zone name', () => {
    expect(formatTimestamp('2026-06-11 03:32:18.767056', UTC)).toBe(
      'Jun 11, 2026, 3:32 AM UTC',
    );
  });

  it('renders the SAME instant for naive and Z-suffixed wire shapes', () => {
    const naive = formatTimestamp('2026-06-11 03:32:18.767056', UTC);
    const iso = formatTimestamp('2026-06-11T03:32:18.767Z', UTC);
    expect(naive).toBe(iso);
  });

  it('always carries a zone token in the viewer zone too', () => {
    // Whatever the machine zone is, a short zone name must be present.
    const rendered = formatTimestamp('2026-06-11T03:32:18.767Z', {
      locale: 'en-US',
    });
    expect(rendered).toMatch(/ (?:[A-Z]{2,5}|GMT[+-]\d{1,2}(?::\d{2})?)$/);
  });

  it('supports year-less and seconds variants', () => {
    expect(
      formatTimestamp('2026-06-11 03:32:18', { ...UTC, withYear: false }),
    ).toBe('Jun 11, 3:32 AM UTC');
    expect(
      formatTimestamp('2026-06-11 03:32:18', { ...UTC, withSeconds: true }),
    ).toBe('Jun 11, 2026, 3:32:18 AM UTC');
  });

  it('falls back to the unavailable sentinel', () => {
    expect(formatTimestamp(null, UTC)).toBe(TIMESTAMP_UNAVAILABLE);
    expect(formatTimestamp('nonsense', UTC)).toBe(TIMESTAMP_UNAVAILABLE);
  });
});

describe('formatTimeOfDay', () => {
  it('renders a 24h clock with an explicit zone for audit rows', () => {
    expect(formatTimeOfDay('2026-06-11 14:05:31', UTC)).toBe('14:05:31 UTC');
  });

  it('falls back to the unavailable sentinel', () => {
    expect(formatTimeOfDay(undefined, UTC)).toBe(TIMESTAMP_UNAVAILABLE);
  });
});

/*
 * 2026-09-21 audit (responsive-07): one date vocabulary. `NOW` is the
 * fixture harness's frozen clock; the snapshot is three hours before it.
 */
const NOW = Date.parse('2026-07-14T15:00:00Z');
const SNAPSHOT = '2026-07-14T12:00:00Z';

describe('formatDate', () => {
  it('renders a calendar date without shifting it in a zone west of UTC', () => {
    // A gold snapshot_date is a calendar date. Parsed with `new Date()` it is
    // UTC midnight, which a New York viewer would see as the day before.
    expect(formatDate('2026-07-14', { now: NOW })).toBe('Jul 14');
    expect(formatDate('2026-07-14', { now: NOW, timeZone: 'America/Los_Angeles' })).toBe('Jul 14');
  });

  it('renders an instant in the requested zone', () => {
    expect(formatDate('2026-07-14T02:00:00Z', { now: NOW, timeZone: 'America/New_York' })).toBe('Jul 13');
    expect(formatDate('2026-07-14 02:00:00', { now: NOW, timeZone: 'UTC' })).toBe('Jul 14');
  });

  it('includes the year once the date is more than 11 months from now', () => {
    expect(formatDate('2025-07-14', { now: NOW })).toBe('Jul 14, 2025');
    expect(formatDate('2025-09-01', { now: NOW })).toBe('Sep 1');
    expect(formatDate('2026-07-14', { now: NOW, withYear: true })).toBe('Jul 14, 2026');
  });

  it('renders the unknown glyph for a missing or unparseable value', () => {
    expect(formatDate(null)).toBe(DATE_UNKNOWN);
    expect(formatDate('not a date')).toBe(DATE_UNKNOWN);
  });
});

describe('formatDateTimeShort', () => {
  it('pins a naive wire timestamp to UTC and always names the zone', () => {
    // The duplicated LeadTable / Borrower 360 formatter parsed this naive
    // shape as viewer-local time and printed no zone at all.
    const opts = { now: NOW, timeZone: 'America/New_York' } as const;
    expect(formatDateTimeShort('2026-07-14 12:00:00', opts)).toBe('Jul 14, 8:00 AM EDT');
    expect(formatDateTimeShort('2026-07-14T12:00:00Z', opts)).toBe('Jul 14, 8:00 AM EDT');
  });

  it('includes the year for a stale instant', () => {
    expect(formatDateTimeShort('2025-06-01T12:00:00Z', { now: NOW, timeZone: 'UTC' })).toBe('Jun 1, 2025, 12:00 PM UTC');
  });

  it('renders the unknown glyph, not the raw wire text, for a bad value', () => {
    expect(formatDateTimeShort(null)).toBe(DATE_UNKNOWN);
    expect(formatDateTimeShort('')).toBe(DATE_UNKNOWN);
    expect(formatDateTimeShort('garbage')).toBe(DATE_UNKNOWN);
  });
});

describe('formatRelative', () => {
  const ago = (ms: number) => new Date(NOW - ms);
  const MIN = 60_000;
  const HOUR = 60 * MIN;
  const DAY = 24 * HOUR;

  it('reads a freshness age in plain words', () => {
    expect(formatRelative(SNAPSHOT, { now: NOW })).toBe('3 hours ago');
    expect(formatRelative(ago(20_000), { now: NOW })).toBe('now');
    expect(formatRelative(ago(12 * MIN), { now: NOW })).toBe('12 minutes ago');
    expect(formatRelative(ago(DAY), { now: NOW })).toBe('yesterday');
    expect(formatRelative(ago(5 * DAY), { now: NOW })).toBe('5 days ago');
    expect(formatRelative(ago(40 * DAY), { now: NOW })).toBe('last month');
    expect(formatRelative(ago(150 * DAY), { now: NOW })).toBe('5 months ago');
    expect(formatRelative(new Date(NOW + 3 * DAY), { now: NOW })).toBe('in 3 days');
  });

  it('switches to the absolute date WITH the year past 11 months', () => {
    expect(formatRelative('2025-07-01T12:00:00Z', { now: NOW })).toBe('Jul 1, 2025');
  });

  it('renders the prototype trigger-timeline form in the narrow style', () => {
    // design_files/Design System.html `.trig__when`: "2D AGO" (uppercased by CSS).
    expect(formatRelative(ago(2 * DAY), { now: NOW, style: 'narrow' })).toBe('2d ago');
    expect(formatRelative(ago(150 * DAY), { now: NOW, style: 'narrow' })).toBe('5mo ago');
  });

  it('renders the unknown glyph for a missing value', () => {
    expect(formatRelative(undefined, { now: NOW })).toBe(DATE_UNKNOWN);
  });

  it('builds each Intl formatter once, not once per call', () => {
    formatRelative(SNAPSHOT, { now: NOW, style: 'short' });
    formatTimestamp(SNAPSHOT, { timeZone: 'Asia/Tokyo' });
    const relative = vi.spyOn(Intl, 'RelativeTimeFormat');
    const dateTime = vi.spyOn(Intl, 'DateTimeFormat');
    try {
      for (let i = 0; i < 20; i += 1) {
        formatRelative(SNAPSHOT, { now: NOW, style: 'short' });
        formatTimestamp(SNAPSHOT, { timeZone: 'Asia/Tokyo' });
      }
      expect(relative).not.toHaveBeenCalled();
      expect(dateTime).not.toHaveBeenCalled();
    } finally {
      relative.mockRestore();
      dateTime.mockRestore();
    }
  });
});

describe('formatUtcTitle / isoDateTimeAttr', () => {
  it('gives the absolute UTC instant for the tooltip', () => {
    expect(formatUtcTitle(SNAPSHOT)).toBe('Jul 14, 2026, 12:00 PM UTC');
    expect(formatUtcTitle('2026-07-14 12:00:00')).toBe('Jul 14, 2026, 12:00 PM UTC');
    expect(formatUtcTitle('2026-07-14')).toBe('Jul 14, 2026');
    expect(formatUtcTitle('nope')).toBe('');
  });

  it('gives a valid <time dateTime> value', () => {
    expect(isoDateTimeAttr('2026-07-14 12:00:00')).toBe('2026-07-14T12:00:00.000Z');
    expect(isoDateTimeAttr('2026-07-14')).toBe('2026-07-14');
    expect(isoDateTimeAttr(null)).toBeNull();
  });
});

describe('formatTimestamp year display', () => {
  it("shows the year only past 11 months under withYear: 'auto'", () => {
    expect(formatTimestamp(SNAPSHOT, { ...UTC, withYear: 'auto', now: NOW })).toBe('Jul 14, 12:00 PM UTC');
    expect(formatTimestamp('2025-06-01T12:00:00Z', { ...UTC, withYear: 'auto', now: NOW })).toBe('Jun 1, 2025, 12:00 PM UTC');
  });
});
