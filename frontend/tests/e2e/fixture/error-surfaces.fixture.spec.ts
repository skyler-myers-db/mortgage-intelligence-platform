/**
 * Rendered-layer proofs for the w4-error-surfaces lane (wave 4a), at
 * 1440x900 on the built app:
 *
 *   1. states-03 a: a warehouse outage the DegradedBanner already names reads
 *      as a calm "reloads when the analytics warehouse reconnects" status on
 *      Lead Queue, Borrower 360 and Segments (no alert), after exactly one
 *      request per endpoint (delivery-v2: no inner re-send), and exactly one
 *      refetch when health reports the warehouse back.
 *   2. A 403 under the same banner stays red, in the buyer-safe copy.
 *   3. states-08: a 429 with Retry-After: 12 surfaces at once and counts
 *      down; Retry is aria-disabled until the wait ends, then one click is
 *      exactly one request.
 *   4. delivery-v2: an open breaker is sent once in the first 5 s.
 *   5. states-v2: a measured zero is an EmptyState, never a table; Clear
 *      filters and a prefilled (never submitted) Ask Genie.
 *   6. states-09: FetchedAt's Refresh is one GET /leads; "Queue updated ·
 *      Refresh" follows a new version with no /leads read; a hidden page
 *      polls nothing; no audited read happens after the natural load except
 *      the explicit Refresh.
 *   7. No transport jargon or server detail text in any error state.
 *   8. axe-clean in states 1, 2, 3, 5 and 6.
 *   9. The LeadTable header keeps Export fully visible with the Console open,
 *      with FetchedAt beside the title, out of the action row.
 */
import type { Page } from '@playwright/test';
import {
  BREAKER_OPEN_503,
  FIXTURE_DETAIL_STRINGS,
  FORBIDDEN_403,
  RATE_LIMITED_429,
  TRANSPORT_JARGON,
  TX_ITM_PROMPT,
  WAREHOUSE_OUTAGE_503,
} from './data/errorSurfaces';
import { PRIMARY_BORROWER } from './data/borrowers';
import { QUEUE_VERSION_UPDATED, leadFixtures } from './data/leads';
import { HEALTH_OK } from './data/shell';
import { HEALTH_WAREHOUSE_DOWN, switchHealth } from './data/warehouseResume';
import { expectAxeClean } from './axe';
import { json, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { auditedReadsAfter } from './visual';
import { expect, test } from './test';

const MAIN = '#main-content';
const CALM = 'reloads when the analytics warehouse reconnects';
const TABLE = 'table.tbl:not([aria-hidden="true"])';

const callsTo = (mockApi: MockApi, path: string | RegExp) =>
  mockApi.calls.filter((call) => (typeof path === 'string' ? call.path === path : path.test(call.path))).length;

/** No transport jargon, no server detail string, anywhere in <main>. */
async function expectBuyerSafe(page: Page): Promise<void> {
  const text = (await page.locator(MAIN).textContent()) ?? '';
  expect(text, 'transport jargon reached the page').not.toMatch(TRANSPORT_JARGON);
  for (const detail of FIXTURE_DETAIL_STRINGS) expect(text, `server detail "${detail}" reached the page`).not.toContain(detail);
}

test.describe('a bannered warehouse outage (states-03 a)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`reads calm on Lead Queue, Borrower 360 and Segments, one request each, one refetch on recovery (${theme})`, async ({ app, mockApi, page }) => {
      await app.setTheme(theme);
      const health = switchHealth(mockApi, HEALTH_WAREHOUSE_DOWN);
      const borrowerPath = `/api/borrowers/${PRIMARY_BORROWER.borrower_id}`;
      const routes = [
        { url: '/lead-queue', endpoints: ['/api/leads'], statuses: 1 },
        { url: `/borrower-360/${PRIMARY_BORROWER.borrower_id}`, endpoints: [borrowerPath], statuses: 0 },
        { url: '/segment-intelligence', endpoints: ['/api/segments', '/api/leads'], statuses: 2 },
      ];
      for (const route of routes) {
        health.set(HEALTH_WAREHOUSE_DOWN);
        const restores = route.endpoints.map((endpoint) => app.degrade(endpoint, WAREHOUSE_OUTAGE_503));
        const before = route.endpoints.map((endpoint) => callsTo(mockApi, endpoint));
        await app.gotoRoute(route.url);

        await expect(page.locator('.degraded-banner').first()).toContainText(/Reconnecting/);
        if (route.statuses > 0) {
          await expect(page.locator(`${MAIN} [data-async-status="bannered"]`)).toHaveCount(route.statuses);
          await expect(page.locator(`${MAIN} [data-async-status="bannered"]`).first()).toContainText(CALM);
        } else {
          await expect(page.locator(MAIN)).toContainText('This dossier reloads when the analytics warehouse reconnects.');
        }
        await expect(page.locator(`${MAIN} [role="alert"]`)).toHaveCount(0);
        await expectBuyerSafe(page);
        route.endpoints.forEach((endpoint, index) => {
          expect(callsTo(mockApi, endpoint) - before[index], `${endpoint}: one request, no inner re-send`).toBe(1);
        });
        if (route.url === '/lead-queue') {
          await expectAxeClean(page, { key: { route: 'lead-queue', state: 'es-bannered-outage' }, theme, known: {} });
        }

        // The warehouse comes back; nothing is clicked from here on.
        const recovered = route.endpoints.map((endpoint) => callsTo(mockApi, endpoint));
        restores.forEach((restore) => restore());
        health.set(HEALTH_OK);
        await expect(page.locator(`${MAIN} [data-async-status="bannered"]`)).toHaveCount(0, { timeout: 30_000 });
        await expect(page.locator(MAIN)).not.toContainText(CALM, { timeout: 30_000 });
        await app.settle();
        route.endpoints.forEach((endpoint, index) => {
          expect(callsTo(mockApi, endpoint) - recovered[index], `${endpoint}: exactly one recovery refetch`).toBe(1);
        });
      }
    });
  }
});

test('a 403 under the same banner stays red, in the buyer-safe copy', async ({ app, mockApi, page }) => {
  switchHealth(mockApi, HEALTH_WAREHOUSE_DOWN);
  app.degrade('/api/leads', FORBIDDEN_403);
  await app.gotoRoute('/lead-queue');

  const alert = page.locator(`${MAIN} [role="alert"]`);
  await expect(alert).toHaveCount(1);
  await expect(alert).toContainText("Your role can't open ranked borrowers.");
  await expect(alert).toContainText('Ask an administrator for access.');
  await expect(alert.getByRole('button', { name: /Retry/ })).toHaveCount(0);
  await expect(alert.getByTestId('async-status-reference')).toHaveText('fixture-correlation-es03');
  await expect(page.locator(`${MAIN} [data-async-status="bannered"]`)).toHaveCount(0);
  await expectBuyerSafe(page);
  await expectAxeClean(page, { key: { route: 'lead-queue', state: 'es-forbidden' }, theme: 'dark', known: {} });
});

test.describe('timed waits', () => {
  test.use({ fixtureNow: null });

  test('a 429 counts its Retry-After down; Retry is inert until then, then one click is one request', async ({ app, mockApi, page }) => {
    await page.clock.install();
    const restore = app.degrade('/api/leads', RATE_LIMITED_429);
    await app.gotoRoute('/lead-queue');
    expect(callsTo(mockApi, '/api/leads'), 'a 12 s wait surfaces at once: one request').toBe(1);

    const alert = page.locator(`${MAIN} [role="alert"]`);
    await expect(alert).toContainText('Too many requests right now.');
    const wait = alert.locator('.async-status__wait');
    await expect(wait).toContainText(/Try again in (1[0-2]|[1-9]) s/);
    const retry = alert.getByRole('button', { name: 'Retry loading ranked borrowers' });
    // aria-disabled, never native disabled: it stays focusable and announced.
    await expect(retry).toHaveAttribute('aria-disabled', 'true');
    await expect(retry).not.toHaveAttribute('disabled');
    await expectBuyerSafe(page);
    await expectAxeClean(page, { key: { route: 'lead-queue', state: 'es-rate-limited' }, theme: 'dark', known: {} });

    // Playwright treats aria-disabled as not actionable; force the click through.
    await retry.click({ force: true });
    expect(callsTo(mockApi, '/api/leads'), 'an inert Retry sends nothing').toBe(1);

    await page.clock.runFor(12_000);
    await expect(retry).not.toHaveAttribute('aria-disabled', 'true');
    await expect(wait).not.toContainText('Try again in');
    restore();
    await retry.click();
    await expect(page.locator(`${TABLE} tbody tr`).first()).toBeVisible({ timeout: 30_000 });
    expect(callsTo(mockApi, '/api/leads'), 'one click, one request').toBe(2);
  });

  test('an open breaker is sent once in the first 5 s (no inner re-send)', async ({ app, mockApi, page }) => {
    await page.clock.install();
    app.degrade('/api/leads', BREAKER_OPEN_503);
    await page.goto('/lead-queue', { waitUntil: 'domcontentloaded' });
    await expect(page.locator(`${MAIN} h1`)).toBeVisible();
    await page.clock.runFor(5_000);
    expect(callsTo(mockApi, '/api/leads')).toBe(1);
    await expectBuyerSafe(page);
  });
});

/** The default GET /api/leads, answering zero rows for `?state=TX&segment=itm`. */
function zeroForTexasItm(mockApi: MockApi): void {
  const defaultLeads = leadFixtures.find((entry) => entry.method === 'GET' && entry.pattern === '/api/leads');
  if (!defaultLeads) throw new Error('the default GET /api/leads fixture is missing');
  mockApi.register('GET', '/api/leads', (request) => {
    const measuredZero = request.query.get('state') === 'TX' && request.query.get('segment') === 'itm';
    return measuredZero
      ? json([], { headers: { 'X-Total-Matching': '0', 'X-Returned-Rows': '0' } })
      : defaultLeads.handler(request);
  });
}

test.describe('a measured zero (states-v2)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`is an EmptyState with Clear filters and a prefilled Ask Genie (${theme})`, async ({ app, mockApi, page }) => {
      zeroForTexasItm(mockApi);
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue?state=TX&segment=itm');

      const empty = page.locator(`${MAIN} .empty`);
      await expect(empty).toBeVisible();
      await expect(empty).toContainText('No leads match this filter.');
      await expect(page.locator(TABLE)).toHaveCount(0);
      await expect(page.locator(`${MAIN}`)).not.toContainText('Showing 0');
      await expectAxeClean(page, { key: { route: 'lead-queue', state: 'es-measured-zero' }, theme, known: {} });

      await empty.getByRole('button', { name: 'Ask Genie about this filter selection' }).click();
      const dialog = page.getByRole('dialog', { name: 'Genie chat' });
      await expect(dialog).toBeVisible();
      await expect(dialog.getByRole('textbox', { name: 'Ask Genie' })).toHaveValue(TX_ITM_PROMPT);
      expect(callsTo(mockApi, '/api/genie/message/submit'), 'a prefill is never a submit').toBe(0);
      await page.keyboard.press('Escape');

      await empty.getByRole('button', { name: 'Clear lead queue filters' }).click();
      await expect(page).toHaveURL(/\/lead-queue$/);
      await expect(page.locator(`${TABLE} tbody tr`).first()).toBeVisible({ timeout: 30_000 });
      await expect(page.locator(`${MAIN} .empty`)).toHaveCount(0);
    });
  }

  test('a county filter never reaches an Ask Genie prompt', async ({ app, mockApi, page }) => {
    mockApi.register('GET', '/api/leads', () => json([], { headers: { 'X-Total-Matching': '0', 'X-Returned-Rows': '0' } }));
    await app.gotoRoute('/lead-queue?state=TX&segment=itm&county=48201');
    await expect(page.locator(`${MAIN} .empty`)).toBeVisible();
    await expect(page.locator(MAIN).getByRole('button', { name: /Ask Genie about this filter selection/ })).toHaveCount(0);
  });
});

test.describe('freshness and the queue version (states-09)', () => {
  test.use({ fixtureNow: null });

  test('Refresh is one GET /leads; a new version shows "Queue updated" with no /leads read; a hidden page polls nothing', async ({ app, mockApi, page }) => {
    await page.clock.install();
    await app.gotoRoute('/lead-queue');
    const naturalLoadEnd = mockApi.calls.length;
    const header = page.locator(`${MAIN} .surface__hdr:has(.lead-table__header-actions)`);
    await expect(header.getByTestId('fetched-at')).toContainText('Fetched');
    await expectAxeClean(page, { key: { route: 'lead-queue', state: 'es-fetched-at' }, theme: 'dark', known: {} });

    const leadsBefore = callsTo(mockApi, '/api/leads');
    await header.getByRole('button', { name: 'Refresh ranked borrowers' }).click();
    await app.settle();
    expect(callsTo(mockApi, '/api/leads') - leadsBefore, 'Refresh is exactly one GET /leads').toBe(1);

    // Another approver changes the queue: the next poll carries a new version.
    mockApi.register('GET', '/api/workspace/queue-version', () => json(QUEUE_VERSION_UPDATED));
    const leadsAtChange = callsTo(mockApi, '/api/leads');
    await page.clock.runFor(61_000);
    const changed = header.getByTestId('queue-updated');
    await expect(changed).toContainText('Queue updated');
    await expect(changed.getByRole('button', { name: 'Refresh ranked borrowers' })).toBeVisible();
    await expect(page.locator(`${MAIN} [role="alert"]`)).toHaveCount(0);
    expect(callsTo(mockApi, '/api/leads') - leadsAtChange, 'a version change never re-reads the leads').toBe(0);
    await expectAxeClean(page, { key: { route: 'lead-queue', state: 'es-queue-updated' }, theme: 'dark', known: {} });

    // Only the explicit Refresh was an audited read after the natural load.
    expect(auditedReadsAfter(mockApi.calls, naturalLoadEnd)).toEqual([expect.stringMatching(/^GET \/api\/leads(\?\S*)? \(VIEW_LEADS\)$/)]);

    // A hidden page polls nothing (TanStack pauses the interval).
    await page.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
      document.dispatchEvent(new Event('visibilitychange'));
    });
    const polls = callsTo(mockApi, '/api/workspace/queue-version');
    await page.clock.runFor(180_000);
    expect(callsTo(mockApi, '/api/workspace/queue-version') - polls, 'no poll while hidden').toBe(0);
  });
});

test('the LeadTable header keeps Export fully visible with the Console open, FetchedAt beside the title', async ({ app, page }) => {
  await app.gotoRoute('/lead-queue');
  await app.openConsole();
  const header = page.locator(`${MAIN} .surface__hdr:has(.lead-table__header-actions)`);
  await expect(header.getByTestId('fetched-at')).toBeVisible();
  // FetchedAt sits beside the title, so the action row keeps its width.
  await expect(page.locator(`${MAIN} .lead-table__header-actions [data-testid="fetched-at"]`)).toHaveCount(0);
  const overflow = await header.evaluate((el) => el.scrollWidth - el.clientWidth);
  expect(overflow, 'the header never overflows horizontally').toBeLessThanOrEqual(0);
  const exportButton = page.getByTestId('lead-export');
  const [exportBox, headerBox] = await Promise.all([exportButton.boundingBox(), header.boundingBox()]);
  expect(exportBox && headerBox, 'both boxes render').toBeTruthy();
  if (exportBox && headerBox) {
    expect(exportBox.x + exportBox.width, 'Export ends inside the header').toBeLessThanOrEqual(headerBox.x + headerBox.width + 0.5);
    expect(exportBox.x, 'Export starts inside the header').toBeGreaterThanOrEqual(headerBox.x - 0.5);
  }
});
