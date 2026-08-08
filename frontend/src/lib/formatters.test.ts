/**
 * Unit contract for the shared display formatters. Each case here is a
 * defect the 2026-08-07 rendering audit found live, pinned so the fix can't
 * regress back into a per-call-site template.
 */

import { describe, expect, it } from 'vitest';
import {
  compactCurrency,
  currency,
  rangeLabel,
  ratePct,
  ratePctFromFraction,
  signedBps,
  signedBpsLabel,
} from './formatters';

describe('signedBps', () => {
  it('prefixes a positive spread and never double-signs a negative one', () => {
    expect(signedBps(180)).toBe('+180');
    // The C1 defect: `+{value}` on a signed field rendered "+-422".
    expect(signedBps(-422)).toBe('-422');
  });

  it('renders an at-market spread without a sign', () => {
    expect(signedBps(0)).toBe('0');
    // Math.round(-0.4) is -0 — must not surface as "-0".
    expect(signedBps(-0.4)).toBe('0');
  });

  it('rounds to whole basis points', () => {
    expect(signedBps(167.4)).toBe('+167');
    expect(signedBps(-167.6)).toBe('-168');
  });

  it('renders an em dash for a non-finite value rather than NaN', () => {
    expect(signedBps(Number.NaN)).toBe('—');
    expect(signedBps(Number.POSITIVE_INFINITY)).toBe('—');
  });
});

describe('signedBpsLabel', () => {
  it('carries the unit on every branch', () => {
    expect(signedBpsLabel(180)).toBe('+180 bps');
    expect(signedBpsLabel(-422)).toBe('-422 bps');
    expect(signedBpsLabel(0)).toBe('0 bps');
  });

  it('does not render "— bps" for an unknown value', () => {
    expect(signedBpsLabel(Number.NaN)).toBe('—');
  });
});

describe('compactCurrency', () => {
  it('rolls up to millions instead of rendering $4410K', () => {
    expect(compactCurrency(4_410_000)).toBe('$4.4M');
    expect(compactCurrency(1_389_000)).toBe('$1.4M');
  });

  it('keeps the thousands form under $1M', () => {
    expect(compactCurrency(806_500)).toBe('$807K');
    expect(compactCurrency(520_000)).toBe('$520K');
    expect(compactCurrency(37_400)).toBe('$37K');
  });

  it('applies the K→M boundary after rounding', () => {
    // Rounds to 1000K — must promote rather than render "$1000K".
    expect(compactCurrency(999_600)).toBe('$1.0M');
    expect(compactCurrency(999_000)).toBe('$999K');
  });

  it('renders sub-thousand and negative values honestly', () => {
    expect(compactCurrency(950)).toBe('$950');
    expect(compactCurrency(0)).toBe('$0');
    expect(compactCurrency(-12_000)).toBe('-$12K');
  });

  it('renders an em dash for a non-finite value', () => {
    expect(compactCurrency(Number.NaN)).toBe('—');
  });
});

describe('rate percent conventions', () => {
  it('pins percent-form rates to two decimals', () => {
    expect(ratePct(2.27)).toBe('2.27%');
    // 0.07 * 100 in SQL reaches the UI as 7.000000000000001.
    expect(ratePct(7.000000000000001)).toBe('7.00%');
  });

  it('scales fraction-form rates exactly once, at the same precision', () => {
    expect(ratePctFromFraction(0.0649)).toBe('6.49%');
    expect(ratePctFromFraction(0.06125)).toBe('6.13%');
  });

  it('renders an em dash for a non-finite rate', () => {
    expect(ratePct(Number.NaN)).toBe('—');
    expect(ratePctFromFraction(Number.NaN)).toBe('—');
  });
});

describe('currency', () => {
  it('renders whole dollars with thousands separators', () => {
    expect(currency(806_500)).toBe('$806,500');
  });
});

describe('signedBpsLabel spacing', () => {
  it('separates magnitude and unit with exactly one space', () => {
    // A 2026-08-08 UX walk read the Borrower 360 refi panel as "+330  bps".
    // Every bps string in the product comes from this one formatter, and it
    // emits a single space -- pinned here so a future call site cannot
    // reintroduce a hand-rolled `{value} + ' bps'` template with two.
    expect(signedBpsLabel(330)).toBe('+330 bps');
    for (const value of [330, -422, 0, 1_250]) {
      expect(signedBpsLabel(value)).not.toMatch(/ {2}/);
      expect(signedBpsLabel(value).split(' ')).toHaveLength(2);
    }
  });
});

describe('rangeLabel', () => {
  it('collapses a degenerate band to its single value', () => {
    // Live 2026-08-08: the estimated-UPB chip read "$100,000-$100,000".
    expect(rangeLabel(100_000, 100_000, currency)).toBe('$100,000');
  });

  it('keeps a real band as a range', () => {
    expect(rangeLabel(95_000, 105_000, currency)).toBe('$95,000-$105,000');
  });

  it('collapses ends that round to the same displayed value', () => {
    expect(rangeLabel(100_000.2, 100_000.4, currency)).toBe('$100,000');
  });

  it('works with any formatter, not just currency', () => {
    expect(rangeLabel(4_410_000, 4_410_000, compactCurrency)).toBe('$4.4M');
  });
});
