/**
 * @vitest-environment happy-dom
 *
 * A/R hotkey + expanded-preview state contract (re-audit #3, 2026-06-12).
 * The audit pressed A with focus on the row's borrower button and saw no
 * POST, then read the expanded preview's stale "Approval: pending" and
 * concluded the hotkeys were broken. Two real defects hid in that story:
 * the preview ignored the optimistic approval (so a terminal row LOOKED
 * pending), and nothing pinned that the window-level key handler fires
 * from realistic focus positions INSIDE the row. Pins:
 *
 * 1. keydown 'a' bubbling from the row-internal borrower button approves
 *    the expanded pending row (focus inside the row is NOT an excuse);
 * 2. keydown 'a' on an already-approved expanded row is a no-op BY
 *    DESIGN (no POST) — and the preview now says "approved", so the
 *    no-op is legible;
 * 3. keydown from an editable element (the row checkbox) never approves.
 *
 * Focus scope (audit tables-v2 / a11y-09, 2026-09-21, WCAG 2.1.4): the
 * listener is window-level, so with a row expanded A used to approve — and
 * write an audit row — from a filter button, the evidence drawer or Genie
 * chrome. Pins 4-8 below: A/R act only while focus is inside the `.tbl-wrap`
 * region and no dialog, drawer, listbox or menu is open.
 *
 * Wave 1c (audit flow-03 / states-06): A no longer approves blind. It opens
 * the approve review, which drafts on that intent and shows the copy;
 * Confirm approves that exact draft. "Opened the review" (one draft call, no
 * approve) is therefore what an in-scope A proves here; the review itself is
 * pinned in LeadTable.approveReview.test.tsx.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, type ComponentProps } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { commandVerbActions } from '../command/commandActions';
import { currentCommandSelection } from '../command/commandSelection';

const draftOutreach = vi.fn();
const approve = vi.fn();
const campaign = vi.fn();

const DRAFT = {
  generation_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  response_hash: 'a'.repeat(64),
  source_refreshed_at: '2026-07-13T12:00:00Z',
  borrower_id: 'B-AAAAAAAAAAAA1',
  offer_code: 'refi',
  channel: 'email',
  subject: 'Your governed mortgage review',
  body: 'draft',
  status: 'draft',
  disclosure_version: 'fixture-2026-07',
  disclosure_state: 'IL',
  evidence_assets: ['mip.gold.borrower_360'],
};

let approvalsFixture: Record<string, 'approved' | 'rejected'> = {};
const APPROVER_SESSION = {
  canApprove: true,
  actorEmail: 'approver.one@summit.example' as string | null,
  sessionStatus: 'ready' as 'loading' | 'ready' | 'error',
};
let sessionFixture = { ...APPROVER_SESSION };

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: approvalsFixture,
    setApproval: vi.fn(),
    setLastBorrowerId: vi.fn(),
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
    ...sessionFixture,
  }),
}));

vi.mock('../../lib/api', () => ({
  api: {
    draftOutreach: (...args: unknown[]) => draftOutreach(...args),
    approve: (...args: unknown[]) => approve(...args),
    campaign: (...args: unknown[]) => campaign(...args),
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

function lead(borrowerId: string): LeadSummary {
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
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
  } as unknown as LeadSummary;
}

describe('LeadTable A/R hotkeys from row-internal focus', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    vi.clearAllMocks();
    approvalsFixture = {};
    sessionFixture = { ...APPROVER_SESSION };
    draftOutreach.mockResolvedValue(DRAFT);
    approve.mockResolvedValue({ approved: true });
    campaign.mockResolvedValue({
      campaign_id: '11111111-1111-4111-8111-111111111111',
      name: 'Saved campaign',
      owner_email: 'growth@summit.example',
      status: 'draft',
      criteria: {},
      message_variants: [{
        variant_name: 'B',
        channel: 'email',
        subject: 'Your governed mortgage review',
        body: 'Reply to review your options.',
      }],
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function mount(
    initialEntry = '/lead-queue',
    growthAgentVerification: ComponentProps<typeof LeadTable>['growthAgentVerification'] = null,
    leads: LeadSummary[] = [lead('B-AAAAAAAAAAAA1')],
  ) {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[initialEntry]}>
            <LeadTable
              leads={leads}
              growthAgentVerification={growthAgentVerification}
            />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  function expandViaBorrowerButton(): HTMLButtonElement {
    const btn = container.querySelector<HTMLButtonElement>(
      'button.lead-table__borrower-btn',
    );
    if (!btn) throw new Error('borrower button not rendered');
    act(() => btn.click());
    btn.focus();
    return btn;
  }

  function pressKey(target: Element, key: string) {
    act(() => {
      target.dispatchEvent(
        new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }),
      );
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

  /** The review's Confirm button, in the row or in the dialog. */
  function confirmButton(): HTMLButtonElement {
    const button = document.querySelector<HTMLButtonElement>('[data-testid="lead-approve-review-confirm"]');
    if (!button) throw new Error('approve review confirm not rendered');
    return button;
  }

  /** React-visible typing into a controlled input. */
  function typeInto(input: HTMLInputElement, value: string) {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
    act(() => {
      setter?.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  it("'a' from the borrower button opens the review, and only Confirm approves that exact draft", async () => {
    mount();
    const btn = expandViaBorrowerButton();
    expect(container.querySelector('.tbl__expand')).not.toBeNull();

    pressKey(btn, 'a');
    await waitForReview();

    // The draft is generated on this explicit intent, and nothing is approved.
    expect(draftOutreach).toHaveBeenCalledTimes(1);
    expect(draftOutreach).toHaveBeenCalledWith('B-AAAAAAAAAAAA1', 'email', expect.any(AbortSignal));
    expect(approve).not.toHaveBeenCalled();
    const review = container.querySelector('.tbl__expand [data-testid="lead-approve-review"]');
    expect(review?.textContent).toContain(DRAFT.subject);

    act(() => confirmButton().click());
    await flush();

    expect(approve).toHaveBeenCalledWith(
      'B-AAAAAAAAAAAA1',
      expect.objectContaining({
        channel: 'email',
        draft_subject: DRAFT.subject,
        draft_body: DRAFT.body,
        draft_generation_id: DRAFT.generation_id,
        draft_response_hash: DRAFT.response_hash,
        draft_source_refreshed_at: DRAFT.source_refreshed_at,
      }),
      undefined,
    );
    expect(draftOutreach).toHaveBeenCalledTimes(1);
  });

  it('forwards the governed email subject through the rationale-gated bulk approval API contract', async () => {
    mount('/lead-queue', null, [lead('B-AAAAAAAAAAAA1'), lead('B-AAAAAAAAAAAA2')]);
    for (const id of ['B-AAAAAAAAAAAA1', 'B-AAAAAAAAAAAA2']) {
      const checkbox = container.querySelector<HTMLInputElement>(`[data-testid="lead-select-${id}"]`);
      if (!checkbox) throw new Error('lead checkbox not rendered');
      act(() => checkbox.click());
    }

    const bulkApprove = container.querySelector<HTMLButtonElement>(
      '[data-testid="lead-bulk-approve"]',
    );
    if (!bulkApprove) throw new Error('bulk approve button not rendered');
    // The first click opens the required rationale gate: nothing drafts.
    act(() => bulkApprove.click());
    await flush();
    expect(draftOutreach).not.toHaveBeenCalled();
    const rationale = container.querySelector<HTMLInputElement>('.bulk-actions__rationale input');
    if (!rationale) throw new Error('bulk rationale gate not rendered');
    typeInto(rationale, 'Q3 retention sweep');

    act(() => bulkApprove.click());
    await flush();
    await flush();

    expect(approve).toHaveBeenCalledTimes(2);
    expect(approve).toHaveBeenCalledWith(
      'B-AAAAAAAAAAAA1',
      expect.objectContaining({
        channel: 'email',
        draft_subject: DRAFT.subject,
        draft_body: DRAFT.body,
        draft_generation_id: DRAFT.generation_id,
        draft_response_hash: DRAFT.response_hash,
        draft_source_refreshed_at: DRAFT.source_refreshed_at,
        bulk_rationale: 'Q3 retention sweep',
      }),
      expect.any(AbortSignal),
    );
  });

  it('preserves a saved campaign variant through draft, approval, and the Offer handoff', async () => {
    const campaignId = '11111111-1111-4111-8111-111111111111';
    draftOutreach.mockResolvedValue({
      ...DRAFT,
      campaign_id: campaignId,
      variant_name: 'B',
    });
    mount(`/lead-queue?campaign_id=${campaignId}&variant_name=B`);
    await flush();
    await flush();
    const checkbox = container.querySelector<HTMLInputElement>(
      '[data-testid="lead-select-B-AAAAAAAAAAAA1"]',
    );
    if (!checkbox) throw new Error('lead checkbox not rendered');
    act(() => checkbox.click());

    const bulkApprove = container.querySelector<HTMLButtonElement>(
      '[data-testid="lead-bulk-approve"]',
    );
    if (!bulkApprove) throw new Error('bulk approve button not rendered');
    // One selected row: "Approve 1 eligible" opens that row's review (a
    // dialog, the row is collapsed), never a blind approve.
    act(() => bulkApprove.click());
    await waitForReview();

    expect(draftOutreach).toHaveBeenCalledWith(
      'B-AAAAAAAAAAAA1',
      'email',
      expect.any(AbortSignal),
      { campaign_id: campaignId, variant_name: 'B' },
    );
    expect(approve).not.toHaveBeenCalled();
    expect(document.querySelector('dialog.lead-approve-dialog [data-testid="lead-approve-review"]')).not.toBeNull();
    act(() => confirmButton().click());
    await flush();
    expect(approve).toHaveBeenCalledWith(
      'B-AAAAAAAAAAAA1',
      expect.objectContaining({ campaign_id: campaignId, variant_name: 'B' }),
      undefined,
    );
    expect(container.querySelector('[data-testid="campaign-operational-provenance"]')?.textContent)
      .toContain('variant B');

    const borrower = container.querySelector<HTMLButtonElement>('.lead-table__borrower-btn');
    if (!borrower) throw new Error('borrower row button not rendered');
    act(() => borrower.click());
    const offer = container.querySelector<HTMLAnchorElement>('a[href*="/offer-orchestrator/"]');
    if (!offer) throw new Error('bound Offer action not rendered');
    const offerUrl = new URL(offer.href, 'https://mortgage-intelligence.local');
    expect(offerUrl.searchParams.get('campaign_id')).toBe(campaignId);
    expect(offerUrl.searchParams.get('variant_name')).toBe('B');
  });

  it('shows verified Growth Agent cohort provenance only from backend verification', () => {
    const verification = {
      status: 'verified' as const,
      runId: '11111111-1111-4111-8111-111111111111',
      total: 5394,
      cohortFingerprint: 'b'.repeat(64),
      snapshotId: '2026-07-14 12:00:00',
    };
    mount('/lead-queue', verification);

    const proof = container.querySelector('[data-testid="growth-agent-cohort-proof"]');
    expect(proof?.textContent).toContain('Verified Growth Agent cohort');
    expect(proof?.textContent).toContain('5,394 borrowers');
    expect(proof?.textContent).toContain('proof bbbbbbbbbbbb');
    expect(proof?.textContent).toContain('snapshot 2026-07-14 12:00:00');
    expect(proof?.textContent).toContain('run 11111111-111');
  });

  it('does not trust a proof-shaped Lead Queue URL without backend verification', () => {
    mount(
      `/lead-queue?growth_agent_run_id=11111111-1111-4111-8111-111111111111`
      + '&actionable_total=5394'
      + `&actionable_cohort_fingerprint=${'b'.repeat(64)}`
      + '&actionable_snapshot_id=2026-07-14+12%3A00%3A00',
    );

    expect(container.querySelector('[data-testid="growth-agent-cohort-proof"]')).toBeNull();
  });

  it("no-ops 'a' on an already-approved row, and the preview SAYS approved", async () => {
    approvalsFixture = { 'B-AAAAAAAAAAAA1': 'approved' };
    mount();
    const btn = expandViaBorrowerButton();

    // The fixed preview reflects the effective (optimistic) state, so the
    // operator can SEE why A/R will not fire here.
    const previewText = container.querySelector('.tbl__expand')?.textContent ?? '';
    expect(previewText).toContain('approved');
    expect(previewText).not.toContain('pending');

    pressKey(btn, 'a');
    await flush();

    expect(draftOutreach).not.toHaveBeenCalled();
    expect(approve).not.toHaveBeenCalled();
  });

  describe('focus scope (tables-v2 / a11y-09)', () => {
    const strays: HTMLElement[] = [];

    function addToBody(html: string): HTMLElement {
      const host = document.createElement('div');
      host.innerHTML = html;
      const el = host.firstElementChild as HTMLElement;
      document.body.appendChild(el);
      strays.push(el);
      return el;
    }

    afterEach(() => {
      strays.splice(0).forEach((el) => el.remove());
    });

    it("does not approve when 'a' is pressed on a focused button outside the table", async () => {
      mount();
      expandViaBorrowerButton();
      const filterButton = addToBody('<button type="button" class="filter">STATE</button>');
      filterButton.focus();
      expect(document.activeElement).toBe(filterButton);

      pressKey(filterButton, 'a');
      pressKey(filterButton, 'r');
      await flush();

      expect(draftOutreach).not.toHaveBeenCalled();
      expect(approve).not.toHaveBeenCalled();
      // R must not open the reject panel either.
      expect(container.querySelector('.decision-panel')).toBeNull();
    });

    it('does not approve from the table header controls outside the scroll region', async () => {
      mount();
      expandViaBorrowerButton();
      const exportButton = container.querySelector<HTMLButtonElement>('[data-testid="lead-export"]');
      if (!exportButton) throw new Error('export button not rendered');
      exportButton.focus();

      pressKey(exportButton, 'a');
      await flush();

      expect(draftOutreach).not.toHaveBeenCalled();
      expect(approve).not.toHaveBeenCalled();
    });

    it('does not approve when nothing is focused (keydown lands on body)', async () => {
      mount();
      const btn = expandViaBorrowerButton();
      btn.blur();

      pressKey(document.body, 'a');
      await flush();

      expect(draftOutreach).not.toHaveBeenCalled();
      expect(approve).not.toHaveBeenCalled();
    });

    it('does not approve while the evidence drawer is open, even with focus in the table', async () => {
      mount();
      const btn = expandViaBorrowerButton();
      addToBody(
        '<aside class="drawer is-open" role="dialog" aria-modal="true" aria-hidden="false">'
        + '<button type="button">Close</button></aside>',
      );

      pressKey(btn, 'a');
      await flush();

      expect(draftOutreach).not.toHaveBeenCalled();
      expect(approve).not.toHaveBeenCalled();
    });

    it('does not approve from inside the evidence drawer', async () => {
      mount();
      expandViaBorrowerButton();
      const drawer = addToBody(
        '<aside class="drawer is-open" role="dialog" aria-modal="true" aria-hidden="false">'
        + '<button type="button">Close</button></aside>',
      );
      const close = drawer.querySelector('button') as HTMLButtonElement;
      close.focus();

      pressKey(close, 'a');
      await flush();

      expect(draftOutreach).not.toHaveBeenCalled();
      expect(approve).not.toHaveBeenCalled();
    });

    it.each([
      ['a filter listbox', '<ul class="filter-menu" role="listbox"><li role="option">IL</li></ul>'],
      ['a menu', '<div class="filter-menu" role="menu"><button role="menuitem">Past chat</button></div>'],
      ['the command palette', '<div class="cmdk"><div class="cmdk__panel" role="dialog" aria-modal="true"></div></div>'],
      ['the Genie panel', '<div class="genie is-open" role="dialog" aria-hidden="false"></div>'],
    ])('does not approve while %s is open', async (_name, html) => {
      mount();
      const btn = expandViaBorrowerButton();
      addToBody(html);

      pressKey(btn, 'a');
      await flush();

      expect(draftOutreach).not.toHaveBeenCalled();
      expect(approve).not.toHaveBeenCalled();
    });

    it('still opens the review with the always-mounted drawer and Genie panel CLOSED', async () => {
      mount();
      const btn = expandViaBorrowerButton();
      addToBody('<aside class="drawer" role="dialog" aria-modal="true" aria-hidden="true"></aside>');
      addToBody(
        '<div class="genie" role="dialog" aria-hidden="true"><div role="menu"></div></div>',
      );

      pressKey(btn, 'a');
      await waitForReview();

      expect(draftOutreach).toHaveBeenCalledTimes(1);
      expect(container.querySelector('[data-testid="lead-approve-review"]')).not.toBeNull();
      act(() => confirmButton().click());
      await flush();
      expect(approve).toHaveBeenCalledTimes(1);
    });

    it('still opens the review when focus is on the table scroll region itself (click-row-then-A)', async () => {
      mount();
      expandViaBorrowerButton();
      const region = container.querySelector<HTMLDivElement>('.tbl-wrap');
      if (!region) throw new Error('table region not rendered');
      region.focus();
      expect(document.activeElement).toBe(region);

      pressKey(region, 'a');
      await waitForReview();

      expect(draftOutreach).toHaveBeenCalledTimes(1);
      expect(container.querySelector('[data-testid="lead-approve-review"]')).not.toBeNull();
      expect(approve).not.toHaveBeenCalled();
    });

    it("still opens the reject panel when 'r' is pressed with focus in the table", () => {
      mount();
      const btn = expandViaBorrowerButton();
      expect(container.querySelector('.decision-panel')).toBeNull();

      pressKey(btn, 'r');

      expect(container.querySelector('.decision-panel')).not.toBeNull();
    });

    it('advertises A / R only on the cursor row and Shift+A on bulk approve', () => {
      mount();
      const approveBtn = () => container.querySelector('[data-testid="lead-approve-B-AAAAAAAAAAAA1"]');
      const rejectBtn = () => container.querySelector('[data-testid="lead-reject-B-AAAAAAAAAAAA1"]');
      expect(approveBtn()?.getAttribute('aria-keyshortcuts')).toBeNull();
      expect(rejectBtn()?.getAttribute('aria-keyshortcuts')).toBeNull();

      expandViaBorrowerButton();
      expect(approveBtn()?.getAttribute('aria-keyshortcuts')).toBe('A');
      expect(rejectBtn()?.getAttribute('aria-keyshortcuts')).toBe('R');

      const checkbox = container.querySelector<HTMLInputElement>(
        '[data-testid="lead-select-B-AAAAAAAAAAAA1"]',
      );
      if (!checkbox) throw new Error('lead checkbox not rendered');
      act(() => checkbox.click());
      expect(
        container.querySelector('[data-testid="lead-bulk-approve"]')?.getAttribute('aria-keyshortcuts'),
      ).toBe('Shift+A');
    });
  });

  describe('approver role gate (flow-02 / shell-06)', () => {
    const approveBtn = () => container.querySelector<HTMLButtonElement>(
      '[data-testid="lead-approve-B-AAAAAAAAAAAA1"]',
    );
    const rejectBtn = () => container.querySelector<HTMLButtonElement>(
      '[data-testid="lead-reject-B-AAAAAAAAAAAA1"]',
    );

    it('keeps the gate visible but disabled, with the accessible reason, for a non-approver', () => {
      sessionFixture = { canApprove: false, actorEmail: 'analyst@summit.example', sessionStatus: 'ready' };
      mount();
      expandViaBorrowerButton();

      const status = container.querySelector('[data-testid="approver-role-status"]');
      expect(status?.textContent).toContain('Requires approver role');
      expect(status?.textContent).toContain('analyst@summit.example');
      for (const btn of [approveBtn(), rejectBtn()]) {
        expect(btn).not.toBeNull();
        expect(btn!.disabled).toBe(true);
        expect(btn!.getAttribute('title')).toBe('Requires approver role');
        expect(btn!.getAttribute('aria-describedby')).toBe(status!.id);
        // A disabled, gated control must not advertise a live shortcut.
        expect(btn!.getAttribute('aria-keyshortcuts')).toBeNull();
      }
      expect(container.querySelector('[data-testid="lead-approving-as"]')).toBeNull();
    });

    it('A / R / Shift+A do nothing for a non-approver, before any draft call', async () => {
      sessionFixture = { canApprove: false, actorEmail: null, sessionStatus: 'ready' };
      mount();
      const checkbox = container.querySelector<HTMLInputElement>(
        '[data-testid="lead-select-B-AAAAAAAAAAAA1"]',
      );
      if (!checkbox) throw new Error('lead checkbox not rendered');
      act(() => checkbox.click());
      const btn = expandViaBorrowerButton();

      pressKey(btn, 'a');
      pressKey(btn, 'r');
      act(() => {
        btn.dispatchEvent(new KeyboardEvent('keydown', {
          key: 'A', shiftKey: true, bubbles: true, cancelable: true,
        }));
      });
      await flush();

      expect(draftOutreach).not.toHaveBeenCalled();
      expect(approve).not.toHaveBeenCalled();
      expect(container.querySelector('.decision-panel')).toBeNull();
    });

    it('publishes the selection to Cmd-K with no approve verb for a non-approver (wow-power-4)', () => {
      sessionFixture = { canApprove: false, actorEmail: null, sessionStatus: 'ready' };
      mount();
      const checkbox = container.querySelector<HTMLInputElement>(
        '[data-testid="lead-select-B-AAAAAAAAAAAA1"]',
      );
      if (!checkbox) throw new Error('lead checkbox not rendered');
      act(() => checkbox.click());

      const published = currentCommandSelection();
      expect(published).toEqual(expect.objectContaining({ selectedCount: 1, approveCount: 1, canApprove: false }));
      expect(commandVerbActions(published).map((action) => action.id)).not.toContain('verb-approve-selected');
    });

    it('disables bulk approve with the reason for a non-approver and never drafts on click', async () => {
      sessionFixture = { canApprove: false, actorEmail: null, sessionStatus: 'ready' };
      mount();
      const checkbox = container.querySelector<HTMLInputElement>(
        '[data-testid="lead-select-B-AAAAAAAAAAAA1"]',
      );
      if (!checkbox) throw new Error('lead checkbox not rendered');
      act(() => checkbox.click());

      const bulk = container.querySelector<HTMLButtonElement>('[data-testid="lead-bulk-approve"]');
      expect(bulk).not.toBeNull();
      expect(bulk!.disabled).toBe(true);
      expect(bulk!.getAttribute('title')).toBe('Requires approver role');
      expect(bulk!.getAttribute('aria-describedby')).toBe('approver-role-status');
      expect(container.querySelector('[data-testid="approver-role-status"]')?.textContent)
        .toContain('for this sign-in');

      act(() => bulk!.click());
      act(() => approveBtn()?.click());
      await flush();

      expect(draftOutreach).not.toHaveBeenCalled();
      expect(approve).not.toHaveBeenCalled();
    });

    it('says "checking" rather than a false missing-role claim while the session loads', () => {
      sessionFixture = { canApprove: false, actorEmail: null, sessionStatus: 'loading' };
      mount();

      const status = container.querySelector('[data-testid="approver-role-status"]');
      expect(status?.textContent).toContain('Checking approver role');
      expect(status?.textContent).not.toContain('Requires approver role');
      expect(approveBtn()!.disabled).toBe(true);
    });

    it('names the approver the audit row will carry and leaves the controls live', () => {
      mount();

      expect(container.querySelector('[data-testid="approver-role-status"]')).toBeNull();
      expect(container.querySelector('[data-testid="lead-approving-as"]')?.textContent)
        .toBe('approver.one@summit.example');
      expect(approveBtn()!.disabled).toBe(false);
      expect(rejectBtn()!.disabled).toBe(false);
      expect(approveBtn()!.getAttribute('aria-describedby')).toBeNull();
    });
  });

  it('never approves from an editable element', async () => {
    mount();
    expandViaBorrowerButton();
    // A text field inside the table region (the row checkbox is no longer
    // an editable target, wave-3 review #9): typing "a" must never act.
    const cell = container.querySelector('.tbl__expand td');
    if (!cell) throw new Error('expanded row not rendered');
    const field = document.createElement('input');
    field.type = 'text';
    cell.appendChild(field);
    field.focus();

    pressKey(field, 'a');
    await flush();

    expect(draftOutreach).not.toHaveBeenCalled();
    expect(approve).not.toHaveBeenCalled();
  });

  // Wave-3 review #9: a focused row checkbox takes no typing, so J / K / X
  // / Enter act from it (they used to be switched off there).
  it('J and X act from a focused row checkbox', () => {
    mount('/lead-queue', null, [lead('B-AAAAAAAAAAAA1'), lead('B-AAAAAAAAAAAA2')]);
    const checkbox = container.querySelector<HTMLInputElement>('[data-testid="lead-select-B-AAAAAAAAAAAA1"]');
    if (!checkbox) throw new Error('row checkbox not rendered');
    checkbox.focus();

    pressKey(checkbox, 'j');
    expect(container.querySelector('tr.is-cursor')?.getAttribute('data-borrower-row')).toBe('B-AAAAAAAAAAAA1');
    pressKey(document.activeElement ?? checkbox, 'j');
    expect(container.querySelector('tr.is-cursor')?.getAttribute('data-borrower-row')).toBe('B-AAAAAAAAAAAA2');
    pressKey(document.activeElement ?? checkbox, 'x');
    expect(container.querySelector<HTMLInputElement>('[data-testid="lead-select-B-AAAAAAAAAAAA2"]')?.checked).toBe(true);
    expect(draftOutreach).not.toHaveBeenCalled();
  });
});
