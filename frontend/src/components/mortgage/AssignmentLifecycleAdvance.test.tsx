/**
 * @vitest-environment happy-dom
 *
 * S6 lifecycle-advance control: advances one legal step via the server,
 * collects the recorded outcome at the terminal step, surfaces a server
 * 409 honestly, and renders nothing once the lifecycle is closed.
 *
 * Audit critic-06: verb-first advance labels; the outcome is picked, then
 * confirmed ("Record <outcome> for <id>?"); Cancel and Escape close the
 * picker with zero writes and return focus to "Record outcome"; only
 * Record posts, once. (The compact stepper is this lane's budget cut 2:
 * deferred.)
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AssignmentLifecycleStatus } from '../../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  updateAssignmentStatus: vi.fn(),
  recordAssignmentOutcome: vi.fn(),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: apiMocks,
}));

import { AssignmentLifecycleAdvance } from './AssignmentLifecycleAdvance';

const ASSIGNMENT_ID = '66666666-6666-4666-8666-666666666601';
const BORROWER = 'B-48291';

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

let root: Root;
const onAdvanced = vi.fn();

function render(status: AssignmentLifecycleStatus): void {
  root.render(
    <AssignmentLifecycleAdvance
      assignmentId={ASSIGNMENT_ID}
      status={status}
      borrowerId={BORROWER}
      onAdvanced={onAdvanced}
    />,
  );
}

function buttonByText(text: string): HTMLButtonElement | undefined {
  return [...document.querySelectorAll<HTMLButtonElement>('button')].find(
    (btn) => btn.textContent?.trim() === text,
  );
}

function pressEscape(): void {
  act(() => {
    (document.activeElement ?? document.body).dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }),
    );
  });
}

async function openConfirm(outcome: 'Success' | 'No response' | 'Declined'): Promise<void> {
  await act(async () => render('actioned'));
  await act(async () => buttonByText('Record outcome')!.click());
  await act(async () => buttonByText(outcome)!.click());
}

describe('AssignmentLifecycleAdvance', () => {
  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.clearAllMocks();
  });

  it('advances one legal step through the server and reports the new status', async () => {
    apiMocks.updateAssignmentStatus.mockResolvedValue({
      assignment: { assignment_id: ASSIGNMENT_ID, status: 'contact_drafted' },
    });
    await act(async () => render('assigned'));

    const advance = buttonByText('Mark contact drafted');
    expect(advance).toBeTruthy();
    expect(advance!.getAttribute('aria-label')).toBe(`Advance assignment for ${BORROWER} to Contact drafted`);
    await act(async () => advance!.click());
    await settle();

    expect(apiMocks.updateAssignmentStatus).toHaveBeenCalledTimes(1);
    expect(apiMocks.updateAssignmentStatus).toHaveBeenCalledWith(ASSIGNMENT_ID, 'contact_drafted');
    expect(onAdvanced).toHaveBeenCalledWith(BORROWER, { assignment_status: 'contact_drafted' });
  });

  it('names every advance verb-first', async () => {
    for (const [status, label] of [
      ['assigned', 'Mark contact drafted'],
      ['contact_drafted', 'Mark approved'],
      ['approved', 'Mark actioned'],
    ] as const) {
      await act(async () => render(status));
      expect(buttonByText(label), `${status} -> ${label}`).toBeTruthy();
    }
  });

  it('records an outcome only after its confirm row: one POST, then the new stage', async () => {
    apiMocks.recordAssignmentOutcome.mockResolvedValue({
      assignment: { assignment_id: ASSIGNMENT_ID, status: 'outcome_recorded' },
      outcome: 'declined',
      feedback_id: 'fb-1',
    });
    await openConfirm('Declined');

    expect(apiMocks.recordAssignmentOutcome, 'picking an outcome writes nothing').not.toHaveBeenCalled();
    const confirm = document.querySelector(`[data-testid="lifecycle-outcome-confirm-${BORROWER}"]`);
    expect(confirm?.textContent).toContain(`Record Declined for ${BORROWER}?`);
    expect(document.activeElement, 'focus moves to Record').toBe(buttonByText('Record'));

    await act(async () => buttonByText('Record')!.click());
    await settle();

    expect(apiMocks.recordAssignmentOutcome).toHaveBeenCalledTimes(1);
    expect(apiMocks.recordAssignmentOutcome).toHaveBeenCalledWith(ASSIGNMENT_ID, 'declined');
    expect(onAdvanced).toHaveBeenCalledWith(BORROWER, { assignment_status: 'outcome_recorded' });
  });

  it('Back returns to the outcome choice with no write', async () => {
    await openConfirm('Success');
    await act(async () => buttonByText('Back')!.click());
    expect(buttonByText('No response')).toBeTruthy();
    expect(apiMocks.recordAssignmentOutcome).not.toHaveBeenCalled();
  });

  it('Cancel closes the picker with zero writes and returns focus to Record outcome', async () => {
    await act(async () => render('actioned'));
    await act(async () => buttonByText('Record outcome')!.click());
    expect(buttonByText('Cancel')).toBeTruthy();

    await act(async () => buttonByText('Cancel')!.click());

    expect(buttonByText('Success')).toBeUndefined();
    expect(document.activeElement).toBe(buttonByText('Record outcome'));
    expect(apiMocks.recordAssignmentOutcome).not.toHaveBeenCalled();
    expect(onAdvanced).not.toHaveBeenCalled();
  });

  it('Escape closes the whole picker, even from the confirm row, with zero writes', async () => {
    await openConfirm('No response');

    pressEscape();

    expect(document.querySelector(`[data-testid="lifecycle-outcome-confirm-${BORROWER}"]`)).toBeNull();
    expect(buttonByText('Success')).toBeUndefined();
    expect(document.activeElement).toBe(buttonByText('Record outcome'));
    expect(apiMocks.recordAssignmentOutcome).not.toHaveBeenCalled();
  });

  it('surfaces a server 409 honestly instead of faking progress', async () => {
    apiMocks.updateAssignmentStatus.mockRejectedValue(
      new Error("illegal transition 'assigned' -> 'approved'; next stage is 'contact_drafted'"),
    );
    await act(async () => render('assigned'));

    await act(async () => buttonByText('Mark contact drafted')!.click());
    await settle();

    expect(onAdvanced).not.toHaveBeenCalled();
    const alert = document.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('illegal transition');
  });

  it('keeps the stage and shows the error when the outcome write is refused (409)', async () => {
    apiMocks.recordAssignmentOutcome.mockRejectedValue(new Error('assignment is not actioned'));
    await openConfirm('Success');

    await act(async () => buttonByText('Record')!.click());
    await settle();

    expect(apiMocks.recordAssignmentOutcome).toHaveBeenCalledTimes(1);
    expect(onAdvanced).not.toHaveBeenCalled();
    expect(document.querySelector('[role="alert"]')?.textContent).toContain('assignment is not actioned');
  });

  it('renders nothing once the lifecycle is terminal', async () => {
    await act(async () => render('outcome_recorded'));
    expect(document.querySelectorAll('button').length).toBe(0);
  });
});
