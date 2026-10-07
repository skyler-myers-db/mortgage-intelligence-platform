/**
 * @vitest-environment happy-dom
 *
 * The audit explorer's URL-synced filters, labels, deep links and page CSV
 * (audit flow-04 phase 1 / tables-10), pinned on the rendered explorer with
 * the real query hook: what reaches the explorer's page read, what the URL
 * holds, and what the table shows. W5d adds the recorded-read discipline of
 * the facets (an explicit menu open; never on mount), the per-viewer
 * exclusion and the audited, admin-only page CSV.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AuditEventPage, AuditEventRow } from '../../lib/apiTypes';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type PageFilters = Record<string, string | null | undefined>;

const mocks = vi.hoisted(() => ({
  pageCalls: [] as PageFilters[],
  rows: [] as AuditEventRow[],
  pageError: null as Error | null,
  rollupsError: null as Error | null,
  facetCalls: [] as Array<{ since: string | null; until: string | null }>,
  receiptCalls: [] as Array<{ row_count: number; filters: Record<string, string> }>,
  order: [] as string[],
}));

// The explorer reads the session's role and lender name (Export PDF's
// provenance; the admin-only page CSV).
const appContext = vi.hoisted(() => ({ canAccessAdmin: true, lender: 'Summit Mortgage' }));
vi.mock('../AppContext', () => ({ useApp: () => appContext }));

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>();
  return {
    ...actual,
    api: {
      auditRollups: () => (mocks.rollupsError ? Promise.reject(mocks.rollupsError) : Promise.resolve([])),
    },
  };
});

vi.mock('../../lib/apiClients/auditExplorer', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/apiClients/auditExplorer')>();
  return {
    ...actual,
    auditEventPage: (_limit: number, _signal: AbortSignal | undefined, filters: PageFilters) => {
      mocks.pageCalls.push(filters);
      if (mocks.pageError) return Promise.reject(mocks.pageError);
      const rows = filters.event_id
        ? mocks.rows.filter((row) => row.event_id === filters.event_id)
        : mocks.rows;
      return Promise.resolve<AuditEventPage>({ items: rows, next_cursor: null });
    },
    auditFacets: (window: { since: string | null; until: string | null }) => {
      mocks.facetCalls.push(window);
      return Promise.resolve({
        event_types: [{ value: 'LEAD_EXPORT', count: 7 }],
        actions: [
          { value: 'leads.view', count: 3 },
          { value: 'lead_queue.export', count: 9 },
          { value: 'not an action!', count: 50 },
        ],
        actors: [],
        truncated: { event_types: false, actions: false, actors: false },
        since: window.since ?? '2026-07-01T00:00:00Z',
        until: window.until,
      });
    },
    createAuditExportReceipt: (body: { row_count: number; filters: Record<string, string> }) => {
      mocks.receiptCalls.push(body);
      mocks.order.push('receipt');
      return Promise.resolve({ audit_event_id: '6f2a9c1e-3b4d-4e5f-8a6b-7c8d9e0f1a2b' });
    },
  };
});

import { AdminAuditExplorer } from './AdminAuditExplorer';
import { ApiError } from '../../lib/api';
import { preloadDescribedError } from '../ui/DescribedError';
import { AUDIT_PAGE_CSV_HEADER } from './AdminAuditExplorer.csv';
import { auditDayBoundary } from './AdminAuditExplorer.params';

function row(index: number, overrides: Partial<AuditEventRow> = {}): AuditEventRow {
  return {
    event_id: `evt-fixture-${index}`,
    actor: 'approver@summit-mortgage.example',
    action: 'lead_queue.export',
    entity_type: 'lead_queue',
    entity_id: `export-${index}`,
    payload_json: { exported_row_count: 2 },
    evidence_ids: [],
    created_at: `2026-07-14T11:1${index}:00Z`,
    event_type: 'LEAD_EXPORT',
    request_id: `req-${index}`,
    correlation_id: `corr-${index}`,
    ...overrides,
  };
}

let root: Root;
let container: HTMLDivElement;
const seen = { location: { pathname: '', search: '', hash: '' } };

function LocationProbe() {
  const current = useLocation();
  useEffect(() => {
    seen.location = { pathname: current.pathname, search: current.search, hash: current.hash };
  }, [current]);
  return null;
}

async function settle(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

async function render(entry: string): Promise<void> {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[entry]}>
          <AdminAuditExplorer />
          <LocationProbe />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await settle();
  await settle();
}

function input(label: string): HTMLInputElement {
  const found = [...container.querySelectorAll('label')]
    .find((candidate) => candidate.querySelector('.field__label')?.textContent === label)
    ?.querySelector('input');
  if (!(found instanceof HTMLInputElement)) throw new Error(`input not found: ${label}`);
  return found;
}

function type(target: HTMLInputElement, value: string): void {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
  act(() => {
    setter?.call(target, value);
    target.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

function button(name: RegExp): HTMLButtonElement {
  const found = [...container.querySelectorAll('button')].find((candidate) => (
    name.test(candidate.getAttribute('aria-label') ?? candidate.textContent ?? '')
  ));
  if (!(found instanceof HTMLButtonElement)) throw new Error(`button not found: ${name}`);
  return found;
}

const lastCall = () => mocks.pageCalls[mocks.pageCalls.length - 1];

describe('AdminAuditExplorer', () => {
  beforeEach(() => {
    mocks.pageCalls.length = 0;
    mocks.facetCalls.length = 0;
    mocks.receiptCalls.length = 0;
    mocks.order.length = 0;
    appContext.canAccessAdmin = true;
    mocks.rows = [row(1), row(2, { event_type: 'VIEW_LEADS', action: 'leads.view', entity_type: 'borrower', entity_id: 'B-AAAAAAAAAAAA2' })];
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
    mocks.pageError = null;
    mocks.rollupsError = null;
  });

  // The error vocabulary is its own chunk (loaded with the first failure).
  beforeAll(async () => {
    await preloadDescribedError();
  });

  // css-hygiene: the events table has no row-click action (rows expand from
  // their own button), so it opts out of the row pointer and hover.
  it('marks the events table static', async () => {
    await render('/audit-ledger');
    expect(container.querySelector('table[aria-label="Audit events"]')?.className).toBe('tbl tbl--static');
  });

  // Audit states-04: failures read in the shared vocabulary, never the
  // transport message.
  it('says why the explorer and the rollups failed without the transport message', async () => {
    mocks.pageError = new ApiError('SENTINEL 500 Internal Server Error', { path: '/api/v1/audit/events', status: 500 });
    mocks.rollupsError = new ApiError('SENTINEL', { path: '/api/v1/audit/rollups', status: 403 });
    await render('/audit-ledger');
    expect(container.textContent).toContain('Audit explorer unavailable: The server hit an unexpected error.');
    expect(container.textContent).toContain('Audit rollups unavailable: Ask an administrator for access.');
    expect(container.textContent).not.toContain('SENTINEL');
  });

  it('reads every filter from the URL into the request and the controls', async () => {
    await render(
      '/audit-ledger?audit_actor=approver%40summit-mortgage.example&audit_since=2026-07-01'
      + '&audit_until=2026-07-14&audit_event_type=LEAD_EXPORT&audit_correlation_id=corr-1#audit',
    );

    expect(lastCall()).toMatchObject({
      actor: 'approver@summit-mortgage.example',
      event_type: 'LEAD_EXPORT',
      correlation_id: 'corr-1',
      since: auditDayBoundary('2026-07-01', false),
      until: auditDayBoundary('2026-07-14', true),
      event_id: null,
    });
    expect(input('ACTOR').value).toBe('approver@summit-mortgage.example');
    expect(input('SINCE').value).toBe('2026-07-01');
    expect(input('UNTIL').value).toBe('2026-07-14');
    expect(input('CORRELATION ID').value).toBe('corr-1');
    expect(button(/^Event type: /).getAttribute('aria-label')).toBe('Event type: Lead list exported');
    const chips = container.querySelector('[aria-label="Applied audit filters"]')?.textContent ?? '';
    expect(chips).toContain('actor = approver@summit-mortgage.example');
    expect(chips).toContain('event = Lead list exported · LEAD_EXPORT');
    expect(chips).toContain('since = 2026-07-01');
  });

  it('writes applied filters to the URL, keeps the hash, and re-queries with them', async () => {
    await render('/audit-ledger#audit');
    type(input('ACTOR'), 'approver@summit-mortgage.example');
    type(input('SINCE'), '2026-07-01');
    type(input('UNTIL'), '2026-07-14');
    type(input('CORRELATION ID'), 'corr-2');
    act(() => button(/^Event type: /).click());
    const option = [...container.querySelectorAll('[role="option"]')]
      .find((candidate) => candidate.textContent === 'Lead queue reviewed');
    if (!(option instanceof HTMLElement)) throw new Error('event type option not rendered');
    act(() => option.click());
    act(() => button(/^Apply filters$/).click());
    await settle();

    const params = new URLSearchParams(seen.location.search);
    expect(Object.fromEntries(params)).toEqual({
      audit_actor: 'approver@summit-mortgage.example',
      audit_since: '2026-07-01',
      audit_until: '2026-07-14',
      audit_event_type: 'VIEW_LEADS',
      audit_correlation_id: 'corr-2',
    });
    expect(seen.location.hash).toBe('#audit');
    expect(lastCall()).toMatchObject({ actor: 'approver@summit-mortgage.example', event_type: 'VIEW_LEADS', correlation_id: 'corr-2' });
  });

  it('keeps the submitting control focused and in the document after Apply', async () => {
    await render('/audit-ledger#audit');
    const actor = input('ACTOR');
    type(actor, 'approver@summit-mortgage.example');
    const apply = button(/^Apply filters$/);
    apply.focus();
    act(() => apply.click());
    await settle();

    expect(seen.location.search).toBe('?audit_actor=approver%40summit-mortgage.example');
    expect(apply.isConnected).toBe(true);
    expect(document.activeElement).toBe(apply);
    expect(input('ACTOR')).toBe(actor);
  });

  it('refuses an inverted day window without touching the URL, on the date inputs', async () => {
    await render('/audit-ledger');
    type(input('SINCE'), '2026-07-14');
    type(input('UNTIL'), '2026-07-01');
    act(() => button(/^Apply filters$/).click());

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toMatch(/on or before/);
    expect(seen.location.search).toBe('');
    for (const label of ['SINCE', 'UNTIL']) {
      expect(input(label).getAttribute('aria-invalid')).toBe('true');
      expect(input(label).getAttribute('aria-describedby')).toBe(alert?.id);
    }
    for (const label of ['ACTOR', 'CORRELATION ID', 'ENTITY ID']) {
      expect(input(label).getAttribute('aria-invalid')).toBe('false');
      expect(input(label).hasAttribute('aria-describedby')).toBe(false);
    }
  });

  it('marks only the actor input when the actor is refused, until it is edited', async () => {
    await render('/audit-ledger');
    type(input('ACTOR'), 'two people');
    type(input('CORRELATION ID'), 'corr-1');
    act(() => button(/^Apply filters$/).click());

    const alert = container.querySelector('[role="alert"]');
    expect(alert?.textContent).toMatch(/^Actor must be a single principal/);
    expect(input('ACTOR').getAttribute('aria-invalid')).toBe('true');
    expect(input('ACTOR').getAttribute('aria-describedby')).toBe(alert?.id);
    expect(input('CORRELATION ID').getAttribute('aria-invalid')).toBe('false');
    expect(seen.location.search).toBe('');

    // Editing another field leaves the actor refusal standing; fixing the actor retires it.
    type(input('CORRELATION ID'), 'corr-2');
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    type(input('ACTOR'), 'approver@summit-mortgage.example');
    expect(container.querySelector('[role="alert"]')).toBeNull();
    expect(input('ACTOR').getAttribute('aria-invalid')).toBe('false');
  });

  it('removes one filter from the URL when its chip is removed', async () => {
    await render('/audit-ledger?audit_actor=approver%40summit-mortgage.example&audit_event_type=LEAD_EXPORT');
    act(() => button(/^Remove actor filter$/).click());
    await settle();

    expect(Object.fromEntries(new URLSearchParams(seen.location.search))).toEqual({ audit_event_type: 'LEAD_EXPORT' });
    expect(lastCall()).toMatchObject({ actor: null, event_type: 'LEAD_EXPORT' });
  });

  it('moves focus to the next chip, then to Apply, as filter chips are removed', async () => {
    await render('/audit-ledger?audit_actor=approver%40summit-mortgage.example&audit_event_type=LEAD_EXPORT');
    const removeEvent = button(/^Remove event filter$/);
    removeEvent.focus();
    act(() => removeEvent.click());
    await settle();

    expect(seen.location.search).toBe('?audit_actor=approver%40summit-mortgage.example');
    expect(document.activeElement?.getAttribute('aria-label')).toBe('Remove actor filter');

    act(() => (document.activeElement as HTMLButtonElement).click());
    await settle();
    expect(seen.location.search).toBe('');
    expect(document.activeElement).toBe(button(/^Apply filters$/));
  });

  it('shows human labels with the raw code kept in a mono chip', async () => {
    await render('/audit-ledger');
    const rows = [...container.querySelectorAll('table[aria-label="Audit events"] tbody tr')];
    expect(rows.map((tr) => tr.querySelector('td.is-primary > div')?.textContent)).toEqual([
      'Lead list exported',
      'Lead queue reviewed',
    ]);
    const chip = rows[0].querySelector('.chip.mono');
    expect(chip?.textContent).toBe('LEAD_EXPORT');
  });

  it('opens a deep link on exactly that event, already expanded', async () => {
    await render('/audit-ledger?audit_event_id=evt-fixture-2#audit');

    expect(lastCall()).toMatchObject({ event_id: 'evt-fixture-2' });
    const toggle = button(/^Collapse audit event evt-fixture-2$/);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(container.querySelectorAll('table[aria-label="Audit events"] tbody tr[data-audit-event-id]')).toHaveLength(1);
    expect(container.querySelector('[aria-label="Applied audit filters"]')?.textContent)
      .toContain('audit event = evt-fixture-2');
  });

  it('links every event id and correlation id back into the explorer', async () => {
    await render('/audit-ledger#audit');
    act(() => button(/^Expand audit event evt-fixture-1$/).click());
    const eventLink = container.querySelector<HTMLAnchorElement>('a[aria-label="Open audit event evt-fixture-1 on its own"]');
    const correlationLink = container.querySelector<HTMLAnchorElement>(
      'a[aria-label="Show every audit event with correlation id corr-1"]',
    );
    expect(eventLink?.getAttribute('href')).toBe('/audit-ledger?audit_event_id=evt-fixture-1#audit');
    expect(correlationLink?.getAttribute('href')).toBe('/audit-ledger?audit_correlation_id=corr-1#audit');

    act(() => eventLink?.click());
    // The narrowed page arrives through the query's own notify timer after the
    // navigation, so wait on the rendered, refetched row (one row, not the
    // two-row page it replaced) rather than on one timer tick.
    await vi.waitFor(async () => {
      await settle();
      expect(container.querySelectorAll('table[aria-label="Audit events"] tbody tr[data-audit-event-id]')).toHaveLength(1);
      expect(button(/^Collapse audit event evt-fixture-1$/).getAttribute('aria-expanded')).toBe('true');
    });
    expect(seen.location.search).toBe('?audit_event_id=evt-fixture-1');
    expect(lastCall()).toMatchObject({ event_id: 'evt-fixture-1' });
  });

  it('links a masked borrower entity to Borrower 360 and leaves other entities as text', async () => {
    await render('/audit-ledger#audit');
    const rows = [...container.querySelectorAll('table[aria-label="Audit events"] tbody tr[data-audit-event-id]')];
    expect(rows[0].querySelectorAll('td')[2].querySelector('a')).toBeNull();
    const borrowerLink = rows[1].querySelector<HTMLAnchorElement>('a[aria-label="Open Borrower 360 for B-AAAAAAAAAAAA2"]');
    expect(borrowerLink?.getAttribute('href')).toBe('/borrower-360/B-AAAAAAAAAAAA2');
  });

  it('downloads the rows on the current page as CSV with the header row', async () => {
    const blobs: Blob[] = [];
    vi.spyOn(URL, 'createObjectURL').mockImplementation((blob: Blob | MediaSource) => {
      blobs.push(blob as Blob);
      return 'blob:audit-page';
    });
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const clicks: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicks.push(this.download);
    });
    await render('/audit-ledger');

    act(() => button(/^Download page 1 of the audit explorer as CSV$/).click());
    await vi.waitFor(() => expect(blobs).toHaveLength(1));

    expect(mocks.receiptCalls).toHaveLength(1);
    expect(clicks[0]).toMatch(/^mip-audit-page-1-\d{4}-\d{2}-\d{2}\.csv$/);
    const lines = (await blobs[0].text()).trimEnd().split('\n');
    expect(lines[0]).toBe(AUDIT_PAGE_CSV_HEADER.join(','));
    expect(lines.slice(1).map((line) => line.split(',')[0])).toEqual(['evt-fixture-1', 'evt-fixture-2']);
  });

  // --- W5d (tables-10): recorded reads only on a deliberate act -------------

  it('reads no facets on mount, hover or focus', async () => {
    await render('/audit-ledger?audit_since=2026-07-01');
    // Hovering or focusing a counted menu reads nothing either.
    const action = button(/^Action: /);
    act(() => {
      action.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
      action.focus();
    });
    await settle();
    expect(mocks.facetCalls).toEqual([]);
    expect(mocks.pageCalls).toHaveLength(1);
  });

  it('reads the facets on an explicit Action menu open, for the applied window, and feeds its options', async () => {
    await render('/audit-ledger?audit_since=2026-07-01&audit_until=2026-07-14&audit_action=outreach.approve');
    act(() => button(/^Action: /).click());
    // The read resolves on its own schedule; wait for the counts it feeds.
    await vi.waitFor(() => expect(container.querySelectorAll('.filter-menu__count')).toHaveLength(3));

    expect(mocks.facetCalls).toEqual([
      { since: auditDayBoundary('2026-07-01', false), until: auditDayBoundary('2026-07-14', true) },
    ]);
    const labels = [...container.querySelectorAll('[role="option"]')].map((option) => option.firstChild?.textContent);
    // Most frequent first; a value the URL would refuse is dropped; the applied action is kept.
    expect(labels).toEqual(['All actions', 'lead_queue.export', 'leads.view', 'outreach.approve']);
    const counts = [...container.querySelectorAll('.filter-menu__count')].map((count) => count.textContent);
    expect(counts).toEqual(['9', '3', '0']);

    // Closing and reopening within the stale window reads nothing more.
    act(() => button(/^Action: /).click());
    act(() => button(/^Action: /).click());
    await settle();
    expect(mocks.facetCalls).toHaveLength(1);
  });

  it('sends Exclude my ledger reads on the page read, keeps it out of the URL and restarts on page 1', async () => {
    await render('/audit-ledger?audit_event_type=LEAD_EXPORT#audit');
    const toggle = [...container.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')]
      .find((box) => box.parentElement?.textContent === 'Exclude my ledger reads');
    expect(toggle?.checked).toBe(false);
    expect(lastCall()).toMatchObject({ exclude_own_ledger_reads: false });

    act(() => toggle!.click());
    await settle();

    expect(lastCall()).toMatchObject({ event_type: 'LEAD_EXPORT', exclude_own_ledger_reads: true, cursor: null });
    expect(seen.location.search).toBe('?audit_event_type=LEAD_EXPORT');
  });

  it('waits for the AUDIT_EXPORT receipt before the CSV download', async () => {
    vi.spyOn(URL, 'createObjectURL').mockImplementation(() => {
      mocks.order.push('download');
      return 'blob:audit-page';
    });
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    await render('/audit-ledger?audit_event_type=LEAD_EXPORT');

    act(() => button(/^Download page 1 of the audit explorer as CSV$/).click());
    await vi.waitFor(() => expect(mocks.order).toEqual(['receipt', 'download']));
    expect(mocks.receiptCalls[0]).toMatchObject({ row_count: 2, filters: { event_type: 'LEAD_EXPORT' } });
  });

  it('shows a read-only auditor no page CSV, and Export PDF still', async () => {
    appContext.canAccessAdmin = false;
    await render('/audit-ledger');
    expect([...container.querySelectorAll('button')].some((candidate) => /CSV/.test(candidate.getAttribute('aria-label') ?? candidate.textContent ?? ''))).toBe(false);
    expect(container.querySelector('[data-testid="audit-export-pdf"]')).not.toBeNull();
  });
});
