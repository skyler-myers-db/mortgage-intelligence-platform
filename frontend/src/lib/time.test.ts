import { describe, expect, it } from 'vitest';

import {
  formatTimeOfDay,
  formatTimestamp,
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
