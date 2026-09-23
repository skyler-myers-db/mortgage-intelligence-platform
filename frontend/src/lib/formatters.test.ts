/**
 * Unit contract for the shared display formatters. Each case here is a
 * defect the 2026-08-07 rendering audit found live, pinned so the fix can't
 * regress back into a per-call-site template.
 */

import { describe, expect, it, vi } from 'vitest';
import {
  bpsLabel,
  compactCurrency,
  currency,
  formatCompact,
  formatCount,
  formatFixed,
  formatNumber,
  formatPercent,
  formatUsd,
  formatUsdCompact,
  ltvPct,
  pct,
  rangeLabel,
  ratePct,
  ratePctFromFraction,
  signedBps,
  signedBpsLabel,
  signedPct,
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

  it('builds its Intl formatter once, not once per call', () => {
    // responsive-04: `currency()` constructed a new Intl.NumberFormat on
    // every call, i.e. per table cell on a 120-row queue.
    const construct = vi.spyOn(Intl, 'NumberFormat');
    try {
      for (let i = 0; i < 50; i += 1) currency(1_000 + i);
      formatUsdCompact(4_410_000);
      formatCount(89_553);
      expect(construct).not.toHaveBeenCalled();
    } finally {
      construct.mockRestore();
    }
  });

  it('renders the unknown glyph for a non-finite value, never "$NaN"', () => {
    expect(currency(Number.NaN)).toBe('—');
    expect(formatUsd(null)).toBe('—');
  });

  it('puts the sign before the symbol and never renders "-$0"', () => {
    expect(formatUsd(-1_250)).toBe('-$1,250');
    expect(formatUsd(-0.4)).toBe('$0');
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

describe('ltvPct', () => {
  it('renders a reported display LTV as a whole percent', () => {
    expect(ltvPct(54)).toBe('54%');
    expect(ltvPct(0)).toBe('0%'); // a REPORTED 0 is a real value (free and clear)
    expect(ltvPct(137)).toBe('137%'); // underwater borrowers exceed 100
  });

  it('renders the unknown glyph for a withheld LTV, never "null%"', () => {
    // 2026-09-21 audit (quality-04): the wire type is `int | None`.
    expect(ltvPct(null)).toBe('—');
    expect(ltvPct(undefined)).toBe('—');
    expect(ltvPct(Number.NaN)).toBe('—');
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

/*
 * 2026-09-21 audit (responsive-04): one number contract for every route.
 * The goldens below are the defects the audit rendered live.
 */
describe('formatCount', () => {
  it('renders a whole count with en-US grouping, the same text on every route', () => {
    // Home's KpiCard and Analytics' executive KPI both render this figure;
    // Analytics used to abbreviate it to "89.55K".
    expect(formatCount(89_553)).toBe('89,553');
    expect(formatCount(5_156_184)).toBe('5,156,184');
    expect(formatCount(0)).toBe('0');
  });

  it('rounds half up like the KPI cards always did, and never renders "-0"', () => {
    expect(formatCount(12.5)).toBe('13');
    expect(formatCount(12.4)).toBe('12');
    expect(formatCount(-0.4)).toBe('0');
  });

  it('renders the unknown glyph for a missing or non-finite value', () => {
    expect(formatCount(null)).toBe('—');
    expect(formatCount(undefined)).toBe('—');
    expect(formatCount(Number.NaN)).toBe('—');
  });
});

describe('formatNumber / formatFixed', () => {
  it('keeps up to three fraction digits, as a bare toLocaleString() did under en-US', () => {
    expect(formatNumber(1_234.5678)).toBe('1,234.568');
    expect(formatNumber(0.5)).toBe('0.5');
    expect(formatNumber(42)).toBe('42');
  });

  it('renders exactly the requested fraction digits, grouped', () => {
    expect(formatFixed(0.87432, 3)).toBe('0.874');
    expect(formatFixed(1_234.56, 1)).toBe('1,234.6');
    expect(formatFixed(7, 2)).toBe('7.00');
    expect(formatFixed(-0.04, 1)).toBe('0.0');
    expect(formatFixed(null, 2)).toBe('—');
  });
});

describe('formatCompact', () => {
  it('renders chart ticks and bar values at one fraction digit', () => {
    // The audit read axis ticks as "24.1K / 18.08K": one tick at one
    // decimal, the next at two.
    expect(formatCompact(24_100)).toBe('24.1K');
    expect(formatCompact(18_080)).toBe('18.1K');
    expect(formatCompact(89_553)).toBe('89.6K');
    expect(formatCompact(1_234_567)).toBe('1.2M');
    expect(formatCompact(950)).toBe('950');
  });

  it('never renders two fraction digits at any magnitude', () => {
    for (const value of [1_005, 18_080, 99_949, 1_234_567, 987_654_321, 12.345]) {
      expect(formatCompact(value)).not.toMatch(/\.\d{2}/);
    }
  });

  it('renders zero unsigned and an unknown as the glyph', () => {
    expect(formatCompact(-0.04)).toBe('0');
    expect(formatCompact(null)).toBe('—');
  });
});

describe('formatUsdCompact', () => {
  it('puts a negative sign before the currency symbol, never after it', () => {
    // analytics.lib fmtCurrency prefixed "$" to a signed compact number and
    // rendered a negative equity total as "$-4.41M".
    expect(formatUsdCompact(-4_410_000)).toBe('-$4.4M');
    expect(formatUsdCompact(-950)).toBe('-$950');
    expect(formatUsdCompact(-1_680)).toBe('-$2K');
    for (const value of [-1, -950, -12_000, -999_600, -4_410_000, -2_300_000_000]) {
      expect(formatUsdCompact(value)).not.toContain('$-');
    }
  });

  it('rolls the thousands unit over to millions once it rounds to 1000', () => {
    // portfolio-builder formatUsdCompact chose the unit from the raw value
    // and rendered 999,600 as "$1000K".
    expect(formatUsdCompact(999_600)).toBe('$1.0M');
    expect(formatUsdCompact(999_500)).toBe('$1.0M');
    expect(formatUsdCompact(999_499)).toBe('$999K');
    expect(formatUsdCompact(1_000_000)).toBe('$1.0M');
    expect(formatUsdCompact(-999_600)).toBe('-$1.0M');
  });

  it('never prints a four-digit figure in any unit', () => {
    expect(formatUsdCompact(999.6)).toBe('$1K');
    expect(formatUsdCompact(999_950_000)).toBe('$1.0B');
    expect(formatUsdCompact(999_950_000_000)).toBe('$1.0T');
    for (const value of [999.4, 999.6, 999_499, 999_600, 999_940_000, 999_960_000]) {
      expect(formatUsdCompact(value)).not.toMatch(/\d{4}/);
    }
  });

  it('keeps whole thousands and one decimal from millions up', () => {
    expect(formatUsdCompact(244_800)).toBe('$245K');
    expect(formatUsdCompact(24_100)).toBe('$24K');
    expect(formatUsdCompact(16_320_000)).toBe('$16.3M');
    expect(formatUsdCompact(2_300_000_000)).toBe('$2.3B');
    expect(formatUsdCompact(1_500_000_000_000)).toBe('$1.5T');
    expect(formatUsdCompact(940)).toBe('$940');
  });

  it('renders zero unsigned, including a negative that rounds to zero', () => {
    expect(formatUsdCompact(0)).toBe('$0');
    expect(formatUsdCompact(-0)).toBe('$0');
    expect(formatUsdCompact(-0.4)).toBe('$0');
  });

  it('renders an explicit unknown for a missing or non-finite value', () => {
    expect(formatUsdCompact(null)).toBe('—');
    expect(formatUsdCompact(undefined)).toBe('—');
    expect(formatUsdCompact(Number.NaN)).toBe('—');
    expect(formatUsdCompact(Number.POSITIVE_INFINITY)).toBe('—');
  });

  it('is the one compact-USD contract: compactCurrency is the same function', () => {
    for (const value of [-4_410_000, -950, 0, 950, 999_600, 806_500, 2_300_000_000]) {
      expect(compactCurrency(value)).toBe(formatUsdCompact(value));
    }
  });
});

describe('percent formatters', () => {
  it('formats a fraction-form ratio as a percent', () => {
    expect(formatPercent(0.0427)).toBe('4.3%');
    expect(formatPercent(1)).toBe('100.0%');
    expect(formatPercent(0.5, 0)).toBe('50%');
    expect(formatPercent(-0.0004)).toBe('0.0%');
    expect(formatPercent(null)).toBe('—');
  });

  it('formats a percent-form value at one decimal by default', () => {
    expect(pct(42.37)).toBe('42.4%');
    expect(pct(3, 0)).toBe('3%');
    expect(pct(undefined)).toBe('—');
  });

  it('signs a KPI delta through Intl signDisplay, never "-0.0%"', () => {
    expect(signedPct(3.24)).toBe('+3.2%');
    expect(signedPct(-1.5)).toBe('-1.5%');
    expect(signedPct(0)).toBe('0.0%');
    // The hand-rolled `${sign}${pct.toFixed(1)}%` printed "-0.0%" here.
    expect(signedPct(-0.04)).toBe('0.0%');
    expect(signedPct(null)).toBe('—');
  });
});

describe('basis points', () => {
  it('signs the value through Intl signDisplay and groups it', () => {
    expect(signedBps(1_250)).toBe('+1,250');
    expect(signedBps(-1_250)).toBe('-1,250');
    expect(signedBpsLabel(167.4)).toBe('+167 bps');
  });

  it('renders an unsigned magnitude for prose that states the direction', () => {
    expect(bpsLabel(180)).toBe('180 bps');
    expect(bpsLabel(167.6)).toBe('168 bps');
    expect(bpsLabel(null)).toBe('—');
  });
});
