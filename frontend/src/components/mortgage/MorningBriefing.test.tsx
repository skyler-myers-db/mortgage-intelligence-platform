/**
 * @vitest-environment happy-dom
 *
 * MorningBriefing render contract (Buyer-Wow #6): the available state shows
 * the headline + a metric grid with delta chips and the governance note; the
 * pending state shows the honest "first snapshot" copy; loading shows a
 * skeleton. Pure presentational over the preview the Home page already has.
 */
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import type { PortfolioPreview } from '../../types';
import { MorningBriefing } from './MorningBriefing';

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
      approved_count: { series: [], delta_pct: -74.2, direction: 'down', comparison_label: 'vs 2026-06-02', note: 'Material step change on 2026-06-11; verify rules.' },
    },
    ...overrides,
  } as unknown as PortfolioPreview;
}

const render = (el: React.ReactElement) => renderToStaticMarkup(<MemoryRouter>{el}</MemoryRouter>);

describe('MorningBriefing', () => {
  it('renders the headline, metric values, a signed delta, and the governance note', () => {
    const html = render(<MorningBriefing preview={preview()} />);
    expect(html).toContain('Morning briefing');
    expect(html).toContain('Approved outreach');
    expect(html).toContain('5,156,184'); // marketable value formatted
    expect(html).toContain('−74.2%'); // signed delta with true minus
    expect(html).toContain('Material step change'); // governance note surfaced
    expect(html).toContain('briefing__delta--down');
    expect(html).toContain('/lead-queue'); // review-queue CTA
  });

  it('renders the honest pending state when there is no second snapshot', () => {
    const html = render(<MorningBriefing preview={preview({ trend_status: 'empty' as never })} />);
    expect(html).toContain('First snapshot');
    expect(html).not.toContain('briefing__grid'); // no metric grid yet
  });

  it('renders a skeleton while loading', () => {
    const html = render(<MorningBriefing preview={null} loading />);
    expect(html).toContain('briefing__headline-skeleton');
    expect(html).toContain('aria-busy="true"');
  });
});
