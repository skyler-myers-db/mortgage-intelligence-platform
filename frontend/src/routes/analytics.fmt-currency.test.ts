/**
 * fmtCurrency sign + boundary contract (2026-09-21 audit, responsive-04).
 *
 * The defect: "$" was prefixed to an already-signed compact number, so a
 * negative total rendered "$-4.41M". The sign belongs before the symbol.
 */
import { describe, expect, it } from 'vitest';
import { fmtCurrency } from './analytics.lib';

describe('fmtCurrency', () => {
  it('puts a negative sign before the currency symbol, never after it', () => {
    expect(fmtCurrency(-4_410_000)).toBe('-$4.41M');
    expect(fmtCurrency(-999_600)).toBe('-$999.6K');
    expect(fmtCurrency(-950)).toBe('-$950');
    for (const value of [-1, -950, -12_000, -999_600, -4_410_000, -2_300_000_000]) {
      expect(fmtCurrency(value)).not.toContain('$-');
    }
  });

  it('rolls thousands over to millions at the rounding boundary', () => {
    expect(fmtCurrency(999_499)).toBe('$999.5K');
    expect(fmtCurrency(999_500)).toBe('$999.5K');
    expect(fmtCurrency(999_600)).toBe('$999.6K');
    expect(fmtCurrency(999_996)).toBe('$1M'); // rounds past 999.99K: promote, never "$1000K"
    expect(fmtCurrency(1_000_000)).toBe('$1M');
    expect(fmtCurrency(-999_996)).toBe('-$1M');
  });

  it('renders zero unsigned, including a negative that rounds to zero', () => {
    expect(fmtCurrency(0)).toBe('$0');
    expect(fmtCurrency(-0)).toBe('$0');
    expect(fmtCurrency(-0.001)).toBe('$0');
  });

  it('renders an explicit unknown for null, undefined and non-finite input', () => {
    expect(fmtCurrency(null)).toBe('—');
    expect(fmtCurrency(undefined)).toBe('—');
    expect(fmtCurrency(Number.NaN)).toBe('—');
  });

  it('leaves non-negative output byte-identical to the previous formatter', () => {
    expect(fmtCurrency(4_410_000)).toBe('$4.41M');
    expect(fmtCurrency(24_100)).toBe('$24.1K');
    expect(fmtCurrency(1_234_567_890)).toBe('$1.23B');
  });
});
