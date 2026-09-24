/**
 * @vitest-environment happy-dom
 *
 * The approve-review sheet (audit flow-03, states-06). A row approve used to
 * draft and approve in one click, so the audit row certified copy the
 * approver never saw. Pinned here at the rendered layer:
 *
 *  - Approve (click or A) drafts ONLY on that intent and shows subject,
 *    body, channel ("Email"), the disclosure chip and the evidence chips
 *    inside the `.approval` gate; nothing is approved yet.
 *  - Confirm approves that exact draft (generation id and hash); a second A
 *    or Approve never drafts twice; Cancel / Escape abandons it.
 *  - Approve stays pessimistic: no Approved chip, toast or receipt link
 *    before the approve write returns; after it returns, the toast offers
 *    "View receipt" and the cursor advances to the next pending row.
 *  - A collapsed row reviews in a native modal <dialog>.
 *  - Bulk keeps its required rationale gate, adds count by offer, and drafts
 *    samples only behind "Preview N sample drafts"; the sampled rows are
 *    approved with exactly the previewed drafts.
 *  - The Cmd-K selection verbs are published with the table's own handlers.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { installLocalStorage } from '../../test/installLocalStorage';
import { clearSingleKeyShortcutsPreference } from '../../lib/keymapPreference';
import { currentCommandSelection } from '../command/commandSelection';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const draftOutreach = vi.fn();
const approve = vi.fn();
const setApproval = vi.fn();

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval,
    setLastBorrowerId: vi.fn(),
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
    canApprove: true,
    actorEmail: 'approver.one@summit.example',
    sessionStatus: 'ready',
  }),
}));

vi.mock('../../lib/api', () => ({
  api: {
    draftOutreach: (...args: unknown[]) => draftOutreach(...args),
    approve: (...args: unknown[]) => approve(...args),
    campaign: vi.fn(),
    auditReceipt: () => new Promise(() => {}),
    salesTeam: () => Promise.resolve({ members: [] }),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

import { LeadTable } from './LeadTable';

// The review and the bulk review are lazy chunks: transform them once up
// front so the in-test dynamic import resolves in a few microtasks.
beforeAll(async () => {
  await import('./LeadApproveReview');
  await import('./LeadBulkApproveReview');
}, 60_000);

const IDS = ['B-REVIEW0000001', 'B-REVIEW0000002', 'B-REVIEW0000003', 'B-REVIEW0000004'];

function draftFor(borrowerId: string) {
  return {
    generation_id: `gen-${borrowerId}`,
    response_hash: `hash-${borrowerId}`,
    source_refreshed_at: '2026-07-13T12:00:00Z',
    borrower_id: borrowerId,
    offer_code: 'refi',
    channel: 'email',
    subject: `A quick review of your options (${borrowerId})`,
    body: `Hello,\n\nA loan officer can walk you through the numbers for ${borrowerId}.`,
    status: 'draft',
    disclosure_version: 'fixture-2026-07',
    disclosure_state: 'IL',
    marketing_eligible: true,
    generation_mode: 'governed_fallback',
    generator_label: 'Reviewed outreach template',
    strategy_summary: 'Benefit-led framing.',
    evidence_summary: [],
    evidence_assets: ['mip.gold.borrower_360', 'mip.gold.fn_rate_spread'],
  };
}

function lead(borrowerId: string, offer: [string, string] = ['refi', 'Refinance']): LeadSummary {
  return {
    borrower_id: borrowerId,
    clip: `clip_${borrowerId}`,
    display_name: `Owner ${borrowerId}`,
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 250000,
    rate_spread_bps: 120,
    opportunity_score: 88,
    confidence: 80,
    recommended_offer_code: offer[0],
    recommended_offer: offer[1],
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
  } as unknown as LeadSummary;
}

/** An approve reply the test releases by hand (pessimism is observable). */
function holdApprove() {
  let release: () => void = () => undefined;
  approve.mockImplementation((borrowerId: string) => new Promise((resolve) => {
    release = () => resolve({ approved: true, audit_event_id: `audit-${borrowerId}`, approval_id: 'apr-1' });
  }));
  return { release: () => release() };
}

describe('LeadTable approve review', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    draftOutreach.mockImplementation((borrowerId: string) => Promise.resolve(draftFor(borrowerId)));
    approve.mockImplementation((borrowerId: string) => Promise.resolve({
      approved: true, audit_event_id: `audit-${borrowerId}`, approval_id: 'apr-1',
    }));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const leads = [
      lead(IDS[0]),
      lead(IDS[1], ['heloc', 'HELOC']),
      lead(IDS[2]),
      lead(IDS[3], ['purchase', 'Next-home purchase loan']),
    ];
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>
            <LeadTable leads={leads} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const region = () => container.querySelector<HTMLDivElement>('.tbl-wrap')!;
  const review = () => document.querySelector<HTMLFormElement>('[data-testid="lead-approve-review"]');
  const confirm = () => document.querySelector<HTMLButtonElement>('[data-testid="lead-approve-review-confirm"]')!;
  const approveButton = (id: string) => container.querySelector<HTMLButtonElement>(`[data-testid="lead-approve-${id}"]`)!;
  const cursorId = () => container.querySelector('tr.is-cursor')?.getAttribute('data-borrower-row') ?? null;

  function press(key: string, init: KeyboardEventInit = {}) {
    const target = document.activeElement ?? document.body;
    act(() => {
      target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true, ...init }));
    });
  }

  async function flush() {
    await act(async () => {
      await Promise.resolve();
    });
  }

  /** The review is its own lazy chunk: wait until it renders in `phase`. */
  async function waitForReview(phase = 'ready') {
    await vi.waitFor(async () => {
      await act(async () => {
        await Promise.resolve();
      });
      expect(document.querySelector(`[data-testid="lead-approve-review"][data-review-phase="${phase}"]`)).not.toBeNull();
      // The first dynamic import of the chunk is transformed on demand.
    }, { timeout: 15_000 });
  }

  it('Approve on a collapsed row drafts on that intent only and shows the copy in a modal dialog', async () => {
    region().focus();
    press('j');
    press('Enter');
    press('Enter');
    expect(draftOutreach).not.toHaveBeenCalled();

    act(() => approveButton(IDS[0]).click());
    await waitForReview();

    expect(draftOutreach).toHaveBeenCalledTimes(1);
    expect(draftOutreach).toHaveBeenCalledWith(IDS[0], 'email', expect.any(AbortSignal));
    expect(approve).not.toHaveBeenCalled();
    const dialog = document.querySelector('dialog.lead-approve-dialog');
    expect(dialog?.hasAttribute('open')).toBe(true);
    const form = review()!;
    expect(dialog?.contains(form)).toBe(true);
    expect(form.classList.contains('approval')).toBe(true);
    expect(form.querySelector('[data-testid="lead-approve-review-channel"]')?.textContent).toBe('Email');
    expect(form.querySelector('[data-testid="lead-approve-review-subject"]')?.textContent).toBe(draftFor(IDS[0]).subject);
    expect(form.querySelector('[data-testid="lead-approve-review-body"]')?.textContent).toBe(draftFor(IDS[0]).body);
    expect(form.textContent).toContain('Disclosure fixture-2026-07 · IL');
    expect(form.querySelectorAll('.evidence-chip')).toHaveLength(2);
    expect(confirm().getAttribute('aria-disabled')).toBeNull();

    // A second Approve / A on the open review never drafts again.
    act(() => approveButton(IDS[0]).click());
    await flush();
    expect(draftOutreach).toHaveBeenCalledTimes(1);
  });

  it('Confirm approves that exact draft, pessimistically, then offers the receipt and advances the cursor', async () => {
    const held = holdApprove();
    region().focus();
    press('j');
    press('a');
    await waitForReview();

    act(() => confirm().click());
    await flush();
    expect(approve).toHaveBeenCalledTimes(1);
    expect(approve).toHaveBeenCalledWith(IDS[0], expect.objectContaining({
      channel: 'email',
      draft_subject: draftFor(IDS[0]).subject,
      draft_body: draftFor(IDS[0]).body,
      draft_generation_id: `gen-${IDS[0]}`,
      draft_response_hash: `hash-${IDS[0]}`,
    }), undefined);
    // In flight: the review says so, nothing claims "approved" yet.
    expect(confirm().textContent).toBe('Approving…');
    expect(confirm().getAttribute('aria-disabled')).toBe('true');
    expect(container.querySelector('[data-testid="lead-decision-toast"]')).toBeNull();
    expect(container.querySelector('[data-testid="lead-decision-view-receipt"]')).toBeNull();
    expect(setApproval).not.toHaveBeenCalled();
    // A second Confirm while in flight is not a second POST.
    act(() => confirm().click());
    await flush();
    expect(approve).toHaveBeenCalledTimes(1);

    held.release();
    await flush();
    await flush();

    expect(setApproval).toHaveBeenCalledWith(IDS[0], 'approved');
    expect(review()).toBeNull();
    expect(container.querySelector('[data-testid="lead-decision-toast"]')?.textContent).toContain(`Approved ${IDS[0]}`);
    expect(container.querySelector('[data-testid="lead-decision-view-receipt"]')).not.toBeNull();
    expect(cursorId()).toBe(IDS[1]);
    expect(draftOutreach).toHaveBeenCalledTimes(1);
  });

  it('Cancel abandons the review: no approval, the draft already written stays the only one', async () => {
    region().focus();
    press('j');
    press('a');
    await waitForReview();
    const cancel = [...review()!.querySelectorAll('button')].find((button) => button.textContent === 'Cancel')!;
    act(() => cancel.click());
    await flush();
    expect(review()).toBeNull();
    expect(approve).not.toHaveBeenCalled();
    expect(draftOutreach).toHaveBeenCalledTimes(1);
  });

  it('an expanded row reviews inline, and Escape inside it cancels', async () => {
    region().focus();
    press('j');
    press('Enter');
    press('a');
    await waitForReview();
    const form = review()!;
    expect(container.querySelector('.tbl__expand')?.contains(form)).toBe(true);
    expect(document.querySelector('dialog.lead-approve-dialog')).toBeNull();

    // Draft ready: Confirm took focus, so Enter approves and Escape cancels.
    expect(document.activeElement).toBe(confirm());
    act(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    });
    expect(review()).toBeNull();
    expect(approve).not.toHaveBeenCalled();
  });

  it('a review left open on a row that collapsed comes back as the dialog with the same draft', async () => {
    region().focus();
    press('j');
    press('Enter');
    press('a');
    await waitForReview();
    // Expanding the next row collapses the reviewed one: its inline review
    // is out of sight but still holds the one draft generated for it.
    press('j');
    press('Enter');
    expect(container.querySelector('tr.is-expanded')?.getAttribute('data-borrower-row')).toBe(IDS[1]);
    expect(review()).toBeNull();

    // Approve on the reviewed row again: the SAME review, now as the dialog,
    // never a silent no-op and never a second draft.
    act(() => approveButton(IDS[0]).click());
    await waitForReview();
    const dialog = document.querySelector('dialog.lead-approve-dialog');
    expect(dialog?.hasAttribute('open')).toBe(true);
    expect(dialog?.querySelector('[data-testid="lead-approve-review-subject"]')?.textContent)
      .toBe(draftFor(IDS[0]).subject);
    expect(draftOutreach).toHaveBeenCalledTimes(1);
    expect(approve).not.toHaveBeenCalled();
    // Two waits on the lazy review, each up to 15 s on a loaded machine.
  }, 40_000);

  describe('bulk gate', () => {
    function select(ids: readonly string[]) {
      for (const id of ids) {
        const checkbox = container.querySelector<HTMLInputElement>(`[data-testid="lead-select-${id}"]`)!;
        act(() => checkbox.click());
      }
    }
    const bulkApprove = () => container.querySelector<HTMLButtonElement>('[data-testid="lead-bulk-approve"]')!;

    function typeRationale(value: string) {
      const input = container.querySelector<HTMLInputElement>('.bulk-actions__rationale input')!;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
      act(() => {
        setter?.call(input, value);
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
    }

    it('opens with count by offer, drafts nothing until "Preview N sample drafts", then reuses those drafts', async () => {
      select(IDS);
      act(() => bulkApprove().click());
      // The gate's review is its own lazy chunk.
      await vi.waitFor(async () => {
        await flush();
        expect(container.querySelector('[data-testid="lead-bulk-review"]')).not.toBeNull();
      }, { timeout: 15_000 });

      const gate = container.querySelector('[data-testid="lead-bulk-review"]')!;
      const counts = [...gate.querySelectorAll('[data-testid="lead-bulk-offer-counts"] .chip')]
        .map((chip) => chip.textContent?.replace(/\s+/g, ' ').trim());
      expect(counts).toEqual(['Refinance review 2', 'Home-equity line review 1', 'Next-home purchase loan 1']);
      expect(draftOutreach).not.toHaveBeenCalled();
      const preview = gate.querySelector<HTMLButtonElement>('[data-testid="lead-bulk-preview-samples"]')!;
      expect(preview.textContent).toBe('Preview 3 sample drafts');
      expect(gate.textContent).toContain('Generates 3 audited drafts');

      act(() => preview.click());
      await flush();
      await flush();
      expect(draftOutreach).toHaveBeenCalledTimes(3);
      const samples = gate.querySelectorAll('[data-testid="lead-bulk-samples"] li');
      expect(samples).toHaveLength(3);
      expect(samples[0].textContent).toContain(draftFor(IDS[0]).subject);

      typeRationale('Q3 sweep');
      act(() => bulkApprove().click());
      await flush();
      await flush();
      await flush();

      expect(approve).toHaveBeenCalledTimes(4);
      // Only the unsampled row drafted again; the sampled rows bind the
      // exact drafts that were shown.
      expect(draftOutreach).toHaveBeenCalledTimes(4);
      expect(draftOutreach).toHaveBeenLastCalledWith(IDS[3], 'email', expect.any(AbortSignal));
      expect(approve).toHaveBeenCalledWith(IDS[0], expect.objectContaining({
        draft_generation_id: `gen-${IDS[0]}`,
        bulk_rationale: 'Q3 sweep',
      }), expect.any(AbortSignal));
    });

    it('Shift+A and the Cmd-K verb open the same gate and never submit it', async () => {
      select([IDS[0], IDS[2]]);
      const published = currentCommandSelection();
      expect(published).toEqual(expect.objectContaining({ selectedCount: 2, approveCount: 2, canApprove: true }));

      act(() => published!.run('approve-selected'));
      await flush();
      expect(container.querySelector('.bulk-actions__rationale input')).not.toBeNull();
      typeRationale('already typed');

      region().focus();
      press('A', { shiftKey: true });
      act(() => published!.run('approve-selected'));
      await flush();
      expect(approve).not.toHaveBeenCalled();
      expect(draftOutreach).not.toHaveBeenCalled();
    });

    it('one selected row goes to that row\'s review, never a blind approve', async () => {
      select([IDS[2]]);
      act(() => bulkApprove().click());
      await waitForReview();
      expect(draftOutreach).toHaveBeenCalledWith(IDS[2], 'email', expect.any(AbortSignal));
      expect(approve).not.toHaveBeenCalled();
    });

    it('clears the published selection when nothing is selected', () => {
      select([IDS[0]]);
      expect(currentCommandSelection()?.selectedCount).toBe(1);
      select([IDS[0]]);
      expect(currentCommandSelection()).toBeNull();
    });
  });
});
