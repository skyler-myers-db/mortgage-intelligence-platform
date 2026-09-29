/**
 * @vitest-environment happy-dom
 *
 * Focus moves the cursor (w3-queue-place #11b, correction 14's first
 * option). X, Enter and A act on the CURSOR row; a keyboard user who Tabbed
 * to another row's checkbox acted on the row the cursor had been left on.
 * Now a focus landing on any control in a row's main `<tr>` makes that row
 * the cursor row, with no scroll and no announcement.
 *
 * And an Approve waiting on the review chunk is dropped the moment the
 * cursor leaves its row (#11a): every cursor move goes through the cursor
 * hook's one setter, which tells the flow synchronously; the effect that
 * used to compare after each render is gone.
 *
 * The review chunk never loads in this file (its mock hangs), so an Approve
 * stays waiting on it.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { installLocalStorage } from '../../test/installLocalStorage';
import { clearSingleKeyShortcutsPreference } from '../../lib/keymapPreference';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const draftOutreach = vi.fn();

// The approve review's chunk is on a slow network that never answers.
vi.mock('./LeadApproveReview', async () => {
  await new Promise(() => {});
  return {};
});

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: vi.fn(),
    openConsoleRecentActivity: vi.fn(),
    setLastBorrowerId: vi.fn(),
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
    canApprove: true,
    actorEmail: 'approver.one@summit.example',
    sessionStatus: 'ready',
    density: 'comfortable',
  }),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    draftOutreach: (...args: unknown[]) => draftOutreach(...args),
    approve: vi.fn(),
    reject: vi.fn(),
    campaign: vi.fn(),
    auditReceipt: () => new Promise(() => {}),
    salesTeam: () => Promise.resolve({ members: [] }),
  },
}));

import { LeadTable } from './LeadTable';

const IDS = ['B-FOCUSCURSOR01', 'B-FOCUSCURSOR02', 'B-FOCUSCURSOR03'];

function lead(borrowerId: string, index: number): LeadSummary {
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
    opportunity_score: 90 - index,
    confidence: 80,
    recommended_offer_code: 'refi',
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
  } as unknown as LeadSummary;
}

const LEADS = IDS.map(lead);

describe('LeadTable: focus moves the cursor', () => {
  let container: HTMLDivElement;
  let root: Root;
  let scrollIntoView: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView as unknown as Element['scrollIntoView'];
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>
            <LeadTable leads={LEADS} />
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
  const cursorRow = () => container.querySelector('tr.is-cursor')?.getAttribute('data-borrower-row') ?? null;
  const checkbox = (id: string) => container.querySelector<HTMLInputElement>(`[data-testid="lead-select-${id}"]`)!;
  const announcement = () => container.querySelector('[data-testid="lead-cursor-status"]')?.textContent ?? '';
  const opening = () => container.querySelector('[data-testid="lead-approve-review-loading"]')?.textContent ?? null;

  function press(key: string) {
    const target = document.activeElement ?? document.body;
    act(() => {
      target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
    });
  }

  function focus(element: HTMLElement) {
    act(() => element.focus());
  }

  it('focusing a control in another row makes it the cursor row, with no scroll and no announcement', () => {
    focus(region());
    press('j');
    expect(cursorRow()).toBe(IDS[0]);
    const spoken = announcement();
    scrollIntoView.mockClear();

    focus(checkbox(IDS[2]));

    expect(cursorRow()).toBe(IDS[2]);
    expect(announcement(), 'no announcement: the focus already moved').toBe(spoken);
    expect(scrollIntoView, 'no scroll: the focused control is where the reader is').not.toHaveBeenCalled();
    expect(checkbox(IDS[2]).getAttribute('aria-keyshortcuts')).toBe('X Shift+X');
    expect(checkbox(IDS[0]).getAttribute('aria-keyshortcuts')).toBeNull();
  });

  it('X from a Tab-focused row checkbox acts on that row, not on the row the cursor was left on', () => {
    focus(region());
    press('j');
    expect(cursorRow()).toBe(IDS[0]);

    focus(checkbox(IDS[1]));
    press('x');

    expect(checkbox(IDS[1]).checked).toBe(true);
    expect(checkbox(IDS[0]).checked).toBe(false);
  });

  it('an Approve waiting on the review chunk is dropped when focus moves the cursor to another row: no draft', async () => {
    focus(region());
    press('j');
    press('a');
    expect(opening(), 'precondition: the Approve waits on the review chunk').toBe(`Opening the review for ${IDS[0]}…`);

    focus(checkbox(IDS[1]));

    expect(cursorRow()).toBe(IDS[1]);
    expect(opening(), 'the waiting Approve is dropped with the cursor move').toBeNull();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    expect(draftOutreach).not.toHaveBeenCalled();
  });

  it('J from the waiting row drops the Approve too', () => {
    focus(region());
    press('j');
    press('a');
    expect(opening()).toBe(`Opening the review for ${IDS[0]}…`);

    press('j');

    expect(cursorRow()).toBe(IDS[1]);
    expect(opening()).toBeNull();
    expect(draftOutreach).not.toHaveBeenCalled();
  });
});
