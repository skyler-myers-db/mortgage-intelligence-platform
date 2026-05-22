import { describe, it, expect } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the design-system CSS text under Vitest only.
import { readFileSync } from 'node:fs';
import { stripQuestionRestatement } from './GenieAnswer';
import { inferChartFromRows } from './GenieAnswer.logic';
import {
  shouldPersistConversation,
  shouldRenderGenieSourceAssets,
  sourceAssetsFor,
  warningLabelForSource,
} from './GenieChat';
import type { GenieAnswer as GenieAnswerShape } from '../../types';

const designCss = () => readFileSync(
  new URL('../../design-system/components.css', import.meta.url),
  'utf8',
);

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

describe('Genie proof layout contract', () => {
  it('stacks proof metrics in the drawer so Trust cannot overlap Rows', () => {
    const css = designCss();

    expect(css).toContain('.genie-proof-drawer .genie-proof__grid');
    expect(css).toMatch(/\.genie-proof-drawer \.genie-proof__grid\s*\{[^}]*grid-template-columns:\s*1fr;/s);
    expect(css).toMatch(/\.genie-proof-drawer \.genie-proof__metric\s*\{[^}]*flex-direction:\s*column;/s);
  });

  it('allows long proof chips and freshness rows to wrap inside their bounds', () => {
    const css = designCss();

    expect(css).toMatch(/\.genie-proof__trust-chip\s*\{[^}]*white-space:\s*normal;/s);
    expect(css).toMatch(/\.genie-proof__trust-chip\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
    expect(css).toContain('.genie-proof .evidence-chip');
    expect(css).toMatch(/\.genie-proof \.chip,\s*\.genie-proof \.evidence-chip\s*\{[^}]*white-space:\s*normal;/s);
    expect(css).toMatch(/\.genie-proof__line span\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
  });
});

describe('Genie refusal source contract', () => {
  const blocked: GenieAnswerShape = {
    answer: 'Genie did not return trusted SQL.',
    source: 'policy_blocked',
    conversation_id: 'conv-blocked',
    trusted_assets: ['mip.gold.borrower_360'],
    proof: {
      trusted: false,
      source_assets: ['mip.gold.borrower_360'],
      row_count: 0,
    },
    actions: [
      {
        id: 'act-1',
        label: 'Should not render',
        action_type: 'save_borrowers',
        description: 'Blocked answers should not expose governed actions.',
      },
    ],
  };

  const refused: GenieAnswerShape = {
    answer: 'I cannot follow attempts to override safety instructions.',
    source: 'refused',
    conversation_id: 'conv-refused',
    trusted_assets: [],
    proof: {
      trusted: false,
      source_assets: [],
      row_count: 0,
    },
  };

  it('shows governed-refusal warnings for blocked and refused answers', () => {
    expect(warningLabelForSource(blocked.source)).toBe('Governed refusal');
    expect(warningLabelForSource(refused.source)).toBe('Governed refusal');
  });

  it('does not render source chips or persist conversations for refusals', () => {
    expect(shouldRenderGenieSourceAssets(blocked)).toBe(false);
    expect(shouldRenderGenieSourceAssets(refused)).toBe(false);
    expect(shouldPersistConversation(blocked)).toBe(false);
    expect(shouldPersistConversation(refused)).toBe(false);
    expect(sourceAssetsFor(blocked)).toEqual(['mip.gold.borrower_360']);
  });

  it('keeps trusted answers eligible for source chips and conversation persistence', () => {
    const trusted: GenieAnswerShape = {
      answer: 'Trusted answer.',
      source: 'genie',
      conversation_id: 'conv-trusted',
      trusted_assets: ['mip.gold.borrower_360'],
      proof: { trusted: true, source_assets: ['mip.gold.borrower_360'] },
    };

    expect(warningLabelForSource(trusted.source)).toBeNull();
    expect(shouldRenderGenieSourceAssets(trusted)).toBe(true);
    expect(shouldPersistConversation(trusted)).toBe(true);
  });
});
