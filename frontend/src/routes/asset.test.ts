import { describe, expect, it } from 'vitest';
import { formatDateTimeShort } from './asset';

describe('asset route copy formatters', () => {
  it('humanizes generated timestamps instead of showing raw microsecond ISO text', () => {
    const formatted = formatDateTimeShort('2026-06-05T20:57:54.123456Z');

    expect(formatted).not.toContain('T');
    expect(formatted).not.toContain('.123456');
    expect(formatted).not.toContain('Z');
    expect(formatted).toMatch(/\d/);
  });

  it('keeps unavailable and invalid generated timestamps explicit', () => {
    expect(formatDateTimeShort(null)).toBe('Unavailable');
    expect(formatDateTimeShort('not-a-date')).toBe('not-a-date');
  });
});
