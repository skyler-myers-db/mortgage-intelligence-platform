/** @vitest-environment happy-dom */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { PortfolioPreview, SalesConversionResponse, SalesOutcomeSummaryResponse } from '../types';
import { RoiProjector } from './portfolio-builder.components';

const PREVIEW = {
  marketable_population: 10_000,
  high_intent_leads: 1_200,
  top_tier_opportunities: 400,
  offers_recommended: 8_000,
  avg_score: 71,
  avg_current_lien_balance_usd: 300_000,
  avg_high_intent_lien_balance_usd: 320_000,
  total_current_lien_balance_usd: 3_000_000_000,
  avg_equity_pct: 42.5,
  avg_rate_spread_bps: 138.4,
  offer_mix: [],
  data_refreshed_at: '2026-07-13T12:00:00Z',
  approved_count: 0,
  in_outreach_count: 0,
} satisfies PortfolioPreview;

const CONVERSION = {
  from_date: '2026-04-14',
  to_date: '2026-07-13',
  group_by: 'cohort',
  rows: [{
    group_key: 'all',
    calls_attempted: 100,
    contacts_reached: 30,
    callbacks_scheduled: 15,
    applications_started: 20,
    unique_leads_contacted: 80,
    unique_contacts_reached: 80,
    unique_application_starts: 20,
    application_start_rate: 0.25,
  }],
} satisfies SalesConversionResponse;

const OUTCOMES = {
  from_date: '2026-04-14',
  to_date: '2026-07-13',
  total_outcomes: 15,
  applications_submitted: 10,
  closed_funded: 5,
  unique_applications_submitted: 10,
  unique_closed_funded: 5,
  lost_to_competitor: 3,
  withdrawn: 1,
  not_qualified: 1,
  by_source_system: [],
  source_statuses: [],
  by_lo: [],
  top_competitors: [],
} satisfies SalesOutcomeSummaryResponse;

describe('RoiProjector', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function mount(
    conversion: SalesConversionResponse | undefined = CONVERSION,
    outcomes: SalesOutcomeSummaryResponse | undefined = OUTCOMES,
    status: 'loading' | 'available' | 'unavailable' = 'available',
  ) {
    act(() => root.render(
      <RoiProjector
        preview={PREVIEW}
        conversion={conversion}
        outcomes={outcomes}
        performanceStatus={status}
      />,
    ));
  }

  function mountWithoutHistory() {
    act(() => root.render(
      <RoiProjector preview={PREVIEW} performanceStatus="unavailable" />,
    ));
  }

  function setInput(testid: string, value: string) {
    const input = container.querySelector<HTMLInputElement>(`[data-testid="${testid}"]`)!;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
    act(() => {
      setter.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  it('derives expected fundings and volume from exact cohort facts and observed outcomes', () => {
    mount();
    expect(container.querySelector('[data-testid="roi-gross"]')?.textContent).toBe('75');
    expect(container.textContent).toContain('Reached → application start25.0%');
    expect(container.textContent).toContain('Application start → submitted50.0%');
    expect(container.textContent).toContain('Submitted → funded50.0%');
    expect(container.textContent).toContain('Expected origination volume$24.0M');
    expect(container.textContent).toContain('Average refi-economics balance$320K');
    expect(container.textContent).not.toContain('$432K');
  });

  it('withholds projections when outcome history is insufficient', () => {
    mountWithoutHistory();
    expect(container.querySelector('[data-testid="roi-gross"]')?.textContent).toBe('—');
    expect(container.textContent).toContain('No zero or benchmark rate is substituted');
    expect(container.textContent).toContain('performance unavailable');
    expect(container.textContent).toContain('Net revenueAdd tenant economics');
  });

  it('computes net revenue only after the tenant supplies its own economics', () => {
    mount();
    setInput('roi-revenue-rate', '1.5');
    expect(container.textContent).toContain('Net revenueAdd tenant economics');
    setInput('roi-cost-per-lead', '2');
    expect(container.textContent).toContain('Net revenue$358K');
  });

  it('rejects a non-monotonic funnel instead of manufacturing a rate', () => {
    mount(CONVERSION, { ...OUTCOMES, unique_applications_submitted: 25 });
    expect(container.querySelector('[data-testid="roi-gross"]')?.textContent).toBe('—');
    expect(container.textContent).toContain('monotonic stage counts');
  });
});
