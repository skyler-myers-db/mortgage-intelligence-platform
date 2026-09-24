/**
 * Lane w2-queue-query-layer, proven on the production build at 1440x900
 * (audit stack-09, tables-05, wow-power-5, delivery-08, runtime-06):
 *
 *  (a) an approve through the review stays pessimistic while its POST is
 *      held (the row reads "Approving…", no Approved chip, one POST), and
 *      the write refetches nothing afterwards (refetchType 'none': an active
 *      leads / borrower / proof read would write VIEW_* audit rows);
 *  (b) a 409 or 500 leaves the row un-approved with the review's "Not
 *      approved…", and Confirm again after the 500 replays the same
 *      request_id;
 *  (c) assign sends 'manual' for one loan officer and distribute sends
 *      'round_robin' (never 'score_balanced'); the result is a shell toast;
 *  (d) mounting the queue reads no export-only data, and expanding a row
 *      preloads the dossier and offer route CODE, never their audited reads;
 *  (e) the CSV export stamps the X-Data-Refreshed-At header and the rules
 *      version read on the click (exactly once), or 'unknown' when that read
 *      fails, and still downloads with its receipt;
 *  (f) while placeholder rows of the previous filters are on screen, Export
 *      waits, so the LEAD_EXPORT declaration pairs filters with their rows.
 *
 * Every write is registered per test so the test owns its timing.
 */
import { readFile } from 'node:fs/promises';
import AxeBuilder from '@axe-core/playwright';
import type { Locator, Page } from '@playwright/test';
import type { LeadExportReceipt, LeadExportReceiptRequest } from '../../../src/lib/apiClients/leadExport';
import type { ApproveResult, DecisionReceipt } from '../../../src/lib/apiTypes';
import type { LeadAssignment, LeadSummary } from '../../../src/types';
import { LEADS, PRIMARY_BORROWER } from './data/borrowers';
import { APPROVE_AUDIT_ID, RequestGate, approveResult, ledgerReceipt } from './data/decisionReceipt';
import { EXPORT_RECEIPT_ID, leadExportReceiptFor } from './data/exportAudit';
import { leadFixtures } from './data/leads';
import { SALES_TEAM } from './data/portfolio';
import { json, type FixtureReply, type FixtureRequest, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const ID = PRIMARY_BORROWER.borrower_id;
const REFRESHED_AT = '2026-07-14T06:15:00Z';
const LOAN_OFFICERS = SALES_TEAM.filter((member) => member.role === 'loan_officer');

interface ApproveBody {
  borrower_id: string;
  request_id: string;
  draft_generation_id: string | null;
}

interface DistributeBody {
  borrower_ids: string[];
  lo_emails: string[];
  strategy: string;
  request_id: string;
}

type LeadsHandler = (request: FixtureRequest) => FixtureReply | Promise<FixtureReply>;

/** The default GET /api/leads handler from data/leads.ts (wrapped, never edited). */
function defaultLeadsHandler(): LeadsHandler {
  const entry = leadFixtures.find((candidate) => candidate.method === 'GET' && candidate.pattern === '/api/leads');
  if (!entry) throw new Error('data/leads.ts no longer registers GET /api/leads');
  return entry.handler;
}

function callsTo(mockApi: MockApi, method: string, path: RegExp): number {
  return mockApi.calls.filter((call) => call.method === method && path.test(call.path)).length;
}

function approvalCell(page: Page, borrowerId: string): Locator {
  return page.getByTestId(`lead-approval-cell-${borrowerId}`);
}

/** Expand the first row and open its approve review; resolves once the draft is on screen. */
async function openReview(app: { expandFirstLeadRow(): Promise<Locator> }, page: Page): Promise<Locator> {
  await app.expandFirstLeadRow();
  await approvalCell(page, ID).getByRole('button', { name: `Approve ${ID}` }).click();
  const review = page.locator('table.tbl tbody tr.tbl__expand').getByTestId('lead-approve-review');
  await expect(review.getByTestId('lead-approve-review-subject')).not.toBeEmpty();
  return review;
}

function registerReceiptRead(mockApi: MockApi): void {
  mockApi.register<DecisionReceipt>('GET', '/api/audit/receipt/:id', ({ params }) =>
    json<DecisionReceipt>(ledgerReceipt(params.id, PRIMARY_BORROWER, 'approved')),
  );
}

async function axeViolations(page: Page, selector: string) {
  const results = await new AxeBuilder({ page }).include(selector).withTags(WCAG_TAGS).analyze();
  return results.violations.map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(' ')).join(', ')}`);
}

test.describe('(a) a governed approve stays pessimistic and refetches nothing', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: held approve POST -> "Approving…", one POST, no chip until it returns`, async ({ app, mockApi, page }) => {
      await app.setTheme(theme);
      const gate = new RequestGate();
      const bodies: ApproveBody[] = [];
      mockApi.register<ApproveResult>('POST', '/api/outreach/approve', async ({ body }) => {
        bodies.push(body as ApproveBody);
        await gate.hold();
        return approveResult(APPROVE_AUDIT_ID);
      });
      registerReceiptRead(mockApi);
      await app.gotoRoute('/lead-queue');

      const review = await openReview(app, page);
      await review.getByTestId('lead-approve-review-confirm').click();
      await expect.poll(() => gate.received).toBe(true);

      const cell = approvalCell(page, ID);
      const approve = cell.getByRole('button', { name: `Approve ${ID}` });
      await expect(approve).toHaveText('Approving…');
      await expect(approve).toBeDisabled();
      await expect(cell.getByRole('button', { name: `Reject ${ID}` })).toBeDisabled();
      await expect(cell.locator('.chip--success')).toHaveCount(0);
      expect(bodies, 'one approve POST while held').toHaveLength(1);
      expect(await axeViolations(page, '.tbl-wrap'), `${theme} pending row`).toEqual([]);

      const settledCalls = mockApi.calls.length;
      gate.release();
      await expect(cell.locator('.chip--success')).toHaveText(/Approved/);
      // A deliberate observation window: an ACTIVE refetch of the leads,
      // the dossier or its proof would fire right after the write settles.
      await page.waitForTimeout(2000);
      const after = mockApi.calls.slice(settledCalls).filter((call) => call.method === 'GET');
      expect(after.filter((call) => /^\/api\/leads$|^\/api\/borrowers\//.test(call.path))).toEqual([]);
      expect(callsTo(mockApi, 'POST', /^\/api\/outreach\/approve$/)).toBe(1);
      expect(bodies).toHaveLength(1);
    });
  }
});

test.describe('(b) a refused or failed approve is never shown as approved', () => {
  test('409: the row stays un-approved and the review says "Not approved"', async ({ app, hygiene, mockApi, page }) => {
    hygiene.allow('console.error', /status of 409.*\/outreach\/approve/);
    mockApi.register<{ detail: string }>('POST', '/api/outreach/approve', () =>
      json({ detail: 'Borrower is not pending approval.' }, { status: 409 }));
    await app.gotoRoute('/lead-queue');

    const review = await openReview(app, page);
    await review.getByTestId('lead-approve-review-confirm').click();

    await expect(review.getByRole('alert')).toContainText('Not approved.');
    const cell = approvalCell(page, ID);
    await expect(cell.locator('.chip--success')).toHaveCount(0);
    await expect(cell.getByRole('button', { name: `Approve ${ID}` })).toHaveText('Approve');
    expect(callsTo(mockApi, 'POST', /^\/api\/outreach\/approve$/)).toBe(1);
  });

  test('500: Confirm again replays the SAME request_id, and only the second reply approves', async ({ app, hygiene, mockApi, page }) => {
    hygiene.allow('console.error', /status of 500.*\/outreach\/approve/);
    const bodies: ApproveBody[] = [];
    mockApi.register<ApproveResult | { detail: string }>('POST', '/api/outreach/approve', ({ body }) => {
      bodies.push(body as ApproveBody);
      return bodies.length === 1
        ? json({ detail: 'Internal Server Error' }, { status: 500 })
        : approveResult(APPROVE_AUDIT_ID);
    });
    registerReceiptRead(mockApi);
    await app.gotoRoute('/lead-queue');

    const review = await openReview(app, page);
    const confirm = review.getByTestId('lead-approve-review-confirm');
    await confirm.click();
    await expect(review.getByRole('alert')).toContainText('Not approved.');
    const cell = approvalCell(page, ID);
    await expect(cell.locator('.chip--success')).toHaveCount(0);

    await confirm.click();
    await expect(cell.locator('.chip--success')).toHaveText(/Approved/);
    expect(bodies).toHaveLength(2);
    expect(bodies[1].request_id, 'the retry of the same intent replays its id').toBe(bodies[0].request_id);
    expect(bodies[1].draft_generation_id).toBe(bodies[0].draft_generation_id);
    expect(callsTo(mockApi, 'POST', /^\/api\/outreach\/draft$/), 'no second draft').toBe(1);
  });
});

test.describe('(c) assignment strategies are honest and land in the shell toast region', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: Assign sends manual, Distribute sends round_robin, never score_balanced`, async ({ app, mockApi, page }) => {
      await app.setTheme(theme);
      const bodies: DistributeBody[] = [];
      mockApi.register('POST', '/api/sales/distribute', ({ body }) => {
        const request = body as DistributeBody;
        bodies.push(request);
        const assignments: LeadAssignment[] = request.borrower_ids.map((borrowerId, index) => ({
          assignment_id: `asg-${bodies.length}-${index}`,
          borrower_id: borrowerId,
          assigned_to_email: request.lo_emails[index % request.lo_emails.length],
          assigned_by: 'approver@summit-mortgage.example',
          assigned_at: '2026-07-14T15:00:00Z',
          strategy: request.strategy === 'round_robin' ? 'round_robin' : 'manual',
        }));
        return json({
          assigned_count: assignments.length,
          strategy: request.strategy,
          assignments,
          per_lo_counts: {},
          audit_event_id: `audit-distribute-${bodies.length}`,
        });
      });
      await app.gotoRoute('/lead-queue');
      const boxes = page.locator('table.tbl tbody [data-testid^="lead-select-B-"]');
      const bulk = page.getByRole('toolbar', { name: 'Bulk actions' });

      await boxes.nth(0).check();
      await boxes.nth(1).check();
      await bulk.getByRole('button', { name: 'Assign 2 selected leads to selected loan officer' }).click();
      // The result is a shell toast linked to the LEAD_DISTRIBUTE audit row.
      const toastFor = (auditId: string) =>
        page.locator('.toast').filter({ has: page.locator(`a.toast__link[href*="audit_event_id=${auditId}"]`) });
      const toast = toastFor('audit-distribute-1');
      await expect(toast).toBeVisible();
      await expect(toast.locator('.toast__title')).toHaveText('2 leads assigned');
      expect(await axeViolations(page, '.toast'), `${theme} toast`).toEqual([]);

      await boxes.nth(0).check();
      await boxes.nth(1).check();
      await bulk.getByRole('button', { name: 'Distribute 2 selected leads across active loan officers' }).click();
      await expect(toastFor('audit-distribute-2').locator('.toast__title')).toHaveText('2 leads assigned');

      expect(bodies.map((body) => [body.strategy, body.lo_emails.length])).toEqual([
        ['manual', 1],
        ['round_robin', LOAN_OFFICERS.length],
      ]);
      expect(bodies.map((body) => body.strategy)).not.toContain('score_balanced');
      expect(new Set(bodies.map((body) => body.request_id)).size).toBe(2);
      // No in-table sales strip anymore: the shell region owns the result.
      await expect(page.locator('.surface .table-success', { hasText: 'assigned' })).toHaveCount(0);
    });
  }
});

test.describe('(d) no export-only reads on mount; row expand warms route code only', () => {
  test('mount reads neither the portfolio preview nor the admin rules; expand preloads the two route chunks', async ({ app, mockApi, page }) => {
    const assets: string[] = [];
    page.on('request', (request) => {
      const path = new URL(request.url()).pathname;
      if (path.startsWith('/assets/')) assets.push(path);
    });
    await app.gotoRoute('/lead-queue');
    expect(callsTo(mockApi, 'POST', /^\/api\/portfolio\/preview$/)).toBe(0);
    expect(callsTo(mockApi, 'GET', /^\/api\/admin\/rules$/)).toBe(0);
    const routeChunk = (name: string) => new RegExp(`^/assets/${name}-[\\w-]+\\.js$`);
    expect(assets.filter((path) => routeChunk('borrower-360').test(path))).toEqual([]);
    expect(assets.filter((path) => routeChunk('offer-orchestrator').test(path))).toEqual([]);

    const apiBefore = mockApi.calls.length;
    await app.expandFirstLeadRow();
    await expect.poll(() => assets.some((path) => routeChunk('borrower-360').test(path))).toBe(true);
    await expect.poll(() => assets.some((path) => routeChunk('offer-orchestrator').test(path))).toBe(true);
    await app.settle();
    const expandCalls = mockApi.calls.slice(apiBefore);
    expect(expandCalls.filter((call) => /^\/api\/borrowers\//.test(call.path))).toEqual([]);
    expect(expandCalls.filter((call) => /^\/api\/outreach\/draft$/.test(call.path))).toEqual([]);
  });
});

test.describe('(e) the export stamps its provenance on the click', () => {
  test.beforeEach(({ mockApi }) => {
    const base = defaultLeadsHandler();
    mockApi.register<LeadSummary[]>('GET', '/api/leads', async (request) => {
      const reply = (await base(request)) as FixtureReply<LeadSummary[]>;
      return { ...reply, headers: { ...reply.headers, 'X-Data-Refreshed-At': REFRESHED_AT } };
    });
    mockApi.register<LeadExportReceipt>('POST', '/api/leads/export-receipt', ({ body }) =>
      json<LeadExportReceipt>(leadExportReceiptFor(body as LeadExportReceiptRequest)));
  });

  test('admin: refreshed_at from the leads header and the rules version read once, after the click', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/lead-queue');
    expect(callsTo(mockApi, 'GET', /^\/api\/admin\/rules$/)).toBe(0);

    const download = page.waitForEvent('download');
    await page.getByTestId('lead-export').click();
    const csv = await readFile(await (await download).path(), 'utf8');

    expect(csv).toContain(`# refreshed_at=${REFRESHED_AT}`);
    expect(csv).toContain('# rules_version=fixture-v1');
    expect(callsTo(mockApi, 'GET', /^\/api\/admin\/rules$/)).toBe(1);
    await expect(page.getByTestId('lead-export-receipt')).toContainText(`audit ${EXPORT_RECEIPT_ID}`);
  });

  test('a failed rules read stamps unknown and the file still downloads with its receipt', async ({ app, mockApi, page }) => {
    app.degrade('/api/admin/rules', { method: 'GET', status: 500, body: { detail: 'rules store unavailable' } });
    await app.gotoRoute('/lead-queue');

    const download = page.waitForEvent('download');
    await page.getByTestId('lead-export').click();
    const csv = await readFile(await (await download).path(), 'utf8');

    expect(csv).toContain('# rules_version=unknown');
    expect(csv).toContain(`# refreshed_at=${REFRESHED_AT}`);
    expect(callsTo(mockApi, 'GET', /^\/api\/admin\/rules$/)).toBe(1);
    await expect(page.getByTestId('lead-export-receipt')).toContainText(`audit ${EXPORT_RECEIPT_ID}`);
  });
});

test.describe('(f) placeholder rows never export under the new filters', () => {
  test('Export waits while the new filters’ rows are held, then declares filters that match its rows', async ({ app, mockApi, page }) => {
    const base = defaultLeadsHandler();
    const gate = new RequestGate();
    mockApi.register<LeadSummary[]>('GET', '/api/leads', async (request) => {
      if (request.query.get('state') === 'TX') await gate.hold();
      return (await base(request)) as FixtureReply<LeadSummary[]>;
    });
    const receipts: LeadExportReceiptRequest[] = [];
    mockApi.register<LeadExportReceipt>('POST', '/api/leads/export-receipt', ({ body }) => {
      receipts.push(body as LeadExportReceiptRequest);
      return json<LeadExportReceipt>(leadExportReceiptFor(body as LeadExportReceiptRequest));
    });
    await app.gotoRoute('/lead-queue');

    const menu = await app.openFilterMenu('STATE');
    await menu.getByRole('option', { name: 'TX', exact: true }).click();
    await expect.poll(() => gate.received).toBe(true);

    const exportButton = page.getByTestId('lead-export');
    await expect(exportButton).toHaveAttribute('aria-disabled', 'true');
    await expect(exportButton).toHaveAttribute('title', 'Export waits for the rows of the current filters');
    // aria-disabled, never native `disabled` (a focused control must keep focus).
    expect(await exportButton.evaluate((button) => (button as HTMLButtonElement).disabled)).toBe(false);
    // Every placeholder row is still the previous (national) cohort.
    await expect(page.locator('table.tbl tbody .lead-table__borrower')).toHaveCount(LEADS.length);
    // A keyboard press still reaches an aria-disabled button (a pointer click
    // would too); the export itself must refuse.
    await exportButton.focus();
    await page.keyboard.press('Enter');
    await expect(exportButton).toBeFocused();
    expect(receipts, 'a blocked export declares nothing').toEqual([]);
    expect(callsTo(mockApi, 'GET', /^\/api\/admin\/rules$/)).toBe(0);

    gate.release();
    const texasIds = LEADS.filter((lead) => lead.state === 'TX').map((lead) => lead.borrower_id);
    await expect(page.locator('table.tbl tbody .lead-table__borrower')).toHaveCount(texasIds.length);
    await expect(exportButton).not.toHaveAttribute('aria-disabled', 'true');

    const download = page.waitForEvent('download');
    await exportButton.click();
    await download;
    expect(receipts).toHaveLength(1);
    expect(receipts[0].filters).toEqual({ state: 'TX' });
    expect([...receipts[0].borrower_ids].sort()).toEqual([...texasIds].sort());
  });
});
