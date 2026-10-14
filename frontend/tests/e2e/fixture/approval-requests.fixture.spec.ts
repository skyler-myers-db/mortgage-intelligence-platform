/**
 * Maker-checker approval requests on the Lead Queue (audit flow-02 /
 * shell-06, D-approval-flow-c; W5d w5-lq-requests-binding;
 * deviation:request-approval), proven on the production build at 1440x900
 * with two sessions (spec-local /api/session overrides that spread SESSION
 * and change only can_approve / can_access_admin, W5d C7):
 *
 *  (a) a non-approver selects 3 -> "Request approval for 3" -> a reason ->
 *      Send: exactly one POST {borrower_ids, rationale, request_key}, no
 *      POST /outreach/draft and no audited read; the held 200 gates the
 *      "Approval requested" chips; the toast carries the audit id; then
 *      "My requests · 1";
 *  (b) a campaign-bound queue: aria-disabled with the binding reason;
 *  (c) a governed 422 keeps the text and says the refusal was recorded;
 *  (d) My requests -> Withdraw -> confirm: one POST, the toast, rows unmarked;
 *      by keyboard, focus never falls to <body> (the confirm, Keep back to
 *      Withdraw, then the panel title after the 200);
 *  (e) an approver: "Approval requests · 3" -> the panel -> Open in queue:
 *      one GET /api/leads with only approval_request_batch, the scope row,
 *      an approve through the review carrying approval_request_batch_id, and
 *      "Approval requests · 2" after the active refetch;
 *  (f) the list GET is audit-free and read once per mount (a dossier and
 *      Back through the keep-alive slot read it no second time);
 *  (g) an errored session shows no request control;
 *  (h) axe on the form, both panels and the scope row, both themes;
 *  (i) Button loading computes a grid display over an unchanged box on
 *      Confirm approval and the row Reject, and on the bulk Reject of its
 *      own run (busy, never native disabled);
 *  (j) the 83 px header with the Console open, in both sessions; eight rows
 *      above the fold for an approver, seven for a non-approver, whose
 *      pre-existing role line keeps ONE line with its request note, at Linux
 *      text widths and for an address of any length;
 *  (k) the Approval column's request marks inside its 156 px and the 44 px
 *      row, Console open and closed: the approver's drawn glyph after Reject
 *      (named "Requested · <name>", its tooltip on hover) with Approve and
 *      Reject where an unmarked row has them and inside the content box; the
 *      requester's "Approval requested" label whole at Linux text widths;
 *  (l) a failed list read: one line in the title row (the 83 px header with
 *      the Console open, both sessions), and Retry reads the list once more.
 *
 * Chromium only (fixture-chromium): this is not a cross-engine spec, so the
 * fixture-webkit and Firefox projects do not collect it (playwright.config's
 * WEBKIT_SPEC / FIREFOX_FORCED_SPEC). Locators filter to visible elements:
 * the queue stays hidden in the DOM behind a dossier.
 */
import type { Locator, Page } from '@playwright/test';
import type { SessionResponse } from '../../../src/types';
import {
  NON_APPROVER_SESSION,
  REQUESTER_LABEL,
  REQUEST_AUDIT_ID,
  REQUEST_BATCH_ID,
  REQUEST_REASON,
  REQUESTED_IDS,
  batchView,
  registerApprovalRequestStore,
} from './data/approvalRequests';
import { APPROVE_AUDIT_ID, RequestGate, approveResult } from './data/decisionReceipt';
import { registerRejectRecorder } from './data/leadQueue';
import { SESSION } from './data/shell';
import { KNOWN_VIOLATIONS, expectAxeClean } from './axe';
import { json, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

function useSession(mockApi: MockApi, session: SessionResponse): void {
  mockApi.register<SessionResponse>('GET', '/api/session', () => json<SessionResponse>(session));
}

function calls(mockApi: MockApi, method: string, path: RegExp) {
  return mockApi.calls.filter((call) => call.method === method && path.test(call.path));
}

const visible = (locator: Locator) => locator.filter({ visible: true });
const chip = (page: Page) => visible(page.getByTestId('approval-requests-chip'));
const cell = (page: Page, borrowerId: string) => visible(page.getByTestId(`lead-approval-cell-${borrowerId}`));

/**
 * Linux Chromium sets Geist Mono wider than macOS (segments-cards'
 * LINUX_TEXT_EMULATION): off Linux the role line's mono text, the chip and
 * the signed-in address, is widened past that (+0.5px a glyph; the chip
 * keeps its own 0.02em), so its one-line fit holds on the CI renderer too.
 */
const LINUX_TEXT_EMULATION = `
  [data-testid="approver-role-status"] .mono { letter-spacing: 0.5px; }
  [data-testid="approver-role-status"] .chip { letter-spacing: calc(0.02em + 0.5px); }
`;
/**
 * A synthetic address past any cap. The session fixture keeps SESSION's own
 * address (C7: a spec-local session overrides only the role flags), so the
 * rendered address is replaced in the DOM to measure a long one.
 */
const LONG_ACTOR_EMAIL = 'regional.mortgage.loan.analyst.team@summit.example';
/** The share of the role line's content box its one line leaves free (the ~6% text slack). */
const ROLE_LINE_SLACK = 0.06;
/** The Approval column's chips (Geist Mono), widened the same way off Linux. */
const LINUX_APPROVAL_CHIP_EMULATION = '.tbl-cell--approval .chip { letter-spacing: calc(0.02em + 0.5px); }';

/** One rendered row's Approval cell: its edges, Approve / Reject, the approver's glyph, the requester's chip label. */
interface ApprovalCellGeometry {
  id: string;
  rowHeight: number;
  cellRight: number;
  contentRight: number;
  approveLeft: number | null;
  rejectLeft: number | null;
  rejectRight: number | null;
  mark: { width: number; height: number; right: number } | null;
  label: { client: number; scroll: number } | null;
}

function approvalCells(page: Page): Promise<ApprovalCellGeometry[]> {
  return page.locator('tr[data-borrower-row]').evaluateAll((rows) => rows
    .filter((row) => row.getBoundingClientRect().height > 0)
    .map((row) => {
      const td = row.querySelector<HTMLElement>('.tbl-cell--approval');
      const box = td?.getBoundingClientRect();
      const rect = (selector: string) => td?.querySelector(selector)?.getBoundingClientRect() ?? null;
      const approve = rect('[data-testid^="lead-approve-"]');
      const reject = rect('[data-testid^="lead-reject-"]');
      const glyph = rect('.lead-table__request-mark svg');
      const label = td?.querySelector<HTMLElement>('.lead-table__request-chip .chip__label') ?? null;
      return {
        id: row.getAttribute('data-borrower-row') ?? '',
        rowHeight: row.getBoundingClientRect().height,
        cellRight: box?.right ?? 0,
        contentRight: td && box ? box.right - parseFloat(getComputedStyle(td).paddingInlineEnd) : 0,
        approveLeft: approve?.left ?? null,
        rejectLeft: reject?.left ?? null,
        rejectRight: reject?.right ?? null,
        mark: glyph && { width: glyph.width, height: glyph.height, right: glyph.right },
        label: label && { client: label.clientWidth, scroll: label.scrollWidth },
      };
    }));
}

/** The marked rows (every requested id, rendered) and one unmarked row with live Approve / Reject. */
async function markedAndReference(page: Page) {
  const cells = await approvalCells(page);
  const marked = cells.filter((geometry) => REQUESTED_IDS.includes(geometry.id));
  expect(marked.map((geometry) => geometry.id).sort(), 'every requested row is rendered').toEqual([...REQUESTED_IDS].sort());
  const reference = cells.find((geometry) => !REQUESTED_IDS.includes(geometry.id) && geometry.approveLeft !== null);
  if (!reference) throw new Error('no unmarked row with Approve / Reject to compare against');
  return { marked, reference };
}

/** Open the Console and wait for the main column to narrow (the Approval pin then sits mid-page). */
async function openConsoleNarrow(app: { openConsole(): Promise<unknown>; settle(): Promise<void> }, page: Page): Promise<void> {
  await app.openConsole();
  await expect.poll(() => page.locator('#main-content .main__inner').first().evaluate((element) => Math.round(element.getBoundingClientRect().width)))
    .toBeLessThan(1100);
  await app.settle();
}

/** The role line's fit: its height against two chip heights, the free share of its box, the address clipped. */
function roleLineFit(status: Locator) {
  return status.evaluate((row) => {
    const style = getComputedStyle(row);
    const content = row.clientWidth - parseFloat(style.paddingInlineStart) - parseFloat(style.paddingInlineEnd);
    const parts = [...row.children].map((child) => child.getBoundingClientRect().width);
    const needed = parts.reduce((sum, width) => sum + width, 0) + parseFloat(style.columnGap) * (parts.length - 1);
    const address = row.querySelector<HTMLElement>('.mono');
    return {
      height: row.getBoundingClientRect().height,
      twoChips: (row.querySelector('.chip')?.getBoundingClientRect().height ?? 0) * 2,
      slack: (content - needed) / content,
      clipped: address !== null && address.scrollWidth > address.clientWidth,
    };
  });
}

async function selectRequested(page: Page): Promise<void> {
  for (const borrowerId of REQUESTED_IDS) await visible(page.getByTestId(`lead-select-${borrowerId}`)).check();
}

async function openForm(page: Page): Promise<Locator> {
  await selectRequested(page);
  const button = visible(page.getByTestId('lead-bulk-request-approval'));
  await expect(button).toHaveText(`Request approval for ${REQUESTED_IDS.length}`);
  await button.click();
  const form = page.getByTestId('request-approval-form');
  await expect(form.getByLabel('Reason for the request')).toBeFocused();
  return form;
}

test.describe('a non-approver', () => {
  test('(a) requests approval: one POST, no draft, no audited read; chips only after the 200', async ({ app, mockApi, page }) => {
    useSession(mockApi, NON_APPROVER_SESSION);
    const store = registerApprovalRequestStore(mockApi, { holdCreate: true });
    await app.gotoRoute('/lead-queue');
    const n = markNaturalLoad(mockApi);
    const form = await openForm(page);
    await expect(form).toContainText("Nothing is sent. An approver reviews each borrower's copy before anything is approved.");
    await form.getByLabel('Reason for the request').fill(REQUEST_REASON);
    await form.getByTestId('request-approval-send').click();
    await expect.poll(() => store.createGate.received).toBe(true);
    // Held: pessimistic. No chip, no toast, the form still open.
    await expect(form).toBeVisible();
    await expect(cell(page, REQUESTED_IDS[0])).not.toContainText('Approval requested');
    await expect(page.locator('.toast-region .toast')).toHaveCount(0);
    store.createGate.release();
    await expect(form).toHaveCount(0);
    // The form unmounted with focus on Send request: focus is on the table region, never <body>.
    await expect(visible(page.getByRole('region', { name: 'Ranked borrowers table scroll region' }))).toBeFocused();
    const toast = page.locator('.toast-region .toast', { hasText: `Approval requested for ${REQUESTED_IDS.length} borrowers` });
    await expect(toast).toBeVisible();
    // The audit link (or, for a reader without the ledger, the id) rides the toast.
    expect(await toast.innerHTML()).toContain(REQUEST_AUDIT_ID);
    for (const borrowerId of REQUESTED_IDS) await expect(cell(page, borrowerId)).toHaveText('Approval requested');
    await expect(chip(page)).toHaveText('My requests · 1');
    expect(store.creates).toHaveLength(1);
    expect(store.creates[0]).toEqual({
      borrower_ids: [...REQUESTED_IDS],
      rationale: REQUEST_REASON,
      request_key: expect.stringMatching(/^[0-9a-f-]{36}$/),
    });
    expect(calls(mockApi, 'POST', /^\/api\/outreach\/draft$/)).toEqual([]);
    expectNoAuditedReadSince(mockApi, n, 'the request flow');
  });

  test('(b) a campaign-bound queue keeps Request approval aria-disabled with its reason', async ({ app, hygiene, mockApi, page }) => {
    useSession(mockApi, NON_APPROVER_SESSION);
    registerApprovalRequestStore(mockApi);
    // Any binding (an unknown campaign reads invalid) keeps the request path shut.
    mockApi.register('GET', '/api/campaigns/:id', () => json({ detail: 'Campaign not found' }, { status: 404 }));
    hygiene.allow('console.error', /status of 404/);
    await app.gotoRoute('/lead-queue?campaign_id=c-unknown&variant_name=A');
    await selectRequested(page);
    const button = visible(page.getByTestId('lead-bulk-request-approval'));
    await expect(button).toHaveAttribute('aria-disabled', 'true');
    await expect(page.getByTestId('lead-bulk-request-blocked')).toHaveText(
      "Clear the campaign binding to request approval; a campaign's owner or an admin decides bound outreach.",
    );
    // aria-disabled, never native disabled: the click lands and opens nothing.
    await button.click({ force: true });
    await expect(page.getByTestId('request-approval-form')).toHaveCount(0);
  });

  test('(c) a governed 422 keeps the text and says the refusal was recorded', async ({ app, hygiene, mockApi, page }) => {
    hygiene.allow('console.error', /status of 422/);
    useSession(mockApi, NON_APPROVER_SESSION);
    registerApprovalRequestStore(mockApi, { failCreate: { status: 422, detail: 'rationale failed the governed text policy' } });
    await app.gotoRoute('/lead-queue');
    const form = await openForm(page);
    await form.getByLabel('Reason for the request').fill('Prioritize borrowers who are not Hispanic');
    await form.getByTestId('request-approval-send').click();
    await expect(form.getByTestId('request-approval-failure')).toHaveText(
      'The reason was refused by the governed text policy, and the refusal was recorded. '
      + 'Rewrite it without personal details or protected-class language.',
    );
    await expect(form.getByLabel('Reason for the request')).toHaveValue('Prioritize borrowers who are not Hispanic');
  });

  test('(d) My requests: Withdraw behind a confirm is one POST; the rows lose their chips', async ({ app, mockApi, page }) => {
    useSession(mockApi, NON_APPROVER_SESSION);
    const store = registerApprovalRequestStore(mockApi, { seed: batchView(REQUESTED_IDS, true) });
    await app.gotoRoute('/lead-queue');
    await expect(cell(page, REQUESTED_IDS[0])).toHaveText('Approval requested');
    await chip(page).click();
    const panel = page.getByTestId('my-approval-requests-panel');
    await expect(panel).toContainText(`Reason for the request ${REQUEST_REASON}`);
    await expect(panel).toContainText('Waiting for an approver');
    // By keyboard: every step unmounts the focused control, and focus follows it into the panel.
    const trigger = panel.getByTestId(`approval-request-withdraw-${REQUEST_BATCH_ID}`);
    const confirm = panel.getByTestId(`approval-request-withdraw-confirm-${REQUEST_BATCH_ID}`);
    await trigger.press('Enter');
    await expect(panel).toContainText(`Withdraw ${REQUESTED_IDS.length} open borrowers? Approvers stop seeing them.`);
    await expect(confirm).toBeFocused();
    await panel.getByTestId(`approval-request-withdraw-keep-${REQUEST_BATCH_ID}`).press('Enter');
    await expect(trigger).toBeFocused();
    expect(store.withdraws).toEqual([]);
    await trigger.press('Enter');
    await expect(confirm).toBeFocused();
    await confirm.press('Enter');
    await expect(page.locator('.toast-region .toast', { hasText: 'Request withdrawn' })).toBeVisible();
    expect(store.withdraws).toEqual([REQUEST_BATCH_ID]);
    await expect(cell(page, REQUESTED_IDS[0])).not.toContainText('Approval requested');
    await expect(panel.getByRole('heading', { name: 'My requests' })).toBeFocused();
  });

  test('(g) an errored session check offers no request control', async ({ app, hygiene, mockApi, page }) => {
    hygiene.allow('console.error', /status of 500/);
    mockApi.register('GET', '/api/session', () => json({ detail: 'down' }, { status: 500 }));
    registerApprovalRequestStore(mockApi);
    await app.gotoRoute('/lead-queue');
    await selectRequested(page);
    await expect(visible(page.getByTestId('lead-bulk-actions'))).toBeVisible();
    await expect(page.getByTestId('lead-bulk-request-approval')).toHaveCount(0);
    await expect(page.getByTestId('approval-requests-chip')).toHaveCount(0);
  });
});

test.describe('an approver', () => {
  test('(e) opens a request as a scoped queue; the approve carries its id; the count drops', async ({ app, mockApi, page }) => {
    useSession(mockApi, SESSION);
    const store = registerApprovalRequestStore(mockApi, { seed: batchView(REQUESTED_IDS, false) });
    await app.gotoRoute('/lead-queue');
    await expect(chip(page)).toHaveText(`Approval requests · ${REQUESTED_IDS.length}`);
    await expect(visible(page.getByTestId(`lead-request-mark-${REQUESTED_IDS[0]}`))).toHaveAccessibleName(`Requested · ${REQUESTER_LABEL}`);
    await chip(page).click();
    const panel = page.getByTestId('approval-requests-panel');
    await expect(panel).toContainText(REQUESTER_LABEL);
    await expect(panel).toContainText(`${REQUESTED_IDS.length} open`);
    await expect(panel).toContainText(REQUEST_REASON);
    const before = calls(mockApi, 'GET', /^\/api\/leads$/).length;
    await panel.getByTestId(`approval-request-open-${REQUEST_BATCH_ID}`).click();
    await expect(page).toHaveURL(new RegExp(`/lead-queue\\?approval_request_batch=${REQUEST_BATCH_ID}$`));
    const scope = visible(page.getByTestId('approval-request-scope'));
    await expect(scope).toContainText(`${REQUESTER_LABEL} · ${REQUESTED_IDS.length} borrowers`);
    await expect(scope).toContainText(`Reason for the request: ${REQUEST_REASON}`);
    const reads = calls(mockApi, 'GET', /^\/api\/leads$/).slice(before);
    expect(reads.map((call) => call.search)).toEqual([`approval_request_batch=${REQUEST_BATCH_ID}`]);

    await visible(page.getByTestId(`lead-approve-${REQUESTED_IDS[0]}`)).click();
    const confirm = page.getByTestId('lead-approve-review-confirm');
    await expect(confirm).toBeFocused();
    await confirm.click();
    await expect(cell(page, REQUESTED_IDS[0]).locator('.chip--success')).toHaveText(/Approved/);
    expect(store.approvals).toHaveLength(1);
    expect(store.approvals[0]).toMatchObject({ borrower_id: REQUESTED_IDS[0], approval_request_batch_id: REQUEST_BATCH_ID });
    await expect(chip(page)).toHaveText(`Approval requests · ${REQUESTED_IDS.length - 1}`);
    // Clear drops the scope (the param) and returns focus to the table region.
    await visible(page.getByTestId('approval-request-scope-clear')).click();
    await expect(page).not.toHaveURL(/approval_request_batch=/);
    await expect(visible(page.locator('.tbl-wrap'))).toBeFocused();
  });

  test('(f) the list read is audit-free and once per mount: a dossier and Back read it no second time', async ({ app, mockApi, page }) => {
    useSession(mockApi, SESSION);
    const store = registerApprovalRequestStore(mockApi, { seed: batchView(REQUESTED_IDS, false) });
    await app.gotoRoute('/lead-queue');
    await expect(chip(page)).toBeVisible();
    expect(store.lists).toEqual(['open']);
    const row = await app.expandFirstLeadRow();
    await row.getByRole('link', { name: 'Open Borrower 360' }).click();
    await expect(page).toHaveURL(/\/borrower-360\//);
    await page.goBack();
    await expect(chip(page)).toBeVisible();
    expect(store.lists, 'no second list read').toEqual(['open']);
  });

  test('(i) Button loading: a grid over the same box on Confirm approval and the row Reject', async ({ app, mockApi, page }) => {
    useSession(mockApi, SESSION);
    const approveGate = new RequestGate();
    mockApi.register('POST', '/api/outreach/approve', async () => {
      await approveGate.hold();
      return approveResult(APPROVE_AUDIT_ID);
    });
    const rejects = registerRejectRecorder(mockApi, { holdFirst: 1 });
    await app.gotoRoute('/lead-queue');
    const display = (locator: Locator) => locator.evaluate((element) => getComputedStyle(element).display);
    const box = async (locator: Locator) => {
      const rect = await locator.boundingBox();
      return rect && [Math.round(rect.width), Math.round(rect.height)];
    };

    const reject = visible(page.getByTestId(`lead-reject-${REQUESTED_IDS[1]}`));
    const rejectBox = await box(reject);
    await reject.click();
    const panel = page.locator('.decision-panel').filter({ has: page.getByTestId('lead-reject-reason') });
    await panel.getByTestId('lead-reject-reason').selectOption('low_intent');
    await panel.getByTestId('lead-reject-confirm').click();
    await expect.poll(() => rejects.gate.received).toBe(true);
    await expect(reject).toHaveClass(/btn--loading/);
    expect(['grid', 'inline-grid']).toContain(await display(reject));
    expect(await box(reject)).toEqual(rejectBox);
    rejects.gate.release();

    await visible(page.getByTestId(`lead-approve-${REQUESTED_IDS[0]}`)).click();
    const confirm = page.getByTestId('lead-approve-review-confirm');
    await expect(confirm).toBeFocused();
    const confirmBox = await box(confirm);
    await confirm.click();
    await expect.poll(() => approveGate.received).toBe(true);
    await expect(confirm).toHaveClass(/btn--loading/);
    await expect(confirm).toBeFocused();
    expect(['grid', 'inline-grid']).toContain(await display(confirm));
    expect(await box(confirm)).toEqual(confirmBox);
    approveGate.release();
  });

  test('(i) Button loading: the bulk Reject of its own run computes a grid, busy, focus kept', async ({ app, mockApi, page }) => {
    useSession(mockApi, SESSION);
    const rejects = registerRejectRecorder(mockApi, { holdFirst: 1 });
    await app.gotoRoute('/lead-queue');
    await selectRequested(page);
    const bulkReject = visible(page.getByTestId('lead-bulk-reject'));
    await bulkReject.click();
    const gate = page.getByTestId('lead-bulk-reject-gate');
    await gate.getByTestId('lead-bulk-reject-reason').selectOption('low_intent');
    await gate.getByTestId('lead-bulk-reject-note').fill('Q3 sweep: no intent signal in this cohort.');
    await gate.getByTestId('lead-bulk-reject-confirm').click();
    // The canary is on the wire, held: the toolbar's Reject is its own run's.
    await expect.poll(() => rejects.bodies.length).toBe(1);
    await expect(bulkReject).toHaveClass(/btn--loading/);
    await expect(bulkReject).toHaveAttribute('aria-busy', 'true');
    await expect(bulkReject).not.toHaveAttribute('disabled');
    expect(['grid', 'inline-grid']).toContain(await bulkReject.evaluate((element) => getComputedStyle(element).display));
    rejects.gate.release();
    for (const borrowerId of REQUESTED_IDS) await expect(cell(page, borrowerId)).toContainText('Rejected');
  });
});

test.describe('the request marks and entry at 1440x900', () => {
  for (const consoleOpen of [false, true]) {
    const consoleState = consoleOpen ? 'Console open' : 'Console closed';
    test(`(k) approver, ${consoleState}: a drawn, named glyph after Reject; Approve and Reject keep their unmarked places`, async ({ app, mockApi, page }) => {
      useSession(mockApi, SESSION);
      registerApprovalRequestStore(mockApi, { seed: batchView(REQUESTED_IDS, false) });
      await app.gotoRoute('/lead-queue');
      await expect(chip(page)).toHaveText(`Approval requests · ${REQUESTED_IDS.length}`);
      if (consoleOpen) await openConsoleNarrow(app, page);
      else await app.settle();
      const mark = visible(page.getByTestId(`lead-request-mark-${REQUESTED_IDS[0]}`));
      await expect(mark).toHaveRole('img');
      await expect(mark).toHaveAccessibleName(`Requested · ${REQUESTER_LABEL}`);
      await expect(mark).not.toHaveAttribute('title');
      const { marked, reference } = await markedAndReference(page);
      for (const geometry of marked) {
        const at = `${geometry.id} (${consoleState})`;
        expect(geometry.mark?.width ?? 0, `${at}: the glyph is drawn`).toBeGreaterThan(0);
        expect(geometry.mark?.height ?? 0, `${at}: the glyph is drawn`).toBeGreaterThan(0);
        expect(geometry.mark?.right ?? Infinity, `${at}: the glyph stays in the column`).toBeLessThanOrEqual(geometry.cellRight);
        expect(geometry.rejectRight ?? Infinity, `${at}: Reject stays in the content box`).toBeLessThanOrEqual(geometry.contentRight + 0.5);
        expect(geometry.approveLeft ?? NaN, `${at}: Approve where an unmarked row has it`).toBeCloseTo(reference.approveLeft ?? NaN, 0);
        expect(geometry.rejectLeft ?? NaN, `${at}: Reject where an unmarked row has it`).toBeCloseTo(reference.rejectLeft ?? NaN, 0);
        expect(geometry.rowHeight, `${at}: the row height holds`).toBe(reference.rowHeight);
      }
      // A pointer reads the same text on the design-system tooltip (no title=).
      await mark.hover();
      await expect(page.getByRole('tooltip')).toHaveText(`Requested · ${REQUESTER_LABEL}`);
    });

    test(`(k) requester, ${consoleState}: "Approval requested" keeps its whole label at Linux text widths`, async ({ app, mockApi, page }) => {
      useSession(mockApi, NON_APPROVER_SESSION);
      registerApprovalRequestStore(mockApi, { seed: batchView(REQUESTED_IDS, true) });
      await app.gotoRoute('/lead-queue');
      await expect(cell(page, REQUESTED_IDS[0])).toHaveText('Approval requested');
      if (consoleOpen) await openConsoleNarrow(app, page);
      else await app.settle();
      if (process.platform !== 'linux') await page.addStyleTag({ content: LINUX_APPROVAL_CHIP_EMULATION });
      const { marked, reference } = await markedAndReference(page);
      for (const geometry of marked) {
        const at = `${geometry.id} (${consoleState})`;
        expect(geometry.label, `${at}: the chip is there`).not.toBeNull();
        expect(geometry.label?.client ?? 0, `${at}: the label is not clipped`).toBeGreaterThanOrEqual(geometry.label?.scroll ?? Infinity);
        expect(geometry.rowHeight, `${at}: the row height holds`).toBe(reference.rowHeight);
      }
    });
  }

  for (const [name, session] of [['non-approver', NON_APPROVER_SESSION], ['approver', SESSION]] as const) {
    test(`(l) ${name}: a failed list read is one line in the 83 px header with the Console open; Retry reads once more`, async ({ app, hygiene, mockApi, page }) => {
      hygiene.allow('console.error', /status of 500/);
      useSession(mockApi, session);
      const store = registerApprovalRequestStore(mockApi, { seed: batchView(REQUESTED_IDS, name === 'non-approver'), failLists: 1 });
      await app.gotoRoute('/lead-queue');
      const entry = visible(page.getByTestId('approval-requests-unavailable'));
      await expect(entry).toHaveRole('status');
      // The exact copy is the vitest's; here, the layout ("Approval" is spoken, not shown).
      await expect(entry).toContainText(/requests unavailable/i);
      await expect(page.getByTestId('approval-requests-chip')).toHaveCount(0);
      await expect(page.locator('.lead-table__request-mark, .lead-table__request-chip')).toHaveCount(0);
      await openConsoleNarrow(app, page);
      const header = page.locator('.surface:has(> .tbl-wrap) > .surface__hdr').first();
      expect(await header.evaluate((element) => Math.round(element.getBoundingClientRect().height)), 'the header keeps 83 px').toBeLessThanOrEqual(84);
      const fit = await entry.evaluate((element) => {
        const retry = element.querySelector('button')?.getBoundingClientRect().height ?? 0;
        const row = element.closest('.lead-table__title-row');
        return {
          height: element.getBoundingClientRect().height,
          retry,
          clipped: element.scrollWidth > element.clientWidth,
          rowOverflow: row ? row.scrollWidth - row.clientWidth : 0,
        };
      });
      expect(fit.height, 'the entry is one line').toBeLessThanOrEqual(fit.retry + 1);
      expect(fit.clipped, 'the entry is not clipped').toBe(false);
      expect(fit.rowOverflow, 'the title row does not overflow').toBeLessThanOrEqual(0);
      expect(store.lists).toHaveLength(1);
      await entry.getByRole('button', { name: 'Retry' }).click();
      await expect.poll(() => store.lists.length).toBe(2);
      await expect(chip(page)).toHaveText(name === 'approver' ? `Approval requests · ${REQUESTED_IDS.length}` : 'My requests · 1');
      await expect(page.getByTestId('approval-requests-unavailable')).toHaveCount(0);
    });
  }
});

for (const theme of FIXTURE_THEMES) {
  test.describe(`${theme}`, () => {
    test('(h) axe: the form, both panels and the scope row', async ({ app, mockApi, page }) => {
      // Two sessions and four axe scans in one test: the slow budget, not a retry.
      test.slow();
      await app.setTheme(theme);
      useSession(mockApi, NON_APPROVER_SESSION);
      registerApprovalRequestStore(mockApi, { seed: batchView(REQUESTED_IDS.slice(0, 1), true) });
      await app.gotoRoute('/lead-queue');
      for (const borrowerId of REQUESTED_IDS.slice(1)) await visible(page.getByTestId(`lead-select-${borrowerId}`)).check();
      await visible(page.getByTestId('lead-bulk-request-approval')).click();
      await expect(page.getByTestId('request-approval-form')).toBeVisible();
      const key = (state: string) => ({ route: 'lead-queue', state });
      await expectAxeClean(page, { key: key('request-approval-form'), theme, known: KNOWN_VIOLATIONS, include: '[data-testid="request-approval-form"]' });
      await page.keyboard.press('Escape');
      await chip(page).click();
      await expectAxeClean(page, { key: key('my-approval-requests'), theme, known: KNOWN_VIOLATIONS, include: '[data-testid="my-approval-requests-panel"]' });

      useSession(mockApi, SESSION);
      registerApprovalRequestStore(mockApi, { seed: batchView(REQUESTED_IDS, false) });
      await app.gotoRoute('/lead-queue');
      await chip(page).click();
      await expectAxeClean(page, { key: key('approval-requests'), theme, known: KNOWN_VIOLATIONS, include: '[data-testid="approval-requests-panel"]' });
      await page.getByTestId(`approval-request-open-${REQUEST_BATCH_ID}`).click();
      await expect(visible(page.getByTestId('approval-request-scope'))).toBeVisible();
      await expectAxeClean(page, { key: key('approval-request-scope'), theme, known: KNOWN_VIOLATIONS, include: '[data-testid="approval-request-scope"]' });
    });

    for (const [name, session] of [['non-approver', NON_APPROVER_SESSION], ['approver', SESSION]] as const) {
      test(`(j) ${name}: the header stays 83 px and eight rows clear the fold with the Console open`, async ({ app, mockApi, page }) => {
        await app.setTheme(theme);
        useSession(mockApi, session);
        registerApprovalRequestStore(mockApi, { seed: batchView(REQUESTED_IDS, name === 'non-approver') });
        await app.gotoRoute('/lead-queue');
        await expect(chip(page)).toBeVisible();
        await app.openConsole();
        await expect.poll(() => page.locator('#main-content .main__inner').first().evaluate((element) => Math.round(element.getBoundingClientRect().width)))
          .toBeLessThan(1100);
        await app.settle();
        const header = page.locator('.surface:has(> .tbl-wrap) > .surface__hdr').first();
        expect(await header.evaluate((element) => Math.round(element.getBoundingClientRect().height))).toBeLessThanOrEqual(84);
        // The W5c queue-layout pin (approver): eight rows. A non-approver keeps
        // the pre-existing role line above the table (one line, its request
        // note included), which costs one row; the request UI adds no line.
        if (name === 'non-approver') {
          const status = page.getByTestId('approver-role-status').filter({ visible: true });
          await expect(status).toContainText('Request approval sends them to an approver.');
          if (process.platform !== 'linux') await page.addStyleTag({ content: LINUX_TEXT_EMULATION });
          const fixtureFit = await roleLineFit(status);
          expect(fixtureFit.height, 'the role line stays one line').toBeLessThan(fixtureFit.twoChips);
          expect(fixtureFit.slack, 'the role line keeps its text slack').toBeGreaterThanOrEqual(ROLE_LINE_SLACK);
          await status.locator('.mono').evaluate((address, email) => {
            address.textContent = email;
          }, LONG_ACTOR_EMAIL);
          const longFit = await roleLineFit(status);
          expect(longFit.clipped, 'a long address ends in an ellipsis').toBe(true);
          expect(longFit.height, 'one line for a long address too').toBeLessThan(longFit.twoChips);
          expect(longFit.slack, 'and the same slack').toBeGreaterThanOrEqual(ROLE_LINE_SLACK);
          await expect(status, 'the DOM text keeps the whole address').toContainText(LONG_ACTOR_EMAIL);
        }
        await expect.poll(() => page.locator('tr[data-borrower-row]').evaluateAll(
          (rows) => rows.filter((row) => row.getBoundingClientRect().bottom <= window.innerHeight).length,
        )).toBeGreaterThanOrEqual(name === 'approver' ? 8 : 7);
      });
    }
  });
}
