import { describe, expect, it } from 'vitest';
import { formatDateTimeShort } from './asset';

describe('asset route copy formatters', () => {
  it('humanizes generated timestamps and attaches an explicit timezone', () => {
    const formatted = formatDateTimeShort('2026-06-05T20:57:54.123456Z');

    // No raw ISO leakage (the old `not.toContain('T')` assertion collided
    // with timezone abbreviations like EDT/PDT once zones were attached).
    expect(formatted).not.toMatch(/\d{4}-\d{2}-\d{2}T/);
    expect(formatted).not.toContain('.123456');
    // Human shape: "Jun 5, 2026, 4:57 PM EDT". Zone and clock style vary by
    // machine, and ICU emits U+202F narrow no-break space before AM/PM, so
    // assertions stay whitespace- and locale-tolerant. The contract: a
    // year, a clock time, and a trailing short timezone token so no
    // rendered time is ever ambiguous.
    expect(formatted).toContain('2026');
    expect(formatted).toMatch(/\d{1,2}:\d{2}/);
    expect(formatted).toMatch(/\s(?:[A-Z]{2,5}|GMT[+-]\d{1,2}(?::\d{2})?)$/);
  });

  it('keeps unavailable and invalid generated timestamps explicit', () => {
    expect(formatDateTimeShort(null)).toBe('Unavailable');
    // Invalid wire values render the shared sentinel instead of echoing
    // attacker-/bug-controlled raw strings into the UI (lib/time contract).
    expect(formatDateTimeShort('not-a-date')).toBe('timestamp unavailable');
  });
});
