/**
 * Rendered-layer proofs for the wave-4b lane "w4-workflow" (audit 2026-09-21
 * shell-04 / flow-09, runtime-06, delivery-08 L slice, states-03 item 2,
 * critic-04, critic-v3, a11y-05 item 4), in the production build at 1440x900:
 *
 *   1. "Build offer" from a filtered queue opens Offer with the queue pager
 *      ("2 of 3 ranked in IL"), clear of the docked decision bar with the
 *      Console closed and open.
 *   2. J / K step offer to offer and keep the queue crumb; the keys are inert
 *      inside the reject rationale and while a decision is on the wire.
 *   3. "Next in queue" appears only after the approve RETURNED, opens the next
 *      offer at "3 of 3", and at the end of the queue links the filtered queue.
 *   4. Audit parity: only visited borrowers are read, a re-visit re-reads (the
 *      approval surface writes its own VIEW_BORROWER), one /draft per open,
 *      and hovering the pager, the Next link or the nav "Offer" reads nothing.
 *   5. A retries_exhausted 503 on the borrower read, then a health down -> up
 *      flip: exactly one more borrower / recommend / lifecycle read, no /draft.
 *   6-8. Campaign setup announces clamps, restores the operator's draft after
 *      a reload (cleared by a save and by an actor change), and shows the
 *      server copy as text.
 *   9. axe-clean on those Portfolio Builder and Offer states.
 */
import type { Locator, Page } from '@playwright/test';
import type { DecisionReceipt, HealthPayload } from '../../../src/lib/apiTypes';
import { expectAxeClean, KNOWN_VIOLATIONS } from './axe';
import { BORROWERS } from './data/borrowers';
import { RequestGate, approveResult, ledgerReceipt } from './data/decisionReceipt';
import { WAREHOUSE_OUTAGE_503 } from './data/errorSurfaces';
import { portfolioCreated } from './data/feedbackGuard';
import { HEALTH_OK } from './data/shell';
import { HEALTH_WAREHOUSE_DOWN, switchHealth } from './data/warehouseResume';
import { json, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';
import { expect, test } from './test';

const MAIN = '#main-content';
const CAMPAIGN_DRAFT_KEY = 'mip.campaignDraft';

interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

async function boxOf(locator: Locator): Promise<Box> {
  const box = await locator.boundingBox();
  if (!box) throw new Error('element has no bounding box');
  return { left: box.x, top: box.y, right: box.x + box.width, bottom: box.y + box.height };
}

function overlaps(a: Box, b: Box): boolean {
  return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
}

/** The masked ids of the ranked rows, in the order the queue shows them. */
async function queueIds(page: Page): Promise<string[]> {
  const rows = page.locator('table.tbl tbody tr:not(.tbl__expand)');
  await expect(rows.first()).toBeVisible();
  const texts = await rows.allInnerTexts();
  return texts.map((text) => /B-[0-9A-Z]{13}/.exec(text)?.[0]).filter((id): id is string => Boolean(id));
}

/** Expand a queue row and follow its "Build offer" link (it carries the queue in link state). */
async function buildOfferFromQueue(page: Page, rowIndex: number): Promise<void> {
  const toggle = page.locator('table.tbl tbody [aria-expanded]').nth(rowIndex);
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await page.locator('tr.tbl__expand').getByRole('link', { name: 'Build offer' }).click();
}

const pager = (page: Page) => page.getByRole('navigation', { name: 'Lead queue position' });
const approveButton = (page: Page) => page.getByTestId('offer-action-bar').getByRole('button', { name: 'Approve outreach' });
const nextStep = (page: Page) => page.getByTestId('offer-next-in-queue');
const queueCrumb = (page: Page) =>
  page.getByRole('banner').getByRole('navigation', { name: 'Breadcrumb' }).getByRole('link', { name: 'Lead Queue · IL' });

const borrowerReads = (mockApi: MockApi) =>
  mockApi.calls
    .filter((call) => call.method === 'GET' && /^\/api(\/v1)?\/borrowers\/B-[0-9A-Z]{13}$/.test(call.path))
    .map((call) => /B-[0-9A-Z]{13}/.exec(call.path)?.[0] ?? '');
const callsTo = (mockApi: MockApi, method: string, path: RegExp) =>
  mockApi.calls.filter((call) => call.method === method && path.test(call.path)).length;
const reads = (mockApi: MockApi) => ({
  borrower: callsTo(mockApi, 'GET', /^\/api(\/v1)?\/borrowers\/B-[0-9A-Z]{13}$/),
  recommend: callsTo(mockApi, 'POST', /^\/api(\/v1)?\/offers\/recommend$/),
  lifecycle: callsTo(mockApi, 'GET', /^\/api(\/v1)?\/borrowers\/B-[0-9A-Z]{13}\/lifecycle$/),
  draft: callsTo(mockApi, 'POST', /^\/api(\/v1)?\/outreach\/draft$/),
});

/** One audit id per approved borrower, so each receipt reads back its own row. */
function auditIdFor(borrowerId: string): string {
  const index = BORROWERS.findIndex((borrower) => borrower.borrower_id === borrowerId);
  return `6f2a9c1e-3b4d-4e5f-8a6b-7c8d9e0f1a${index.toString(16).padStart(2, '0')}`;
}

/** Approve (optionally held open by `gate`) and the receipt read-back for any fixture borrower. */
function registerApprovals(mockApi: MockApi, gate: RequestGate | null = null): string[] {
  const approved: string[] = [];
  mockApi.register('POST', '/api/outreach/approve', async (request) => {
    const borrowerId = (request.body as { borrower_id?: string } | null)?.borrower_id ?? '';
    if (gate) await gate.hold();
    approved.push(borrowerId);
    return approveResult(auditIdFor(borrowerId));
  });
  mockApi.register('GET', '/api/audit/receipt/:id', ({ params }) => {
    const borrower = BORROWERS.find((candidate) => auditIdFor(candidate.borrower_id) === params.id) ?? BORROWERS[0];
    return json<DecisionReceipt>(ledgerReceipt(params.id, borrower, 'approved'));
  });
  return approved;
}

test.describe('Offer queue pager and Next in queue (shell-04 / flow-09)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: Build offer from the IL queue shows "2 of 3", clear of the decision bar with the Console closed and open`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue?state=IL');
      const ids = await queueIds(page);
      expect(ids).toHaveLength(3);
      await buildOfferFromQueue(page, 1);
      await expect(page).toHaveURL(new RegExp(`/offer-orchestrator/${ids[1]}$`));
      await app.settle();
      await expect(pager(page)).toContainText('2 of 3 ranked in IL');
      await expect(approveButton(page)).toBeEnabled();
      const bar = page.getByTestId('offer-action-bar');
      expect(overlaps(await boxOf(pager(page)), await boxOf(bar)), 'the pager runs under the decision bar').toBe(false);

      await expectAxeClean(page, { key: { route: 'offer-orchestrator-detail', state: 'queue-pager' }, theme, known: {} });

      const consolePanel = await app.openConsole();
      await expect(pager(page)).toBeVisible();
      expect(overlaps(await boxOf(pager(page)), await boxOf(bar)), 'Console open: the pager runs under the bar').toBe(false);
      expect(overlaps(await boxOf(pager(page)), await boxOf(consolePanel)), 'Console open: the pager runs under the Console').toBe(false);
    });
  }

  test('J / K step offer to offer and keep the queue crumb; the keys are inert in the rationale and while a decision is in flight', async ({ app, page, mockApi }) => {
    const gate = new RequestGate();
    registerApprovals(mockApi, gate);
    await app.gotoRoute('/lead-queue?state=IL');
    const ids = await queueIds(page);
    await buildOfferFromQueue(page, 1);
    await app.settle();
    await expect(pager(page)).toContainText('2 of 3 ranked in IL');

    await page.locator(`${MAIN} h1`).focus();
    await page.keyboard.press('j');
    await expect(page).toHaveURL(new RegExp(`/offer-orchestrator/${ids[2]}$`));
    await app.settle();
    await expect(pager(page)).toContainText('3 of 3 ranked in IL');
    await expect(queueCrumb(page)).toHaveAttribute('href', /^\/lead-queue\?state=IL(&|$)/);
    await page.locator(`${MAIN} h1`).focus();
    await page.keyboard.press('k');
    await expect(page).toHaveURL(new RegExp(`/offer-orchestrator/${ids[1]}$`));
    await app.settle();
    await expect(pager(page)).toContainText('2 of 3 ranked in IL');
    await expect(queueCrumb(page)).toHaveAttribute('href', /^\/lead-queue\?state=IL(&|$)/);

    // The open reject rationale holds the keys: even from the page heading
    // (the note is still empty, so no unsaved-changes prompt could be what
    // stops the step), J pages nowhere; inside the note it is text.
    const bar = page.getByTestId('offer-action-bar');
    await bar.getByRole('button', { name: /^Reject/ }).click();
    const form = bar.getByRole('form', { name: 'Reject rationale' });
    await expect(form).toBeVisible();
    await page.locator(`${MAIN} h1`).focus();
    await page.keyboard.press('j');
    expect(page.url(), 'J with the rationale open stays put').toMatch(new RegExp(`/offer-orchestrator/${ids[1]}$`));
    const note = form.getByRole('textbox');
    await note.press('j');
    await expect(note).toHaveValue('j');
    expect(page.url()).toMatch(new RegExp(`/offer-orchestrator/${ids[1]}$`));
    await note.fill('');
    await form.getByRole('button', { name: 'Cancel' }).click();
    await expect(form).toHaveCount(0);

    // A decision on the wire: the pager is disabled and J / K do nothing.
    await approveButton(page).click();
    await expect.poll(() => gate.received).toBe(true);
    await expect(pager(page).getByRole('button', { name: 'Next' })).toBeDisabled();
    await page.locator(`${MAIN} h1`).focus();
    await page.keyboard.press('j');
    expect(page.url(), 'J while the approve is in flight stays put').toMatch(new RegExp(`/offer-orchestrator/${ids[1]}$`));
    gate.release();
    await expect(nextStep(page)).toBeVisible();
  });

  test('Next in queue appears only after the approve returns, opens the next offer, and at the end links the filtered queue', async ({ app, page, mockApi }) => {
    const gate = new RequestGate();
    registerApprovals(mockApi, gate);
    await app.gotoRoute('/lead-queue?state=IL');
    const ids = await queueIds(page);
    await buildOfferFromQueue(page, 1);
    await app.settle();

    await approveButton(page).click();
    await expect.poll(() => gate.received, 'the approve reached the ledger').toBe(true);
    // Pessimistic: nothing of the decision, and no Next step, while it is held.
    await expect(nextStep(page)).toHaveCount(0);
    gate.release();
    const next = nextStep(page).getByRole('link', { name: `Next in queue: ${ids[2]}` });
    await expect(next).toBeVisible();
    // The receipt keeps focus: the Next step never takes it.
    await expect(next).not.toBeFocused();
    await expectAxeClean(page, { key: { route: 'offer-orchestrator-detail', state: 'next-in-queue' }, theme: 'dark', known: {} });

    await next.click();
    await expect(page).toHaveURL(new RegExp(`/offer-orchestrator/${ids[2]}$`));
    await app.settle();
    await expect(pager(page)).toContainText('3 of 3 ranked in IL');
    await expect(nextStep(page)).toHaveCount(0);

    await approveButton(page).click();
    const back = nextStep(page).getByRole('link', { name: 'Back to lead queue' });
    await expect(back).toBeVisible();
    await expect(back).toHaveAttribute('href', /^\/lead-queue\?state=IL(&|$)/);
  });

  test('reads only visited borrowers, re-reads on a re-visit, one /draft per open, and hovering reads nothing', async ({ app, page, mockApi }) => {
    registerApprovals(mockApi);
    await app.gotoRoute('/lead-queue?state=IL');
    const ids = await queueIds(page);
    const before = reads(mockApi);
    await buildOfferFromQueue(page, 1);
    await app.settle();
    await page.locator(`${MAIN} h1`).focus();
    await page.keyboard.press('j');
    await expect(page).toHaveURL(new RegExp(`/offer-orchestrator/${ids[2]}$`));
    await app.settle();
    await page.locator(`${MAIN} h1`).focus();
    await page.keyboard.press('k');
    await expect(page).toHaveURL(new RegExp(`/offer-orchestrator/${ids[1]}$`));
    await app.settle();
    // Base parity: every open re-reads its borrower (unlike the Borrower 360
    // cache), and the neighbour behind the reviewer (ids[0]) is never read.
    expect(borrowerReads(mockApi)).toEqual([ids[1], ids[2], ids[1]]);
    const after = reads(mockApi);
    expect({
      borrower: after.borrower - before.borrower,
      recommend: after.recommend - before.recommend,
      lifecycle: after.lifecycle - before.lifecycle,
      draft: after.draft - before.draft,
    }).toEqual({ borrower: 3, recommend: 3, lifecycle: 3, draft: 3 });

    const naturalLoadEnd = markNaturalLoad(mockApi);
    await pager(page).getByRole('button', { name: 'Next' }).hover();
    await pager(page).getByRole('button', { name: 'Previous' }).hover();
    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Offer', exact: true }).hover();
    await approveButton(page).click();
    await nextStep(page).getByRole('link', { name: `Next in queue: ${ids[2]}` }).hover();
    await page.waitForTimeout(500);
    expectNoAuditedReadSince(mockApi, naturalLoadEnd, 'hovering the pager, the Next link and the nav Offer');
    expect(borrowerReads(mockApi)).toEqual([ids[1], ids[2], ids[1]]);
  });

  test('a retries_exhausted 503, then a health down -> up flip: one more borrower, recommend and lifecycle read, no /draft', async ({ app, page, mockApi }) => {
    const borrower = BORROWERS[0].borrower_id;
    const health = switchHealth(mockApi, HEALTH_WAREHOUSE_DOWN);
    const restore = app.degrade(`/api/borrowers/${borrower}`, WAREHOUSE_OUTAGE_503);
    await page.goto(`/offer-orchestrator/${borrower}`, { waitUntil: 'domcontentloaded' });
    await expect(page.locator(MAIN)).toContainText("Couldn't load borrower or offer", { timeout: 30_000 });
    await expect.poll(() => mockApi.inflight).toBe(0);
    const before = reads(mockApi);
    expect(before).toEqual({ borrower: 1, recommend: 1, lifecycle: 1, draft: 1 });

    restore();
    health.set(HEALTH_OK);
    await expect(approveButton(page)).toBeEnabled({ timeout: 30_000 });
    await app.settle();
    const after = reads(mockApi);
    expect({
      borrower: after.borrower - before.borrower,
      recommend: after.recommend - before.recommend,
      lifecycle: after.lifecycle - before.lifecycle,
      draft: after.draft - before.draft,
    }).toEqual({ borrower: 1, recommend: 1, lifecycle: 1, draft: 0 });
  });
});

test.describe('campaign setup: Field, clamps and the restored draft (critic-04, critic-v3)', () => {
  const field = (page: Page, name: string) => page.getByRole('spinbutton', { name, exact: true });
  const draftChip = (page: Page) => page.getByTestId('campaign-draft-restored');
  const storedDraft = (page: Page) => page.evaluate((key) => window.sessionStorage.getItem(key), CAMPAIGN_DRAFT_KEY);

  async function commit(page: Page, name: string, value: string): Promise<void> {
    await field(page, name).click();
    await field(page, name).fill(value);
    await field(page, name).blur();
  }

  /** Reload through the unsaved-changes prompt a dirty setup raises. */
  async function reloadThroughPrompt(page: Page, app: { settle(): Promise<void> }): Promise<void> {
    const accept = (dialog: { accept(): Promise<void> }) => void dialog.accept();
    page.on('dialog', accept);
    await page.reload({ waitUntil: 'domcontentloaded' });
    page.off('dialog', accept);
    await app.settle();
  }

  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: a clamp is announced in the field and the server copy is text`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/portfolio-builder');
      await expectAxeClean(page, { key: { route: 'portfolio-builder', state: 'default' }, theme, known: KNOWN_VIOLATIONS });

      await commit(page, 'Holdout % (0-50)', '80');
      await expect(field(page, 'Holdout % (0-50)')).toHaveValue('50');
      const holdoutNotice = page.locator('.field', { has: field(page, 'Holdout % (0-50)') }).getByRole('status');
      await expect(holdoutNotice).toHaveText('Capped at 50%');
      await commit(page, 'Budget', '20000000');
      await expect(field(page, 'Budget')).toHaveValue('10000000');
      await expect(page.locator('.field', { has: field(page, 'Budget') }).getByRole('status')).toHaveText('Capped at $10,000,000');
      await expectAxeClean(page, { key: { route: 'portfolio-builder', state: 'clamp-notice' }, theme, known: {} });

      // The reviewed copy is read-only text: nothing to type into.
      const setup = page.locator(`${MAIN} .campaign-setup`);
      await expect(setup.locator('textarea')).toHaveCount(0);
      await expect(setup.locator('input[readonly]')).toHaveCount(0);
      const subject = page.getByLabel('Benefit-led subject');
      await expect(subject).toBeVisible();
      await expect(subject).toHaveJSProperty('tagName', 'DD');
    });
  }

  test('a draft comes back after a reload with the chip and Reset, and a save clears it', async ({ app, page, mockApi }) => {
    mockApi.register('POST', '/api/portfolio/create', (request) =>
      portfolioCreated(String((request.body as { name?: unknown } | null)?.name ?? '')));
    await app.gotoRoute('/portfolio-builder');
    await commit(page, 'Budget', '25000');
    await expect.poll(() => storedDraft(page)).not.toBeNull();
    const stored = JSON.parse((await storedDraft(page)) ?? '{}') as { setup?: Record<string, unknown> };
    expect(Object.keys(stored.setup ?? {}).sort()).toEqual([
      'budget', 'emailCost', 'endLocal', 'holdoutPct', 'mailCost', 'marketHouseholdTogether', 'smsCost', 'startLocal',
    ]);

    await reloadThroughPrompt(page, app);
    await expect(draftChip(page)).toContainText('Draft restored');
    await expect(field(page, 'Budget')).toHaveValue('25000');
    await expectAxeClean(page, { key: { route: 'portfolio-builder', state: 'draft-restored' }, theme: 'dark', known: {} });

    await draftChip(page).getByRole('button', { name: 'Reset restored draft' }).click();
    await expect(draftChip(page)).toHaveCount(0);
    await expect(field(page, 'Budget')).toHaveValue('');
    await expect.poll(() => storedDraft(page)).toBeNull();

    // A saved build is the new baseline: its setup is not a draft any more.
    await commit(page, 'Budget', '30000');
    await expect.poll(() => storedDraft(page)).not.toBeNull();
    await page.getByTestId('portfolio-save-build').click();
    await page.getByTestId('portfolio-save-confirm').click();
    await expect(page.getByTestId('portfolio-save-name')).toHaveCount(0);
    await expect.poll(() => storedDraft(page)).toBeNull();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await app.settle();
    await expect(draftChip(page)).toHaveCount(0);
  });

  test('an actor_cache_key change clears the draft, so the next actor restores nothing', async ({ app, page, mockApi }) => {
    let actor = 'fixture-actor-a';
    mockApi.register('GET', '/api/health', () => json<HealthPayload>({ ...HEALTH_OK, actor_cache_key: actor }));
    await app.gotoRoute('/portfolio-builder');
    await commit(page, 'Budget', '25000');
    await expect.poll(() => storedDraft(page)).not.toBeNull();

    actor = 'fixture-actor-b';
    const healthCalls = () => mockApi.calls.filter((call) => /^\/api(\/v1)?\/health$/.test(call.path)).length;
    const healthBefore = healthCalls();
    await page.clock.runFor(8_000);
    await expect.poll(healthCalls, { timeout: 15_000 }).toBeGreaterThan(healthBefore);
    await expect.poll(() => storedDraft(page)).toBeNull();

    await reloadThroughPrompt(page, app);
    await expect(draftChip(page)).toHaveCount(0);
    await expect(field(page, 'Budget')).toHaveValue('');
  });
});
