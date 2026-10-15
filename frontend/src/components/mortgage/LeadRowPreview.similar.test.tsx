/**
 * @vitest-environment happy-dom
 *
 * The row preview's "More like this" entry (wow-power-2, W5e item 4c): a
 * ghost button beside "Build offer"; expanding the row reads nothing and
 * loads no panel; the click loads the panel chunk and reads ONE audit-free
 * count; a second click closes it. The Triage deck link is an approver's.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { mount } from '../../test/render';

const app = vi.hoisted(() => ({ canApprove: false }));
vi.mock('../AppContext', () => ({
  useApp: () => ({
    setLastBorrowerId: () => undefined,
    saveLead: () => undefined,
    isLeadSaved: () => false,
    setDrawer: () => undefined,
    showEvidence: true,
    showConfidence: true,
    canApprove: app.canApprove,
  }),
}));

import { RowPreview } from './LeadRowPreview';

vi.setConfig({ testTimeout: 60_000 });

const LEAD = {
  borrower_id: 'B-0000000000007',
  display_name: 'Owner 7',
  city: 'Austin',
  state: 'TX',
  zip: '78701',
  clip: 'clip_demo_7',
  segment_codes: ['itm'],
  equity_estimate: 250000,
  rate_spread_bps: 88,
  opportunity_score: 91,
  confidence: 87,
  recommended_offer_code: 'refi',
  recommended_offer: 'Refinance',
  why_now: 'Rate spread supports review.',
  evidence_ids: ['ev-1'],
  approval_status: 'pending',
  outreach_status: 'none',
} as LeadSummary;

let calls: string[] = [];

beforeEach(() => {
  calls = [];
  app.canApprove = false;
  vi.stubGlobal('fetch', async (path: string) => {
    calls.push(path);
    return new Response(JSON.stringify({ total_matching: 12 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

async function flush() {
  await act(async () => {
    for (let i = 0; i < 6; i += 1) await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

async function render() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const mounted = await mount(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <RowPreview lead={LEAD} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await flush();
  return mounted.container;
}

const trigger = (container: HTMLElement) =>
  container.querySelector<HTMLButtonElement>(`[data-testid="lead-more-like-this-${LEAD.borrower_id}"]`)!;

describe('RowPreview More like this', () => {
  it('sits beside Build offer and reads nothing until it is clicked', async () => {
    const container = await render();
    const build = container.querySelector(`[data-testid="lead-build-offer-${LEAD.borrower_id}"]`)!;
    expect(build.nextElementSibling).toBe(trigger(container));
    expect(trigger(container).textContent).toBe('More like this');
    expect(trigger(container).getAttribute('aria-expanded')).toBe('false');
    expect(calls.filter((path) => path.includes('/leads/count'))).toEqual([]);
    expect(container.querySelector('.similar-cohort')).toBeNull();
  });

  it('opens the panel on a click with one count read, links only, and closes on a second click', async () => {
    app.canApprove = true;
    const container = await render();
    await act(async () => trigger(container).click());
    await vi.waitFor(async () => {
      await flush();
      expect(container.querySelector('.similar-cohort')).not.toBeNull();
      expect(container.querySelector('.similar-cohort [role="status"]')?.textContent).toBe('12 borrowers match this pattern');
    }, { timeout: 45_000 });
    expect(trigger(container).getAttribute('aria-expanded')).toBe('true');
    expect(trigger(container).getAttribute('aria-controls')).toBe(container.querySelector('.similar-cohort')!.id);
    const counts = calls.filter((path) => path.includes('/leads/count'));
    expect(counts).toHaveLength(1);
    expect(new URL(counts[0], 'http://queue.test').searchParams.has('borrower_ids')).toBe(false);
    expect(container.querySelector('[data-testid="lead-similar-open-triage"]')).not.toBeNull();
    await act(async () => trigger(container).click());
    expect(container.querySelector('.similar-cohort')).toBeNull();
  });
});
