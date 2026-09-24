import { describe, expect, it } from 'vitest';
import type { GenieAnswer as GenieAnswerShape } from '../types';
import {
  answerDigest,
  buildFallbackFollowUps,
  buildPinFromAnswer,
  flattenGenieMarkdown,
  parseGenieMarkdownBlocks,
} from './genieAnswerText';

/**
 * The pin summary and the follow-up fallback (moved with their builders out
 * of lib/pinnedInsights.ts, which stays in the initial closure).
 */

function answer(overrides: Partial<GenieAnswerShape> = {}): GenieAnswerShape {
  return {
    answer: 'The average loan age is 5.25 years.',
    source: 'genie',
    trusted_assets: ['mip.gold.lockin_cohort'],
    question_hash: 'abc123',
    message_id: 'm1',
    metric_value: '5.25 years',
    table_rows: null,
    follow_up_questions: [],
    ...overrides,
  } as unknown as GenieAnswerShape;
}

describe('buildPinFromAnswer summary', () => {
  it('falls back to the cleaned answer when there is no metric, truncating', () => {
    const long = 'x'.repeat(500);
    const pin = buildPinFromAnswer(answer({ metric_value: null }), long, 'Q');
    expect(pin.summary.length).toBeLessThanOrEqual(221); // 220 + the ellipsis
  });

  it('flattens markdown in the summary (the Home card renders text, not markdown)', () => {
    const md = 'Illinois (**IL**) leads with **55,037** in-the-money borrowers';
    const pin = buildPinFromAnswer(answer({ metric_value: null }), md, 'Q');
    expect(pin.summary).toBe('Illinois (IL) leads with 55,037 borrowers passing the refinance-economics screen');
    expect(pin.summary).not.toContain('**');
    expect(pin.summary).not.toContain('`');
    expect(pin.summary).not.toContain('in-the-money');
  });

  it('collapses newlines/bullets and code spans into a single clean line', () => {
    const md = '- `mip.gold.state_rollup`\n- **55,037** borrowers';
    const pin = buildPinFromAnswer(answer({ metric_value: null }), md, 'Q');
    expect(pin.summary).toBe('mip.gold.state_rollup 55,037 borrowers');
    expect(pin.summary).not.toMatch(/\n/);
  });

  it('truncates at a word boundary with an ellipsis, never mid-token or on a dangling bracket', () => {
    // After stripping markdown the cut must not leave a half-word or a stray "(".
    const long = `${'word '.repeat(60)}Illinois (**IL**) with **55,037**`;
    const pin = buildPinFromAnswer(answer({ metric_value: null }), long, 'Q');
    expect(pin.summary.endsWith('…')).toBe(true);
    expect(pin.summary.length).toBeLessThanOrEqual(221);
    // No dangling opener / partial markup right before the ellipsis.
    expect(pin.summary).not.toMatch(/[([{*`\-–—,;:/&]…$/);
    expect(pin.summary).not.toContain('**');
  });
});

describe('buildFallbackFollowUps', () => {
  it('suggests a state breakdown + top cohorts for a tabular answer', () => {
    const ups = buildFallbackFollowUps(answer({ table_rows: [{ a: 1 }], metric_value: null }));
    expect(ups).toContain('Break this down by state.');
    expect(ups).toContain('Show the top cohorts.');
    expect(ups).toHaveLength(2);
  });

  it('suggests a segment pivot for a metric-only answer', () => {
    const ups = buildFallbackFollowUps(answer({ table_rows: null, metric_value: '5.25 years' }));
    expect(ups).toContain('Break this down by state.');
    expect(ups).toContain('Which segments drive this?');
  });
});

/**
 * The plain-text flatten reads the same grammar MarkdownAnswer renders
 * (audit 2026-09-21 `stack-02`): one case per construct, both modes.
 */
describe('flattenGenieMarkdown', () => {
  it('(a) keeps ordered numbering and leaves a year or a decimal as prose', () => {
    expect(flattenGenieMarkdown('1. **Illinois** leads\n2) Texas', 'lines')).toBe('1. Illinois leads\n2. Texas');
    expect(flattenGenieMarkdown('2026. A year.\n3.5% moved.', 'lines')).toBe('2026. A year.\n3.5% moved.');
    expect(flattenGenieMarkdown('1. a\n2. b', 'single')).toBe('1. a 2. b');
  });

  it('(a) bullets become "- " in lines mode and drop in single mode', () => {
    expect(flattenGenieMarkdown('* one\n• two\n- three', 'lines')).toBe('- one\n- two\n- three');
    expect(flattenGenieMarkdown('* one\n- two', 'single')).toBe('one two');
  });

  it('(b) strips ATX heading marks, a closing run included', () => {
    expect(flattenGenieMarkdown('### Where demand sits ###\nBody.', 'lines')).toBe('Where demand sits\nBody.');
    expect(flattenGenieMarkdown('#1 is not a heading', 'lines')).toBe('#1 is not a heading');
  });

  it('(c) strips guarded italics and leaves snake_case and UC names unchanged', () => {
    expect(flattenGenieMarkdown('The *largest* cohort', 'single')).toBe('The largest cohort');
    const names = 'mip.gold.lead_scores and snake_case_column and _x_ and 2*3*4';
    expect(flattenGenieMarkdown(names, 'lines')).toBe(names);
  });

  it('(d) links and images become their text; the URL never survives', () => {
    const flat = flattenGenieMarkdown(
      'See [the table](https://example.com/t) and ![chart](https://example.com/c.png).',
      'single',
    );
    expect(flat).toBe('See the table and chart.');
    expect(flat).not.toContain('example.com');
  });

  it('(e) narrative-table rows stay verbatim in lines mode and become words in single mode', () => {
    const table = '| State | Borrowers |\n|---|---:|\n| IL | 1,204 |';
    expect(flattenGenieMarkdown(`Breakdown:\n${table}`, 'lines')).toBe(`Breakdown:\n${table}`);
    expect(flattenGenieMarkdown(`Breakdown:\n${table}`, 'single')).toBe('Breakdown: State Borrowers IL 1,204');
  });

  it('(f) drops the fence lines and keeps the fenced body as written', () => {
    const fenced = ['Query:', '```sql', 'SELECT **x**', '```', 'Done.'].join('\n');
    expect(flattenGenieMarkdown(fenced, 'lines')).toBe('Query:\nSELECT **x**\nDone.');
  });

  it('(g) drops thematic breaks', () => {
    expect(flattenGenieMarkdown('One.\n---\nTwo.\n***', 'lines')).toBe('One.\nTwo.');
  });

  it('keeps the existing copy output for bold, code and bullets unchanged', () => {
    expect(flattenGenieMarkdown('  - **IL** has `mip.gold.x`  \nnext', 'lines')).toBe('- IL has mip.gold.x\nnext');
  });
});

describe('parseGenieMarkdownBlocks', () => {
  it('joins prose lines, separates on blank lines, and ends a paragraph at every construct', () => {
    expect(parseGenieMarkdownBlocks('a\nb\n\nc\n# H\nd')).toEqual([
      { type: 'p', text: 'a b' },
      { type: 'p', text: 'c' },
      { type: 'h', text: 'H' },
      { type: 'p', text: 'd' },
    ]);
  });
});

describe('answerDigest', () => {
  it('prefers the metric, then the summary, then the answer, flattened to one short line', () => {
    expect(answerDigest(answer(), 'ignored')).toBe('5.25 years');
    expect(answerDigest(answer({ metric_value: null, summary: '**Illinois** leads.' }), 'ignored')).toBe(
      'Illinois leads.',
    );
    const long = `${'word '.repeat(60)}end`;
    const digest = answerDigest(answer({ metric_value: null }), long);
    expect(digest.endsWith('…')).toBe(true);
    expect(digest.length).toBeLessThanOrEqual(161);
  });
});
