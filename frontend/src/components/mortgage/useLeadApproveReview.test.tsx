/**
 * @vitest-environment happy-dom
 *
 * useLeadApproveReview's own re-check (audit flow-03, wave 1c review round
 * 1). The table closes a review whose row was decided another way, but the
 * hook must not rely on that: Confirm and "Generate draft again" re-read
 * `isEligible` at the moment they act, so a review that is still on screen
 * when its row stopped being approvable can never approve it, nor write a
 * fresh DRAFT_OUTREACH row for it.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { OutreachDraftResult } from '../../lib/apiTypes';
import { useLeadApproveReview } from './useLeadApproveReview';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

vi.mock('../../lib/api', () => ({ isAbortError: () => false }));

const BORROWER = 'B-HOOKREVIEW001';

const DRAFT = {
  generation_id: 'gen-1',
  response_hash: 'hash-1',
  source_refreshed_at: '2026-07-13T12:00:00Z',
  borrower_id: BORROWER,
  offer_code: 'refi',
  channel: 'email',
  subject: 'A quick review of your options',
  body: 'Hello.',
  status: 'draft',
  disclosure_version: 'fixture-2026-07',
  disclosure_state: 'IL',
  marketing_eligible: true,
  generation_mode: 'governed_fallback',
  generator_label: 'Reviewed outreach template',
  strategy_summary: '',
  evidence_summary: [],
  evidence_assets: [],
} satisfies OutreachDraftResult;

type Review = ReturnType<typeof useLeadApproveReview>;

describe('useLeadApproveReview eligibility re-check', () => {
  let container: HTMLDivElement;
  let root: Root;
  let hook: Review | null;
  let eligible: boolean;
  const draftForApproval = vi.fn<(borrowerId: string, signal?: AbortSignal) => Promise<OutreachDraftResult>>();
  const approveLead = vi.fn<(...args: unknown[]) => Promise<'ok' | 'network' | 'backend' | 'aborted' | 'duplicate'>>();
  const onApproved = vi.fn();

  function Harness() {
    hook = useLeadApproveReview({
      canStartApproval: () => true,
      draftForApproval,
      approveLead,
      isEligible: () => eligible,
      onApproved,
    });
    return null;
  }

  beforeEach(() => {
    vi.clearAllMocks();
    hook = null;
    eligible = true;
    approveLead.mockResolvedValue('ok');
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root.render(<Harness />));
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  async function settle() {
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
  }

  it('Confirm on a review whose row was decided meanwhile closes it and approves nothing', async () => {
    draftForApproval.mockResolvedValue(DRAFT);
    act(() => {
      hook!.open(BORROWER, 'inline');
    });
    await settle();
    expect(hook!.review?.phase).toBe('ready');

    // Decided another way while the review was on screen.
    eligible = false;
    await act(async () => {
      await hook!.confirm();
    });

    expect(approveLead).not.toHaveBeenCalled();
    expect(onApproved).not.toHaveBeenCalled();
    expect(hook!.review).toBeNull();
  });

  it('Confirm on a still-eligible row approves the exact draft on screen', async () => {
    draftForApproval.mockResolvedValue(DRAFT);
    act(() => {
      hook!.open(BORROWER, 'inline');
    });
    await settle();
    await act(async () => {
      await hook!.confirm();
    });
    expect(approveLead).toHaveBeenCalledWith(BORROWER, undefined, {}, DRAFT);
    expect(onApproved).toHaveBeenCalledWith(BORROWER);
  });

  it('"Generate draft again" for a row decided meanwhile closes the review and drafts nothing', async () => {
    draftForApproval.mockRejectedValueOnce(new Error('draft service unavailable'));
    act(() => {
      hook!.open(BORROWER, 'inline');
    });
    await settle();
    expect(hook!.review?.phase).toBe('error');
    expect(draftForApproval).toHaveBeenCalledTimes(1);

    eligible = false;
    act(() => {
      hook!.retryDraft();
    });
    await settle();

    expect(draftForApproval).toHaveBeenCalledTimes(1);
    expect(hook!.review).toBeNull();
  });

  it('a failed Confirm keeps the review open and says the draft stays on record', async () => {
    draftForApproval.mockResolvedValue(DRAFT);
    approveLead.mockResolvedValueOnce('backend');
    act(() => {
      hook!.open(BORROWER, 'inline');
    });
    await settle();
    await act(async () => {
      await hook!.confirm();
    });
    expect(hook!.review?.phase).toBe('ready');
    expect(hook!.review?.error).toBe('Not approved. The generated draft stays on record; confirm again or cancel.');
  });

  it('a Confirm that met another decision in flight says so instead of "nothing recorded"', async () => {
    draftForApproval.mockResolvedValue(DRAFT);
    approveLead.mockResolvedValueOnce('duplicate');
    act(() => {
      hook!.open(BORROWER, 'inline');
    });
    await settle();
    await act(async () => {
      await hook!.confirm();
    });
    expect(hook!.review?.error).toBe(
      'Not approved yet: another decision for this borrower is still being recorded. Wait for it to finish, then check the row.',
    );
  });

  it('claims each landed draft once: a moved or remounted review never re-claims it (review round 2)', async () => {
    draftForApproval.mockResolvedValue(DRAFT);
    act(() => {
      hook!.open(BORROWER, 'dialog');
    });
    expect(hook!.claimDraftLanding(BORROWER), 'nothing landed while drafting').toBe(false);
    await settle();
    expect(hook!.review?.phase).toBe('ready');
    expect(hook!.claimDraftLanding('B-SOMEONEELSE1'), 'another row never claims it').toBe(false);
    expect(hook!.claimDraftLanding(BORROWER), 'the first review showing it').toBe(true);
    expect(hook!.claimDraftLanding(BORROWER), 'claimed already').toBe(false);
    act(() => {
      hook!.moveInline();
    });
    expect(hook!.claimDraftLanding(BORROWER), 'the review moved into its row').toBe(false);

    // A new explicit intent is a new landing.
    act(() => {
      hook!.cancel();
    });
    act(() => {
      hook!.open(BORROWER, 'inline');
    });
    await settle();
    expect(hook!.claimDraftLanding(BORROWER)).toBe(true);
  });
});
