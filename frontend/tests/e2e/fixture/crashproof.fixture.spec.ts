/**
 * Rendered-layer proofs for the wave-0 crash-proofing (audit stack-01,
 * shell-01, states-01, bundle-01, shell-v2): the stale-chunk reload, the
 * route error boundary with its Try again re-read, and the no-skeleton
 * render of a preloaded route.
 *
 * Every test runs the production build under the fixture harness; the only
 * thing faked is the API (and, for the stale-chunk case, one hashed chunk
 * answered 404 the way a retired build does).
 */
import type { Page, Route } from '@playwright/test';
import type { Borrower360 } from '../../../src/types';
import { ERROR_SURFACE_SELECTOR } from './app';
import { PRIMARY_BORROWER } from './data/borrowers';
import type { Hygiene } from './hygiene';
import type { MockApi } from './mockApi';
import { expect, test } from './test';

const ERROR_SURFACE = '[data-testid="error-surface"]';
/** sessionStorage key of the one guarded reload (src/lib/staleChunkRecovery.ts). */
const STALE_CHUNK_RELOAD_KEY = 'mip.staleChunkReloadAt';

/** Answer one hashed route chunk 404, as a server does after a deploy retired it. */
async function retireChunk(page: Page, chunkPrefix: string): Promise<void> {
  await page.route(new RegExp(`/assets/${chunkPrefix}-[^/]+\\.js$`), (route: Route) =>
    route.fulfill({ status: 404, contentType: 'text/plain', body: 'retired chunk (fixture)' }),
  );
}

/**
 * Record every distinct rendering of the error surface for the rest of the
 * test (from the next document on), so a leak check covers each state the
 * surface passed through, not only the moment a test looks at it.
 */
async function recordErrorSurfaces(page: Page): Promise<() => Promise<string[]>> {
  await page.addInitScript((selector) => {
    const win = window as Window & { __errorSurfaceHtml?: string[] };
    const seen = new Set<string>();
    win.__errorSurfaceHtml = [];
    new MutationObserver(() => {
      for (const node of document.querySelectorAll(selector)) {
        const html = node.outerHTML;
        if (seen.has(html)) continue;
        seen.add(html);
        win.__errorSurfaceHtml?.push(html);
      }
    }).observe(document, { childList: true, subtree: true, characterData: true, attributes: true });
  }, ERROR_SURFACE);
  return () => page.evaluate(() => (window as Window & { __errorSurfaceHtml?: string[] }).__errorSurfaceHtml ?? []);
}

/**
 * Console noise a caught render throw produces, which is the behaviour under
 * test: the app's message-free client error report, and React 19 printing the
 * boundary-caught error through its own console channel before onCaughtError.
 */
function allowCaughtRenderThrow(hygiene: Hygiene): void {
  hygiene.allow('console.error', /\[mip\] client error/);
  hygiene.allow('console.error', /Cannot read properties of null/);
  hygiene.allow('console.error', /above error occurred|recreate this component tree/);
}

/** Every boundary catch is reported once through lib/clientErrorLog; count them. */
function countCaughtReports(page: Page): () => number {
  let reports = 0;
  page.on('console', (message) => {
    if (message.type() === 'error' && /\[mip\] client error/.test(message.text())) reports += 1;
  });
  return () => reports;
}

/**
 * `evidence_events` is a required array on the wire; a null one is a payload
 * Borrower 360 provably cannot render (it maps over it).
 */
function answerMalformedBorrower(mockApi: MockApi): void {
  const broken = { ...PRIMARY_BORROWER, evidence_events: null } as unknown as Borrower360;
  mockApi.register<Borrower360>('GET', '/api/borrowers/:id', () => ({ body: broken }));
}

function borrowerReadCounter(mockApi: MockApi): () => number {
  const borrowerPath = `/api/borrowers/${PRIMARY_BORROWER.borrower_id}`;
  return () => mockApi.calls.filter((call) => call.path === borrowerPath).length;
}

/** Count full document loads from now on (client-side route changes fire none). */
function countDocumentLoads(page: Page): () => number {
  let loads = 0;
  page.on('load', () => {
    loads += 1;
  });
  return () => loads;
}

test.describe('stale chunk after a deploy', () => {
  test('reloads exactly once, then renders the chunk error surface inside the shell', async ({ app, hygiene, page }) => {
    // A retired chunk logs the browser's own 404 line and the app's
    // message-free client error report; both are the behaviour under test.
    hygiene.allow('console.error', /Failed to load resource/);
    hygiene.allow('console.error', /\[mip\] client error/);

    await app.gotoRoute('/');
    await retireChunk(page, 'glossary');
    await page.evaluate((key) => window.sessionStorage.removeItem(key), STALE_CHUNK_RELOAD_KEY);
    const loads = countDocumentLoads(page);

    const reloaded = page.waitForEvent('load');
    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Glossary' }).click();
    await reloaded;

    const surface = page.locator(ERROR_SURFACE);
    await expect(surface).toBeVisible();
    await expect(surface).toHaveAttribute('data-error-kind', 'chunk');
    await expect(surface).toHaveAttribute('data-error-boundary', 'route');
    await expect(surface.getByRole('button')).toHaveCount(1);
    await expect(surface.getByRole('button', { name: 'Reload' })).toBeVisible();
    await expect(surface).toContainText('Route · Glossary');
    await expect(page).toHaveURL(/\/glossary$/);

    // The guarded reload was spent on this tab and only once.
    const stamp = await page.evaluate((key) => window.sessionStorage.getItem(key), STALE_CHUNK_RELOAD_KEY);
    expect(Number(stamp)).toBeGreaterThan(0);
    expect(loads()).toBe(1);

    // The rest of the shell survives the broken route.
    await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible();
    await expect(page.getByRole('banner')).toBeVisible();
    await expect(app.genieToggle()).toBeVisible();

    // A second retired chunk inside the reload window must NOT reload again:
    // looping would only hammer the server. It renders its own surface.
    await retireChunk(page, 'ask-genie');
    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Ask Genie' }).click();
    await expect(surface).toContainText('Route · Ask Genie');
    await expect(surface).toHaveAttribute('data-error-kind', 'chunk');
    expect(loads()).toBe(1);
  });
});

test.describe('render throw inside a route', () => {
  test('is caught by the route boundary without leaking the id, and Try again re-reads the fixed data', async ({ app, hygiene, mockApi, page }) => {
    allowCaughtRenderThrow(hygiene);
    const caughtReports = countCaughtReports(page);
    const errorSurfaceRenderings = await recordErrorSurfaces(page);
    const borrowerReads = borrowerReadCounter(mockApi);

    // The URL carries a query string so the leak check below has one to find.
    answerMalformedBorrower(mockApi);
    await page.goto(`/borrower-360/${PRIMARY_BORROWER.borrower_id}?from=lead-queue&state=TX`, {
      waitUntil: 'domcontentloaded',
    });

    const surface = page.locator(ERROR_SURFACE);
    await expect(surface).toBeVisible();
    await expect(surface).toHaveAttribute('data-error-kind', 'render');
    await expect(surface).toHaveAttribute('data-error-boundary', 'route');
    await expect(surface.getByRole('button', { name: 'Try again' })).toBeVisible();
    await expect(surface.getByRole('button', { name: 'Reload' })).toBeVisible();
    await expect(surface).toContainText('Route · Borrower 360');
    await expect.poll(caughtReports, 'the boundary reported the catch once').toBe(1);
    const readsAtCatch = borrowerReads();
    expect(readsAtCatch, 'the route read the (malformed) borrower').toBeGreaterThan(0);

    // The shell around it is intact.
    await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible();
    await expect(page.getByRole('banner')).toBeVisible();

    // Nothing re-reads by itself while the surface is up: once the mock API
    // has gone quiet, the borrower read count is unchanged.
    await expect.poll(() => mockApi.inflight === 0 && mockApi.idleMs >= 500).toBe(true);
    expect(borrowerReads(), 'no passive re-read behind the error surface').toBe(readsAtCatch);

    // The API is fixed; the user clicks Try again.
    mockApi.register<Borrower360>('GET', '/api/borrowers/:id', () => ({ body: PRIMARY_BORROWER }));
    await surface.getByRole('button', { name: 'Try again' }).click();

    const main = page.locator('#main-content');
    await expect(main.locator('h1')).toHaveText(`Borrower ${PRIMARY_BORROWER.borrower_id}`);
    await app.settle();
    await expect(main).toContainText(PRIMARY_BORROWER.clip);
    await expect(main).toContainText(PRIMARY_BORROWER.evidence_events[0].display_text);
    await expect(page.locator(ERROR_SURFACE_SELECTOR), 'the recovered route shows no error surface').toHaveCount(0);
    expect(borrowerReads(), 'Try again issued exactly one (user-initiated) re-read').toBe(readsAtCatch + 1);
    expect(caughtReports(), 'the fixed data rendered without a second catch').toBe(1);
    expect(await page.locator('#root').evaluate((node) => node.childElementCount)).toBeGreaterThan(0);
    await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible();
    await expect(page.getByRole('banner')).toBeVisible();

    // No rendering of the error surface, at any point, carried the borrower
    // id, the URL's query string or the raw error message.
    const renderings = await errorSurfaceRenderings();
    expect(renderings.length, 'the error surface was recorded').toBeGreaterThan(0);
    for (const html of renderings) {
      expect(html).not.toContain(PRIMARY_BORROWER.borrower_id);
      expect(html).not.toMatch(/[?&][a-z_]+=/);
      expect(html).not.toContain('Cannot read properties');
    }
  });

  test('Try again also recovers a Back revisit that re-threw from the payload the first visit cached', async ({ app, hygiene, mockApi, page }) => {
    allowCaughtRenderThrow(hygiene);
    const caughtReports = countCaughtReports(page);
    const borrowerReads = borrowerReadCounter(mockApi);
    const surface = page.locator(ERROR_SURFACE);
    const dossierPath = `/borrower-360/${PRIMARY_BORROWER.borrower_id}`;

    answerMalformedBorrower(mockApi);
    await page.goto(dossierPath, { waitUntil: 'domcontentloaded' });
    await expect(surface).toBeVisible();
    await expect(surface).toHaveAttribute('data-error-kind', 'render');
    await expect.poll(caughtReports, 'the first visit was caught once').toBe(1);
    const readsAtCatch = borrowerReads();
    expect(readsAtCatch, 'the route read the (malformed) borrower').toBeGreaterThan(0);

    // The user leaves for the Lead Queue...
    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Leads' }).click();
    await expect(page).toHaveURL(/\/lead-queue$/);
    await app.settle();
    await expect(surface).toHaveCount(0);

    // ...and comes back with Back, well inside the cache's lifetime. The route
    // throws on its first render straight from the cached payload: a render
    // that throws never commits, so it subscribes no observer and reads nothing.
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`${dossierPath}$`));
    await expect(surface).toBeVisible();
    await expect(surface).toHaveAttribute('data-error-kind', 'render');
    await expect.poll(caughtReports, 'the revisit re-threw and was caught once more').toBe(2);
    await expect.poll(() => mockApi.inflight === 0 && mockApi.idleMs >= 500).toBe(true);
    expect(borrowerReads(), 'the revisit re-threw from the cache without a borrower read').toBe(readsAtCatch);

    // The API is fixed; the user clicks Try again.
    mockApi.register<Borrower360>('GET', '/api/borrowers/:id', () => ({ body: PRIMARY_BORROWER }));
    await surface.getByRole('button', { name: 'Try again' }).click();

    const main = page.locator('#main-content');
    await expect(main.locator('h1')).toHaveText(`Borrower ${PRIMARY_BORROWER.borrower_id}`);
    await app.settle();
    await expect(main).toContainText(PRIMARY_BORROWER.evidence_events[0].display_text);
    await expect(page.locator(ERROR_SURFACE_SELECTOR), 'the recovered route shows no error surface').toHaveCount(0);
    expect(borrowerReads(), 'Try again issued exactly one (user-initiated) re-read').toBe(readsAtCatch + 1);
    expect(caughtReports(), 'the fixed data rendered without a further catch').toBe(2);
  });
});

test.describe('preloaded route', () => {
  test('renders without the route skeleton once its chunk was preloaded on idle', async ({ app, page }) => {
    const chunkRequests: string[] = [];
    page.on('request', (request) => {
      if (/\/assets\/analytics-[^/]+\.js$/.test(request.url())) chunkRequests.push(request.url());
    });
    await app.gotoRoute('/');
    // The idle preloader (lib/routePreloaders) warms Analytics among the
    // likely next routes; wait for the network to go quiet so it has landed.
    await page.waitForLoadState('networkidle');
    expect(chunkRequests.length, 'the Analytics chunk was preloaded on idle').toBeGreaterThan(0);

    await page.evaluate(() => {
      const win = window as Window & { __fallbackMounts?: number };
      win.__fallbackMounts = 0;
      // The page-shaped RouteFallback (components/layout/RouteFallback, states-10).
      const isFallback = (node: Element) => node.matches('.route-transition > [data-route-fallback]');
      const observer = new MutationObserver((records) => {
        for (const record of records) {
          for (const added of record.addedNodes) {
            if (!(added instanceof Element)) continue;
            if (isFallback(added)) win.__fallbackMounts = (win.__fallbackMounts ?? 0) + 1;
            for (const nested of added.querySelectorAll('[data-route-fallback]')) {
              if (isFallback(nested)) win.__fallbackMounts = (win.__fallbackMounts ?? 0) + 1;
            }
          }
        }
      });
      observer.observe(document.getElementById('main-content')!, { childList: true, subtree: true });
    });

    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Analytics' }).click();
    await expect(page.locator('#main-content h1')).toHaveText('Analytics');
    await app.settle();
    const mounts = await page.evaluate(() => (window as Window & { __fallbackMounts?: number }).__fallbackMounts);
    expect(mounts, 'the route fallback skeleton never mounted').toBe(0);
    expect(chunkRequests.length, 'the chunk was not fetched a second time').toBe(1);
  });
});
