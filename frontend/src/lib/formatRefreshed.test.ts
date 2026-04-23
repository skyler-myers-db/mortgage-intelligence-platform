import { describe, expect, it } from 'vitest';
import { formatRefreshed } from './formatRefreshed';

describe('formatRefreshed', () => {
  it('returns null for null / undefined / unparseable input', () => {
    expect(formatRefreshed(null)).toBeNull();
    expect(formatRefreshed(undefined)).toBeNull();
    expect(formatRefreshed('')).toBeNull();
    expect(formatRefreshed('not-a-date')).toBeNull();
  });

  it('returns viewer-local display + canonical ISO-UTC for a valid timestamp', () => {
    const input = '2026-04-23T20:07:00Z';
    const out = formatRefreshed(input);
    expect(out).not.toBeNull();
    // The display string is viewer-local (depends on the test runner
    // host timezone) — we only assert the invariant prefix so the test
    // is timezone-independent.
    expect(out?.display.startsWith('Refreshed ')).toBe(true);
    // iso is canonical ISO-8601 UTC, unambiguous across timezones.
    expect(out?.iso).toBe('2026-04-23T20:07:00.000Z');
  });

  it('preserves UTC instant when input has an offset other than Z', () => {
    // 2026-04-23T13:07 in America/Los_Angeles (-07:00) == 20:07 UTC.
    const out = formatRefreshed('2026-04-23T13:07:00-07:00');
    expect(out?.iso).toBe('2026-04-23T20:07:00.000Z');
  });
});
