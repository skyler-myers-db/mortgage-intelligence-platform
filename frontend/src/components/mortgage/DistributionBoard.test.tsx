/**
 * @vitest-environment happy-dom
 *
 * The Distribution Board (wow-power-5 step 2) with a real QueryClient and the
 * real toast store; only the roster read and the salesLoop client are mocked.
 * Pinned: ONE dry run on open; Commit is aria-disabled (never native, so it
 * keeps focus) until a plan with placed leads is on screen; a 409
 * distribution_plan_changed keeps the dialog open, re-previews once, shows
 * before -> after and commits the NEW fingerprint with a NEW request id; a
 * failed re-preview never recommits the refused plan and offers Try again;
 * the dialog cannot be dismissed while committing; closing returns focus to
 * the opener; the 200 raises the run's Undo toast.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DistributionPreview } from '../../lib/apiClients/sales';
import { clearToasts, getToasts } from '../../lib/toast';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({ salesTeam: vi.fn() }));
vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: apiMocks,
}));

const loopMocks = vi.hoisted(() => ({
  releaseAssignment: vi.fn(),
  releaseDistribution: vi.fn(),
  previewDistribution: vi.fn(),
  commitDistribution: vi.fn(),
}));
vi.mock('../../lib/apiClients/salesLoop', () => ({ salesLoopApi: loopMocks }));

import { ApiError } from '../../lib/apiTransport';
import { preloadDescribedError } from '../ui/DescribedError';
import DistributionBoard from './DistributionBoard';

beforeAll(async () => {
  await preloadDescribedError();
});

const TEAM = [
  { email: 'lo02@summit.example', display_label: 'Summit LO 02', role: 'loan_officer', region: 'CA', manager_email: null, capacity_per_day: 10, active: true },
  { email: 'lo01@summit.example', display_label: 'Summit LO 01', role: 'loan_officer', region: 'IL', manager_email: null, capacity_per_day: 10, active: true },
  { email: 'mgr@summit.example', display_label: 'Sales Manager', role: 'sales_manager', region: null, manager_email: null, capacity_per_day: 0, active: true },
];

function plan(fingerprint: string, added: [number, number]): DistributionPreview {
  return {
    strategy: 'score_balanced',
    plan_fingerprint: fingerprint,
    pool_count: added[0] + added[1] + 1,
    placed_count: added[0] + added[1],
    placed_score_sum: 400,
    unplaced_no_capacity: 1,
    skipped_not_assignable: 2,
    skipped_already_assigned: 0,
    officers: [
      { email: 'lo01@summit.example', display_label: 'Summit LO 01', region: 'IL', capacity_per_day: 10, open_count: 4, added_count: added[0], added_score_sum: 250, in_region_count: added[0] },
      { email: 'lo02@summit.example', display_label: 'Summit LO 02', region: 'CA', capacity_per_day: 10, open_count: 12, added_count: added[1], added_score_sum: 150, in_region_count: 0 },
    ],
  };
}

function previewResponse(preview: DistributionPreview) {
  return { assigned_count: 0, strategy: 'score_balanced', assignments: [], per_lo_counts: {}, audit_event_id: null, preview };
}

let root: Root;
let client: QueryClient;
let closed = 0;

const eventually = (assertion: () => void) => vi.waitFor(assertion, { timeout: 10_000 });

function Opener() {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button type="button" id="opener" onClick={() => setOpen(true)}>Open</button>
      {open && (
        <DistributionBoard
          onClose={() => {
            closed += 1;
            setOpen(false);
          }}
        />
      )}
    </>
  );
}

async function openBoard() {
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <Opener />
      </QueryClientProvider>,
    );
  });
  const opener = document.getElementById('opener') as HTMLButtonElement;
  opener.focus();
  await act(async () => {
    opener.click();
  });
}

function commitButton(): HTMLButtonElement {
  return [...document.querySelectorAll<HTMLButtonElement>('dialog button')].find((b) => b.textContent?.includes('Commit distribution')) as HTMLButtonElement;
}

function cancelButton(): HTMLButtonElement {
  return [...document.querySelectorAll<HTMLButtonElement>('dialog button')].find((b) => b.textContent === 'Cancel') as HTMLButtonElement;
}

describe('DistributionBoard', () => {
  beforeEach(() => {
    // reset, not clear: a failed test's unconsumed mock*Once answers must not
    // leak into the next test and turn one red into a cascade of timeouts.
    vi.resetAllMocks();
    clearToasts();
    closed = 0;
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    apiMocks.salesTeam.mockResolvedValue(TEAM);
  });

  afterEach(() => {
    act(() => root.unmount());
    client.clear();
    clearToasts();
    document.body.innerHTML = '';
  });

  it('runs one dry run on open, across the loan officers by label, and shows the plan', async () => {
    loopMocks.previewDistribution.mockResolvedValue(previewResponse(plan('a'.repeat(64), [3, 0])));
    await openBoard();
    await eventually(() => expect(document.querySelectorAll('dialog tbody tr').length).toBe(2));

    expect(loopMocks.previewDistribution).toHaveBeenCalledTimes(1);
    expect(loopMocks.previewDistribution).toHaveBeenCalledWith({
      borrowerIds: [],
      loEmails: ['lo01@summit.example', 'lo02@summit.example'],
    });
    const dialog = document.querySelector('dialog.distribution-board');
    expect(dialog?.textContent).toContain('Distribute approved leads');
    expect(dialog?.textContent).toContain('3 of 4 leads placed · 1 left unplaced: no capacity · 2 skipped: not approved or not contactable');
    const bars = [...document.querySelectorAll<HTMLElement>('.distribution-board__bar')];
    expect(bars.map((bar) => bar.style.gridTemplateColumns)).toEqual(['4fr 3fr 3fr', '12fr 0fr 0fr']);
    expect(bars.every((bar) => bar.getAttribute('aria-hidden') === 'true')).toBe(true);
    // The over-capacity marker only on the officer whose open book exceeds capacity.
    expect(bars.map((bar) => bar.querySelector('.distribution-board__over') !== null)).toEqual([false, true]);
    expect(document.activeElement).toBe(cancelButton());
  });

  it('keeps Commit inert while previewing and when nothing would be placed (aria-disabled, never native)', async () => {
    let answer: (value: unknown) => void = () => undefined;
    loopMocks.previewDistribution.mockReturnValue(new Promise((resolve) => {
      answer = resolve;
    }));
    await openBoard();
    await eventually(() => expect(loopMocks.previewDistribution).toHaveBeenCalledTimes(1));
    expect(commitButton().getAttribute('aria-disabled')).toBe('true');
    expect(commitButton().disabled).toBe(false);

    await act(async () => {
      answer(previewResponse(plan('b'.repeat(64), [0, 0])));
    });
    await eventually(() => expect(document.body.textContent).toContain('0 of 1 leads placed'));
    expect(commitButton().getAttribute('aria-disabled')).toBe('true');
    await act(async () => {
      commitButton().click();
    });
    expect(loopMocks.commitDistribution).not.toHaveBeenCalled();
  });

  it('keeps focus on Commit when a re-preview after a moved plan places nothing', async () => {
    loopMocks.previewDistribution
      .mockResolvedValueOnce(previewResponse(plan('1'.repeat(64), [2, 1])))
      .mockResolvedValueOnce(previewResponse(plan('2'.repeat(64), [0, 0])));
    loopMocks.commitDistribution.mockRejectedValueOnce(
      new ApiError('moved', { path: '/api/sales/distribute', status: 409, reason: 'distribution_plan_changed' }),
    );
    await openBoard();
    await eventually(() => expect(commitButton().getAttribute('aria-disabled')).toBeNull());
    commitButton().focus();

    await act(async () => {
      commitButton().click();
    });
    await eventually(() => expect(document.body.textContent).toContain('0 of 1 leads placed'));
    // Never native disabled: a focused button that turns disabled drops focus to <body>.
    expect(commitButton().disabled).toBe(false);
    expect(commitButton().getAttribute('aria-disabled')).toBe('true');
    expect(document.activeElement).toBe(commitButton());
    await act(async () => {
      commitButton().click();
    });
    expect(loopMocks.commitDistribution).toHaveBeenCalledTimes(1);
  });

  it('never recommits a refused plan when its re-preview fails, and Try again re-plans in place', async () => {
    loopMocks.previewDistribution
      .mockResolvedValueOnce(previewResponse(plan('3'.repeat(64), [3, 1])))
      .mockRejectedValueOnce(new ApiError('boom', { path: '/api/sales/distribute', status: 500 }))
      .mockResolvedValueOnce(previewResponse(plan('4'.repeat(64), [2, 1])));
    loopMocks.commitDistribution
      .mockRejectedValueOnce(new ApiError('moved', { path: '/api/sales/distribute', status: 409, reason: 'distribution_plan_changed' }))
      .mockResolvedValueOnce({
        assigned_count: 3,
        strategy: 'score_balanced',
        assignments: [],
        per_lo_counts: {},
        audit_event_id: 'audit-distribute-4',
        preview: plan('4'.repeat(64), [2, 1]),
      });
    await openBoard();
    await eventually(() => expect(commitButton().getAttribute('aria-disabled')).toBeNull());

    await act(async () => {
      commitButton().click();
    });
    await eventually(() => expect(document.querySelector('dialog [role="alert"]')?.textContent).toContain("Couldn't preview the distribution"));
    // The refused plan is still on screen, but it is neither "changed" nor committable.
    expect(document.body.textContent).not.toContain('The plan changed since your preview');
    expect(document.body.textContent).not.toContain('boom');
    expect(commitButton().getAttribute('aria-disabled')).toBe('true');
    await act(async () => {
      commitButton().click();
    });
    expect(loopMocks.commitDistribution).toHaveBeenCalledTimes(1);

    const tryAgain = [...document.querySelectorAll<HTMLButtonElement>('dialog [role="alert"] button')].find((b) => b.textContent?.includes('Try again'));
    tryAgain?.focus();
    await act(async () => {
      tryAgain?.click();
    });
    await eventually(() => expect(document.body.textContent).toContain('The plan changed since your preview'));
    expect(loopMocks.previewDistribution).toHaveBeenCalledTimes(3);
    expect(document.body.textContent).toContain('Summit LO 01: +3 → +2');
    expect(document.querySelector('dialog [role="alert"]')).toBeNull();
    // The Try again that held focus handed it to Commit, not to <body>.
    expect(document.activeElement).toBe(commitButton());

    await act(async () => {
      commitButton().click();
    });
    await eventually(() => expect(loopMocks.commitDistribution).toHaveBeenCalledTimes(2));
    const [, second] = loopMocks.commitDistribution.mock.calls as Array<[{ fingerprint: string }]>;
    expect(second[0].fingerprint).toBe('4'.repeat(64));
  });

  it('re-previews on a moved plan, shows before -> after, and commits the new fingerprint with a new request id', async () => {
    loopMocks.previewDistribution
      .mockResolvedValueOnce(previewResponse(plan('c'.repeat(64), [3, 1])))
      .mockResolvedValueOnce(previewResponse(plan('d'.repeat(64), [2, 1])));
    loopMocks.commitDistribution
      .mockRejectedValueOnce(new ApiError('moved', { path: '/api/sales/distribute', status: 409, reason: 'distribution_plan_changed' }))
      .mockResolvedValueOnce({
        assigned_count: 3,
        strategy: 'score_balanced',
        assignments: [],
        per_lo_counts: {},
        audit_event_id: 'audit-distribute-9',
        preview: plan('d'.repeat(64), [2, 1]),
      });
    await openBoard();
    await eventually(() => expect(commitButton().getAttribute('aria-disabled')).toBeNull());

    await act(async () => {
      commitButton().click();
    });
    await eventually(() => expect(loopMocks.previewDistribution).toHaveBeenCalledTimes(2));
    await eventually(() => expect(document.body.textContent).toContain('The plan changed since your preview'));
    expect(document.body.textContent).toContain('Summit LO 01: +3 → +2');
    expect(closed).toBe(0);
    expect(document.querySelector('dialog')).toBeTruthy();

    await act(async () => {
      commitButton().click();
    });
    await eventually(() => expect(loopMocks.commitDistribution).toHaveBeenCalledTimes(2));
    const [first, second] = loopMocks.commitDistribution.mock.calls as Array<[{ fingerprint: string; requestId: string }]>;
    expect(first[0].fingerprint).toBe('c'.repeat(64));
    expect(second[0].fingerprint).toBe('d'.repeat(64));
    expect(second[0].requestId).not.toBe(first[0].requestId);
    await eventually(() => expect(closed).toBe(1));
    expect(getToasts()).toEqual([
      expect.objectContaining({ tone: 'success', title: '3 leads assigned', auditEventId: 'audit-distribute-9', action: expect.objectContaining({ label: 'Undo' }) }),
    ]);
    // Focus goes back to the button that opened the Board.
    expect(document.activeElement?.id).toBe('opener');
  });

  it('reads a 409 that reached it without a reason (the base transport parses none) as the moved plan', async () => {
    loopMocks.previewDistribution
      .mockResolvedValueOnce(previewResponse(plan('f'.repeat(64), [2, 2])))
      .mockResolvedValueOnce(previewResponse(plan('0'.repeat(64), [1, 2])));
    loopMocks.commitDistribution.mockRejectedValueOnce(
      new ApiError('The distribution plan changed since the preview.', { path: '/api/sales/distribute', status: 409 }),
    );
    await openBoard();
    await eventually(() => expect(commitButton().getAttribute('aria-disabled')).toBeNull());

    await act(async () => {
      commitButton().click();
    });
    await eventually(() => expect(loopMocks.previewDistribution).toHaveBeenCalledTimes(2));
    await eventually(() => expect(document.body.textContent).toContain('The plan changed since your preview'));
    expect(document.querySelector('dialog [role="alert"]')).toBeNull();
  });

  it('cannot be dismissed while committing, and keeps any other failure inside the dialog', async () => {
    loopMocks.previewDistribution.mockResolvedValue(previewResponse(plan('e'.repeat(64), [1, 1])));
    let fail: (reason: unknown) => void = () => undefined;
    loopMocks.commitDistribution.mockReturnValue(new Promise((_, reject) => {
      fail = reject;
    }));
    await openBoard();
    await eventually(() => expect(commitButton().getAttribute('aria-disabled')).toBeNull());

    await act(async () => {
      commitButton().click();
    });
    await eventually(() => expect(commitButton().getAttribute('aria-busy')).toBe('true'));
    expect(cancelButton().disabled).toBe(true);
    await act(async () => {
      document.querySelector('dialog')?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(closed).toBe(0);

    await act(async () => {
      fail(new ApiError('boom', { path: '/api/sales/distribute', status: 500 }));
    });
    await eventually(() => expect(document.querySelector('dialog [role="alert"]')?.textContent).toContain("Couldn't distribute the leads"));
    expect(document.body.textContent).not.toContain('boom');
    expect(closed).toBe(0);
    expect(getToasts()).toEqual([]);
  });
});
