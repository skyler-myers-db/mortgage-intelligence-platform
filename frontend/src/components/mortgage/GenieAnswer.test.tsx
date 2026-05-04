import { describe, it, expect } from 'vitest';
import { inferChartFromBullets, stripQuestionRestatement } from './GenieAnswer';

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

/**
 * inferChartFromBullets is the parser that fixes the round-4 bug
 * where Genie returns prose-with-embedded-data and no chart shows.
 * The screenshot the user sent has the EXACT shape exercised here.
 */
describe('inferChartFromBullets', () => {
  it('parses the user-reported screenshot case (top-5 ZIPs)', () => {
    const text = [
      'Defined as having at least 35% equity.',
      '',
      'The top 5 ZIP codes with the highest number of HELOC-eligible borrowers (with at least 35% equity) are:',
      '',
      '- **60611**: 6,506 borrowers',
      '- **60605**: 4,896 borrowers',
      '- **60610**: 4,507 borrowers',
      '- **92602**: 4,421 borrowers',
      '- **60607**: 3,314 borrowers',
      '',
      'ZIP 60611 leads by a significant margin, with nearly 2,000 more eligible borrowers than the next highest ZIP. Source: mip.gold.borrower_360.',
    ].join('\n');
    const chart = inferChartFromBullets(text);
    expect(chart).not.toBeNull();
    expect(chart?.rows).toHaveLength(5);
    expect(chart?.rows[0]).toEqual({ label: '60611', value: 6506 });
    expect(chart?.rows[4]).toEqual({ label: '60607', value: 3314 });
    expect(chart?.source).toBe('answer_bullets');
  });

  it('handles em-dash separators ("Cook County — 95,432")', () => {
    const text = [
      'Top counties by lead count:',
      '- Cook County — 95,432',
      '- Travis County — 14,200',
      '- Harris County — 12,500',
    ].join('\n');
    const chart = inferChartFromBullets(text);
    expect(chart?.rows).toEqual([
      { label: 'Cook County', value: 95432 },
      { label: 'Travis County', value: 14200 },
      { label: 'Harris County', value: 12500 },
    ]);
  });

  it('handles "label has number" pattern', () => {
    const text = [
      'Top markets:',
      'Chicago has 87,432 borrowers',
      'Austin has 14,200 borrowers',
      'Seattle has 9,876 borrowers',
    ].join('\n');
    const chart = inferChartFromBullets(text);
    expect(chart?.rows).toHaveLength(3);
    expect(chart?.rows[0]).toEqual({ label: 'Chicago', value: 87432 });
  });

  it('returns null when fewer than 2 chartable rows', () => {
    expect(inferChartFromBullets('Just one: 42 borrowers.')).toBeNull();
    expect(inferChartFromBullets('No bullets here at all, just prose.')).toBeNull();
    expect(inferChartFromBullets('')).toBeNull();
  });

  it('dedupes identical labels (defensive against reparse)', () => {
    // If Genie repeats a label (e.g., header line that also matches),
    // we should only count it once. Without the dedupe we could pair
    // a row with itself and emit a single-bar chart pretending to be
    // a multi-bar one.
    const text = [
      '- Foo: 100',
      '- Bar: 200',
      '- Foo: 100',
    ].join('\n');
    const chart = inferChartFromBullets(text);
    expect(chart?.rows).toHaveLength(2);
  });

  it('skips lines whose label is too long to fit a bar (defensive)', () => {
    // 60-char label cap — anything longer is probably a sentence,
    // not a label. Without this, a sentence ending in a number
    // would be parsed as one giant bar.
    const giant = 'a'.repeat(80);
    const text = [
      `- ${giant}: 9999`,
      '- ZIP 60611: 6,506 borrowers',
      '- ZIP 60605: 4,896 borrowers',
    ].join('\n');
    const chart = inferChartFromBullets(text);
    expect(chart?.rows).toHaveLength(2);
    expect(chart?.rows[0].label.startsWith('a')).toBe(false);
  });
});
