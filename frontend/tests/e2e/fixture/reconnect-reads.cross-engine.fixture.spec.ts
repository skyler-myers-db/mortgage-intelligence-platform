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
 * A read the server answered 200 whose body the network then lost fails
 * unreachable (or offline) with its status. An audited one has written its
 * row, so nothing completes it on its own; a reviewed audit-free one
 * (lib/auditFreeReads) wrote none, so a reconnect and the "Connection lost"
 * recovery both complete it and its panel loads by itself.
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

/** The in-page switch loseBodies installs (init scripts cannot close over test state). */
const LOSE_BODIES = '__mipFixtureLoseBodies';

/**
 * Until `keepBodies`, every GET to `target` (path, and query when given)
 * loses its body right after its 200 headers, as a connection that drops
 * mid-transfer does: the mock served the read, and the app's res.json() fails
 * with a TypeError. Every one, not the first: a route can read the same
 * query twice while it loads (Analytics prefetches the executive read, and
 * the mount re-reads it after a failed prefetch).
 */
async function loseBodies(page: Page, target: { pathname: string; search?: string }): Promise<void> {
  await page.addInitScript(
    ({ wanted, key }) => {
      const realFetch = window.fetch.bind(window);
      const state = { armed: true };
      Object.defineProperty(window, key, { value: state });
      window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
        const response = await realFetch(input, init);
        const raw = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
        const url = new URL(raw, window.location.href);
        const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
        const hit = url.pathname === wanted.pathname && (wanted.search === undefined || url.search === wanted.search);
        if (!state.armed || method !== 'GET' || !hit) return response;
        const bytes = new Uint8Array(await response.arrayBuffer());
        const body = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(bytes.slice(0, Math.max(1, Math.floor(bytes.length / 2))));
            controller.error(new TypeError('network connection was lost'));
          },
        });
        return new Response(body, { status: response.status, statusText: response.statusText, headers: response.headers });
      };
    },
    { wanted: target, key: LOSE_BODIES },
  );
}

/** From now on the network delivers every body again. */
async function keepBodies(page: Page): Promise<void> {
  await page.evaluate((key) => {
    (window as unknown as Record<string, { armed: boolean }>)[key].armed = false;
  }, LOSE_BODIES);
}

const EXECUTIVE = { pathname: '/api/v1/analytics/executive' };
const reads = (mockApi: MockApi, method: string, path: string) =>
  mockApi.calls.filter((call) => call.method === method && call.path === path).length;
const executiveReads = (mockApi: MockApi) => reads(mockApi, 'GET', '/api/analytics/executive');

test.describe('a recovery completes an audit-free read whose answer lost its body, never an audited one', () => {
  test('Analytics: the executive read answered 200, lost its body, and loads on reconnect', async ({ app, context, mockApi, page }) => {
    const assets = trackAssets(page);
    await loseBodies(page, EXECUTIVE);
    await app.gotoRoute('/analytics');
    const served = executiveReads(mockApi);
    expect(served).toBeGreaterThan(0);
    await expect(page.locator('#main-content .funnel-sankey')).toHaveCount(0);
    const natural = markNaturalLoad(mockApi);
    await keepBodies(page);

    await page.clock.setFixedTime(new Date(Date.parse(FIXTURE_NOW) + PAST_EVERY_STALE_TIME_MS));
    await reconnect(app, context, mockApi, page, assets);
    // Analytics reads write no row: completing it adds none.
    await expect.poll(() => executiveReads(mockApi), { message: 'the reconnect completed the executive read' }).toBe(served + 1);
    await app.settle();
    await expect(page.locator('#main-content .funnel-sankey')).toBeVisible();
    expectNoAuditedReadSince(mockApi, natural, 'analytics after a reconnect');
  });

  test('Analytics: the executive read answered 200, lost its body, and loads once "Connection lost" clears', async ({
    app,
    hygiene,
    mockApi,
    page,
  }) => {
    // The app cannot reach its server until the test lets it.
    hygiene.allow('request-failed', /\/api\/v1\/health\b.* failed: /);
    hygiene.allow('console.error', /\/api\/v1\/health\b/);
    await loseBodies(page, EXECUTIVE);
    await app.gotoRoute('/analytics');
    const served = executiveReads(mockApi);
    expect(served).toBeGreaterThan(0);
    await expect(page.locator('#main-content .funnel-sankey')).toHaveCount(0);
    const natural = markNaturalLoad(mockApi);
    await keepBodies(page);

    const health = /\/api\/v1\/health(?:\?|$)/;
    await page.route(health, (route) => route.abort('failed'));
    const lost = page.locator('.degraded-banner[data-connection="unreachable"]');
    await expect(lost).toBeVisible({ timeout: 60_000 });
    expect(executiveReads(mockApi)).toBe(served);
    await page.unroute(health);
    await expect(lost).toHaveCount(0, { timeout: 60_000 });
    await expect.poll(() => executiveReads(mockApi), { message: 'the recovery completed the executive read' }).toBe(served + 1);
    await app.settle();
    await expect(page.locator('#main-content .funnel-sankey')).toBeVisible();
    expectNoAuditedReadSince(mockApi, natural, 'analytics after "Connection lost" cleared');
  });

  test("Borrower 360, no borrower: Today's top leads answered 200 and lost its body; a reconnect never re-reads it", async ({
    app,
    context,
    mockApi,
    page,
  }) => {
    const assets = trackAssets(page);
    await loseBodies(page, { pathname: '/api/v1/leads', search: '?limit=5' });
    await app.gotoRoute('/borrower-360');
    // The mock served it, so the server wrote its VIEW_LEADS row.
    expect(auditedReadsAfter(mockApi.calls, 0)).toEqual(['GET /api/leads?limit=5 (VIEW_LEADS)']);
    await expect(page.locator('#main-content').getByText("Today's top leads")).toHaveCount(0);
    const natural = markNaturalLoad(mockApi);
    // A re-read would now load the panel: the absence below is not the network's doing.
    await keepBodies(page);

    await page.clock.setFixedTime(new Date(Date.parse(FIXTURE_NOW) + PAST_EVERY_STALE_TIME_MS));
    await reconnect(app, context, mockApi, page, assets);
    expectNoAuditedReadSince(mockApi, natural, 'borrower-360 after a reconnect');
    await expect(page.locator('#main-content').getByText("Today's top leads")).toHaveCount(0);
  });
});

test.describe('a recovery completes the ledger reads that write no row', () => {
  test('Console: My recent activity answered 200, lost its body, and loads on reconnect', async ({ app, context, mockApi, page }) => {
    const assets = trackAssets(page);
    await loseBodies(page, { pathname: '/api/v1/audit/my-events' });
    await app.gotoRoute('/glossary');
    const consolePanel = await app.openConsole();
    const activity = consolePanel.getByRole('region', { name: 'My recent activity' });
    await expect(activity.getByText('Recent activity is unavailable.')).toBeVisible();
    const served = reads(mockApi, 'GET', '/api/audit/my-events');
    expect(served).toBeGreaterThan(0);
    await app.settle();
    const natural = markNaturalLoad(mockApi);
    await keepBodies(page);

    await page.clock.setFixedTime(new Date(Date.parse(FIXTURE_NOW) + PAST_EVERY_STALE_TIME_MS));
    await reconnect(app, context, mockApi, page, assets);
    // It lists the reader's own ledger events and writes no row: completing it adds none.
    await expect
      .poll(() => reads(mockApi, 'GET', '/api/audit/my-events'), { message: 'the reconnect completed the my-events read' })
      .toBe(served + 1);
    await expect(activity.getByText('Recent activity is unavailable.')).toHaveCount(0);
    expectNoAuditedReadSince(mockApi, natural, 'the Console after a reconnect');
  });
});
