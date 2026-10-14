/**
 * @vitest-environment happy-dom
 *
 * RefusalReportsPanel on /audit-ledger (D-audit-reads-d), rendered with a
 * real QueryClient; only the refusal-reports client is mocked.
 *
 *   - Nothing loads with the route: the explicit "Show refusal reports"
 *     toggle loads the panel and makes exactly one (recorded) list read.
 *   - The list renders report metadata only: family chips, the reporter,
 *     mono ids, the audit event linked through auditEventHref, and the
 *     window's family counts; the family filter and Load more each make one
 *     more list read.
 *   - "Show question" makes exactly ONE audited question read per click and
 *     none on hover or focus; the text renders in a `.source-card` with its
 *     Redacted chip and expiry, copies, and hides. A 404 and a 503 show their
 *     fixed copy.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../lib/api';
import type { RefusalReportListResponse } from '../../lib/apiClients/refusalReports';
import { auditEventHref } from '../../lib/auditLinks';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({ list: vi.fn(), question: vi.fn() }));
vi.mock('../../lib/apiClients/refusalReports', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/apiClients/refusalReports')>()),
  refusalReportsApi: api,
}));
vi.mock('./AdminAuditExplorer', () => ({ AdminAuditExplorer: () => <div data-testid="explorer-stub" /> }));

import AuditLedger from '../../routes/audit-ledger';
import { RefusalReportsPanel } from './RefusalReportsPanel';
import { QUESTION_EXPIRED, QUESTION_NOT_RECORDED } from './RefusalReportRow';

const WITH_TEXT = '0d2c0f4e-6b6a-4b8e-9a51-3c0f7b1d2e4f';
const HASH_ONLY = '5a1f3e2d-1c0b-4a9e-8d7c-6b5a4f3e2d1c';
const AUDIT_ID = '7f6e5d4c-3b2a-4190-8f7e-6d5c4b3a2f1e';
const PAGE: RefusalReportListResponse = {
  items: [
    {
      report_id: WITH_TEXT,
      reported_at: '2026-10-05T14:30:00Z',
      refusal_reason: 'unreviewed_criterion',
      reporter: 'lo.alpha@summit.example',
      conversation_id: '01f13d4968af1b249dc388fd5b18b195',
      message_id: null,
      has_text: true,
      text_expires_at: '2027-01-03T14:30:00Z',
      audit_event_id: AUDIT_ID,
    },
    {
      report_id: HASH_ONLY,
      reported_at: '2026-10-04T09:00:00Z',
      refusal_reason: 'protected_class',
      reporter: 'lo.bravo@summit.example',
      conversation_id: null,
      message_id: null,
      has_text: false,
      text_expires_at: null,
      audit_event_id: null,
    },
  ],
  family_counts: [
    { refusal_reason: 'protected_class', count: 1 },
    { refusal_reason: 'unreviewed_criterion', count: 1 },
  ],
  next_cursor: 'cursor-2',
};
const QUESTION = 'Which zyrplax borrowers are eligible for a HELOC, call 555-123-4567?';

async function settle() {
  for (let i = 0; i < 4; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

describe('RefusalReportsPanel', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  beforeEach(() => {
    api.list.mockReset();
    api.question.mockReset();
    api.list.mockResolvedValue(PAGE);
    client = new QueryClient();
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    client.clear();
  });

  function render(node: ReactNode) {
    act(() => root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>{node}</MemoryRouter>
      </QueryClientProvider>,
    ));
  }
  const showButton = () => container.querySelector<HTMLButtonElement>('[data-testid="refusal-report-show-question"]');

  it('loads nothing with the route: the toggle loads the panel and makes one list read', async () => {
    render(<AuditLedger />);
    await settle();
    expect(api.list).not.toHaveBeenCalled();
    const toggle = container.querySelector<HTMLButtonElement>('[data-testid="refusal-reports-toggle"]');
    expect(toggle?.getAttribute('aria-expanded')).toBe('false');
    expect(toggle?.className).toBe('btn btn--ghost btn--sm');

    act(() => toggle?.click());
    await settle();
    expect(toggle?.getAttribute('aria-expanded')).toBe('true');
    expect(toggle?.getAttribute('aria-controls')).toBe('refusal-reports');
    expect(container.querySelector('#refusal-reports [data-testid="refusal-reports-panel"]')).not.toBeNull();
    expect(api.list).toHaveBeenCalledTimes(1);
    expect(api.list.mock.calls[0][0]).toEqual({ family: null, cursor: null });
  });

  it('renders report metadata only, with the audit event linked and the family counts', async () => {
    render(<RefusalReportsPanel />);
    await settle();
    const rows = container.querySelectorAll('[data-testid="refusal-report-row"]');
    expect(rows).toHaveLength(2);
    expect(rows[0].querySelector('.chip')?.textContent).toBe('Outside the reviewed vocabulary');
    expect(rows[0].textContent).toContain('lo.alpha@summit.example');
    expect(rows[0].querySelector('td.mono.fs-11')?.textContent).toContain('01f13d4968af1b249dc388fd5b18b195');
    const link = rows[0].querySelector<HTMLAnchorElement>('a.mono');
    expect(link?.getAttribute('href')).toBe(auditEventHref(AUDIT_ID));
    expect(rows[1].textContent).toContain('Hash only');
    expect(rows[1].querySelector('[data-testid="refusal-report-show-question"]')).toBeNull();
    const counts = [...container.querySelectorAll('[aria-label="Reports per refusal family"] .chip')].map((chip) => chip.textContent);
    expect(counts).toEqual(['Fair-lending scope · 1', 'Outside the reviewed vocabulary · 1']);
    expect(container.textContent).not.toContain('zyrplax');
    expect(api.question).not.toHaveBeenCalled();
  });

  it('filters by family and loads more with the cursor, one list read each', async () => {
    render(<RefusalReportsPanel />);
    await settle();
    const select = container.querySelector<HTMLSelectElement>('[data-testid="refusal-reports-family"]')!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')?.set?.call(select, 'protected_class');
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
    await settle();
    expect(api.list.mock.calls.map((call) => call[0])).toEqual([
      { family: null, cursor: null },
      { family: 'protected_class', cursor: null },
    ]);
    api.list.mockResolvedValueOnce({ ...PAGE, items: [{ ...PAGE.items[1], report_id: 'aa' }], next_cursor: null });
    const loadMore = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Load more');
    act(() => loadMore?.click());
    await settle();
    expect(api.list.mock.calls[2][0]).toEqual({ family: 'protected_class', cursor: 'cursor-2' });
    expect(container.querySelectorAll('[data-testid="refusal-report-row"]')).toHaveLength(3);
    expect([...container.querySelectorAll('button')].some((button) => button.textContent === 'Load more')).toBe(false);
  });

  it('reads the question once per click, never on hover or focus, and shows, copies and hides it', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
    api.question.mockResolvedValue({
      question_text: QUESTION.replace('555-123-4567', '[PHONE-REDACTED]'),
      redacted: true,
      captured_at: '2026-10-05T14:30:00Z',
      expires_at: '2027-01-03T14:30:00Z',
    });
    render(<RefusalReportsPanel />);
    await settle();
    const button = showButton()!;
    act(() => {
      button.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
      button.dispatchEvent(new MouseEvent('mouseenter', { bubbles: true }));
      button.dispatchEvent(new PointerEvent('pointerenter', { bubbles: true }));
      button.focus();
    });
    await settle();
    expect(api.question).not.toHaveBeenCalled();

    act(() => button.click());
    await settle();
    expect(api.question).toHaveBeenCalledTimes(1);
    expect(api.question.mock.calls[0][0]).toBe(WITH_TEXT);
    const card = container.querySelector<HTMLElement>('.source-card[data-testid="refusal-report-question"]');
    expect(card?.textContent).toContain('[PHONE-REDACTED]');
    expect(card?.querySelector('.chip')?.textContent).toBe('Redacted');
    expect(card?.textContent).toContain('Kept until');
    expect(button.textContent).toBe('Hide question');
    expect(button.getAttribute('aria-expanded')).toBe('true');
    expect(client.getQueryCache().getAll().some((query) => JSON.stringify(query.state.data ?? '').includes('PHONE-REDACTED'))).toBe(false);

    const copy = [...(card?.querySelectorAll('button') ?? [])].find((b) => b.textContent === 'Copy');
    act(() => copy?.click());
    await settle();
    expect(writeText).toHaveBeenCalledWith(QUESTION.replace('555-123-4567', '[PHONE-REDACTED]'));
    expect(container.querySelector('[data-testid="refusal-report-question"]')?.textContent).toContain('Copied');

    act(() => button.click());
    expect(container.querySelector('[data-testid="refusal-report-question"]')).toBeNull();
    expect(button.textContent).toBe('Show question');
    expect(api.question).toHaveBeenCalledTimes(1);
  });

  it.each([
    [404, QUESTION_EXPIRED],
    [503, QUESTION_NOT_RECORDED],
  ])('a %s shows its fixed copy and no text', async (status, copy) => {
    api.question.mockRejectedValueOnce(new ApiError(`fixture ${status}`, { path: '/api/audit/refusal-reports/x/question', status }));
    render(<RefusalReportsPanel />);
    await settle();
    act(() => showButton()?.click());
    await settle();
    expect(container.querySelector('[role="alert"]')?.textContent).toBe(copy);
    expect(container.querySelector('[data-testid="refusal-report-question"]')).toBeNull();
    expect(showButton()?.textContent).toBe('Show question');
  });
});
