/**
 * @vitest-environment happy-dom
 *
 * The More like this panel (wow-power-2, W5e item 4b; D-approval-flow-a2/a3):
 * the pattern's chips, ONE audit-free GET /api/leads/count without a borrower
 * list, links only ('Open in Lead Queue'; 'Open as Triage deck' for an
 * approver), no POST and no approve, assign or batch control; a failed
 * count says 'Count unavailable' and keeps the links.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { mount } from '../../test/render';
import { SimilarCohortPanel } from './SimilarCohortPanel';

const LEAD = {
  borrower_id: 'B-SIMILAR000001',
  state: 'TX',
  zip: '75201',
  city: 'Dallas',
  segment_codes: ['equity'],
  recommended_offer_code: 'heloc',
  opportunity_score: 77,
  is_current_customer: true,
} as unknown as LeadSummary;

let calls: Array<{ path: string; method: string }> = [];
let fail = false;

beforeEach(() => {
  calls = [];
  fail = false;
  vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
    calls.push({ path, method: init?.method ?? 'GET' });
    return fail
      ? new Response(JSON.stringify({ detail: 'down' }), { status: 503, headers: { 'Content-Type': 'application/json' } })
      : new Response(JSON.stringify({ total_matching: 41 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

async function render(canApprove: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const mounted = await mount(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <SimilarCohortPanel id="similar" lead={LEAD} canApprove={canApprove} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
  return mounted.container;
}

describe('SimilarCohortPanel', () => {
  it('shows the pattern, one allowlisted count and the queue links for an approver', async () => {
    const container = await render(true);
    expect([...container.querySelectorAll('.similar-cohort__chip')].map((chip) => chip.textContent)).toEqual([
      'SEGMENTHome Equity Candidate', 'PRODUCTHELOC', 'STATETX', 'RELATIONSHIPCurrent customer', 'SCORE70–79',
    ]);
    expect(calls).toHaveLength(1);
    expect(calls[0].method).toBe('GET');
    const query = new URL(calls[0].path, 'http://queue.test');
    expect(query.pathname).toMatch(/\/leads\/count$/);
    expect(query.searchParams.has('borrower_ids')).toBe(false);
    expect(query.searchParams.get('state')).toBe('TX');
    expect(container.querySelector('[role="status"]')?.textContent).toBe('41 borrowers match this pattern');
    expect(container.textContent).toContain("Contactable borrowers only, the queue's default.");
    const queueHref = container.querySelector('[data-testid="lead-similar-open-queue"]')?.getAttribute('href');
    expect(queueHref).toBe('/lead-queue?segment=equity&product=HELOC&state=TX&lender_relationship=Current+customer&min_opportunity_score=70&max_opportunity_score=79');
    expect(container.querySelector('[data-testid="lead-similar-open-triage"]')?.getAttribute('href')).toBe(`${queueHref}&mode=triage`);
    // Links only: no approve, assign or batch control.
    expect([...container.querySelectorAll('button')]).toEqual([]);
    expect(container.textContent).not.toMatch(/Approve|Assign/);
  });

  it('offers no Triage deck link to someone who may not approve', async () => {
    const container = await render(false);
    expect(container.querySelector('[data-testid="lead-similar-open-triage"]')).toBeNull();
    expect(container.querySelector('[data-testid="lead-similar-open-queue"]')).not.toBeNull();
  });

  it('says Count unavailable and keeps the links when the count fails', async () => {
    fail = true;
    const container = await render(true);
    expect(container.querySelector('[role="status"]')?.textContent).toBe('Count unavailable');
    expect(container.querySelector('[data-testid="lead-similar-open-queue"]')).not.toBeNull();
    expect(calls.every((call) => call.method === 'GET')).toBe(true);
  });
});
