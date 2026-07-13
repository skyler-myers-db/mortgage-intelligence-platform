/** @vitest-environment happy-dom */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { CampaignPerformanceFunnelResponse, PortfolioPreview } from '../types';
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

const PERFORMANCE = {
  from_date: '2026-04-14',
  to_date: '2026-07-13',
  unique_leads_attempted: 100,
  unique_contacts_reached: 80,
  unique_application_starts: 20,
  unique_applications_submitted: 10,
  unique_closed_funded: 5,
  methodology: 'same_borrower_nested_funnel',
} satisfies CampaignPerformanceFunnelResponse;

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
    performance: CampaignPerformanceFunnelResponse | undefined = PERFORMANCE,
    status: 'loading' | 'available' | 'unavailable' = 'available',
  ) {
    act(() => root.render(
      <RoiProjector
        preview={PREVIEW}
        performance={performance}
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

  function click(testid: string) {
    const button = container.querySelector<HTMLButtonElement>(`[data-testid="${testid}"]`)!;
    act(() => button.dispatchEvent(new MouseEvent('click', { bubbles: true })));
  }

  it('projects every current lead through observed reach and downstream conditional rates', () => {
    mount();
    expect(container.querySelector('[data-testid="roi-gross"]')?.textContent).toBe('60');
    expect(container.textContent).toContain('Lead → reached80.0%');
    expect(container.textContent).toContain('Reached → application start25.0%');
    expect(container.textContent).toContain('Application start → submitted50.0%');
    expect(container.textContent).toContain('Submitted → funded50.0%');
    expect(container.textContent).toContain('Expected origination volume$19.2M');
    expect(container.textContent).toContain('Average refi-economics balance$320K');
    expect(container.textContent).not.toContain('$432K');
  });

  it('shows qualified-source provenance and a Wilson 95% funding range', () => {
    mount();
    expect(container.textContent).toContain('Funding range (Wilson 95%)26–134');
    expect(container.textContent).toContain('Projection qualificationQualified observed baseline');
    expect(container.textContent).toContain('Activity sourceCall dispositions');
    expect(container.textContent).toContain('Outcome sourceLead outcomes');
    expect(container.textContent).toContain('unique funded / attempted borrowers');
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
    expect(container.textContent).toContain('Net revenue$286K');
  });

  it('rejects a non-monotonic funnel instead of manufacturing a rate', () => {
    mount({ ...PERFORMANCE, unique_applications_submitted: 25 });
    expect(container.querySelector('[data-testid="roi-gross"]')?.textContent).toBe('—');
    expect(container.textContent).toContain('monotonic stage counts');
  });

  it('discloses that the observed rate chain is a same-borrower benchmark', () => {
    mount();
    expect(container.textContent).toContain('Observed baseline');
    expect(container.textContent).toContain('Sources: mip_app.call_dispositions and mip_app.lead_outcomes');
  });

  it('keeps manual scenarios distinct and requires four explicit rate overrides', () => {
    mount();
    click('roi-mode-manual');
    expect(container.querySelector('[data-testid="roi-gross"]')?.textContent).toBe('—');
    expect(container.textContent).toContain('Projection qualificationOverrides required');
    setInput('roi-manual-reach', '50');
    setInput('roi-manual-application', '25');
    setInput('roi-manual-submission', '50');
    setInput('roi-manual-funding', '50');
    expect(container.querySelector('[data-testid="roi-gross"]')?.textContent).toBe('38');
    expect(container.textContent).toContain('Projection qualificationExplicit manual override');
    expect(container.textContent).toContain('Funding range (Wilson 95%)Observed baseline only');
    expect(container.textContent).toContain('does not inherit the observed baseline or its Wilson interval');
    click('roi-mode-baseline');
    expect(container.querySelector('[data-testid="roi-gross"]')?.textContent).toBe('60');
  });
});
