/**
 * @vitest-environment happy-dom
 *
 * The expanded row's body chunk fails to load (a stale chunk after a deploy,
 * or offline): the expanded row says so in its status line instead of
 * staying empty (states-07), and the warm-up preload never rejects (a
 * failure is the slot's to show; the slot's own load retries it). The loaded
 * case is LeadTableRow.expanded.test.tsx.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';

vi.mock('./LeadRowExpanded', () => {
  throw new Error('Chunk unavailable');
});

import { LeadRowExpandedSlot, preloadLeadRowExpanded } from './LeadTableRow.expanded';

const LEAD = {
  borrower_id: 'B-EXPANDFAIL001',
  city: 'Chicago',
  state: 'IL',
  zip: '60611',
  segment_codes: ['itm'],
} as unknown as LeadSummary;

describe('the expanded row when its body chunk fails', () => {
  let root: Root;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
  });

  it('the preload settles without rejecting', async () => {
    await expect(preloadLeadRowExpanded()).resolves.toBeUndefined();
  });

  it('says the preview could not load, in the status line', async () => {
    act(() => {
      root.render(
        <LeadRowExpandedSlot
          lead={LEAD}
          approval={undefined}
          decisionReceipt={null}
          approvalGate={null}
          salesBusy={false}
          salesTeamCount={0}
          onOpenDisposition={vi.fn()}
          onAssignmentUpdate={vi.fn()}
        />,
      );
    });
    const line = () => document.querySelector(`[data-testid="lead-preview-pending-${LEAD.borrower_id}"]`);
    expect(line()?.textContent).toBe('Loading the preview…');

    await vi.waitFor(async () => {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
      expect(line()?.textContent).toBe('The preview could not load; reload the page.');
    });
    expect(line()?.getAttribute('role')).toBe('status');
  });
});
