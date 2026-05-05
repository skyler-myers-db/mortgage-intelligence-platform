import { describe, it, expect } from 'vitest';
import { inferChartFromRows, stripQuestionRestatement } from './GenieAnswer';

/**
 * GenieAnswer.stripQuestionRestatement is the small piece of pre-render
 * normalization that drops Genie's habit of opening with "You want to
 * see...". The actual answer below the preamble is what we want shown.
 *
 * Test policy: only strip when the FIRST sentence is an obvious
 * restatement leader; never blow away an answer that starts with "You"
 * for unrelated reasons (e.g. "You can refinance ..." would be a real
 * answer about the user, not a restatement).
 */
describe('stripQuestionRestatement', () => {
  it('strips a "You want to see..." preamble', () => {
    const input =
      'You want to see which ZIP codes have the highest number of borrowers ' +
      'who are considered in-the-money for refinancing. The zip codes with the most ' +
      'in-the-money refinance candidates are: 60610 (195 candidates).';
    const out = stripQuestionRestatement(input);
    expect(out.startsWith('You want')).toBe(false);
    expect(out.startsWith('The zip codes')).toBe(true);
  });

  it('strips a "You\'re asking about..." preamble', () => {
    const input =
      "You're asking about Travis County. There are 14,200 borrowers in scope.";
    expect(stripQuestionRestatement(input)).toBe(
      'There are 14,200 borrowers in scope.',
    );
  });

  it('strips "Based on your question..." preamble', () => {
    const input =
      'Based on your question, the top segment in Texas is Investor / Multi-Property.';
    expect(stripQuestionRestatement(input)).toBe(
      'The top segment in Texas is Investor / Multi-Property.',
    );
  });

  it('does NOT strip an actual answer that happens to start with "You"', () => {
    const input =
      'Your highest-opportunity borrowers are in Cook County, IL.';
    // "Your" doesn't match the restatement leaders — keep verbatim.
    expect(stripQuestionRestatement(input)).toBe(input);
  });

  it('does NOT strip when the leader has no terminating punctuation', () => {
    // Edge case: bare leader with no period — better to keep the whole
    // answer than risk truncating it. Asserts the safety guard.
    const input = 'You want to know more';
    expect(stripQuestionRestatement(input)).toBe(input);
  });

  it('passes through an empty string', () => {
    expect(stripQuestionRestatement('')).toBe('');
  });

  it('strips and returns the original if remainder is empty', () => {
    // If the answer IS only the restatement (nothing after the period),
    // return the original — never render an empty bubble.
    const input = 'You want to see the data.';
    expect(stripQuestionRestatement(input)).toBe('You want to see the data.');
  });
});

describe('inferChartFromRows', () => {
  it('treats ZIP columns as categorical identifiers and borrowers as the measure', () => {
    const rows = [
      { zip: 60617, state: 'IL', borrowers: 1503, avg_score: 60.3 },
      { zip: 60628, state: 'IL', borrowers: 1482, avg_score: 60.3 },
      { zip: 60629, state: 'IL', borrowers: 1387, avg_score: 59 },
    ];

    const chart = inferChartFromRows(rows, ['zip', 'state', 'borrowers', 'avg_score']);

    expect(chart?.labelCol).toBe('zip');
    expect(chart?.valueCol).toBe('borrowers');
    expect(chart?.rows[0]).toEqual({ label: '60617', value: 1503 });
  });

  it('does not use numeric-looking ZIP strings as chart values', () => {
    const rows = [
      { zip: '02139', state: 'MA', borrowers: '42' },
      { zip: '10001', state: 'NY', borrowers: '39' },
    ];

    const chart = inferChartFromRows(rows, ['zip', 'state', 'borrowers']);

    expect(chart?.labelCol).toBe('zip');
    expect(chart?.valueCol).toBe('borrowers');
    expect(chart?.rows.map((row) => row.value)).toEqual([42, 39]);
  });

  it.each([
    ['zip', 2139, '02139'],
    ['zip_code', 2139, '02139'],
    ['postal_code', 2139, '02139'],
    ['fips_5', 17031, '17031'],
    ['county_fips_5', 6037, '06037'],
    ['cbsa_code', 16980, '16980'],
    ['msa_cbsa_code', 38060, '38060'],
    ['borrower_id', 'B-102FL7THC6Q3L', 'B-102FL7THC6Q3L'],
    ['clip', '9154364327', '9154364327'],
    ['id', 'row-001', 'row-001'],
  ])('uses %s as the chart label, never the numeric measure', (column, value, expectedLabel) => {
    const rows = [
      { [column]: value, borrowers: 42, avg_score: 60.3 },
      { [column]: `${value}-B`, borrowers: 39, avg_score: 61.1 },
    ];

    const chart = inferChartFromRows(rows, [column, 'borrowers', 'avg_score']);

    expect(chart?.labelCol).toBe(column);
    expect(chart?.valueCol).toBe('borrowers');
    expect(chart?.rows[0]).toEqual({ label: expectedLabel, value: 42 });
  });
});
