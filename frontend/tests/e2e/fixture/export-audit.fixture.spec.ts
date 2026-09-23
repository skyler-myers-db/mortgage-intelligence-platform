/**
 * Export-audit lane (audit tables-08, flow-04 phase 1, tables-10), proven on
 * the production build as an admin session:
 *
 *  - the Lead Queue CSV export asks for exactly one LEAD_EXPORT receipt and
 *    the browser download starts only after that receipt resolves; a refused
 *    receipt (422) downloads nothing and shows why;
 *  - the audit explorer's filters round-trip through the URL, a deep link
 *    opens its event, rows read as human labels with the raw code in a mono
 *    chip, and the current page downloads as CSV with its header row.
 *
 * The download-ordering assertion reads an in-page probe (fetch + anchor
 * click, installed before the app boots), so it does not race Playwright's
 * own download event against the mocked response.
 */
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import type { LeadExportReceipt, LeadExportReceiptRequest } from '../../../src/lib/apiClients/leadExport';
import {
  EXPLORER_ROWS,
  EXPORT_ACTOR,
  EXPORT_RECEIPT_ID,
  filteringAuditPage,
  leadExportReceiptFor,
} from './data/exportAudit';
import { json } from './mockApi';
import { expect, test } from './test';

type ExportStep = 'receipt-request' | 'receipt-response' | 'download';

declare global {
  interface Window {
    __mipExportOrder?: ExportStep[];
  }
}

/** Record, in page order, the receipt request, its response and every anchor download. */
function installExportOrderProbe(): void {
  const order: ExportStep[] = [];
  window.__mipExportOrder = order;
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const isReceipt = url.includes('/leads/export-receipt');
    if (isReceipt) order.push('receipt-request');
    const response = await originalFetch(input, init);
    if (isReceipt) order.push('receipt-response');
    return response;
  };
  const originalClick = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function click(this: HTMLAnchorElement) {
    if (this.download) order.push('download');
    return originalClick.call(this);
  };
}

const SHA256_HEX = /^[0-9a-f]{64}$/;
const exportOrder = (page: import('@playwright/test').Page) =>
  page.evaluate(() => [...(window.__mipExportOrder ?? [])]);

const EXPORT_BUTTON_FOCUSED = 'BUTTON lead-export';
/** The focused element as `TAG data-testid`, so a focus drop reads as `BODY`. */
const focusedControl = (page: import('@playwright/test').Page) =>
  page.evaluate(() => {
    const active = document.activeElement;
    if (!active) return 'none';
    const testId = active.getAttribute('data-testid');
    return testId ? `${active.tagName} ${testId}` : active.tagName;
  });

/** A colour token as the computed value the page paints (for toHaveCSS). */
const resolvedColor = (page: import('@playwright/test').Page, token: string) =>
  page.evaluate((name) => {
    const probe = document.createElement('span');
    probe.style.color = `var(${name})`;
    document.body.append(probe);
    const value = getComputedStyle(probe).color;
    probe.remove();
    return value;
  }, token);

test.describe('audited lead CSV export', () => {
  test('two selected rows: one receipt, and the download waits for it', async ({ app, page, mockApi }) => {
    const receipts: LeadExportReceiptRequest[] = [];
    let releaseReceipt: () => void = () => undefined;
    const receiptHeld = new Promise<void>((resolve) => {
      releaseReceipt = resolve;
    });
    mockApi.register<LeadExportReceipt>('POST', '/api/leads/export-receipt', async ({ body }) => {
      const declaration = body as LeadExportReceiptRequest;
      receipts.push(declaration);
      await receiptHeld;
      return json<LeadExportReceipt>(leadExportReceiptFor(declaration));
    });
    await page.addInitScript(installExportOrderProbe);
    await app.gotoRoute('/lead-queue');

    const rowBoxes = page.locator('table.tbl tbody [data-testid^="lead-select-B-"]');
    await rowBoxes.nth(0).check();
    await rowBoxes.nth(1).check();
    const exportButton = page.getByTestId('lead-export');
    await expect(exportButton).toHaveText('Export 2 selected');

    const downloadEvent = page.waitForEvent('download');
    await exportButton.click();
    await expect.poll(() => receipts.length).toBe(1);

    // The receipt is held: the ledger row does not exist yet, so nothing may download.
    await expect(exportButton).toHaveText('Recording export…');
    expect(await exportOrder(page)).toEqual(['receipt-request']);

    releaseReceipt();
    const download = await downloadEvent;
    expect(await exportOrder(page)).toEqual(['receipt-request', 'receipt-response', 'download']);
    expect(receipts).toHaveLength(1);

    const [declaration] = receipts;
    expect(declaration.scope).toBe('selected');
    expect(declaration.row_count).toBe(2);
    expect(declaration.borrower_ids).toHaveLength(2);
    expect(declaration.csv_sha256).toMatch(SHA256_HEX);
    expect(declaration.borrower_ids_sha256).toMatch(SHA256_HEX);

    const csv = await readFile(await download.path(), 'utf8');
    expect(createHash('sha256').update(csv, 'utf8').digest('hex')).toBe(declaration.csv_sha256);
    expect(download.suggestedFilename()).toMatch(/^mip-leads-\d{4}-\d{2}-\d{2}\.csv$/);

    const receiptLine = page.getByTestId('lead-export-receipt');
    await expect(receiptLine).toHaveText(`Exported 2 rows · audit ${EXPORT_RECEIPT_ID}`);
    const receiptLink = receiptLine.getByRole('link', { name: EXPORT_RECEIPT_ID });
    await expect(receiptLink).toHaveAttribute('href', `/admin-config?audit_event_id=${EXPORT_RECEIPT_ID}#audit`);

    // The audit id opens the explorer on that one LEAD_EXPORT row, in-app.
    mockApi.register('GET', '/api/audit/events/page', filteringAuditPage([]));
    await receiptLink.click();
    await expect(page).toHaveURL(new RegExp(`/admin-config\\?audit_event_id=${EXPORT_RECEIPT_ID}#audit$`));
    const explorer = page.locator('#audit');
    await expect(explorer.getByRole('button', { name: `Collapse audit event ${EXPORT_RECEIPT_ID}` }))
      .toHaveAttribute('aria-expanded', 'true');
    await expect(explorer.locator('tbody tr[data-audit-event-id] td.is-primary > div').first()).toHaveText('Lead list exported');
    expect(await exportOrder(page), 'client-side navigation: the page-order probe survived').toContain('download');
  });

  test('keyboard focus stays on the export button across a held receipt', async ({ app, page, mockApi }) => {
    let releaseReceipt: () => void = () => undefined;
    const receiptHeld = new Promise<void>((resolve) => {
      releaseReceipt = resolve;
    });
    let receipts = 0;
    mockApi.register<LeadExportReceipt>('POST', '/api/leads/export-receipt', async ({ body }) => {
      receipts += 1;
      await receiptHeld;
      return json<LeadExportReceipt>(leadExportReceiptFor(body as LeadExportReceiptRequest));
    });
    await app.gotoRoute('/lead-queue');
    await page.locator('table.tbl tbody [data-testid^="lead-select-B-"]').nth(0).check();

    const exportButton = page.getByTestId('lead-export');
    await exportButton.focus();
    expect(await focusedControl(page)).toBe(EXPORT_BUTTON_FOCUSED);
    const downloadEvent = page.waitForEvent('download');
    await page.keyboard.press('Enter');
    await expect.poll(() => receipts).toBe(1);

    // Pending: announced as busy and unavailable, but still the focused control.
    await expect(exportButton).toHaveText('Recording export…');
    await expect(exportButton).toHaveAttribute('aria-disabled', 'true');
    expect(await focusedControl(page), 'focus while the receipt is held').toBe(EXPORT_BUTTON_FOCUSED);
    // It also looks unavailable (the .btn[disabled] colours, a progress cursor),
    // not like an active button that merely changed its label.
    await expect(exportButton).toHaveCSS('cursor', 'progress');
    await expect(exportButton).toHaveCSS('color', await resolvedColor(page, '--text-2'));

    releaseReceipt();
    await downloadEvent;
    await expect(page.getByTestId('lead-export-receipt')).toContainText(`audit ${EXPORT_RECEIPT_ID}`);
    await expect(exportButton).not.toHaveAttribute('aria-disabled');
    await expect(exportButton).toHaveCSS('cursor', 'pointer');
    expect(await focusedControl(page), 'focus after the download').toBe(EXPORT_BUTTON_FOCUSED);
    expect(receipts).toBe(1);
  });

  test('a refused receipt (422) downloads nothing and says why', async ({ app, page, mockApi }) => {
    await page.addInitScript(installExportOrderProbe);
    app.degrade('/api/leads/export-receipt', {
      method: 'POST',
      status: 422,
      body: { detail: 'export declaration does not match the borrower id list' },
    });
    let downloads = 0;
    page.on('download', () => {
      downloads += 1;
    });
    await app.gotoRoute('/lead-queue');

    await page.locator('table.tbl tbody [data-testid^="lead-select-B-"]').nth(0).check();
    await page.getByTestId('lead-export').click();

    const error = page.getByTestId('lead-export-error');
    await expect(error).toHaveText(/Export refused: the audit receipt did not match the file\. Nothing was downloaded\./);
    await expect(error).toHaveAttribute('role', 'alert');
    await expect(page.getByTestId('lead-export')).toBeEnabled();
    expect(await exportOrder(page)).toEqual(['receipt-request', 'receipt-response']);
    expect(downloads).toBe(0);
    await expect(page.getByTestId('lead-export-receipt')).toHaveCount(0);
    expect(mockApi.calls.filter((call) => call.path.endsWith('/leads/export-receipt'))).toHaveLength(1);
  });
});

test.describe('audit explorer', () => {
  test('filters round-trip through the URL and reach the API', async ({ app, page, mockApi }) => {
    const requests: URLSearchParams[] = [];
    mockApi.register('GET', '/api/audit/events/page', filteringAuditPage(requests));
    await app.gotoRoute('/admin-config#audit');
    const explorer = page.locator('#audit');

    await explorer.getByLabel('ACTOR', { exact: true }).fill(EXPORT_ACTOR);
    await explorer.getByLabel('SINCE', { exact: true }).fill('2026-07-10');
    await explorer.getByLabel('UNTIL', { exact: true }).fill('2026-07-14');
    await explorer.getByRole('combobox', { name: /^Event type: / }).click();
    await explorer.getByRole('option', { name: 'Outreach approved', exact: true }).click();
    await explorer.getByRole('button', { name: 'Apply filters' }).click();

    await expect(page).toHaveURL(/[?&]audit_event_type=APPROVE(&|#|$)/);
    const url = new URL(page.url());
    expect(Object.fromEntries(url.searchParams)).toEqual({
      audit_actor: EXPORT_ACTOR,
      audit_since: '2026-07-10',
      audit_until: '2026-07-14',
      audit_event_type: 'APPROVE',
    });
    expect(url.hash).toBe('#audit');
    await expect(explorer.locator('table[aria-label="Audit events"] tbody tr[data-audit-event-id]')).toHaveCount(1);
    const sent = requests[requests.length - 1];
    expect(sent.get('actor')).toBe(EXPORT_ACTOR);
    expect(sent.get('event_type')).toBe('APPROVE');
    // Whole local days in America/New_York (the harness timezone), until inclusive.
    expect(sent.get('since')).toBe('2026-07-10T04:00:00.000Z');
    expect(sent.get('until')).toBe('2026-07-15T03:59:59.999Z');

    // A reload (or a shared link) restores the same view from the URL alone.
    await page.reload();
    await app.settle();
    await expect(explorer.getByLabel('ACTOR', { exact: true })).toHaveValue(EXPORT_ACTOR);
    await expect(explorer.getByLabel('SINCE', { exact: true })).toHaveValue('2026-07-10');
    await expect(explorer.getByLabel('UNTIL', { exact: true })).toHaveValue('2026-07-14');
    await expect(explorer.getByRole('combobox', { name: 'Event type: Outreach approved' })).toBeVisible();
    await expect(explorer.getByLabel('Applied audit filters')).toContainText('event = Outreach approved · APPROVE');
    await expect(explorer.locator('table[aria-label="Audit events"] tbody tr[data-audit-event-id]')).toHaveCount(1);
  });

  test('an inverted day window is refused by the governed alert, on the date inputs', async ({ app, page, mockApi }) => {
    const requests: URLSearchParams[] = [];
    mockApi.register('GET', '/api/audit/events/page', filteringAuditPage(requests));
    await app.gotoRoute('/admin-config#audit');
    const explorer = page.locator('#audit');
    await expect(explorer.locator('table[aria-label="Audit events"] tbody tr[data-audit-event-id]'))
      .toHaveCount(EXPLORER_ROWS.length);
    const requestsBefore = requests.length;
    const urlBefore = page.url();

    const since = explorer.getByLabel('SINCE', { exact: true });
    const until = explorer.getByLabel('UNTIL', { exact: true });
    await since.fill('2026-07-14');
    await until.fill('2026-07-01');
    await explorer.getByRole('button', { name: 'Apply filters' }).click();

    // Chromium's own constraint bubble would block the submit and say nothing
    // governed; the form is noValidate, so the product's message is what shows.
    const alert = explorer.getByRole('alert');
    await expect(alert).toHaveText('The "since" day must be on or before the "until" day.');
    await expect(alert).toBeVisible();
    const alertId = await alert.getAttribute('id');
    for (const field of [since, until]) {
      await expect(field).toHaveAttribute('aria-invalid', 'true');
      await expect(field).toHaveAttribute('aria-describedby', alertId ?? '');
    }
    await expect(explorer.getByLabel('CORRELATION ID', { exact: true })).toHaveAttribute('aria-invalid', 'false');
    await expect(explorer.getByLabel('ACTOR', { exact: true })).toHaveAttribute('aria-invalid', 'false');
    expect(page.url()).toBe(urlBefore);
    expect(requests).toHaveLength(requestsBefore);
  });

  test('a day the URL cannot hold is refused on its own input, not silently dropped', async ({ app, page, mockApi }) => {
    const requests: URLSearchParams[] = [];
    mockApi.register('GET', '/api/audit/events/page', filteringAuditPage(requests));
    await app.gotoRoute('/admin-config#audit');
    const explorer = page.locator('#audit');
    await expect(explorer.locator('table[aria-label="Audit events"] tbody tr[data-audit-event-id]'))
      .toHaveCount(EXPLORER_ROWS.length);
    const requestsBefore = requests.length;
    const urlBefore = page.url();

    // Chromium's date input holds a five-digit year; the URL parser keeps
    // only YYYY-MM-DD, so applying it would drop the "until" bound unseen.
    const since = explorer.getByLabel('SINCE', { exact: true });
    const until = explorer.getByLabel('UNTIL', { exact: true });
    await since.fill('2026-07-01');
    await until.fill('20260-07-14');
    await expect(until).toHaveValue('20260-07-14');
    await explorer.getByRole('button', { name: 'Apply filters' }).click();

    const alert = explorer.getByRole('alert');
    await expect(alert).toHaveText('The "until" day must be a calendar day written YYYY-MM-DD.');
    await expect(until).toHaveAttribute('aria-invalid', 'true');
    await expect(until).toHaveAttribute('aria-describedby', (await alert.getAttribute('id')) ?? '');
    await expect(since).toHaveAttribute('aria-invalid', 'false');
    expect(page.url()).toBe(urlBefore);
    expect(requests).toHaveLength(requestsBefore);
  });

  test('removing a filter chip hands focus to the next chip, then to Apply', async ({ app, page, mockApi }) => {
    const requests: URLSearchParams[] = [];
    mockApi.register('GET', '/api/audit/events/page', filteringAuditPage(requests));
    await app.gotoRoute(`/admin-config?audit_actor=${encodeURIComponent(EXPORT_ACTOR)}&audit_event_type=APPROVE#audit`);
    const explorer = page.locator('#audit');
    const chips = explorer.getByLabel('Applied audit filters');
    await expect(chips).toContainText('event = Outreach approved · APPROVE');

    const removeEvent = chips.getByRole('button', { name: 'Remove event filter' });
    await removeEvent.focus();
    await page.keyboard.press('Enter');
    await expect(page).not.toHaveURL(/audit_event_type=/);
    await expect(chips.getByRole('button', { name: 'Remove actor filter' })).toBeFocused();

    await page.keyboard.press('Enter');
    await expect(page).not.toHaveURL(/audit_actor=/);
    await expect(explorer.getByRole('button', { name: 'Apply filters' })).toBeFocused();
    await expect.poll(() => requests[requests.length - 1]?.get('actor')).toBeNull();
  });

  test('a deep link opens its event; its correlation id opens the whole request', async ({ app, page, mockApi }) => {
    const requests: URLSearchParams[] = [];
    mockApi.register('GET', '/api/audit/events/page', filteringAuditPage(requests));
    await app.gotoRoute(`/admin-config?audit_event_id=${EXPORT_RECEIPT_ID}#audit`);
    const explorer = page.locator('#audit');

    expect(requests[requests.length - 1].get('event_id')).toBe(EXPORT_RECEIPT_ID);
    await expect(explorer).toBeInViewport();
    const rows = explorer.locator('table[aria-label="Audit events"] tbody tr[data-audit-event-id]');
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toHaveAttribute('data-audit-event-id', EXPORT_RECEIPT_ID);
    await expect(explorer.getByRole('button', { name: `Collapse audit event ${EXPORT_RECEIPT_ID}` }))
      .toHaveAttribute('aria-expanded', 'true');
    // The linked row itself is on screen at 1440x900, not below the rollups.
    await expect(rows.first()).toBeInViewport();
    await expect(explorer.getByRole('link', { name: `Open audit event ${EXPORT_RECEIPT_ID} on its own` })).toBeInViewport();
    await expect(explorer.getByLabel('Applied audit filters')).toContainText(`audit event = ${EXPORT_RECEIPT_ID}`);

    // The correlation id opens every row of the same request. The URL moves
    // first and the query follows it, so wait on the wire, not the URL.
    await explorer.getByRole('link', { name: 'Show every audit event with correlation id corr-export-0001' }).click();
    await expect(page).toHaveURL(/[?&]audit_correlation_id=corr-export-0001(&|#|$)/);
    await expect.poll(() => requests[requests.length - 1].get('correlation_id')).toBe('corr-export-0001');
    expect(requests[requests.length - 1].get('event_id')).toBeNull();
    await expect(explorer.getByLabel('Applied audit filters')).toContainText('correlation = corr-export-0001');
    await expect(explorer.getByLabel('Applied audit filters')).not.toContainText('audit event =');
    await expect(rows).toHaveCount(1);
    await expect(rows.first()).toHaveAttribute('data-audit-event-id', EXPORT_RECEIPT_ID);
    // A filtered view, not the single-row deep link: nothing opens by itself.
    await expect(explorer.getByRole('button', { name: `Expand audit event ${EXPORT_RECEIPT_ID}` }))
      .toHaveAttribute('aria-expanded', 'false');
  });

  test('rows read as human labels with the raw code in a mono chip', async ({ app, page, mockApi }) => {
    mockApi.register('GET', '/api/audit/events/page', filteringAuditPage([]));
    await app.gotoRoute('/admin-config#audit');
    const rows = page.locator('#audit table[aria-label="Audit events"] tbody tr[data-audit-event-id]');
    await expect(rows).toHaveCount(EXPLORER_ROWS.length);

    const expected: Array<[string, string]> = [
      ['Lead list exported', 'LEAD_EXPORT'],
      ['Outreach approved', 'APPROVE'],
      ['Lead queue reviewed', 'VIEW_LEADS'],
      ['Outreach rejected', 'OUTREACH_REJECT'],
      ['Genie analysis run', 'RUN_GENIE'],
    ];
    for (const [index, [label, code]] of expected.entries()) {
      const cell = rows.nth(index).locator('td.is-primary');
      await expect(cell.locator('> div').first()).toHaveText(label);
      const chip = cell.locator('.chip.mono');
      await expect(chip).toHaveText(code);
      await expect(chip).toHaveCSS('font-family', /Geist Mono/);
    }
  });

  test('the current page downloads as CSV with its header row', async ({ app, page, mockApi }) => {
    mockApi.register('GET', '/api/audit/events/page', filteringAuditPage([]));
    await app.gotoRoute('/admin-config#audit');
    await expect(page.locator('#audit table[aria-label="Audit events"] tbody tr[data-audit-event-id]'))
      .toHaveCount(EXPLORER_ROWS.length);

    const downloadEvent = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Download page 1 of the audit explorer as CSV' }).click();
    const download = await downloadEvent;
    expect(download.suggestedFilename()).toBe('mip-audit-page-1-2026-07-14.csv');
    const lines = (await readFile(await download.path(), 'utf8')).trimEnd().split('\n');
    expect(lines[0]).toBe(
      'event_id,created_at,event,event_code,action,actor,entity_type,entity_id,correlation_id,request_id,evidence_ids',
    );
    expect(lines.slice(1).map((line) => line.split(',')[0])).toEqual(EXPLORER_ROWS.map((row) => row.event_id));
    expect(lines[1]).toContain(',Lead list exported,LEAD_EXPORT,lead_queue.export,');
  });
});
