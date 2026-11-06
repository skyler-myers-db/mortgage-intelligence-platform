/**
 * Coming back online never re-reads what a route has already been served.
 *
 * Many reads write an audit row (visual.ts AUDITED_READS), so a read the app
 * makes on its own, with no act behind it, inflates governance evidence. The
 * app turned focus refetch off for that reason; reconnect refetch stayed at
 * TanStack's default, which refetches every observed query that has gone stale
 * on the browser's `online` event. Verified 2026-11-05 on main: past its
 * staleTime, an offline/online toggle re-issued the natural-load reads of the
 * Offer Orchestrator and Borrower 360 indexes (Today's top leads, VIEW_LEADS),
 * the Borrower 360 detail (VIEW_BORROWER), Segment Intelligence (VIEW_LEADS)
 * and the audit ledger (VIEW_AUDIT_LEDGER x2), in Chromium and WebKit.
 *
 * The fixture freezes Date.now() (test.ts `fixtureNow`), so served data never
 * goes stale on its own; each test moves the frozen clock past every
 * staleTime the app sets before it toggles the network.
 *
 * A read whose last attempt never reached the server (offline, unreachable)
 * wrote no row: reconnect still completes it, which the control test pins, and
 * which proves the wait below is long enough to see a reconnect refetch.
 *
 * Each test lets the route's idle chunk preloads land before it goes offline
 * (assetsSettled): a preload the toggle cuts off is the test's own doing, and
 * the hygiene gate stays strict for everything else.
 */
import type { BrowserContext, Page, Request } from '@playwright/test';
import { FIXTURE_NOW } from './data/reference';
import { FIXTURE_ROUTES } from './routes';
import { expect, test } from './test';
import type { MockApi } from './mockApi';
import type { AppDriver } from './app';
import { auditedReadsAfter, expectNoAuditedReadSince, markNaturalLoad } from './visual';

/** Past every staleTime the app sets (the longest is five minutes). */
const PAST_EVERY_STALE_TIME_MS = 10 * 60_000;

/**
 * The longest idle-preload timeout the app passes to createIdlePreloader
 * (lib/prefetch.ts; AppShell, routePreloaders and the rest pass 5 s).
 */
const LONGEST_IDLE_PRELOAD_TIMEOUT_MS = 5_000;
/** prefetch.ts falls back to setTimeout(min(timeout, 1000)) without requestIdleCallback (WebKit). */
const IDLE_PRELOAD_TIMER_CAP_MS = 1_000;
/** How long no asset request may start or be in flight before the page counts as quiet. */
const ASSET_QUIET_MS = 300;

interface AssetTraffic {
  /** Milliseconds since the last asset request started or ended, or 0 while one is in flight. */
  quietForMs(): number;
}

/** Same-origin `/assets/` requests (chunks, CSS, fonts): attach before the first navigation. */
function trackAssets(page: Page): AssetTraffic {
  const inflight = new Set<Request>();
  let lastChange = Date.now();
  const isAsset = (request: Request) => new URL(request.url()).pathname.startsWith('/assets/');
  const ended = (request: Request) => {
    if (inflight.delete(request)) lastChange = Date.now();
  };
  page.on('request', (request) => {
    if (!isAsset(request)) return;
    inflight.add(request);
    lastChange = Date.now();
  });
  page.on('requestfinished', ended);
  page.on('requestfailed', ended);
  return { quietForMs: () => (inflight.size > 0 ? 0 : Date.now() - lastChange) };
}

/**
 * Let the route's idle chunk preloads land. settle() waits for the mock API,
 * not for asset traffic, and the app preloads chunks on idle after a route
 * mounts; going offline with one in flight fails it (CI run 42111817327,
 * WebKit, lead-queue: the LeadRowExpanded, ScoreAnatomyGate and
 * useBorrowerProof chunks). waitForLoadState('networkidle') cannot wait for
 * them: Playwright fires it once per navigation, 500 ms after the last
 * request, before a preload WebKit schedules 1 s after mount.
 *
 * An idle callback queued now runs after every one queued while the route
 * mounted (they run in order, and a timed-out one by its earlier deadline);
 * without requestIdleCallback a timer longer than the preloader's does the
 * same. Every preload has then started, and the poll waits for it to end.
 */
async function assetsSettled(page: Page, assets: AssetTraffic): Promise<void> {
  await page.evaluate(
    ({ idleTimeoutMs, timerMs }) =>
      new Promise<void>((resolve) => {
        const win = window as Window & { requestIdleCallback?: (cb: () => void, opts?: { timeout?: number }) => number };
        if (typeof win.requestIdleCallback === 'function') win.requestIdleCallback(() => resolve(), { timeout: idleTimeoutMs });
        else window.setTimeout(resolve, timerMs);
      }),
    { idleTimeoutMs: LONGEST_IDLE_PRELOAD_TIMEOUT_MS + 1_000, timerMs: IDLE_PRELOAD_TIMER_CAP_MS + 100 },
  );
  await expect.poll(() => assets.quietForMs(), { message: 'asset requests settled before going offline' }).toBeGreaterThanOrEqual(ASSET_QUIET_MS);
}

/** Go offline and back, then wait until the app has acted on `online`. */
async function reconnect(app: AppDriver, context: BrowserContext, mockApi: MockApi, page: Page, assets: AssetTraffic): Promise<void> {
  const banner = page.locator('.degraded-banner[data-connection="offline"]');
  await assetsSettled(page, assets);
  await context.setOffline(true);
  await expect(banner).toBeVisible();
  const healthBefore = mockApi.calls.filter((call) => call.path === '/api/health').length;
  await context.setOffline(false);
  await expect(banner).toHaveCount(0);
  // The health poll probes at once on `online`, the same event TanStack's
  // reconnect refetch runs on: once that probe has reached the mock and the
  // route has settled again, a reconnect refetch would have been recorded.
  await expect.poll(() => mockApi.calls.filter((call) => call.path === '/api/health').length).toBeGreaterThan(healthBefore);
  await app.settle();
}

test.describe('a reconnect after the data went stale reads nothing audited', () => {
  for (const route of FIXTURE_ROUTES) {
    test(route.name, async ({ app, context, mockApi, page }) => {
      const assets = trackAssets(page);
      await app.gotoRoute(route.path);
      const natural = markNaturalLoad(mockApi);
      await page.clock.setFixedTime(new Date(Date.parse(FIXTURE_NOW) + PAST_EVERY_STALE_TIME_MS));
      await reconnect(app, context, mockApi, page, assets);
      expectNoAuditedReadSince(mockApi, natural, `${route.name} after a reconnect`);
    });
  }
});

test.describe('a reconnect completes a read that never reached the server', () => {
  test("Borrower 360, no borrower: Today's top leads failed unreachable, then loads", async ({ app, context, hygiene, mockApi, page }) => {
    // This test fails the first top-leads request on purpose.
    hygiene.allow('request-failed', /\/api\/v1\/leads\?limit=5 failed: /);
    hygiene.allow('console.error', /\/api\/v1\/leads\?limit=5/);
    const assets = trackAssets(page);
    let failed = 0;
    await page.route(/\/api\/v1\/leads\?limit=5$/, async (route) => {
      if (failed === 0) {
        failed += 1;
        await route.abort('failed');
        return;
      }
      await route.fallback();
    });
    await app.gotoRoute('/borrower-360');
    expect(failed).toBe(1);
    const natural = markNaturalLoad(mockApi);
    expect(auditedReadsAfter(mockApi.calls, 0)).toEqual([]);
    await expect(page.locator('#main-content').getByText("Today's top leads")).toHaveCount(0);

    await page.clock.setFixedTime(new Date(Date.parse(FIXTURE_NOW) + PAST_EVERY_STALE_TIME_MS));
    await reconnect(app, context, mockApi, page, assets);
    // Its one audit row, written now: the failed attempt wrote none.
    expect(auditedReadsAfter(mockApi.calls, natural)).toEqual(['GET /api/leads?limit=5 (VIEW_LEADS)']);
    await expect(page.locator('#main-content').getByText("Today's top leads")).toBeVisible();
  });
});
