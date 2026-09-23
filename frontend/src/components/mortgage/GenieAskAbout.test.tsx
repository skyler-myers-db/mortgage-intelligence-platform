/**
 * @vitest-environment happy-dom
 *
 * "Ask Genie about this" entry points (audit 2026-09-21 `genie-04`, phase 1)
 * on the KPI card, the segment card and the lead row preview: each carries a
 * button whose prompt is the reviewed template for its enum value, opens the
 * panel through `openGenie` (a prefill, never a submit), and renders nothing
 * when the surface has no reviewed template.
 */
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { genieKpiPrompt, genieLeadPrompt, genieSegmentPrompt, genieStatePrompt } from '../../lib/genieContext';
import { consumeGeniePrefill, subscribeGenieOpenRequests } from '../../lib/genieOpen';
import type { LeadSummary, SegmentSummary } from '../../types';
import { GenieAskAbout } from './GenieAskAbout';
import { KpiCard } from './KpiCard';
import { RowPreview } from './LeadRowPreview';
import { SegmentCard } from './SegmentCard';

const setDrawer = vi.fn();
const setLastBorrowerId = vi.fn();
const saveLead = vi.fn();
vi.mock('../AppContext', () => ({
  useApp: () => ({ setDrawer, showEvidence: true, setLastBorrowerId, saveLead, isLeadSaved: () => false }),
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SEGMENT: SegmentSummary = {
  code: 'itm',
  name: 'Prime Refi Candidates',
  count: 12_840,
  contactable: 1_286,
  delta: '+18%',
  avg_score: 82,
  description: 'Lien rate >= 75 bps above par and equity >= 15%.',
  color: 'var(--seg-itm)',
  source_status: 'connected',
  source_name: null,
  loan_product_mix: [],
  origination_channel_mix: [],
};

const LEAD = {
  borrower_id: 'B-0123456789ABC',
  display_name: 'Owner 012345',
  city: 'Chicago',
  state: 'IL',
  zip: '60611',
  clip: 'clip_demo_012345',
  segment_codes: ['itm', 'equity'],
  equity_estimate: 140_000,
  rate_spread_bps: 112,
  opportunity_score: 91,
  confidence: 84,
  recommended_offer: 'Refinance + HELOC',
  recommended_offer_code: 'refi_plus_heloc',
  why_now: 'Lien rate is well above par.',
  approval_status: 'pending',
  outreach_status: 'queued',
  current_lien_balance: 340_000,
} as unknown as LeadSummary;

describe('Ask Genie about this', () => {
  let container: HTMLDivElement;
  let root: Root;
  const opens: number[] = [];
  let unsubscribe = () => undefined as void;

  beforeEach(() => {
    opens.length = 0;
    consumeGeniePrefill();
    unsubscribe = subscribeGenieOpenRequests(() => opens.push(1));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    unsubscribe();
    act(() => root.unmount());
    container.remove();
    consumeGeniePrefill();
  });

  const render = (node: ReactNode) => act(() => root.render(<MemoryRouter>{node}</MemoryRouter>));
  const askButton = () => container.querySelector<HTMLButtonElement>('button[aria-label^="Ask Genie about this"]');

  it('the button opens the panel with its prompt queued and submits nothing', () => {
    const fetchSpy = vi.spyOn(globalThis, 'fetch');
    try {
      render(<GenieAskAbout prompt="Compare mean lead score by current coverage state." subject="KPI: test" />);
      const button = askButton();
      expect(button?.getAttribute('aria-label')).toBe('Ask Genie about this KPI: test');
      expect(button?.title).toBe('Ask Genie: Compare mean lead score by current coverage state.');
      expect(button?.textContent).toContain('Ask Genie');
      act(() => button!.click());
      expect(opens).toEqual([1]);
      expect(consumeGeniePrefill()).toBe('Compare mean lead score by current coverage state.');
      expect(fetchSpy).not.toHaveBeenCalled();
    } finally {
      fetchSpy.mockRestore();
    }
  });

  it('the icon variant is a sparkle only, with the same name and tooltip', () => {
    render(<GenieAskAbout prompt={genieStatePrompt('TX')} subject="state: Texas" variant="icon" />);
    const button = askButton();
    expect(button?.classList.contains('genie-ask-about--icon')).toBe(true);
    expect(button?.textContent?.trim()).toBe('');
    expect(button?.getAttribute('aria-label')).toBe('Ask Genie about this state: Texas');
    expect(button?.title).toBe(`Ask Genie: ${genieStatePrompt('TX')}`);
  });

  it('renders nothing without a reviewed prompt', () => {
    render(<GenieAskAbout prompt={null} subject="KPI: unknown" />);
    expect(askButton()).toBeNull();
  });

  it('a KPI card carries the template for its label, and an unknown label carries none', () => {
    render(<KpiCard label="Addressable population" valueAnimated={89_553} />);
    expect(askButton()?.getAttribute('aria-label')).toBe('Ask Genie about this KPI: Addressable population');
    expect(askButton()?.title).toBe(`Ask Genie: ${genieKpiPrompt('Addressable population')}`);
    // Top-right corner slot; the card is marked so its label keeps clear.
    expect(askButton()?.closest('.kpi__ask')).not.toBeNull();
    expect(container.querySelector('.kpi')?.classList.contains('kpi--askable')).toBe(true);
    render(<KpiCard label="Some ad-hoc KPI" valueAnimated={1} />);
    expect(askButton()).toBeNull();
    expect(container.querySelector('.kpi')?.classList.contains('kpi--askable')).toBe(false);
    // A loading card is a skeleton: no entry point yet.
    render(<KpiCard label="Addressable population" valueAnimated={89_553} loading />);
    expect(askButton()).toBeNull();
  });

  it('a segment card asks about its registered segment, above the stretched select button', () => {
    render(<SegmentCard segment={SEGMENT} onClick={() => undefined} />);
    const button = askButton();
    expect(button?.getAttribute('aria-label')).toBe('Ask Genie about this Prime Refi Candidates segment');
    expect(button?.title).toBe(`Ask Genie: ${genieSegmentPrompt('itm')}`);
    expect(button?.closest('.seg-card__ask')).not.toBeNull();
    act(() => button!.click());
    expect(consumeGeniePrefill()).toBe('Which states have the most borrowers in the prime refi candidates segment?');
  });

  it('a gated segment card has no entry point', () => {
    render(<SegmentCard segment={{ ...SEGMENT, source_status: 'not_connected', count: 0 }} />);
    expect(askButton()).toBeNull();
  });

  it("a lead row asks about the borrower's segment and state, never the id or a contact field", () => {
    render(<RowPreview lead={LEAD} />);
    const button = askButton();
    expect(button?.getAttribute('aria-label')).toBe("Ask Genie about this borrower's segment and state");
    const prompt = (button?.title ?? '').replace(/^Ask Genie: /, '');
    expect(prompt).toBe(genieLeadPrompt({ segmentCodes: ['itm', 'equity'], stateCode: 'IL' }));
    expect(prompt).toContain('Illinois');
    expect(prompt).toContain('prime refi candidates');
    for (const forbidden of ['B-0123456789ABC', 'Owner 012345', 'clip_demo', 'Chicago', '60611']) {
      expect(prompt).not.toContain(forbidden);
    }
  });

  it('a lead row outside the USPS list has no entry point', () => {
    render(<RowPreview lead={{ ...LEAD, state: 'ZZ' }} />);
    expect(askButton()).toBeNull();
  });
});
