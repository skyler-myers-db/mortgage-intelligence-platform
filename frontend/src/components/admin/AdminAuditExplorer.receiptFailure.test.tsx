/**
 * @vitest-environment happy-dom
 *
 * A receipt chunk that will not load (a stale deploy) used to be swallowed
 * (`() => undefined`), so an expanded decision row silently showed no
 * receipt. It now says so, with a Reload (w3-score-anatomy review). A
 * non-decision row never mentions a receipt.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AuditEventRow } from '../../lib/apiTypes';

vi.mock('../mortgage/DecisionReceipt', () => {
  throw new Error('Failed to fetch dynamically imported module (fixture)');
});

import { AuditEventTableRow } from './AdminAuditExplorer.row';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const APPROVE_ROW: AuditEventRow = {
  event_id: '6f2a9c1e-3b4d-4e5f-8a6b-7c8d9e0f1a2b',
  actor: 'ledger.approver@summit.example',
  action: 'outreach.approve',
  entity_type: 'borrower',
  entity_id: 'B-0000000000001',
  payload_json: {},
  evidence_ids: [],
  created_at: '2026-07-14T15:00:04Z',
  event_type: 'APPROVE',
  request_id: 'req-1',
  correlation_id: 'corr-1',
};

describe('an explorer receipt that cannot load', () => {
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

  async function renderRow(event: AuditEventRow) {
    await act(async () => {
      root.render(
        <QueryClientProvider client={new QueryClient()}>
          <MemoryRouter>
            <table>
              <tbody>
                <AuditEventTableRow event={event} expanded copiedValue={null} onToggle={() => undefined} onCopy={() => undefined} />
              </tbody>
            </table>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    for (let i = 0; i < 8; i += 1) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }
  }

  it('says so on an expanded decision row, with a Reload', async () => {
    await renderRow(APPROVE_ROW);
    await vi.waitFor(() => expect(container.querySelector('[data-testid="audit-explorer-receipt-failed"]')).not.toBeNull());
    const line = container.querySelector('[data-testid="audit-explorer-receipt-failed"]');
    expect(line?.getAttribute('role')).toBe('status');
    expect(line?.textContent).toContain('Receipt could not load; reload the page to read it.');
    expect(line?.querySelector('button')?.textContent).toBe('Reload');
    expect(container.querySelector('[data-testid="audit-explorer-receipt"]')).toBeNull();
  });

  it('never mentions a receipt on a non-decision row', async () => {
    await renderRow({ ...APPROVE_ROW, event_id: 'evt-view-1', action: 'VIEW_LEADS', event_type: 'VIEW_LEADS' });
    expect(container.textContent).toContain('Event details');
    expect(container.querySelector('[data-testid="audit-explorer-receipt-failed"]')).toBeNull();
  });
});
