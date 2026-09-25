/**
 * @vitest-environment happy-dom
 *
 * Score anatomy in the Lead Queue's expanded row (wow-stage-2). The /proof
 * read writes a VIEW_BORROWER_PROOF audit row, so expanding, collapsing and
 * re-expanding a row reads nothing; only the preview's own disclosure does,
 * once. The queue has no proof drawer of its own, so a segment opens the one
 * the chunk mounts, over the cache.
 *
 * Mutation check (lane report): mounting the gate with its cache observer
 * enabled (`useBorrowerProof(borrowerId, false)` -> `true` in
 * ScoreAnatomyGate.tsx) fails "expanding, collapsing and re-expanding the row
 * reads no proof".
 */
import type { QueryClient } from '@tanstack/react-query';
import { QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { api } from '../../lib/api';
import { createMipQueryClient } from '../../lib/queryClient';
import { invalidateOperationalQueries, queryKeys } from '../../lib/queryKeys';
import { sampleProof } from '../../mocks/scoreAnatomyProof';
import type { LeadSummary } from '../../types';
import { RowPreview } from './LeadRowPreview';

vi.mock('../AppContext', () => ({
  useApp: () => ({
    setLastBorrowerId: () => undefined,
    saveLead: () => undefined,
    isLeadSaved: () => false,
    setDrawer: () => undefined,
    showEvidence: true,
    showConfidence: true,
  }),
}));

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** The first import of the Score anatomy chunk transforms its module graph: allow for a loaded machine. */
const WAIT = { timeout: 15_000 };
vi.setConfig({ testTimeout: 30_000 });

const LEAD: LeadSummary = {
  borrower_id: 'B-0000000000001',
  display_name: 'Owner 000001',
  city: 'Chicago',
  state: 'IL',
  zip: '60601',
  clip: 'clip_demo_000001',
  segment_codes: ['itm'],
  equity_estimate: 250000,
  rate_spread_bps: 88,
  opportunity_score: 90,
  confidence: 87,
  recommended_offer_code: 'refi_plus_heloc',
  recommended_offer: 'Refinance + HELOC',
  why_now: 'Rate spread and equity support review.',
  evidence_ids: ['ev-1'],
  approval_status: 'pending',
  outreach_status: 'none',
};

async function flush(rounds = 8) {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

describe('RowPreview score anatomy', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let proofSpy: MockInstance<typeof api.borrowerProof>;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    client = createMipQueryClient();
    proofSpy = vi.spyOn(api, 'borrowerProof').mockImplementation(async (id: string) => sampleProof({ borrower_id: id }));
  });

  afterEach(() => {
    act(() => root.unmount());
    client.clear();
    container.remove();
    proofSpy.mockRestore();
    document.querySelectorAll('.drawer-scrim, .proof-drawer').forEach((node) => node.remove());
  });

  async function expandRow() {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <MemoryRouter>
            <RowPreview lead={LEAD} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await flush();
  }

  async function collapseRow() {
    await act(async () => {
      root.render(<QueryClientProvider client={client}><MemoryRouter><div /></MemoryRouter></QueryClientProvider>);
    });
    await flush();
  }

  const disclosure = () =>
    [...container.querySelectorAll<HTMLButtonElement>('button[aria-controls]')].find(
      (button) => button.textContent === 'Score anatomy',
    ) ?? null;

  it('sits in the Primary offer card under the badge row, collapsed', async () => {
    await expandRow();
    const card = container.querySelector('.preview-offer-card');
    const gate = card?.querySelector('[data-testid="score-anatomy-spine"]');
    expect(gate).not.toBeNull();
    expect(gate?.previousElementSibling?.classList.contains('split-row')).toBe(true);
    expect(disclosure()?.getAttribute('aria-expanded')).toBe('false');
  });

  it('expanding, collapsing and re-expanding the row reads no proof', async () => {
    await expandRow();
    await collapseRow();
    await expandRow();
    await act(async () => {
      await invalidateOperationalQueries(client);
    });
    await collapseRow();
    await expandRow();
    expect(proofSpy).not.toHaveBeenCalled();
  });

  it('the preview disclosure reads once; a segment opens its math in the chunk drawer over the cache', async () => {
    await expandRow();
    await act(async () => {
      disclosure()?.click();
    });
    await vi.waitFor(() => expect(container.querySelector('[data-testid="score-spine"]')).not.toBeNull(), WAIT);
    expect(proofSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      container.querySelector<HTMLButtonElement>('button.score-spine__seg[data-part="intent_trigger"]')?.click();
    });
    await flush();
    const drawer = document.querySelector('.proof-drawer');
    expect(drawer?.classList.contains('is-open')).toBe(true);
    await vi.waitFor(() => expect(document.activeElement?.getAttribute('data-component-key')).toBe('intent_trigger'), WAIT);
    expect(proofSpy).toHaveBeenCalledTimes(1);

    // Collapse and re-expand the row: the cache renders it open, no read.
    await collapseRow();
    await expandRow();
    await vi.waitFor(() => expect(container.querySelector('[data-testid="score-spine"]')).not.toBeNull(), WAIT);
    expect(proofSpy).toHaveBeenCalledTimes(1);
  });

  it('a proof another surface cached renders expanded without a click, even after an approve', async () => {
    client.setQueryData(queryKeys.borrowerProof(LEAD.borrower_id), sampleProof({ borrower_id: LEAD.borrower_id }));
    await act(async () => {
      await invalidateOperationalQueries(client);
    });
    await expandRow();
    await vi.waitFor(() => expect(container.querySelector('[data-testid="score-spine-seal"]')).not.toBeNull(), WAIT);
    expect(disclosure()?.getAttribute('aria-expanded')).toBe('true');
    expect(proofSpy).not.toHaveBeenCalled();
  });
});
