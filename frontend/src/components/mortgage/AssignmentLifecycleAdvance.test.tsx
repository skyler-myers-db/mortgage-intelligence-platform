/**
 * @vitest-environment happy-dom
 *
 * S6 lifecycle-advance control: advances one legal step via the server,
 * collects the recorded outcome at the terminal step, surfaces a server
 * 409 honestly, and renders nothing once the lifecycle is closed.
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

vi.mock('../../lib/api', () => ({
  api: apiMocks,
}));

import { AssignmentLifecycleAdvance } from './AssignmentLifecycleAdvance';

const ASSIGNMENT_ID = '66666666-6666-4666-8666-666666666601';

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
      borrowerId="B-48291"
      onAdvanced={onAdvanced}
    />,
  );
}

function buttonByText(text: string): HTMLButtonElement | undefined {
  return [...document.querySelectorAll<HTMLButtonElement>('button')].find(
    (btn) => btn.textContent?.trim() === text,
  );
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

    const advance = buttonByText('Contact drafted');
    expect(advance).toBeTruthy();
    await act(async () => advance!.click());
    await settle();

    expect(apiMocks.updateAssignmentStatus).toHaveBeenCalledWith(ASSIGNMENT_ID, 'contact_drafted');
    expect(onAdvanced).toHaveBeenCalledWith('B-48291', { assignment_status: 'contact_drafted' });
  });

  it('collects the recorded outcome at the terminal step', async () => {
    apiMocks.recordAssignmentOutcome.mockResolvedValue({
      assignment: { assignment_id: ASSIGNMENT_ID, status: 'outcome_recorded' },
      outcome: 'declined',
      feedback_id: 'fb-1',
    });
    await act(async () => render('actioned'));

    await act(async () => buttonByText('Record outcome')!.click());
    expect(buttonByText('Success')).toBeTruthy();
    expect(buttonByText('No response')).toBeTruthy();
    await act(async () => buttonByText('Declined')!.click());
    await settle();

    expect(apiMocks.recordAssignmentOutcome).toHaveBeenCalledWith(ASSIGNMENT_ID, 'declined');
    expect(onAdvanced).toHaveBeenCalledWith('B-48291', { assignment_status: 'outcome_recorded' });
  });

  it('surfaces a server 409 honestly instead of faking progress', async () => {
    apiMocks.updateAssignmentStatus.mockRejectedValue(
      new Error("illegal transition 'assigned' -> 'approved'; next stage is 'contact_drafted'"),
    );
    await act(async () => render('assigned'));

    await act(async () => buttonByText('Contact drafted')!.click());
    await settle();

    expect(onAdvanced).not.toHaveBeenCalled();
    const alert = document.querySelector('[role="alert"]');
    expect(alert?.textContent).toContain('illegal transition');
  });

  it('renders nothing once the lifecycle is terminal', async () => {
    await act(async () => render('outcome_recorded'));
    expect(document.querySelectorAll('button').length).toBe(0);
  });
});
