/**
 * Rendered-layer proofs for wave-0 route continuity (audit shell-03,
 * stack-03, critic-v1, a11y-03, shell-08, bundle-01, states-03): the
 * persistent `.main` scroller, per-route titles, focus and announcement, the
 * new-version notice, and the recovery refetch on the health poll's
 * down → up edge.
 */
import type { Page } from '@playwright/test';
import type { HealthPayload } from '../../../src/lib/apiTypes';
import { PRIMARY_BORROWER } from './data/borrowers';
import { HEALTH_OK } from './data/shell';
import { expect, test } from './test';

const PRODUCT = 'Mortgage Intelligence Platform';

function mainScrollTop(page: Page): Promise<number> {
  return page.evaluate(() => document.querySelector<HTMLElement>('main.main')!.scrollTop);
}

async function scrollMainTo(page: Page, top: number): Promise<number> {
  return page.evaluate((y) => {
    const main = document.querySelector<HTMLElement>('main.main')!;
    main.scrollTop = y;
    return main.scrollTop;
  }, top);
}

function navLink(page: Page, name: string) {
  return page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name });
}

test.describe('the persistent .main scroller', () => {
  test('resets on a new route, restores on Back, and lands a #hash target under the nav', async ({ app, page }) => {
    await app.gotoRoute('/');
    // 400, not 600: the home-answer lane pulled the map above the fold and
    // Home now scrolls about 570px at 1440x900.
    const scrolled = await scrollMainTo(page, 400);
    expect(scrolled, 'Home is tall enough to scroll to 400').toBe(400);

    await navLink(page, 'Analytics').click();
    await expect(page.locator('#main-content h1')).toHaveText('Analytics');
    await app.settle();
    expect(await mainScrollTop(page), 'a new pathname starts at the top').toBe(0);

    await page.goBack();
    await expect(page).toHaveURL(/\/$/);
    await app.settle();
    await expect.poll(() => mainScrollTop(page), 'Back restores the saved offset').toBeGreaterThanOrEqual(398);
    expect(await mainScrollTop(page)).toBeLessThanOrEqual(402);

    // A glossary term link is a client-side navigation with a #hash: the
    // entry scrolls under the sticky route nav and takes focus.
    await app.gotoRoute(`/borrower-360/${PRIMARY_BORROWER.borrower_id}`);
    const ltvTerm = page.locator('#main-content a.glossary-term[href="/glossary#ltv"]').first();
    await ltvTerm.scrollIntoViewIfNeeded();
    await ltvTerm.click();
    await expect(page).toHaveURL(/\/glossary#ltv$/);
    const entry = page.locator('#ltv');
    await expect(entry).toBeVisible();
    await expect.poll(() => mainScrollTop(page), 'the scroller moved to the entry').toBeGreaterThan(0);
    const geometry = await page.evaluate(() => {
      const main = document.querySelector<HTMLElement>('main.main')!.getBoundingClientRect();
      const nav = document.querySelector<HTMLElement>('.route-nav')!.getBoundingClientRect();
      const target = document.getElementById('ltv')!.getBoundingClientRect();
      return { mainTop: main.top, mainBottom: main.bottom, navBottom: nav.bottom, targetTop: target.top };
    });
    expect(geometry.targetTop).toBeGreaterThanOrEqual(geometry.navBottom - 1);
    expect(geometry.targetTop).toBeLessThan(geometry.mainBottom);
    await expect(entry).toBeFocused();
  });
});

test.describe('titles, focus and the route announcer', () => {
  test('every route names itself in the tab title, moves focus to its heading and speaks its name', async ({ app, page }) => {
    await app.gotoRoute('/');
    await expect(page).toHaveTitle(`Home · ${PRODUCT}`);

    await navLink(page, 'Leads').click();
    await expect(page).toHaveTitle(`Lead Queue · ${PRODUCT}`);
    await app.settle();
    const announcer = page.locator('[data-route-announcer]');
    await expect(announcer).toHaveText('Lead Queue');
    await expect(page.locator('#main-content h1')).toBeFocused();

    await navLink(page, 'Analytics').click();
    await expect(page).toHaveTitle(`Analytics · ${PRODUCT}`);
    await expect(announcer).toHaveText('Analytics');
    await expect(page.locator('#main-content h1')).toBeFocused();

    // Detail routes carry the masked id; the page name comes from routeMeta,
    // not from the hero question the route renders as its <h1>.
    await app.gotoRoute(`/borrower-360/${PRIMARY_BORROWER.borrower_id}`);
    await expect(page).toHaveTitle(`Borrower 360 · ${PRIMARY_BORROWER.borrower_id} · ${PRODUCT}`);
    await app.gotoRoute(`/offer-orchestrator/${PRIMARY_BORROWER.borrower_id}`);
    await expect(page).toHaveTitle(`Offer Orchestrator · ${PRIMARY_BORROWER.borrower_id} · ${PRODUCT}`);
    await app.gotoRoute('/this-route-does-not-exist');
    await expect(page).toHaveTitle(`Page not found · ${PRODUCT}`);
  });
});

test.describe('new-version notice', () => {
  test('appears with a Reload button once the health poll reports a different git_sha', async ({ app, mockApi, page }) => {
    let polls = 0;
    mockApi.register<HealthPayload>('GET', '/api/health', () => {
      polls += 1;
      return { body: { ...HEALTH_OK, git_sha: polls < 2 ? 'a1b2c3d4' : 'e5f6a7b8' } };
    });
    await app.gotoRoute('/glossary');
    const notice = page.locator('.degraded-banner--info');
    await expect(notice).toHaveCount(0);
    // The next poll (8 s at the healthy cadence) reports the new build.
    await expect(notice).toBeVisible({ timeout: 20_000 });
    await expect(notice).toContainText('A new version is available');
    await expect(notice.getByRole('button', { name: 'Reload' })).toBeVisible();
    expect(polls).toBeGreaterThanOrEqual(2);
  });

  test('never appears while the sha is missing or empty', async ({ app, mockApi, page }) => {
    let polls = 0;
    mockApi.register<HealthPayload>('GET', '/api/health', () => {
      polls += 1;
      // A bare deploy reports no sha at all; a later build reports an empty one.
      const body: HealthPayload = polls < 2 ? { ...HEALTH_OK } : { ...HEALTH_OK, git_sha: '' };
      return { body };
    });
    await app.gotoRoute('/glossary');
    // A third poll proves the second (empty-sha) reply was processed. At the
    // 8 s healthy cadence it lands at ~16 s; the budget leaves room for load.
    await expect.poll(() => polls, { timeout: 30_000 }).toBeGreaterThanOrEqual(3);
    await expect(page.locator('.degraded-banner--info')).toHaveCount(0);
  });
});

test.describe('recovery refetch', () => {
  test('a panel that failed on a warehouse outage refetches by itself when health reports the warehouse back', async ({ app, mockApi, page }) => {
    let warehouse: 'down' | 'up' = 'down';
    mockApi.register<HealthPayload>('GET', '/api/health', () => ({
      body:
        warehouse === 'down'
          ? { ...HEALTH_OK, status: 'degraded', dependencies: { warehouse: 'down', lakebase: 'up', genie: 'up' } }
          : HEALTH_OK,
    }));
    // The backend's own retryable 503 for a warehouse whose retry budget is
    // spent: the client surfaces the failure instead of a warming loop.
    const restoreLeads = app.degrade('/api/leads', {
      status: 503,
      body: {
        detail: 'SQL warehouse unavailable (fixture degraded state).',
        retryable: true,
        dependency: 'warehouse',
        reason: 'retries_exhausted',
        correlation_id: 'fixture-correlation-0002',
      },
    });

    await app.gotoRoute('/lead-queue');
    const banner = page.locator('.degraded-banner').first();
    await expect(banner).toContainText(/Reconnecting/);
    await expect(page.locator('table.tbl')).toHaveCount(0);
    const failedReads = mockApi.calls.filter((call) => call.path === '/api/leads' && call.status === 503).length;
    expect(failedReads).toBeGreaterThan(0);

    // The warehouse comes back. Nothing is clicked from here on.
    restoreLeads();
    warehouse = 'up';
    await expect(page.locator('table.tbl tbody tr').first(), 'the queue refetched on the down → up edge').toBeVisible({
      timeout: 30_000,
    });
    await expect(page.locator('.degraded-banner:not(.degraded-banner--info)')).toHaveCount(0);
    const okReads = mockApi.calls.filter((call) => call.path === '/api/leads' && call.status === 200).length;
    expect(okReads).toBeGreaterThan(0);
  });
});
