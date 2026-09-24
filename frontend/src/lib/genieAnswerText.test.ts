import { describe, expect, it } from 'vitest';
import type { GenieAnswer as GenieAnswerShape } from '../types';
import { buildFallbackFollowUps, buildPinFromAnswer } from './genieAnswerText';

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
