/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../components/AppContext', () => ({
  useApp: () => ({ setDrawer: vi.fn(), showEvidence: true }),
}));

import { OfferReviewGrid } from './offer-orchestrator.panels';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe('OfferReviewGrid message intelligence', () => {
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

  it('shows the honest generator, strategy, evidence, and regeneration control', () => {
    act(() => root.render(
      <MemoryRouter>
        <OfferReviewGrid
          borrower={null}
          borrowerId="B-TEST1"
          recommendation={null}
          productLabel="Home-equity review"
          leadIsSaved={false}
          saveCurrentLead={vi.fn()}
          draftWarming={null}
          draftLoaded
          draftError={null}
          draftSubject="A clearer mortgage review"
          onDraftSubjectChange={vi.fn()}
          draftText="A useful governed draft."
          onDraftChange={vi.fn()}
          draftChannel="email"
          onDraftChannelChange={vi.fn()}
          approving={false}
          draftDisclosureVersion="v1"
          draftDisclosureState="IL"
          draftGeneratorLabel="Supervisor-optimized message"
          draftGenerationMode="supervisor"
          draftStrategy="Lead with clarity and one low-pressure response path."
          draftEvidence={[
            'Primary offer: a home-equity line review',
            'Relationship: current customer',
          ]}
          draftEvidenceAssets={[
            'mip.gold.borrower_360',
            'mip.gold.evidence_events',
          ]}
          regenerateDraft={vi.fn()}
          draftIsSaved={false}
          saveCurrentDraft={vi.fn()}
          savedDraftExists={false}
          resetCurrentDraft={vi.fn()}
          draftReady
        />
      </MemoryRouter>,
    ));

    const intelligence = container.querySelector('[data-testid="offer-message-intelligence"]');
    expect(intelligence).not.toBeNull();
    expect(intelligence?.textContent).toContain('Supervisor-optimized message');
    expect(intelligence?.textContent).toContain('Lead with clarity');
    expect(intelligence?.textContent).toContain('Primary offer:');
    expect(intelligence?.textContent).toContain('mip.gold.borrower_360');
    expect(intelligence?.querySelectorAll('.evidence-chip')).toHaveLength(2);
    expect(container.querySelector<HTMLInputElement>('[data-testid="outreach-subject"]')?.value)
      .toBe('A clearer mortgage review');
    const regenerate = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Regenerate',
    );
    expect(regenerate?.disabled).toBe(false);
  });
});
