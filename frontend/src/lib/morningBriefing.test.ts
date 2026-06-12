import { describe, expect, it } from 'vitest';
import type { PortfolioPreview } from '../types';
import { buildBriefing, formatBriefingDelta } from './morningBriefing';

function preview(overrides: Partial<PortfolioPreview> = {}): PortfolioPreview {
  return {
    marketable_population: 5_156_184,
    high_intent_leads: 111_726,
    top_tier_opportunities: 3_878,
    offers_recommended: 4_467_395,
    avg_score: 71,
    data_refreshed_at: '2026-06-12T04:16:35Z',
    trend_status: 'live',
    day_zero: false,
    approved_count: 8,
    in_outreach_count: 3,
    trends: {
      marketable_population: { series: [], delta_pct: 0, direction: 'flat', comparison_label: 'vs 2026-06-02', note: null },
      high_intent_leads: { series: [], delta_pct: 0.1, direction: 'flat', comparison_label: 'vs 2026-06-02', note: null },
      top_tier_opportunities: { series: [], delta_pct: 0.4, direction: 'flat', comparison_label: 'vs 2026-06-02', note: null },
      offers_recommended: { series: [], delta_pct: 0, direction: 'flat', comparison_label: 'vs 2026-06-02', note: null },
      avg_score: { series: [], delta_pct: 0, direction: 'flat', comparison_label: 'vs 2026-06-02', note: null },
      approved_count: { series: [], delta_pct: -74.2, direction: 'down', comparison_label: 'vs 2026-06-02', note: 'Material step change on 2026-06-11; verify rules or refresh context before presenting this as market movement.' },
      in_outreach_count: { series: [], delta_pct: 0, direction: 'flat', comparison_label: 'vs 2026-06-05', note: null },
    },
    ...overrides,
  } as unknown as PortfolioPreview;
}

describe('buildBriefing', () => {
  it('is unavailable (deltas pending) when there is no second snapshot', () => {
    expect(buildBriefing(preview({ trend_status: 'empty' as never })).available).toBe(false);
    expect(buildBriefing(preview({ day_zero: true })).available).toBe(false);
    expect(buildBriefing(null).available).toBe(false);
    expect(buildBriefing(undefined).headline).toContain('First snapshot');
    // The gate is presence-of-'live', not absence-of-'empty': an undefined
    // trend_status must also be treated as pending.
    expect(buildBriefing(preview({ trend_status: undefined as never })).available).toBe(false);
  });

  it('keeps a metric whose trend is absent (defaults to flat, no fake delta)', () => {
    const p = preview();
    delete p.trends!.top_tier_opportunities; // backend-shape drift: missing trend
    const b = buildBriefing(p);
    const tt = b.movements.find((m) => m.key === 'top_tier_opportunities')!;
    expect(tt.value).toBe(3_878); // value still shown
    expect(tt.direction).toBe('flat'); // no trend → flat
    expect(tt.deltaPct).toBeNull(); // not a fabricated 0
  });

  it('summarizes the day with the biggest mover in the headline', () => {
    const b = buildBriefing(preview());
    expect(b.available).toBe(true);
    expect(b.comparisonLabel).toBe('vs 2026-06-02');
    expect(b.moversCount).toBe(1); // only approved_count is non-flat
    // Headline names the single mover, its direction and magnitude.
    expect(b.headline).toBe('1 metric moved vs 2026-06-02 — Approved outreach down 74.2%.');
  });

  it('states direction without a fake "0.0%" when a mover has a null delta', () => {
    // Backend can flag a direction but omit delta_pct (e.g. a new metric with
    // no comparable prior value). The headline must not read "down 0.0%".
    const p = preview();
    p.trends!.approved_count = {
      series: [], delta_pct: null, direction: 'down', comparison_label: 'vs 2026-06-02', note: null,
    } as never;
    const b = buildBriefing(p);
    expect(b.moversCount).toBe(1);
    expect(b.headline).toBe('1 metric moved vs 2026-06-02 — Approved outreach down.');
    expect(b.headline).not.toContain('0.0%');
  });

  it('prefers a finite-delta mover for the headline magnitude over a null-delta one', () => {
    const p = preview();
    // Two movers: one with a real magnitude, one direction-only (null delta).
    p.trends!.approved_count = {
      series: [], delta_pct: null, direction: 'up', comparison_label: 'vs 2026-06-02', note: null,
    } as never;
    p.trends!.in_outreach_count = {
      series: [], delta_pct: 12.5, direction: 'up', comparison_label: 'vs 2026-06-02', note: null,
    } as never;
    const b = buildBriefing(p);
    expect(b.moversCount).toBe(2);
    expect(b.headline).toBe('2 metrics moved vs 2026-06-02 — In outreach up 12.5%.');
  });

  it('says the portfolio is steady when nothing moved', () => {
    const flat = preview();
    // Force every trend to flat.
    for (const k of Object.keys(flat.trends!)) flat.trends![k] = { ...flat.trends![k], direction: 'flat', delta_pct: 0 };
    const b = buildBriefing(flat);
    expect(b.moversCount).toBe(0);
    expect(b.headline).toContain('Portfolio steady vs 2026-06-02');
  });

  it('carries each metric value, delta, direction, and the governance note verbatim', () => {
    const b = buildBriefing(preview());
    const approved = b.movements.find((m) => m.key === 'approved_count')!;
    expect(approved.value).toBe(8);
    expect(approved.deltaPct).toBe(-74.2);
    expect(approved.direction).toBe('down');
    expect(approved.note).toContain('Material step change');
    const marketable = b.movements.find((m) => m.key === 'marketable_population')!;
    expect(marketable.value).toBe(5_156_184);
    expect(marketable.note).toBeNull();
    // All six metrics are present and ordered.
    expect(b.movements.map((m) => m.key)).toEqual([
      'marketable_population', 'high_intent_leads', 'top_tier_opportunities',
      'offers_recommended', 'approved_count', 'in_outreach_count',
    ]);
  });

  it('pluralizes the headline for multiple movers and picks the largest magnitude', () => {
    const p = preview();
    p.trends!.high_intent_leads = { ...p.trends!.high_intent_leads, direction: 'up', delta_pct: 5.0 };
    const b = buildBriefing(p);
    expect(b.moversCount).toBe(2);
    // approved (74.2%) outranks high_intent (5%).
    expect(b.headline).toContain('2 metrics moved');
    expect(b.headline).toContain('Approved outreach down 74.2%');
  });
});

describe('formatBriefingDelta', () => {
  it('signs the delta and uses a true minus glyph', () => {
    expect(formatBriefingDelta(0.4)).toBe('+0.4%');
    expect(formatBriefingDelta(-74.2)).toBe('−74.2%');
    expect(formatBriefingDelta(0)).toBe('0.0%');
    expect(formatBriefingDelta(null)).toBe('—');
  });
});
