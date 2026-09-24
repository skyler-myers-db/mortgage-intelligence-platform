/**
 * Rendered-layer proofs for the wave-1c session-recovery lane (audit
 * states-02, critic-v2, shell-v1, states-10), at 1440x900.
 *
 * The failure shapes are the ones captured on the live Databricks Apps proxy
 * with no session (2026-09-23): `/api/*` answers 401 with a `{}` JSON body.
 * An unreachable app is every `/api/*` request aborted; offline is
 * `context.setOffline(true)`.
 *
 * Time: the harness freezes `Date` and leaves timers running; `clock.runFor`
 * fast-forwards them deterministically (no wall-clock sleeps), which is how
 * "nothing retries" and "the second probe" are proven. The session tests
 * leave the health probe answering, so their own click, not the shell's
 * eight-second poll, is what meets the first 401 (under load the poll could
 * otherwise open the blocking dialog before the click). The multi-phase tests
 * (failure, then recovery) are marked slow: they chain two page states.
 */
import AxeBuilder from '@axe-core/playwright';
import type { Locator, Page, Route } from '@playwright/test';
import { PRIMARY_BORROWER } from './data/borrowers';
import {
  EVERY_API_PATH,
  EVERY_API_PATH_BUT_HEALTH,
  PROXY_SESSION_EXPIRED,
  PROXY_SIGN_IN_REDIRECT,
  holdDefaultFixture,
} from './data/sessionRecovery';
import { WAREHOUSE_WARMING_UP, type ApiCall } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
/** Longer than three healthy health-poll intervals (8 s) and a full warming-retry budget. */
const QUIET_WINDOW_MS = 30_000;
/** HealthProvider's healthy cadence (8 s): one poll is due within this window. */
const HEALTHY_POLL_MS = 8_000;
/** Matches `.skeleton::after`'s computed animation name while it sweeps. */
const SWEEP = 'skeleton-sweep';

function sessionDialog(page: Page): Locator {
  return page.getByRole('alertdialog', { name: 'Your session ended' });
}

function navLink(page: Page, name: string): Locator {
  return page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name });
}

function key(call: ApiCall): string {
  return `${call.method} ${call.path}${call.search ? `?${call.search}` : ''}`;
}

/** The computed animation of a skeleton's shimmer band, and of the block itself. */
async function shimmer(skeleton: Locator): Promise<{ band: string; block: string }> {
  return skeleton.evaluate((el) => ({
    band: getComputedStyle(el, '::after').animationName,
    block: getComputedStyle(el).animationName,
  }));
}

test.describe('an ended session (the proxy answers 401 {})', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`opens ONE blocking session dialog with focus on Reload, and no request retries (${theme})`, async ({ app, page, mockApi }) => {
      test.slow();
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      const before = mockApi.calls.length;
      app.degrade(EVERY_API_PATH_BUT_HEALTH, PROXY_SESSION_EXPIRED);

      // An uncached route: its reads are the first to meet the 401.
      await navLink(page, 'Analytics').click();
      const dialog = sessionDialog(page);
      await expect(dialog).toBeVisible();
      await expect(page.locator('dialog.session-dialog')).toHaveCount(1);
      await expect(dialog).toContainText('Reload to sign in.');
      await expect(dialog.locator('[data-session-unrecorded]'), 'nothing was being recorded').toHaveCount(0);
      const reload = dialog.getByRole('button', { name: 'Reload' });
      await expect(reload).toBeFocused();

      // Blocking: Tab stays inside, Escape does not dismiss it, the page
      // behind is inert, and no connection banner competes with it.
      await page.keyboard.press('Tab');
      await expect(reload).toBeFocused();
      await page.keyboard.press('Shift+Tab');
      await expect(reload).toBeFocused();
      await page.keyboard.press('Escape');
      await expect(dialog).toBeVisible();
      await expect(reload).toBeFocused();
      expect(
        await page.locator('dialog.session-dialog').evaluate((el) => el.matches(':modal')),
        'opened with showModal(), so the page behind is inert',
      ).toBe(true);
      await expect(page.locator('.degraded-banner')).toHaveCount(0);

      // No retry storm: every 401'd request was sent once, and once the
      // requests already in flight have landed, nothing at all (the still
      // healthy health poll included) reaches the proxy again.
      await expect.poll(() => mockApi.inflight === 0 && mockApi.idleMs >= 300).toBe(true);
      const expired = mockApi.calls.slice(before).filter((call) => call.status === 401);
      expect(expired.length, 'the route really met the 401').toBeGreaterThan(0);
      const atOpen = mockApi.calls.length;
      await page.clock.runFor(QUIET_WINDOW_MS);
      expect(mockApi.calls.length, 'no request of any kind after the session ended').toBe(atOpen);
      const perRequest = new Map<string, number>();
      for (const call of expired) perRequest.set(key(call), (perRequest.get(key(call)) ?? 0) + 1);
      expect([...perRequest.entries()].filter(([, count]) => count > 1), 'no request was retried').toEqual([]);

      const axe = await new AxeBuilder({ page }).include('dialog.session-dialog').withTags(WCAG_TAGS).analyze();
      expect(axe.violations.map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(' ')).join(', ')}`)).toEqual([]);
    });
  }

  test('an idle page learns from its own health poll: 401 {} on EVERY /api path opens the dialog with no click', async ({ app, page, mockApi }) => {
    test.slow();
    await app.gotoRoute('/lead-queue');
    const before = mockApi.calls.length;
    app.degrade(EVERY_API_PATH, PROXY_SESSION_EXPIRED);

    // Nobody touches the page: the shell's eight-second health poll is what
    // meets the ended session (api.health's probe mapping records it).
    await page.clock.runFor(HEALTHY_POLL_MS);
    const dialog = sessionDialog(page);
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Reload' })).toBeFocused();
    await expect(dialog.locator('[data-session-unrecorded]')).toHaveCount(0);
    await expect(page.locator('.degraded-banner')).toHaveCount(0);

    await expect.poll(() => mockApi.inflight === 0 && mockApi.idleMs >= 300).toBe(true);
    const atOpen = mockApi.calls.length;
    // The poll carries the keep-warm idle hint (delivery-v1); Date is frozen here, so it reads 0.
    expect(mockApi.calls.slice(before).map(key), 'only the poll met the 401').toEqual(['GET /api/health?idle_s=0']);
    await page.clock.runFor(QUIET_WINDOW_MS);
    expect(mockApi.calls.length, 'the poll stops for good once the session ended').toBe(atOpen);
  });

  test('a sign-in REDIRECT on every /api path is confirmed by ONE probe and never followed cross-origin', async ({ app, page, mockApi }) => {
    test.slow();
    await app.gotoRoute('/lead-queue');
    const before = mockApi.calls.length;
    // The proxy's answer to an unauthenticated page request (captured
    // 2026-09-23): a 302 to the workspace's OIDC authorize URL. Following it
    // would be a cross-origin request the production CSP blocks, which the
    // hygiene gate reports as a violation, so a green run proves it was not.
    app.degrade(EVERY_API_PATH, PROXY_SIGN_IN_REDIRECT);

    await page.clock.runFor(HEALTHY_POLL_MS);
    await expect(sessionDialog(page)).toBeVisible();
    await expect.poll(() => mockApi.inflight === 0 && mockApi.idleMs >= 300).toBe(true);
    // The poll's own request came back as an opaque redirect (it could be a
    // trailing-slash 307), so ONE manual-redirect probe decided it.
    expect(mockApi.calls.slice(before).map((call) => `${key(call)} ${call.status}`)).toEqual([
      'GET /api/health?idle_s=0 302',
      'GET /api/health 302',
    ]);
    const atOpen = mockApi.calls.length;
    await page.clock.runFor(QUIET_WINDOW_MS);
    expect(mockApi.calls.length).toBe(atOpen);
  });

  test('an approval in flight when the session ends says it was NOT recorded, and the row never reads Approved', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    const id = PRIMARY_BORROWER.borrower_id;
    const cell = page.getByTestId(`lead-approval-cell-${id}`);
    const approve = cell.getByRole('button', { name: `Approve ${id}` });
    await expect(approve).toBeVisible();
    // Approve opens the review first (flow-03, wave 1c): the draft is read
    // while the session is live, and the session ends before Confirm sends
    // the approval itself.
    await approve.click();
    const review = page.getByTestId('lead-approve-review');
    await expect(review.getByTestId('lead-approve-review-subject')).not.toBeEmpty();
    app.degrade(EVERY_API_PATH_BUT_HEALTH, PROXY_SESSION_EXPIRED);

    await review.getByTestId('lead-approve-review-confirm').click();
    const dialog = sessionDialog(page);
    await expect(dialog).toBeVisible();
    await expect(dialog.locator('[data-session-unrecorded="approval"]')).toHaveText(
      'Your approval was not recorded. Approve it again after you sign in.',
    );
    await expect(cell).not.toContainText('Approved');
  });

  test('opening an Offer page after the session ended says NOTHING was lost: its automatic draft load is not an approval', async ({ app, page, mockApi }) => {
    test.slow();
    await app.gotoRoute('/lead-queue');
    const before = mockApi.calls.length;
    app.degrade(EVERY_API_PATH_BUT_HEALTH, PROXY_SESSION_EXPIRED);

    // Client-side, the way an in-app link gets there, so it is the same
    // document (and session store) that meets the ended session. The Offer
    // page loads its outreach draft on open, with no click.
    await page.evaluate((path) => {
      window.history.pushState(null, '', path);
      window.dispatchEvent(new PopStateEvent('popstate'));
    }, `/offer-orchestrator/${PRIMARY_BORROWER.borrower_id}`);

    const dialog = sessionDialog(page);
    await expect(dialog).toBeVisible();
    // Non-vacuity: the automatic draft POST really went out and met the 401.
    await expect
      .poll(() => mockApi.calls.slice(before).some((call) => call.method === 'POST' && call.path === '/api/outreach/draft' && call.status === 401))
      .toBe(true);
    await expect.poll(() => mockApi.inflight === 0 && mockApi.idleMs >= 300).toBe(true);
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(dialog.locator('[data-session-unrecorded]'), 'the user approved nothing').toHaveCount(0);
    await expect(dialog).not.toContainText('not recorded');
  });

  test('Reload keeps the page the user was on', async ({ app, page }) => {
    test.slow();
    await app.gotoRoute('/analytics?view=geography');
    app.degrade(EVERY_API_PATH_BUT_HEALTH, PROXY_SESSION_EXPIRED);
    await page.getByRole('tab', { name: 'Economics' }).click();
    const dialog = sessionDialog(page);
    await expect(dialog).toBeVisible();
    const url = page.url();

    const reloaded = page.waitForEvent('load');
    await dialog.getByRole('button', { name: 'Reload' }).click();
    await reloaded;
    expect(page.url()).toBe(url);
    // The proxy still has no session, so the fresh document asks again.
    await expect(sessionDialog(page)).toBeVisible();
  });
});

test.describe('an unreachable app (every /api request aborted)', () => {
  test('shows "Connection lost" with Reload only after the SECOND failed probe, then recovers on its own', async ({ app, page, hygiene }) => {
    test.slow();
    // The aborted requests are this test's own doing.
    hygiene.allow('request-failed', /\/api\/v1\/\S* failed: net::ERR_FAILED/);
    hygiene.allow('console.error', /Failed to load resource: net::ERR_FAILED \(http:\/\/[^)]+\/api\/v1\//);
    await app.gotoRoute('/analytics');

    let probes = 0;
    const abort = async (route: Route) => {
      if (new URL(route.request().url()).pathname.endsWith('/health')) probes += 1;
      await route.abort('failed');
    };
    await page.route('**/api/**', abort);

    // A read that cannot reach the server makes the shell probe at once.
    await page.getByRole('tab', { name: 'Geography' }).click();
    const banner = page.locator('.degraded-banner[data-connection="unreachable"]');
    await expect.poll(() => probes).toBeGreaterThanOrEqual(1);
    await expect(banner, 'one failed probe is a blip, not an outage').toHaveCount(0);

    await page.clock.runFor(3_000);
    await expect.poll(() => probes).toBeGreaterThanOrEqual(2);
    await expect(banner).toBeVisible();
    await expect(banner).toContainText('Connection lost');
    await expect(banner.getByRole('button', { name: 'Reload' })).toBeVisible();
    await expect(sessionDialog(page)).toHaveCount(0);
    // The failed panel speaks plainly, never the browser's transport string.
    await expect(page.locator('#main-content')).toContainText('The app could not be reached.');
    await expect(page.locator('#main-content')).not.toContainText('Failed to fetch');

    await page.unroute('**/api/**', abort);
    await page.clock.runFor(3_000);
    await expect(banner).toHaveCount(0);
    // The panel that could not reach the server reloads on its own.
    await expect(page.getByRole('heading', { name: 'Opportunity by State' })).toBeVisible();
  });
});

test.describe('offline', () => {
  // Real motion, so "no shimmer" is not vacuous (the harness default is reduce).
  test.use({ contextOptions: { reducedMotion: 'no-preference' }, traceScreenshots: false });

  test('shows the offline banner, pauses queries and stops the skeleton shimmer, then resumes', async ({ app, page, mockApi, context }) => {
    test.slow();
    await app.gotoRoute('/analytics');
    const skeleton = page.locator('[data-analytics-skeleton]');

    // Control, online: a held read shows the skeleton WITH the transform sweep
    // (the block itself no longer animates its background).
    const held = holdDefaultFixture(mockApi, 'GET', '/api/analytics/geography');
    await page.getByRole('tab', { name: 'Geography' }).click();
    await expect(skeleton).toBeVisible();
    await expect(skeleton).toHaveAttribute('aria-busy', 'true');
    expect(await shimmer(skeleton.locator('.skeleton').first())).toEqual({ band: SWEEP, block: 'none' });
    held.release();
    await expect(skeleton).toHaveCount(0);

    await context.setOffline(true);
    const banner = page.locator('.degraded-banner[data-connection="offline"]');
    await expect(banner).toBeVisible();
    await expect(banner).toContainText('You are offline');
    await expect(banner.getByRole('button')).toHaveCount(0);

    const economicsCalls = () => mockApi.calls.filter((call) => call.path === '/api/analytics/economics').length;
    await page.getByRole('tab', { name: 'Economics' }).click();
    await expect(skeleton).toBeVisible();
    await expect(skeleton).toContainText('Waiting for a connection');
    await expect(skeleton).toHaveAttribute('aria-busy', 'false');
    const bands = await skeleton.locator('.skeleton').evaluateAll((els) =>
      els.map((el) => getComputedStyle(el, '::after').animationName),
    );
    expect(bands.length).toBeGreaterThan(0);
    expect(new Set(bands), 'no shimmer while the query is paused').toEqual(new Set(['none']));
    expect(economicsCalls(), 'the paused query never fired').toBe(0);

    await context.setOffline(false);
    await expect(banner).toHaveCount(0);
    await expect(skeleton).toHaveCount(0);
    expect(economicsCalls(), 'it fired once the network returned').toBe(1);
  });
});

test.describe('a change attempted offline', () => {
  test('an approval confirmed while offline says it was not recorded and to reconnect, never that it "will load"', async ({ app, page, context, hygiene }) => {
    // The approve request Confirm makes cannot leave the browser: that
    // failure is this test's own doing, and nothing else may fail.
    hygiene.allow('request-failed', /^POST \S+\/api\/v1\/outreach\/approve\S* failed: net::ERR_INTERNET_DISCONNECTED$/);
    hygiene.allow('console.error', /Failed to load resource: net::ERR_INTERNET_DISCONNECTED \(http:\/\/[^)]+\/api\/v1\/outreach\/approve[^)]*\)/);
    await app.gotoRoute('/lead-queue');
    const id = PRIMARY_BORROWER.borrower_id;
    const cell = page.getByTestId(`lead-approval-cell-${id}`);
    const approve = cell.getByRole('button', { name: `Approve ${id}` });
    await expect(approve).toBeVisible();
    // Approve opens the review first (flow-03, wave 1c): the review and its
    // draft load while online; the network drops before Confirm.
    await approve.click();
    const review = page.getByTestId('lead-approve-review');
    await expect(review.getByTestId('lead-approve-review-subject')).not.toBeEmpty();

    await context.setOffline(true);
    // The mock API answers through page.route, which offline emulation does
    // not reach, so the approve fails the way a real offline fetch does.
    await page.route('**/api/v1/outreach/approve**', (route) => route.abort('internetdisconnected'));
    await expect(page.locator('.degraded-banner[data-connection="offline"]')).toContainText(
      'Approvals and other changes are not recorded while you are offline.',
    );
    await review.getByTestId('lead-approve-review-confirm').click();
    // A write that failed offline is never replayed on reconnect, so its
    // error must not borrow a read's "this will load" promise.
    const error = page.locator('.table-error[role="alert"]');
    await expect(error).toHaveText(`Couldn't approve ${id}: You are offline. Reconnect, then try again.`);
    await expect(cell).not.toContainText('Approved');
    await expect(sessionDialog(page)).toHaveCount(0);
  });

  test('an Approve while offline, before the review has ever loaded, says to reconnect rather than reload', async ({ app, page, context, hygiene }) => {
    // The review's code chunk cannot load offline: this test's own doing.
    hygiene.allow('request-failed', /LeadApproveReview-[\w-]+\.(js|css) failed: net::ERR_INTERNET_DISCONNECTED$/);
    hygiene.allow('console.error', /ERR_INTERNET_DISCONNECTED|Failed to fetch dynamically imported module|Unable to preload CSS/);
    hygiene.allow('pageerror', /Failed to fetch dynamically imported module|Unable to preload CSS/);
    await app.gotoRoute('/lead-queue');
    const id = PRIMARY_BORROWER.borrower_id;
    const approve = page.getByTestId(`lead-approval-cell-${id}`).getByRole('button', { name: `Approve ${id}` });
    await expect(approve).toBeVisible();

    await context.setOffline(true);
    await approve.click();
    await expect(page.locator('[data-testid="lead-approve-review-loading"][role="alert"]')).toHaveText(
      'You are offline, so the approval review could not open. Nothing was drafted or approved. Reconnect, then approve again.',
    );
    expect(page.url(), 'no reload was attempted while offline').toContain('/lead-queue');
  });
});

test.describe('skeletons shaped like what they stand in for (states-10)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`the Lead Queue skeleton rows are within 2px of the rendered rows, column for column (${theme})`, async ({ app, page, mockApi }) => {
      await app.setTheme(theme);
      const held = holdDefaultFixture(mockApi, 'GET', '/api/leads');
      await page.goto('/lead-queue', { waitUntil: 'domcontentloaded' });

      const skeletonRows = page.locator('.lead-queue-skeleton tbody tr');
      await expect(skeletonRows.first()).toBeVisible();
      const skeletonHeights = await skeletonRows.evaluateAll((rows) => rows.map((row) => row.getBoundingClientRect().height));
      const skeletonColumns = await page
        .locator('.lead-queue-skeleton thead th')
        .evaluateAll((ths) => ths.map((th) => Math.round(th.getBoundingClientRect().width)));

      held.release();
      await app.settle();
      const rows = page.locator('table.lead-table__table:not([aria-hidden]) tbody tr[aria-rowindex]:not(.tbl__expand)');
      await expect(rows.nth(7)).toBeVisible();
      const renderedHeights = await rows.evaluateAll((els) => els.slice(0, 8).map((row) => row.getBoundingClientRect().height));
      const renderedColumns = await page
        .locator('table.lead-table__table:not([aria-hidden]) thead th')
        .evaluateAll((ths) => ths.map((th) => Math.round(th.getBoundingClientRect().width)));

      const rendered = renderedHeights[0];
      expect(rendered, 'one-line rows at --row-h').toBeGreaterThan(30);
      for (const height of [...skeletonHeights, ...renderedHeights]) {
        expect(Math.abs(height - rendered), `row height ${height} vs rendered ${rendered}`).toBeLessThanOrEqual(2);
      }
      expect(skeletonColumns.length).toBe(renderedColumns.length);
      skeletonColumns.forEach((width, index) => {
        expect(Math.abs(width - renderedColumns[index]), `column ${index + 1} width`).toBeLessThanOrEqual(2);
      });
    });
  }

  test('an Analytics dashboard reserves its loaded grid while it loads', async ({ app, page, mockApi }) => {
    await app.gotoRoute('/analytics');
    const held = holdDefaultFixture(mockApi, 'GET', '/api/analytics/geography');
    await page.getByRole('tab', { name: 'Geography' }).click();
    const skeleton = page.locator('[data-analytics-skeleton]');
    await expect(skeleton).toBeVisible();
    const reserved = await skeleton.evaluate((el) => ({
      height: el.getBoundingClientRect().height,
      grid: getComputedStyle(el.querySelector('.layoutA-grid')!).gridTemplateColumns,
    }));

    held.release();
    await app.settle();
    const loaded = await page.locator('.layoutA-grid.analytics-grid').first().evaluate((grid) => ({
      grid: getComputedStyle(grid).gridTemplateColumns,
      height: grid.parentElement!.getBoundingClientRect().height,
    }));
    expect(reserved.grid, 'the same grid track sizes as the loaded view').toBe(loaded.grid);
    // The old placeholder was three text lines in one card (~120px).
    expect(reserved.height).toBeGreaterThan(loaded.height * 0.6);
    expect(reserved.height).toBeLessThan(loaded.height * 1.4);
  });

  test('a route whose chunk is still loading shows the page-shaped fallback with its panel reserved', async ({ app, page }) => {
    await app.gotoRoute('/');
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    // Hold the Glossary route chunk (it is not among the idle-preloaded routes).
    await page.route(/\/assets\/glossary-[^/]+\.js$/, async (route) => {
      await gate;
      await route.continue();
    });
    await navLink(page, 'Glossary').click();

    const fallback = page.locator('.route-transition > [data-route-fallback]');
    await expect(fallback).toHaveCount(1);
    // Visible once the show-delay has passed; the PageShell frame, not a card.
    await expect(fallback).toBeVisible();
    await expect(fallback).toHaveAttribute('aria-busy', 'true');
    await expect(fallback.locator('.main__inner > .proto-hero .skeleton--title')).toBeVisible();
    const reserved = await fallback.locator('.surface__body--reserve').evaluate((el) => el.getBoundingClientRect().height);
    expect(reserved, 'the panel keeps its height reserved').toBeGreaterThanOrEqual(384);

    release();
    await expect(page.locator('#main-content h1')).toHaveText('Mortgage intelligence glossary');
    await expect(fallback).toHaveCount(0);
  });

  test('Home keeps its KPI row mounted, in its loading state, through a warehouse warm-up', async ({ app, page }) => {
    const recover = app.degrade('/api/portfolio/preview', WAREHOUSE_WARMING_UP);
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    // Wait for the warm-up itself (the transport's own retries end first).
    await expect(page.getByTestId('warming-up-block')).toBeVisible({ timeout: 15_000 });
    const cards = page.locator('.kpi-row > .kpi');
    await expect(cards).toHaveCount(4);
    await expect(page.locator('.kpi-row > .kpi.is-loading')).toHaveCount(4);

    recover();
    await page.clock.runFor(6_000);
    await expect(page.locator('.kpi-row > .kpi.is-loading')).toHaveCount(0);
    await expect(cards).toHaveCount(4);
  });
});
