import { describe, expect, it } from 'vitest';
import { inferChartFromRows, isFlagColumn, pickPlan } from './GenieAnswer.logic';
import type { GenieAnswer as GenieAnswerShape } from '../../types';

/**
 * Chart-shape guards.
 *
 * The reported defect: a four-flag co-occurrence table (each flag arriving as
 * the STRING "true"/"false") charted as twelve bars all labelled "true",
 * because the first all-string column became the label. A flag is neither a
 * label nor a measure, and repeated labels are not a chart.
 */

function vizPayload(x: string, y: string): GenieAnswerShape {
  return { answer: '', visualization: { kind: 'bar', x, y } } as GenieAnswerShape;
}

const COOCCURRENCE_ROWS = [
  { in_the_money_flag: 'true', equity_flag: 'true', investor_flag: 'false', borrowers: 4120 },
  { in_the_money_flag: 'true', equity_flag: 'false', investor_flag: 'false', borrowers: 3180 },
  { in_the_money_flag: 'false', equity_flag: 'true', investor_flag: 'true', borrowers: 990 },
  { in_the_money_flag: 'false', equity_flag: 'false', investor_flag: 'false', borrowers: 610 },
];
const COOCCURRENCE_COLUMNS = ['in_the_money_flag', 'equity_flag', 'investor_flag', 'borrowers'];

describe('flag columns are neither labels nor measures', () => {
  it.each([
    ['boolean values', [{ f: true, n: 1 }, { f: false, n: 2 }]],
    ['the strings true/false', [{ f: 'true', n: 1 }, { f: 'FALSE', n: 2 }]],
    ['flags with a null hole', [{ f: 'true', n: 1 }, { f: null, n: 2 }]],
  ])('recognizes %s as a flag column', (_label, rows) => {
    expect(isFlagColumn(rows as Array<Record<string, unknown>>, 'f')).toBe(true);
  });

  it.each([
    ['a genuine category', [{ f: 'TX', n: 1 }, { f: 'CA', n: 2 }]],
    ['an all-null column', [{ f: null, n: 1 }, { f: null, n: 2 }]],
    ['a numeric column', [{ f: 1, n: 1 }, { f: 0, n: 2 }]],
  ])('does not treat %s as a flag column', (_label, rows) => {
    expect(isFlagColumn(rows as Array<Record<string, unknown>>, 'f')).toBe(false);
  });

  it('never labels a chart with a boolean-string flag column', () => {
    const chart = inferChartFromRows(COOCCURRENCE_ROWS, COOCCURRENCE_COLUMNS);

    expect(chart).not.toBeNull();
    expect(chart?.labelCol).not.toBe('in_the_money_flag');
    expect(chart?.rows.map((row) => row.label)).not.toContain('true');
  });

  it('composes the cohort label from the flags that are true', () => {
    const chart = inferChartFromRows(COOCCURRENCE_ROWS, COOCCURRENCE_COLUMNS);

    expect(chart?.valueCol).toBe('borrowers');
    expect(chart?.rows).toEqual([
      { label: 'In The Money · Equity', value: 4120 },
      { label: 'In The Money', value: 3180 },
      { label: 'Equity · Investor', value: 990 },
      { label: 'No flags', value: 610 },
    ]);
  });

  it('keeps a real category as the label when the table has one', () => {
    const rows = [
      { state: 'TX', in_the_money_flag: true, borrowers: 900 },
      { state: 'CA', in_the_money_flag: false, borrowers: 700 },
    ];

    const chart = inferChartFromRows(rows, ['state', 'in_the_money_flag', 'borrowers']);

    expect(chart?.labelCol).toBe('state');
    expect(chart?.valueCol).toBe('borrowers');
  });

  it('does not chart a single flag column with no other category', () => {
    const rows = [
      { in_the_money_flag: 'true', borrowers: 900 },
      { in_the_money_flag: 'false', borrowers: 700 },
    ];

    expect(inferChartFromRows(rows, ['in_the_money_flag', 'borrowers'])).toBeNull();
  });
});

describe('duplicate labels fall back to the table', () => {
  it('refuses to chart repeated label values', () => {
    const rows = [
      { state: 'TX', segment_code: 'itm', borrowers: 900 },
      { state: 'TX', segment_code: 'equity', borrowers: 700 },
      { state: 'CA', segment_code: 'itm', borrowers: 500 },
    ];

    expect(inferChartFromRows(rows, ['state', 'borrowers'])).toBeNull();
  });

  it('still charts a normal state/count table', () => {
    const rows = [
      { state: 'TX', borrowers: 900 },
      { state: 'CA', borrowers: 700 },
      { state: 'FL', borrowers: 480 },
    ];

    const chart = inferChartFromRows(rows, ['state', 'borrowers']);

    expect(chart?.labelCol).toBe('state');
    expect(chart?.rows).toEqual([
      { label: 'TX', value: 900 },
      { label: 'CA', value: 700 },
      { label: 'FL', value: 480 },
    ]);
  });
});

describe('a backend visualization spec is held to the same rules', () => {
  it('refuses an x that is a flag column', () => {
    const plan = pickPlan(
      vizPayload('in_the_money_flag', 'borrowers'),
      COOCCURRENCE_ROWS,
      COOCCURRENCE_COLUMNS,
    );

    // Falls back to inference, which composes the cohort label instead.
    expect(plan.chart?.labelCol).toBe('cohort');
    expect(plan.chart?.rows.map((row) => row.label)).not.toContain('true');
  });

  it('refuses an x whose labels repeat', () => {
    const rows = [
      { state: 'TX', borrowers: 900 },
      { state: 'TX', borrowers: 700 },
    ];

    const plan = pickPlan(vizPayload('state', 'borrowers'), rows, ['state', 'borrowers']);

    expect(plan.chart).toBeNull();
  });

  it('honors an x that can carry distinct labels', () => {
    const rows = [
      { state: 'TX', borrowers: 900 },
      { state: 'CA', borrowers: 700 },
    ];

    const plan = pickPlan(vizPayload('state', 'borrowers'), rows, ['state', 'borrowers']);

    expect(plan.chart?.labelCol).toBe('state');
    expect(plan.kind).toBe('bar');
  });
});
