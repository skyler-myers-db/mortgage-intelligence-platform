/**
 * W5b lane w5-audit-ledger-presenter, proven on the rendered production build
 * at 1440x900 (D-audit-reads-c3, D-shell-deviations-e1, audit critic-09):
 *
 *  (a) a read-only auditor reaches /audit-ledger from the nav ('Audit', in
 *      Admin's place) and the rail, reads it with exactly one page and one
 *      rollup read (no hover, poll or focus re-read), gets the admin 403, and
 *      an old /admin-config explorer link lands on the ledger row;
 *  (b) a plain workspace user gets the ledger 403 and makes no ledger read;
 *  (c) an administrator keeps Admin in the nav (no 'Audit'), has both rail
 *      items, and Admin reads nothing from the ledger; its status row and
 *      sticky section nav move a section below both bars, focus it and mark
 *      it current; Shift+Tab onto a section's first control stops below them;
 *  (d) Data operations Run is a confirm that posts only on Start, with the
 *      chosen reason;
 *  (e) the PROTOTYPE borrower view is presenter-only and never downloaded
 *      otherwise; Deployment readiness reports the mode;
 *  (f) Admin's Audit ledger link card keeps the prototype's own-width link,
 *      and its chip-less header divider is on one line with its chip-bearing
 *      neighbours' (W5b VRT review), Console closed and open.
 */
import type { Page, Request } from '@playwright/test';
import type { SessionResponse } from '../../../src/types';
import { expectSectionSpyMarksLandings } from './adminSectionSpy';
import { expectAxeClean } from './axe';
import { PRIMARY_BORROWER } from './data/borrowers';
import { SESSION } from './data/shell';
import { json, normalizeApiPath, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const AUDITOR: SessionResponse = {
  ...SESSION,
  can_access_admin: false,
  can_approve: false,
  can_read_audit: true,
  role_labels: ['Auditor'],
};
const PLAIN: SessionResponse = {
  ...SESSION,
  can_access_admin: false,
  can_approve: true,
  can_read_audit: false,
  role_labels: ['Workspace user'],
};
const PINNED_EVENT_ID = 'evt-fixture-0000';

const calls = (mockApi: MockApi, pattern: RegExp, method = 'GET') =>
  mockApi.calls.filter((call) => call.method === method && pattern.test(normalizeApiPath(call.path)));
const ledgerReads = (mockApi: MockApi) => calls(mockApi, /^\/api\/audit\/(?!my-events|receipt)/);
const mainNav = (page: Page) => page.getByRole('navigation', { name: 'Main navigation' });
const rail = (page: Page) => page.getByRole('navigation', { name: 'Primary navigation' });

async function navHeight(page: Page): Promise<number> {
  return page.locator('.route-nav').evaluate((el) => Math.round(el.getBoundingClientRect().height));
}

/**
 * The route nav stays one 57px line with the Console closed and open. Called
 * last: the open Console's own activity list is a known axe finding of the
 * shell, not of these pages, so the page scans run with it closed.
 */
async function expectOneLineNav(page: Page, app: { openConsole(): Promise<unknown> }): Promise<void> {
  expect(await navHeight(page), 'the route nav stays one 57px line, Console closed').toBe(57);
  await app.openConsole();
  expect(await navHeight(page), 'the route nav stays one 57px line, Console open').toBe(57);
}

/**
 * The Audit ledger link card (W5b VRT review): its link is the prototype's
 * own-width `.btn` at the start of the body, not a full-width bar.
 */
async function expectOwnWidthLedgerLink(page: Page, state: string): Promise<void> {
  const link = page.locator('#audit').getByRole('link', { name: 'Open audit ledger' });
  const { linkLeft, linkWidth, bodyLeft, bodyWidth, padStart } = await link.evaluate((el) => {
    const body = el.parentElement as HTMLElement;
    const linkBox = el.getBoundingClientRect();
    const bodyBox = body.getBoundingClientRect();
    return {
      linkLeft: linkBox.left,
      linkWidth: linkBox.width,
      bodyLeft: bodyBox.left,
      bodyWidth: bodyBox.width,
      padStart: parseFloat(getComputedStyle(body).paddingInlineStart),
    };
  });
  expect(linkLeft, `${state}: the link starts at the body's content edge`).toBeCloseTo(bodyLeft + padStart, 1);
  expect(linkWidth, `${state}: the link keeps its own width (body ${bodyWidth}px)`).toBeLessThan(bodyWidth / 2);
}

/**
 * The first-row panels share one header line: the chip-less Audit ledger
 * header ends where the chip-bearing Offer rules and Data source readiness
 * headers end, so the three dividers are one line.
 */
async function expectAlignedPanelDividers(page: Page, state: string): Promise<void> {
  const headers = await page.locator('.admin-grid > .surface > .surface__hdr').evaluateAll((els) =>
    els.map((el) => ({
      panel: (el.parentElement as HTMLElement).id,
      chip: el.querySelector('.chip') !== null,
      bottom: el.getBoundingClientRect().bottom,
    })),
  );
  expect(headers.map(({ panel, chip }) => `${panel}:${chip}`), `${state}: the first row`).toEqual([
    'offer-rules:true',
    'audit:false',
    'data-sources:true',
  ]);
  for (const { panel, bottom } of headers) {
    expect(bottom, `${state}: the ${panel} divider is on the row's one header line`).toBeCloseTo(headers[0].bottom, 2);
  }
}

for (const theme of FIXTURE_THEMES) {
  test.describe(`audit ledger and presenter mode (${theme})`, () => {
    test.beforeEach(async ({ app }) => {
      await app.setTheme(theme);
    });

    test('(a) an auditor reads the ledger from the nav and rail, once, and never Admin', async ({ app, mockApi, page }) => {
      mockApi.register<SessionResponse>('GET', '/api/session', () => json(AUDITOR));
      await page.clock.install();
      await app.gotoRoute('/');

      const audit = mainNav(page).getByRole('link', { name: 'Audit', exact: true });
      await expect(audit).toHaveAttribute('href', '/audit-ledger');
      await expect(mainNav(page).getByRole('link', { name: 'Admin' })).toHaveCount(0);
      await expect(rail(page).getByRole('link', { name: 'Audit ledger' })).toHaveAttribute('href', '/audit-ledger');
      await expect(rail(page).locator('a[href="/admin-config"]')).toHaveCount(0);

      // Hover preloads the chunk only: never a ledger read.
      await audit.hover();
      await page.waitForTimeout(300);
      expect(ledgerReads(mockApi), 'hovering Audit reads nothing from the ledger').toEqual([]);

      await audit.click();
      await app.settle();
      await expect(page.locator('#main-content h1')).toHaveText('Audit ledger');
      await expect(page.locator('table[aria-label="Audit events"] tbody tr').first()).toBeVisible();
      await page.clock.runFor(30_000);
      // A focus after the reads went stale must not re-read (the focus fix).
      await page.evaluate(() => {
        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
        document.dispatchEvent(new Event('visibilitychange'));
        Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
        document.dispatchEvent(new Event('visibilitychange'));
      });
      await app.settle();
      expect(calls(mockApi, /^\/api\/audit\/events\/page$/), 'one page read').toHaveLength(1);
      expect(calls(mockApi, /^\/api\/audit\/rollups$/), 'one rollup read').toHaveLength(1);
      expect(calls(mockApi, /^\/api\/admin\//), 'an auditor reads nothing from /admin').toEqual([]);
      await expectAxeClean(page, { key: { route: 'audit-ledger', state: 'auditor' }, theme, known: {} });

      await app.gotoRoute('/admin-config');
      await expect(page.getByTestId('admin-access-denied').getByTestId('access-denied-role')).toContainText(
        'Required role: Administrator.',
      );

      await app.gotoRoute(`/admin-config?audit_event_id=${PINNED_EVENT_ID}#audit`);
      await expect(page).toHaveURL(new RegExp(`/audit-ledger\\?audit_event_id=${PINNED_EVENT_ID}#audit$`));
      await expect(page.locator('#audit').getByRole('button', { name: `Collapse audit event ${PINNED_EVENT_ID}` })).toHaveAttribute(
        'aria-expanded',
        'true',
      );
      expect(calls(mockApi, /^\/api\/admin\//)).toEqual([]);
      await expectOneLineNav(page, app);
    });

    test('(c) an admin keeps Admin in the nav; Admin reads no ledger; the section nav clears both bars', async ({ app, mockApi, page }) => {
      await app.gotoRoute('/admin-config');

      await expect(mainNav(page).getByRole('link', { name: 'Admin' })).toBeVisible();
      await expect(mainNav(page).getByRole('link', { name: 'Audit', exact: true })).toHaveCount(0);
      await expect(rail(page).getByRole('link', { name: 'Audit ledger' })).toBeVisible();
      await expect(rail(page).locator('a[href="/admin-config"]')).toBeVisible();

      const card = page.locator('#audit');
      await expect(card.getByRole('heading', { name: 'Audit ledger' })).toBeVisible();
      await expect(card.getByRole('link', { name: 'Open audit ledger' })).toHaveAttribute('href', '/audit-ledger');
      expect(ledgerReads(mockApi), 'opening Admin reads nothing from the ledger').toEqual([]);

      const status = page.getByRole('list', { name: 'Administration status' });
      await expect(status).toContainText('Offer rules: active');
      await expect(status).toContainText('Presenter mode: Off');
      const sections = page.getByRole('navigation', { name: 'Administration sections' });
      await expect(sections.getByRole('link')).toHaveCount(11);

      // Scroll-spy (critic-09 fix round): Offer rules and Data estate scrolled
      // to the landing line, then Audit ledger, Data sources, Data estate,
      // Offer rules and Appearance followed, each marked aria-current.
      await expectSectionSpyMarksLandings(page);

      await sections.getByRole('link', { name: 'Data operations' }).click();
      const operations = page.locator('#data-operations');
      await expect(operations).toBeFocused();
      await expect(sections.getByRole('link', { name: 'Data operations' })).toHaveAttribute('aria-current', 'location');
      const navBottom = await sections.evaluate((el) => el.getBoundingClientRect().bottom);
      await expect
        .poll(() => operations.evaluate((el) => Math.round(el.getBoundingClientRect().top)), 'the section starts below both sticky bars')
        .toBeGreaterThanOrEqual(Math.floor(navBottom));

      // Shift+Tab onto a section's first control from below: it stops below the section nav.
      for (const control of [
        page.locator('#offer-rules button').first(),
        card.getByRole('link', { name: 'Open audit ledger' }),
        operations.getByRole('button', { name: 'Run', exact: true }).first(),
      ]) {
        await control.focus();
        await page.keyboard.press('Tab');
        await page.locator('.main').evaluate((main) => main.scrollTo({ top: main.scrollHeight }));
        await page.keyboard.press('Shift+Tab');
        await expect(control).toBeFocused();
        const top = await control.evaluate((el) => el.getBoundingClientRect().top);
        const bottom = await sections.evaluate((el) => el.getBoundingClientRect().bottom);
        expect(top, 'the focused control is not under the section nav').toBeGreaterThanOrEqual(bottom);
      }

      await expectAxeClean(page, { key: { route: 'admin-config', state: 'section-nav' }, theme, known: {} });
      await expectOneLineNav(page, app);
    });

    test('(f) the Audit ledger link card keeps an own-width link and the row\'s header line, Console closed and open', async ({ app, page }) => {
      await app.gotoRoute('/admin-config');
      await expect(page.locator('#audit').getByRole('heading', { name: 'Audit ledger' })).toBeVisible();
      await expectOwnWidthLedgerLink(page, 'Console closed');
      await expectAlignedPanelDividers(page, 'Console closed');
      await app.openConsole();
      await app.settle();
      await expectOwnWidthLedgerLink(page, 'Console open');
      await expectAlignedPanelDividers(page, 'Console open');
    });

    test('(d) Run opens a confirm; only Start posts, with the chosen reason', async ({ app, mockApi, page }) => {
      const posted: unknown[] = [];
      mockApi.register('POST', '/api/admin/operations/run', ({ body }) => {
        posted.push(body);
        return json({
          accepted: true,
          key: 'fred_rates',
          label: 'FRED rate ingest',
          job_name: 'mip_fred_rates',
          job_id: 1001,
          run_id: 6001,
          run_page_url: null,
          audit_event_id: '3b7c9f2e-5a41-4d8e-9c06-2f1a8b7d4e91',
        });
      });
      await app.gotoRoute('/admin-config#data-operations');
      const run = page.locator('#data-operations').getByRole('button', { name: 'Run', exact: true }).first();
      await run.click();

      const dialog = page.locator('dialog.admin-run-dialog');
      await expect(dialog).toHaveAttribute('open', '');
      await expect(dialog.getByText('Run FRED rate ingest?')).toBeVisible();
      await expect(dialog).toContainText('mip_fred_rates');
      await expect(dialog).toContainText('Run 5001');
      await expect(dialog).toContainText('Updates the weekly 30-year rate in silver');
      const start = dialog.getByRole('button', { name: 'Start FRED rate ingest' });
      await expect(start).toBeDisabled();
      await expect(dialog.getByRole('button', { name: 'Cancel' })).toBeFocused();
      await expectAxeClean(page, { key: { route: 'admin-config', state: 'run-dialog-open' }, theme, known: {} });

      await page.keyboard.press('Escape');
      await expect(dialog).toHaveCount(0);
      await expect(run).toBeFocused();
      expect(posted, 'Escape posts nothing').toEqual([]);

      await run.click();
      await dialog.getByRole('radio', { name: 'Source data was updated' }).check();
      await expect(start).toBeEnabled();
      await start.click();
      await expect(dialog).toHaveCount(0);
      await expect(page.locator('#data-operations')).toContainText('started');
      expect(posted).toHaveLength(1);
      expect(posted[0]).toMatchObject({ job_key: 'fred_rates', confirm: true, reason: 'source_update' });
    });
  });
}

test('(b) a plain workspace user gets the ledger 403 and makes no ledger read', async ({ app, mockApi, page }) => {
  mockApi.register<SessionResponse>('GET', '/api/session', () => json(PLAIN));
  await app.gotoRoute('/audit-ledger');

  const denied = page.getByTestId('audit-ledger-access-denied');
  await expect(denied.getByTestId('access-denied-role')).toContainText('Required role: Administrator or Auditor.');
  await expect(mainNav(page).getByRole('link', { name: 'Audit', exact: true })).toHaveCount(0);
  await expect(rail(page).getByRole('link', { name: 'Audit ledger' })).toHaveCount(0);
  expect(ledgerReads(mockApi)).toEqual([]);
  await expectAxeClean(page, { key: { route: 'audit-ledger', state: 'denied' }, theme: 'dark', known: {} });
});

test('(e) the PROTOTYPE borrower view is presenter-only and never downloaded otherwise', async ({ app, mockApi, page }) => {
  const mockChunks: string[] = [];
  page.on('request', (request: Request) => {
    if (/BorrowerOfferPreviewMock/.test(request.url())) mockChunks.push(request.url());
  });
  const offer = `/offer-orchestrator/${PRIMARY_BORROWER.borrower_id}`;
  await app.gotoRoute(offer);
  await expect(page.locator('#main-content h1')).toBeVisible();
  await expect(page.getByTestId('preview-borrower-offer')).toHaveCount(0);
  await app.gotoRoute('/admin-config#buyer-readiness');
  const readiness = page.locator('#buyer-readiness');
  await expect(readiness.getByRole('heading', { name: 'Deployment readiness' })).toBeVisible();
  await expect(readiness.locator('.admin-rollup', { hasText: 'Presenter mode' })).toContainText('Off');
  expect(mockChunks, 'customer mode never downloads the mock').toEqual([]);

  mockApi.register<SessionResponse>('GET', '/api/session', () => json({ ...SESSION, presenter_mode: true }));
  await page.reload({ waitUntil: 'domcontentloaded' });
  await app.settle();
  await expect(page.locator('#buyer-readiness .admin-rollup', { hasText: 'Presenter mode' })).toContainText('On');
  await app.gotoRoute(offer);
  await page.getByTestId('preview-borrower-offer').click();
  await expect(page.getByTestId('borrower-offer-mock')).toBeVisible();
  expect(mockChunks.length, 'presenter mode loads the mock on open').toBeGreaterThan(0);
});
