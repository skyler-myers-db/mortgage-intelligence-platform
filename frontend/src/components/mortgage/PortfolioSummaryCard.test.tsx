/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PortfolioPreview } from '../../types';

const setDrawer = vi.fn();
vi.mock('../AppContext', () => ({ useApp: () => ({ setDrawer }) }));

import { PortfolioSummaryCard } from './PortfolioSummaryCard';

function preview(o: Partial<PortfolioPreview> = {}): PortfolioPreview {
  return {
    marketable_population: 5_156_184,
    high_intent_leads: 111_726,
    top_tier_opportunities: 3_878,
    offers_recommended: 4_467_395,
    avg_score: 71,
    data_refreshed_at: '2026-06-12T04:16:35Z',
    day_zero: false,
    approved_count: 8,
    in_outreach_count: 3,
    trends: {},
    trend_status: 'live',
    ...o,
  } as unknown as PortfolioPreview;
}

describe('PortfolioSummaryCard', () => {
  let container: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    setDrawer.mockClear();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('renders the grounded narrative without restating KPI figures as claim chips', () => {
    act(() => root.render(<PortfolioSummaryCard preview={preview()} />));
    expect(container.querySelector('.portfolio-summary')).not.toBeNull();
    expect(container.textContent).toContain('Your book today');
    expect(container.querySelector('.portfolio-summary__narrative')!.textContent).toContain('5,156,184');
    // The claim chip row was removed (evidence lives on the Home KPI cards); a
    // clean book shows no ambient "traces to snapshot" verdict either.
    expect(container.querySelectorAll('.portfolio-summary__claim').length).toBe(0);
    expect(container.querySelector('.portfolio-summary__verdict')).toBeNull();
    expect(container.textContent).not.toContain('Every figure traces to the current gold snapshot');
  });

  it('renders nothing on day-zero (no fabricated summary)', () => {
    act(() => root.render(<PortfolioSummaryCard preview={preview({ day_zero: true })} />));
    expect(container.querySelector('.portfolio-summary')).toBeNull();
  });

  it('renders a skeleton while loading', () => {
    act(() => root.render(<PortfolioSummaryCard preview={null} loading />));
    expect(container.querySelector('[aria-busy="true"]')).not.toBeNull();
    expect(container.querySelector('.portfolio-summary__narrative-skeleton')).not.toBeNull();
  });
});
