/**
 * @vitest-environment happy-dom
 *
 * The audit explorer's URL-synced filters, labels, deep links and page CSV
 * (audit flow-04 phase 1 / tables-10), pinned on the rendered explorer with
 * the real query hook: what reaches `api.auditEventPage`, what the URL holds,
 * and what the table shows.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useEffect } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AuditEventPage, AuditEventRow } from '../../lib/apiTypes';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type PageFilters = Record<string, string | null | undefined>;

const mocks = vi.hoisted(() => ({
  pageCalls: [] as PageFilters[],
  rows: [] as AuditEventRow[],
}));

vi.mock('../../lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../lib/api')>();
  return {
    ...actual,
    api: {
      auditEventPage: (_limit: number, _signal: AbortSignal | undefined, filters: PageFilters) => {
        mocks.pageCalls.push(filters);
        const rows = filters.event_id
          ? mocks.rows.filter((row) => row.event_id === filters.event_id)
          : mocks.rows;
        return Promise.resolve<AuditEventPage>({ items: rows, next_cursor: null });
      },
      auditRollups: () => Promise.resolve([]),
    },
  };
});

import { AdminAuditExplorer } from './AdminAuditExplorer';
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
    mocks.rows = [row(1), row(2, { event_type: 'VIEW_LEADS', action: 'leads.view', entity_type: 'borrower', entity_id: 'B-AAAAAAAAAAAA2' })];
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  it('reads every filter from the URL into the request and the controls', async () => {
    await render(
      '/admin-config?audit_actor=approver%40summit-mortgage.example&audit_since=2026-07-01'
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
    await render('/admin-config#audit');
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
    await render('/admin-config#audit');
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
    await render('/admin-config');
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
    for (const label of ['ACTOR', 'CORRELATION ID', 'ENTITY ID', 'ACTION']) {
      expect(input(label).getAttribute('aria-invalid')).toBe('false');
      expect(input(label).hasAttribute('aria-describedby')).toBe(false);
    }
  });

  it('marks only the actor input when the actor is refused, until it is edited', async () => {
    await render('/admin-config');
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
    await render('/admin-config?audit_actor=approver%40summit-mortgage.example&audit_event_type=LEAD_EXPORT');
    act(() => button(/^Remove actor filter$/).click());
    await settle();

    expect(Object.fromEntries(new URLSearchParams(seen.location.search))).toEqual({ audit_event_type: 'LEAD_EXPORT' });
    expect(lastCall()).toMatchObject({ actor: null, event_type: 'LEAD_EXPORT' });
  });

  it('moves focus to the next chip, then to Apply, as filter chips are removed', async () => {
    await render('/admin-config?audit_actor=approver%40summit-mortgage.example&audit_event_type=LEAD_EXPORT');
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
    await render('/admin-config');
    const rows = [...container.querySelectorAll('table[aria-label="Audit events"] tbody tr')];
    expect(rows.map((tr) => tr.querySelector('td.is-primary > div')?.textContent)).toEqual([
      'Lead list exported',
      'Lead queue reviewed',
    ]);
    const chip = rows[0].querySelector('.chip.mono');
    expect(chip?.textContent).toBe('LEAD_EXPORT');
  });

  it('opens a deep link on exactly that event, already expanded', async () => {
    await render('/admin-config?audit_event_id=evt-fixture-2#audit');

    expect(lastCall()).toMatchObject({ event_id: 'evt-fixture-2' });
    const toggle = button(/^Collapse audit event evt-fixture-2$/);
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(container.querySelectorAll('table[aria-label="Audit events"] tbody tr[data-audit-event-id]')).toHaveLength(1);
    expect(container.querySelector('[aria-label="Applied audit filters"]')?.textContent)
      .toContain('audit event = evt-fixture-2');
  });

  it('links every event id and correlation id back into the explorer', async () => {
    await render('/admin-config#audit');
    act(() => button(/^Expand audit event evt-fixture-1$/).click());
    const eventLink = container.querySelector<HTMLAnchorElement>('a[aria-label="Open audit event evt-fixture-1 on its own"]');
    const correlationLink = container.querySelector<HTMLAnchorElement>(
      'a[aria-label="Show every audit event with correlation id corr-1"]',
    );
    expect(eventLink?.getAttribute('href')).toBe('/admin-config?audit_event_id=evt-fixture-1#audit');
    expect(correlationLink?.getAttribute('href')).toBe('/admin-config?audit_correlation_id=corr-1#audit');

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
    await render('/admin-config#audit');
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
    await render('/admin-config');

    act(() => button(/^Download page 1 of the audit explorer as CSV$/).click());

    expect(blobs).toHaveLength(1);
    expect(clicks[0]).toMatch(/^mip-audit-page-1-\d{4}-\d{2}-\d{2}\.csv$/);
    const lines = (await blobs[0].text()).trimEnd().split('\n');
    expect(lines[0]).toBe(AUDIT_PAGE_CSV_HEADER.join(','));
    expect(lines.slice(1).map((line) => line.split(',')[0])).toEqual(['evt-fixture-1', 'evt-fixture-2']);
  });
});
