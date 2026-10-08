import { describe, expect, it } from 'vitest';
import {
  normalizedParams,
  paramsForPeriod,
  parseIsoDay,
  periodEyebrow,
  previousWindow,
  resolveSalesPeriod,
} from './analytics.sales-ops.period';

// Wednesday 2026-10-07, local time.
const TODAY = new Date(2026, 9, 7, 15, 30);

function resolve(query: string) {
  return resolveSalesPeriod(new URLSearchParams(query), TODAY);
}

describe('resolveSalesPeriod', () => {
  it('defaults to week to date from Monday', () => {
    expect(resolve('')).toEqual({ preset: 'wtd', from: '2026-10-05', to: '2026-10-07', days: 3 });
  });

  it('starts a Sunday week on the previous Monday', () => {
    expect(resolveSalesPeriod(new URLSearchParams(''), new Date(2026, 9, 11))).toMatchObject({ from: '2026-10-05', to: '2026-10-11' });
  });

  it('resolves every preset', () => {
    expect(resolve('sales_period=yesterday')).toMatchObject({ from: '2026-10-06', to: '2026-10-06', days: 1 });
    expect(resolve('sales_period=7d')).toMatchObject({ from: '2026-10-01', to: '2026-10-07', days: 7 });
    expect(resolve('sales_period=30d')).toMatchObject({ from: '2026-09-08', to: '2026-10-07', days: 30 });
    expect(resolve('sales_period=mtd')).toMatchObject({ from: '2026-10-01', to: '2026-10-07', days: 7 });
  });

  it('reads a valid custom range', () => {
    expect(resolve('sales_period=custom&sales_from=2026-07-01&sales_to=2026-09-30')).toEqual({
      preset: 'custom', from: '2026-07-01', to: '2026-09-30', days: 92,
    });
  });

  it.each([
    ['an unknown preset', 'sales_period=quarter'],
    ['a range over 92 days', 'sales_period=custom&sales_from=2026-06-30&sales_to=2026-09-30'],
    ['a future end', 'sales_period=custom&sales_from=2026-10-01&sales_to=2026-10-08'],
    ['a backwards range', 'sales_period=custom&sales_from=2026-10-03&sales_to=2026-10-01'],
    ['an impossible date', 'sales_period=custom&sales_from=2026-02-30&sales_to=2026-03-02'],
    ['a malformed date', 'sales_period=custom&sales_from=10/01/2026&sales_to=2026-10-02'],
  ])('falls back to week to date for %s and normalises the URL', (_, query) => {
    expect(resolve(query).preset).toBe('wtd');
    const cleaned = normalizedParams(new URLSearchParams(`view=sales-ops&${query}`), TODAY);
    expect(cleaned?.toString()).toBe('view=sales-ops');
  });

  it('leaves a clean URL alone and drops stray range keys from a preset', () => {
    expect(normalizedParams(new URLSearchParams('view=sales-ops&sales_period=7d'), TODAY)).toBeNull();
    expect(normalizedParams(new URLSearchParams('view=sales-ops'), TODAY)).toBeNull();
    expect(normalizedParams(new URLSearchParams('sales_period=7d&sales_from=2026-10-01'), TODAY)?.toString()).toBe('sales_period=7d');
  });
});

describe('paramsForPeriod', () => {
  it('writes the preset, drops it for the default, and keeps other params', () => {
    const base = new URLSearchParams('view=sales-ops&sales_period=7d');
    expect(paramsForPeriod(base, 'wtd').toString()).toBe('view=sales-ops');
    expect(paramsForPeriod(base, 'mtd').toString()).toBe('view=sales-ops&sales_period=mtd');
    expect(paramsForPeriod(base, 'custom', { from: '2026-09-01', to: '2026-09-30' }).toString()).toBe(
      'view=sales-ops&sales_period=custom&sales_from=2026-09-01&sales_to=2026-09-30',
    );
  });
});

describe('previousWindow and the eyebrow', () => {
  it('is the same number of days immediately before from', () => {
    expect(previousWindow(resolve('sales_period=7d'))).toEqual({ from: '2026-09-24', to: '2026-09-30' });
    expect(previousWindow(resolve('sales_period=yesterday'))).toEqual({ from: '2026-10-05', to: '2026-10-05' });
  });

  it('names the window on the conversion card, a custom range in the short date form', () => {
    expect(periodEyebrow(resolve(''), TODAY)).toBe('Week-to-date conversion');
    expect(periodEyebrow(resolve('sales_period=custom&sales_from=2026-09-01&sales_to=2026-09-30'), TODAY)).toBe(
      'Conversion Sep 1 to Sep 30',
    );
  });

  it('parses only real calendar days', () => {
    expect(parseIsoDay('2026-02-28')).not.toBeNull();
    expect(parseIsoDay('2026-02-29')).toBeNull();
    expect(parseIsoDay(null)).toBeNull();
  });
});
