/**
 * @vitest-environment happy-dom
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../components/AppContext', () => ({
  useApp: () => ({ setDrawer: vi.fn(), showEvidence: true }),
}));

import {
  OfferOrchestratorEmptyState,
  OfferReviewGrid,
  RejectRationalePanel,
} from './offer-orchestrator.panels';

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

  it('shows the honest generator, strategy, evidence, and guarded regeneration control', () => {
    const regenerate = vi.fn();
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
          draftText="A useful governed draft."
          draftChannel="email"
          draftProofFresh
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
          regenerateDraft={regenerate}
          draftIsSaved={false}
          saveCurrentDraft={vi.fn()}
          savedDraftExists={false}
          resetCurrentDraft={vi.fn()}
          draftReady
          canAccessAdmin
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
    const regenerateButton = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Regenerate',
    );
    expect(regenerateButton?.disabled).toBe(false);
    expect(container.textContent).toContain('Review the exact audited copy before approval');
    expect(container.querySelector<HTMLInputElement>('[data-testid="outreach-subject"]')?.readOnly)
      .toBe(true);
    expect(container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.readOnly)
      .toBe(true);
    expect(container.querySelector('a[href="/admin-config#offer-rules"]')).not.toBeNull();
    act(() => regenerateButton?.click());
    expect(regenerate).not.toHaveBeenCalled();
    expect(container.textContent).toContain('Regenerating replaces the current audited subject');
    const replace = Array.from(container.querySelectorAll('button')).find(
      (button) => button.textContent?.trim() === 'Replace current draft',
    );
    act(() => replace?.click());
    expect(regenerate).toHaveBeenCalledTimes(1);
  });

  it('renders the no-borrower explanation without implying an offer already exists', () => {
    act(() => root.render(
      <MemoryRouter><OfferOrchestratorEmptyState /></MemoryRouter>,
    ));

    expect(container.textContent).toContain("What you'll see");
    expect(container.textContent).toContain('keeps outreach in human review');
    expect(container.textContent).not.toContain('recommended for this borrower');
  });

  it('requires text for Other rejection and preserves cancel and submit actions', () => {
    const cancel = vi.fn();
    const submit = vi.fn();
    const renderPanel = (rationale: string) => act(() => root.render(
      <MemoryRouter>
        <RejectRationalePanel
          reasonCode="other_with_text"
          rationale={rationale}
          onReasonChange={vi.fn()}
          onRationaleChange={vi.fn()}
          onCancel={cancel}
          onSubmit={submit}
        />
      </MemoryRouter>,
    ));

    renderPanel('');
    const confirm = [...container.querySelectorAll('button')].find(
      (button) => button.textContent?.trim() === 'Confirm reject',
    );
    expect(confirm?.disabled).toBe(true);
    const cancelButton = [...container.querySelectorAll('button')].find(
      (button) => button.textContent?.trim() === 'Cancel',
    );
    act(() => cancelButton?.click());
    expect(cancel).toHaveBeenCalledTimes(1);

    renderPanel('Signal was not appropriate for this borrower.');
    const enabledConfirm = [...container.querySelectorAll('button')].find(
      (button) => button.textContent?.trim() === 'Confirm reject',
    );
    expect(enabledConfirm?.disabled).toBe(false);
    act(() => enabledConfirm?.click());
    expect(submit).toHaveBeenCalledTimes(1);
  });

  it('shows real warming progress and keeps the draft editor disabled', () => {
    act(() => root.render(
      <MemoryRouter>
        <OfferReviewGrid
          borrower={null}
          borrowerId="B-WARMING"
          recommendation={null}
          productLabel="Loading offer"
          leadIsSaved={false}
          saveCurrentLead={vi.fn()}
          draftWarming={{
            dependency: 'warehouse',
            label: 'Warehouse warming up',
            attempt: 2,
            maxAttempts: 6,
            correlationId: 'corr-warming',
          }}
          draftLoaded={false}
          draftError={null}
          draftSubject=""
          draftText=""
          draftChannel="email"
          draftProofFresh={false}
          onDraftChannelChange={vi.fn()}
          approving={false}
          draftDisclosureVersion={null}
          draftDisclosureState={null}
          draftGeneratorLabel={null}
          draftGenerationMode={null}
          draftStrategy={null}
          draftEvidence={[]}
          draftEvidenceAssets={[]}
          regenerateDraft={vi.fn()}
          draftIsSaved={false}
          saveCurrentDraft={vi.fn()}
          savedDraftExists={false}
          resetCurrentDraft={vi.fn()}
          draftReady={false}
        />
      </MemoryRouter>,
    ));

    expect(container.textContent).toContain('Warehouse warming up');
    expect(container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.disabled)
      .toBe(true);
    expect(container.querySelector<HTMLButtonElement>('[aria-label="Save outreach draft for B-WARMING"]')?.disabled)
      .toBe(true);
    expect(container.textContent).toContain('LO call follow-up within 5 days');
    expect(container.querySelector('a[href="/admin-config#offer-rules"]')).toBeNull();
  });

  it('surfaces a draft failure and prevents editing or saving stale copy', () => {
    act(() => root.render(
      <MemoryRouter>
        <OfferReviewGrid
          borrower={{ borrower_id: 'B-ERROR', opportunity_score: 80 } as never}
          borrowerId="B-ERROR"
          recommendation={null}
          productLabel="Offer unavailable"
          leadIsSaved={false}
          saveCurrentLead={vi.fn()}
          draftWarming={null}
          draftLoaded={false}
          draftError="The audited draft could not be loaded."
          draftSubject="Do not edit"
          draftText="Do not send"
          draftChannel="email"
          draftProofFresh={false}
          onDraftChannelChange={vi.fn()}
          approving={false}
          draftDisclosureVersion={null}
          draftDisclosureState={null}
          draftGeneratorLabel={null}
          draftGenerationMode={null}
          draftStrategy={null}
          draftEvidence={[]}
          draftEvidenceAssets={[]}
          regenerateDraft={vi.fn()}
          draftIsSaved={false}
          saveCurrentDraft={vi.fn()}
          savedDraftExists={false}
          resetCurrentDraft={vi.fn()}
          draftReady={false}
        />
      </MemoryRouter>,
    ));

    expect(container.querySelector('[data-testid="draft-unavailable-note"]')?.textContent)
      .toContain('audited draft could not be loaded');
    expect(container.querySelector<HTMLInputElement>('[data-testid="outreach-subject"]')?.disabled)
      .toBe(true);
    expect(container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.disabled)
      .toBe(true);
    expect(container.querySelector<HTMLButtonElement>('[aria-label="Save outreach draft for B-ERROR"]')?.disabled)
      .toBe(true);
  });

  it('explains stale proof and styles stale supervisor copy neutrally', () => {
    const regenerate = vi.fn();
    act(() => root.render(
      <MemoryRouter>
        <OfferReviewGrid
          borrower={{ borrower_id: 'B-STALE', opportunity_score: 80 } as never}
          borrowerId="B-STALE"
          recommendation={null}
          productLabel="Refinance review"
          leadIsSaved={false}
          saveCurrentLead={vi.fn()}
          draftWarming={null}
          draftLoaded
          draftError={null}
          draftSubject="Audited subject"
          draftText="Audited message"
          draftChannel="email"
          draftProofFresh={false}
          onDraftChannelChange={vi.fn()}
          approving={false}
          draftDisclosureVersion="v1"
          draftDisclosureState="IL"
          draftGeneratorLabel="Supervisor-optimized message"
          draftGenerationMode="supervisor"
          draftStrategy={null}
          draftEvidence={[]}
          draftEvidenceAssets={[]}
          regenerateDraft={regenerate}
          draftIsSaved={false}
          saveCurrentDraft={vi.fn()}
          savedDraftExists={false}
          resetCurrentDraft={vi.fn()}
          draftReady={false}
        />
      </MemoryRouter>,
    ));

    const staleNote = container.querySelector('[data-testid="draft-proof-stale-note"]');
    expect(staleNote?.textContent).toContain('borrower data changed');
    const regenerateButton = Array.from(staleNote?.querySelectorAll('button') ?? []).find(
      (button) => button.textContent?.trim() === 'Regenerate draft',
    );
    act(() => regenerateButton?.click());
    expect(regenerate).toHaveBeenCalledTimes(1);
    const generatorChip = container.querySelector('[data-testid="offer-message-intelligence"] .chip');
    expect(generatorChip?.classList.contains('chip--neutral')).toBe(true);
    expect(generatorChip?.textContent).toContain('Supervisor-optimized message');
  });
});
