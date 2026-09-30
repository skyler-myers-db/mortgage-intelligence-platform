/**
 * @vitest-environment happy-dom
 *
 * The expanded row's Contactability field states its compliance source as
 * visible text (audit critic-08, compliance part): "DNC source: <source>"
 * on a DNC row, "Eligibility source: <source>" on every other row, Eligible
 * rows included. It used to live only in the chips' `title`, which a
 * keyboard or touch reader never sees. The in-row chips stay.
 */
import { createRoot, type Root } from 'react-dom/client';
import { act, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { leadComplianceFlags, leadEligibilitySourceText } from './LeadTable.status';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../Primitives', () => ({
  Button: ({ children, ...props }: { children: ReactNode }) => <button type="button" {...props}>{children}</button>,
  Chip: ({ children, title }: { children: ReactNode; title?: string }) => <span className="chip" title={title}>{children}</span>,
}));

import { LeadRowWorkflowPanel } from './LeadRowWorkflowPanel';

const BASE = {
  borrower_id: 'B-COMPLIANCE001',
  approval_status: 'pending',
  segment_codes: ['itm'],
  evidence_ids: ['ev-1'],
} as unknown as LeadSummary;

describe('the expanded row\'s compliance source', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  function renderPanel(lead: LeadSummary) {
    act(() => {
      root.render(
        <LeadRowWorkflowPanel
          lead={lead}
          salesBusy={false}
          salesTeamCount={1}
          onOpenDisposition={() => undefined}
          onAssignmentUpdate={() => undefined}
        />,
      );
    });
    return document.querySelector(`[data-testid="lead-eligibility-source-${lead.borrower_id}"]`)?.textContent;
  }

  it('reads "DNC source: <source>" on a DNC row, and the DNC chip stays', () => {
    const lead = { ...BASE, dnc: true, marketing_eligible: false, eligibility_source: 'first_party_dnc_feed' } as LeadSummary;
    expect(renderPanel(lead)).toBe('DNC source: first_party_dnc_feed');
    expect([...document.querySelectorAll('.chip')].map((chip) => chip.textContent)).toContain('DNC');
  });

  it('reads "Eligibility source: <source>" on an Eligible row', () => {
    const lead = { ...BASE, dnc: false, marketing_eligible: true, eligibility_source: 'consent_ledger' } as LeadSummary;
    expect(renderPanel(lead)).toBe('Eligibility source: consent_ledger');
    expect(document.body.textContent).toContain('Eligible');
  });

  it('reads the eligibility source on a suppressed row, and falls back to the synthetic seed', () => {
    const lead = { ...BASE, dnc: false, marketing_eligible: false, suppression_reason: 'opt_out' } as LeadSummary;
    expect(renderPanel(lead)).toBe('Eligibility source: synthetic_seed');
  });

  it('is the text the chips\' titles use (one helper)', () => {
    const dnc = { ...BASE, dnc: true, eligibility_source: 'first_party_dnc_feed' } as LeadSummary;
    expect(leadComplianceFlags(dnc).find((flag) => flag.key === 'dnc')?.title).toBe(leadEligibilitySourceText(dnc));
    const suppressed = { ...BASE, dnc: false, marketing_eligible: false, eligibility_source: 'consent_ledger' } as LeadSummary;
    expect(leadComplianceFlags(suppressed).find((flag) => flag.key === 'suppressed')?.title)
      .toBe(leadEligibilitySourceText(suppressed));
  });
});
