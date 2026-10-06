/**
 * @vitest-environment happy-dom
 *
 * BorrowerDecisionHistory (audit flow-04 phase 2): rendered through the real
 * client and query factory, with only the transport's getJson faked.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { BorrowerDecisionEvent, BorrowerDecisionHistoryResponse } from '../../lib/apiClients/borrowerDecisions';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const transport = vi.hoisted(() => ({ getJson: vi.fn() }));

vi.mock('../../lib/apiTransport', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/apiTransport')>()),
  getJson: transport.getJson,
}));

// The receipt has its own suite; here it is a probe of what the row passes.
vi.mock('./DecisionReceipt', () => ({
  DecisionReceipt: (props: { auditEventId: string; decision: string; compact?: boolean; headingLevel?: number }) => (
    <div data-testid="receipt-probe" data-decision={props.decision} data-compact={String(props.compact)} data-level={props.headingLevel}>
      {props.auditEventId}
    </div>
  ),
}));

import { ApiError } from '../../lib/apiTransport';
import { BorrowerDecisionHistory } from './BorrowerDecisionHistory';

const ID = 'B-DECISIONS0001';
const SENTINEL = 'SENTINEL transport text 500 Internal Server Error';

function event(overrides: Partial<BorrowerDecisionEvent> = {}): BorrowerDecisionEvent {
  return {
    audit_event_id: `audit-${Math.random().toString(16).slice(2)}`,
    event_type: 'CALL_DISPOSITION',
    outcome: 'disposition',
    occurred_at: '2026-09-01T12:00:00Z',
    actor_display: 'Summit LO 02 (Loan officer)',
    actor_kind: 'staff',
    is_own: false,
    offer_code: null,
    channel: null,
    rationale_label: null,
    contact_block_label: null,
    assigned_to_display: null,
    from_status: null,
    to_status: null,
    disposition_outcome: 'connected',
    lead_outcome_type: null,
    activation_status: null,
    bulk: false,
    receipt_available: false,
    ...overrides,
  };
}

function history(items: BorrowerDecisionEvent[], truncated = false): BorrowerDecisionHistoryResponse {
  return { borrower_id: ID, items, truncated };
}

let root: Root;
let queryClient: QueryClient;

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
  queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  transport.getJson.mockReset();
});

afterEach(() => {
  act(() => root.unmount());
  queryClient.clear();
  document.body.innerHTML = '';
});

async function mount(variant: 'surface' | 'disclosure' = 'surface') {
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <BorrowerDecisionHistory borrowerId={ID} variant={variant} />
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    await vi.dynamicImportSettled();
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

const text = () => document.body.textContent ?? '';
const rows = () => [...document.querySelectorAll('ol[aria-label="Decision history"] > li.audit')];
const button = (name: RegExp) => [...document.querySelectorAll('button')].find((b) => name.test(b.textContent ?? ''));

describe('BorrowerDecisionHistory', () => {
  it('reads the history once and renders each row as the prototype audit row', async () => {
    transport.getJson.mockResolvedValue(history([
      event({
        event_type: 'OUTREACH_REJECT', outcome: 'rejected', offer_code: 'heloc', channel: 'sms',
        rationale_label: 'Compliance review', disposition_outcome: null,
      }),
      event({ event_type: 'APPROVE', outcome: 'approved', is_own: true, bulk: true, actor_display: 'Summit LO 01 (Loan officer)', disposition_outcome: null }),
      event({ event_type: 'SUPPRESS_CONTACT', outcome: 'contact_blocked', contact_block_label: 'No marketing consent', disposition_outcome: null }),
      event({ event_type: 'LEAD_ASSIGNMENT_STATUS', outcome: 'status_changed', from_status: 'assigned', to_status: 'contact_drafted', disposition_outcome: null }),
    ]));
    await mount();

    expect(transport.getJson).toHaveBeenCalledTimes(1);
    expect(transport.getJson.mock.calls[0][0]).toBe(`/api/borrowers/${ID}/decisions`);
    expect(document.querySelector('h2')?.textContent).toBe('Decision history');
    const [reject, approve, blocked, status] = rows();
    expect(reject.querySelector('.audit__what')?.textContent).toBe('Outreach rejected');
    expect(reject.querySelector('.audit__who')?.textContent).toContain('Reason: Compliance review');
    expect(reject.querySelector('.audit__who')?.textContent).toContain('SMS');
    expect(reject.querySelector('.audit__ico')?.className).toBe('audit__ico red');
    expect(reject.querySelector('.audit__ico')?.getAttribute('aria-hidden')).toBe('true');
    expect(approve.querySelector('.audit__who')?.textContent).toBe('Summit LO 01 (Loan officer) · you · bulk');
    expect(approve.querySelector('.audit__ico')?.className).toBe('audit__ico green');
    expect(blocked.querySelector('.audit__who')?.textContent).toContain('Contact blocked: No marketing consent');
    expect(status.querySelector('.audit__who')?.textContent).toContain('Assigned → Contact drafted');
    expect(status.querySelector('.audit__ico')?.className).toBe('audit__ico amber');
    const time = reject.querySelector('time.audit__time');
    expect(time?.getAttribute('dateTime')).toBe('2026-09-01T12:00:00.000Z');
    expect(time?.hasAttribute('title')).toBe(false);
  });

  it('renders nothing for a viewer outside the working team (403)', async () => {
    transport.getJson.mockRejectedValue(new ApiError(SENTINEL, { path: `/api/v1/borrowers/${ID}/decisions`, status: 403 }));
    await mount();
    expect(document.getElementById('root')?.innerHTML).toBe('');

    act(() => root.unmount());
    root = createRoot(document.getElementById('root') as HTMLElement);
    await mount('disclosure');
    expect(document.getElementById('root')?.innerHTML).toBe('');
  });

  it('speaks the shared error vocabulary with an explicit Retry, never the transport text', async () => {
    transport.getJson.mockRejectedValueOnce(new ApiError(SENTINEL, { path: `/api/v1/borrowers/${ID}/decisions`, status: 500 }));
    await mount();

    expect(text()).not.toContain('SENTINEL');
    expect(text()).toContain("Couldn't load the decision history");
    transport.getJson.mockResolvedValueOnce(history([]));
    await act(async () => {
      button(/^Retry$/)?.click();
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(transport.getJson).toHaveBeenCalledTimes(2);
    expect(text()).toContain('No decisions recorded for this borrower yet.');
  });

  it('keeps the list in flow and puts rows after the eighth behind Show all', async () => {
    transport.getJson.mockResolvedValue(history(Array.from({ length: 11 }, () => event())));
    await mount();

    const list = document.querySelector('ol[aria-label="Decision history"]');
    expect(list?.className).toBe('audit-panel audit-panel--flow');
    expect(rows()).toHaveLength(8);
    const toggle = button(/^Show all 11 decisions$/);
    expect(toggle?.getAttribute('aria-expanded')).toBe('false');
    expect(toggle?.getAttribute('aria-controls')).toBe(list?.id);
    act(() => toggle?.click());
    expect(rows()).toHaveLength(11);
    expect(button(/^Show the latest 8$/)?.getAttribute('aria-expanded')).toBe('true');
    expect(text()).not.toContain('Showing the latest 50');
  });

  it('says when older decisions exist', async () => {
    transport.getJson.mockResolvedValue(history(Array.from({ length: 50 }, () => event()), true));
    await mount();
    expect(text()).toContain('Showing the latest 50');
  });

  it('offers a receipt only where the server allows one, and mounts it on the click', async () => {
    const own = event({ event_type: 'APPROVE', outcome: 'approved', receipt_available: true, is_own: true, disposition_outcome: null });
    const revoke = event({ event_type: 'OUTREACH_REVOKE', outcome: 'revoked', receipt_available: true, disposition_outcome: null });
    const colleague = event({ event_type: 'OUTREACH_REJECT', outcome: 'rejected', receipt_available: false, disposition_outcome: null });
    transport.getJson.mockResolvedValue(history([own, revoke, colleague]));
    await mount();

    const [ownRow, revokeRow, colleagueRow] = rows();
    expect(colleagueRow.querySelector('button')).toBeNull();
    const receiptButton = ownRow.querySelector('button');
    expect(receiptButton?.textContent).toBe('Receipt');
    expect(receiptButton?.getAttribute('aria-expanded')).toBe('false');
    expect(document.querySelector('[data-testid="receipt-probe"]')).toBeNull();

    await act(async () => {
      receiptButton?.click();
    });
    await act(async () => {
      await vi.dynamicImportSettled();
    });
    const probe = ownRow.querySelector('[data-testid="receipt-probe"]');
    expect(probe?.textContent).toBe(own.audit_event_id);
    expect(probe?.getAttribute('data-decision')).toBe('approved');
    expect(probe?.getAttribute('data-compact')).toBe('true');
    expect(probe?.getAttribute('data-level')).toBe('3');
    expect(receiptButton?.getAttribute('aria-expanded')).toBe('true');
    const region = document.getElementById(receiptButton?.getAttribute('aria-controls') ?? '');
    expect(region?.className).toBe('audit__receipt');
    expect(revokeRow.querySelector('[data-testid="receipt-probe"]')).toBeNull();
    // The receipt is the only read a click adds; the history is not re-read.
    expect(transport.getJson).toHaveBeenCalledTimes(1);
  });

  it('collapses on the Offer with the count in its toggle, and 50+ when truncated', async () => {
    transport.getJson.mockResolvedValue(history([event(), event()]));
    await mount('disclosure');

    const toggle = document.querySelector<HTMLButtonElement>('[data-testid="offer-prior-decisions"] > button');
    expect(toggle?.textContent).toBe('Prior decisions (2)');
    expect(toggle?.getAttribute('aria-expanded')).toBe('false');
    expect(rows()).toHaveLength(0);
    act(() => toggle?.click());
    expect(toggle?.getAttribute('aria-expanded')).toBe('true');
    expect(rows()).toHaveLength(2);
    expect(document.getElementById(toggle?.getAttribute('aria-controls') ?? '')?.hidden).toBe(false);

    act(() => root.unmount());
    queryClient.clear();
    root = createRoot(document.getElementById('root') as HTMLElement);
    transport.getJson.mockResolvedValue(history(Array.from({ length: 50 }, () => event()), true));
    await mount('disclosure');
    expect(document.querySelector('[data-testid="offer-prior-decisions"] > button')?.textContent).toBe('Prior decisions (50+)');
  });
});
