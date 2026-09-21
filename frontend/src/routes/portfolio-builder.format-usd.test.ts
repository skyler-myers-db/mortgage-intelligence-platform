/**
 * formatUsdCompact boundary contract (2026-09-21 audit, responsive-04).
 *
 * The defect: the unit was chosen from the RAW value, so 999,600 took the
 * thousands branch and rounded up into "$1000K". The unit must be chosen
 * after rounding, at every boundary, and the sign must sit before the "$".
 */
import { describe, expect, it } from 'vitest';
import { formatUsdCompact } from './portfolio-builder.logic';

describe('formatUsdCompact boundaries', () => {
  it('rolls the thousands unit over to millions once it rounds to 1000', () => {
    expect(formatUsdCompact(999_499)).toBe('$999K'); // rounds down: stays K
    expect(formatUsdCompact(999_500)).toBe('$1.0M'); // rounds to 1000K: promote
    expect(formatUsdCompact(999_600)).toBe('$1.0M'); // the audited value
    expect(formatUsdCompact(1_000_000)).toBe('$1.0M');
  });

  it('never prints a four-digit figure in any unit', () => {
    expect(formatUsdCompact(999.6)).toBe('$1K');
    expect(formatUsdCompact(999_950_000)).toBe('$1.0B');
    expect(formatUsdCompact(999_950_000_000)).toBe('$1.0T');
    for (const value of [999.4, 999.6, 999_499, 999_600, 999_940_000, 999_960_000]) {
      expect(formatUsdCompact(value)).not.toMatch(/\d{4}/);
    }
  });

  it('puts the sign before the currency symbol at the same boundaries', () => {
    expect(formatUsdCompact(-999_499)).toBe('-$999K');
    expect(formatUsdCompact(-999_600)).toBe('-$1.0M');
    expect(formatUsdCompact(-4_410_000)).toBe('-$4.4M');
    expect(formatUsdCompact(-1_680)).toBe('-$2K');
  });

  it('renders zero unsigned, including a negative that rounds to zero', () => {
    expect(formatUsdCompact(0)).toBe('$0');
    expect(formatUsdCompact(-0)).toBe('$0');
    expect(formatUsdCompact(-0.4)).toBe('$0');
  });

  it('renders an explicit unknown for a non-finite value', () => {
    expect(formatUsdCompact(Number.NaN)).toBe('—');
    expect(formatUsdCompact(Number.POSITIVE_INFINITY)).toBe('—');
  });

  it('leaves every previously pinned value unchanged', () => {
    expect(formatUsdCompact(244_800)).toBe('$245K');
    expect(formatUsdCompact(16_320_000)).toBe('$16.3M');
    expect(formatUsdCompact(2_300_000_000)).toBe('$2.3B');
    expect(formatUsdCompact(1_500_000_000_000)).toBe('$1.5T');
    expect(formatUsdCompact(940)).toBe('$940');
  });
});
