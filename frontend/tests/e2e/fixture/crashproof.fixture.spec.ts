/**
 * Rendered-layer proofs for the wave-0 crash-proofing (audit stack-01,
 * shell-01, states-01, bundle-01, shell-v2): the stale-chunk reload, the
 * route error boundary, and the no-skeleton render of a preloaded route.
 *
 * Every test runs the production build under the fixture harness; the only
 * thing faked is the API (and, for the stale-chunk case, one hashed chunk
 * answered 404 the way a retired build does).
 */
import type { Page, Route } from '@playwright/test';
import type { Borrower360 } from '../../../src/types';
import { PRIMARY_BORROWER } from './data/borrowers';
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
  test('is caught by the route boundary with Try again and Reload, and leaks no borrower id', async ({ hygiene, mockApi, page }) => {
    hygiene.allow('console.error', /\[mip\] client error/);
    // React 19 also prints the boundary-caught error through its own console
    // channel before onCaughtError runs.
    hygiene.allow('console.error', /Cannot read properties of null/);
    hygiene.allow('console.error', /above error occurred|recreate this component tree/);

    // Every boundary catch is reported once through lib/clientErrorLog.
    let caughtReports = 0;
    page.on('console', (message) => {
      if (message.type() === 'error' && /\[mip\] client error/.test(message.text())) caughtReports += 1;
    });

    // `evidence_events` is a required array on the wire; a null one is a
    // payload Borrower 360 provably cannot render (it maps over it).
    const broken = { ...PRIMARY_BORROWER, evidence_events: null } as unknown as Borrower360;
    mockApi.register<Borrower360>('GET', '/api/borrowers/:id', () => ({ body: broken }));
    await page.goto(`/borrower-360/${PRIMARY_BORROWER.borrower_id}`, { waitUntil: 'domcontentloaded' });

    const surface = page.locator(ERROR_SURFACE);
    await expect(surface).toBeVisible();
    await expect(surface).toHaveAttribute('data-error-kind', 'render');
    await expect(surface).toHaveAttribute('data-error-boundary', 'route');
    await expect(surface.getByRole('button', { name: 'Try again' })).toBeVisible();
    await expect(surface.getByRole('button', { name: 'Reload' })).toBeVisible();
    await expect(surface).toContainText('Route · Borrower 360');

    const surfaceHtml = await surface.evaluate((node) => node.outerHTML);
    expect(surfaceHtml).not.toContain(PRIMARY_BORROWER.borrower_id);
    expect(surfaceHtml).not.toMatch(/[?&][a-z_]+=/);
    expect(surfaceHtml).not.toContain('Cannot read properties');

    // The shell around it is intact.
    await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible();
    await expect(page.getByRole('banner')).toBeVisible();

    // Try again clears the boundary and re-renders the route. The bad payload
    // is still what the query cache holds (fresh for 30 s, and never stale
    // under the frozen clock), so no refetch happens even after the API is
    // fixed: the route throws again and the boundary catches it AGAIN (a
    // second report, still no white page). Reload is the path to a fresh
    // fetch. Pinned as observed on the integrated base; see the lane report.
    await expect.poll(() => caughtReports).toBe(1);
    mockApi.register<Borrower360>('GET', '/api/borrowers/:id', () => ({ body: PRIMARY_BORROWER }));
    const borrowerReadsBefore = mockApi.calls.filter((call) => call.path.startsWith('/api/borrowers/')).length;
    await surface.getByRole('button', { name: 'Try again' }).click();
    await expect.poll(() => caughtReports, 'the boundary caught the re-rendered throw').toBe(2);
    await expect(surface).toBeVisible();
    await expect(surface).toHaveAttribute('data-error-kind', 'render');
    expect(mockApi.calls.filter((call) => call.path.startsWith('/api/borrowers/')).length).toBe(borrowerReadsBefore);
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
      const isFallback = (node: Element) =>
        node.matches('.route-transition > .surface[aria-busy="true"][role="status"]');
      const observer = new MutationObserver((records) => {
        for (const record of records) {
          for (const added of record.addedNodes) {
            if (!(added instanceof Element)) continue;
            if (isFallback(added)) win.__fallbackMounts = (win.__fallbackMounts ?? 0) + 1;
            for (const nested of added.querySelectorAll('.surface[aria-busy="true"][role="status"]')) {
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
