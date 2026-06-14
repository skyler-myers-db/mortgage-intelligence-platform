import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 to run route performance and overlap checks.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const API_URL = process.env.MIP_API_URL || APP_URL.replace(':5173', ':8000');
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER ? { Authorization: `Bearer ${BEARER}` } : {};

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

type RouteProbe = {
  label: string;
  path: string | ((request: APIRequestContext) => Promise<string>);
  ready: RegExp;
  maxLoadMs: number;
};

const ROUTES: RouteProbe[] = [
  { label: 'Home', path: '/', ready: /Who should we contact, why now, and with what offer/i, maxLoadMs: 4_000 },
  { label: 'Portfolio', path: '/portfolio-builder', ready: /Build a borrower population/i, maxLoadMs: 4_000 },
  { label: 'Segments', path: '/segment-intelligence', ready: /borrower segments/i, maxLoadMs: 5_000 },
  { label: 'Lead Queue', path: '/lead-queue', ready: /Ranked borrowers|Lead queue/i, maxLoadMs: 6_000 },
  { label: 'Borrower 360', path: liveBorrowerPath('/borrower-360'), ready: /Borrower dossier|Refi economics check/i, maxLoadMs: 7_000 },
  { label: 'Offer', path: liveBorrowerPath('/offer-orchestrator'), ready: /Draft outreach|Recommended offer/i, maxLoadMs: 7_000 },
  { label: 'Ask Genie', path: '/ask-genie', ready: /Ask Genie|Ready for governed analysis/i, maxLoadMs: 4_000 },
  { label: 'Admin', path: '/admin-config', ready: /Offer rules|Audit explorer|Admin/i, maxLoadMs: 4_000 },
];

function liveBorrowerPath(prefix: string): (request: APIRequestContext) => Promise<string> {
  return async (request) => {
    const resp = await request.get(apiV1('/leads?limit=1'), { headers: AUTH_HEADERS });
    expect(resp.status(), `GET /api/v1/leads for ${prefix}`).toBe(200);
    const rows = (await resp.json()) as Array<{ borrower_id?: string }>;
    const borrowerId = rows[0]?.borrower_id;
    expect(borrowerId, `need live borrower id for ${prefix}`).toBeTruthy();
    return `${prefix}/${borrowerId}`;
  };
}

function apiV1(pathWithQuery: string): string {
  return `${API_URL}/api/v1${pathWithQuery}`;
}

function normalizedApiPath(rawUrl: string): string {
  return new URL(rawUrl).pathname.replace(/^\/api\/v\d+(?=\/)/, '/api');
}

async function routeLoadMs(page: Page): Promise<number> {
  return page.evaluate(() => {
    const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined;
    if (!nav) return performance.now();
    return nav.loadEventEnd > 0 ? nav.loadEventEnd - nav.startTime : performance.now() - nav.startTime;
  });
}

function routeNavLink(page: Page, name: RegExp) {
  return page.locator('.route-nav').getByRole('link', { name });
}

async function assertNoHorizontalOverflow(page: Page, label: string): Promise<void> {
  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    return Math.max(0, doc.scrollWidth - doc.clientWidth);
  });
  expect(overflow, `${label}: document should not horizontally overflow`).toBeLessThanOrEqual(2);
}

async function assertNoObviousTextOverlap(page: Page, label: string): Promise<void> {
  const collisions = await page.evaluate(() => {
    const selectors = [
      '.topbar__crumbs',
      '.topbar__search',
      '.nav-tabs > *',
      '.seg-card',
      '.filter',
      '.kpi',
      '.surface__hdr > *',
      '.tbl th',
      '.tbl td',
      '.map-corner-chips > *',
      '.decision-panel__field',
    ];
    const nodes = Array.from(document.querySelectorAll<HTMLElement>(selectors.join(',')));
    const boxes = nodes
      .map((node) => {
        const rect = node.getBoundingClientRect();
        const text = (node.textContent || '').trim();
        const style = window.getComputedStyle(node);
        return {
          node,
          text,
          hidden: style.visibility === 'hidden' || style.display === 'none',
          x: rect.x,
          y: rect.y,
          right: rect.right,
          bottom: rect.bottom,
          width: rect.width,
          height: rect.height,
          selector: node.className || node.tagName.toLowerCase(),
        };
      })
      .filter((box) => !box.hidden && box.text.length > 0 && box.width > 4 && box.height > 4);
    const found: string[] = [];
    for (let i = 0; i < boxes.length; i += 1) {
      for (let j = i + 1; j < boxes.length; j += 1) {
        const a = boxes[i];
        const b = boxes[j];
        if (a.node.contains(b.node) || b.node.contains(a.node)) continue;
        const xOverlap = Math.min(a.right, b.right) - Math.max(a.x, b.x);
        const yOverlap = Math.min(a.bottom, b.bottom) - Math.max(a.y, b.y);
        if (xOverlap <= 1 || yOverlap <= 1) continue;
        const overlapArea = xOverlap * yOverlap;
        const smallerArea = Math.min(a.width * a.height, b.width * b.height);
        if (overlapArea / smallerArea > 0.35) {
          found.push(`${a.selector} overlaps ${b.selector}`);
        }
      }
    }
    return found.slice(0, 10);
  });
  expect(collisions, `${label}: no obvious text/control overlaps`).toEqual([]);
}

async function assertNoBrokenRuntimeText(page: Page, label: string): Promise<void> {
  const brokenRuntimeToken = await page.evaluate(() => document.body.innerText.match(/\b(undefined|null|NaN)\b/)?.[0] ?? null);
  expect(brokenRuntimeToken, `${label}: no undefined/null leakage`).toBeNull();
}

test.describe('route performance and layout canaries', () => {
  test('authenticated health exposes breaker state required by the live UI', async ({ request }) => {
    const resp = await request.get(apiV1(`/health?ts=${Date.now()}`), { headers: AUTH_HEADERS });
    expect(resp.status(), 'GET /api/v1/health').toBe(200);
    const body = (await resp.json()) as {
      circuit_breakers?: Record<string, string>;
      dependencies?: Record<string, string>;
      status?: string;
    };

    expect(body.status, 'health status should be present').toMatch(/^(ok|degraded)$/);
    expect(body.dependencies, 'authenticated health should include coarse dependency state').toEqual(
      expect.objectContaining({
        warehouse: expect.any(String),
        lakebase: expect.any(String),
        genie: expect.any(String),
      }),
    );
    expect(body.circuit_breakers, 'authenticated health should include UI breaker state').toEqual(
      expect.objectContaining({
        warehouse: expect.stringMatching(/^(closed|open|half_open)$/),
        lakebase: expect.stringMatching(/^(closed|open|half_open)$/),
        genie: expect.stringMatching(/^(closed|open|half_open)$/),
      }),
    );
  });

  for (const route of ROUTES) {
    test(`${route.label} desktop route stays responsive and non-overlapping`, async ({ page, request }) => {
      const path = typeof route.path === 'string' ? route.path : await route.path(request);
      await page.goto(path);
      await expect(page.getByText(route.ready).first()).toBeVisible({ timeout: route.maxLoadMs + 5_000 });
      expect(await routeLoadMs(page), `${route.label}: loadEvent budget`).toBeLessThanOrEqual(route.maxLoadMs);
      await assertNoHorizontalOverflow(page, route.label);
      await assertNoObviousTextOverlap(page, route.label);
      await assertNoBrokenRuntimeText(page, route.label);
    });
  }

  test('mobile shell keeps primary routes usable without horizontal overflow', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    for (const path of ['/', '/segment-intelligence', '/lead-queue', '/ask-genie']) {
      await page.goto(path);
      await expect(page.locator('#main-content')).toBeVisible({ timeout: 10_000 });
      await assertNoHorizontalOverflow(page, `mobile ${path}`);
      await assertNoBrokenRuntimeText(page, `mobile ${path}`);
    }
  });

  test('QueryClient keeps hot Home reads cached during Home -> Segments -> Home', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText(/Who should we contact, why now, and with what offer/i).first()).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('.kpi:not(.is-loading) .kpi__value').first()).toBeVisible({
      timeout: 10_000,
    });
    await expect(page.locator('.data-estate:not([aria-busy]) .data-estate__lane-proof').first()).toBeVisible({
      timeout: 10_000,
    });

    const hotReads: string[] = [];
    page.on('request', (request) => {
      const path = normalizedApiPath(request.url());
      if (path === '/api/portfolio/preview' || path === '/api/data-estate') {
        hotReads.push(`${request.method()} ${path}`);
      }
    });

    await routeNavLink(page, /^Segments$/i).hover();
    await routeNavLink(page, /^Segments$/i).click();
    await expect(page.getByText(/borrower segments/i).first()).toBeVisible({ timeout: 10_000 });
    await page.getByRole('link', { name: /^Home$/i }).click();
    await expect(page.getByText(/Who should we contact, why now, and with what offer/i).first()).toBeVisible({ timeout: 10_000 });

    expect(hotReads, 'Home preview/data-estate should remain in QueryClient stale window').toEqual([]);
  });

  test('config options fetch is shared by shell and route-level consumers', async ({ page }) => {
    const configReads: string[] = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      if (normalizedApiPath(request.url()) === '/api/config/options') {
        configReads.push(`${request.method()} ${url.pathname}`);
      }
    });

    await page.goto('/');
    await expect(page.getByText(/Who should we contact, why now, and with what offer/i).first()).toBeVisible({ timeout: 10_000 });
    await routeNavLink(page, /^Portfolio$/i).click();
    await expect(page.getByText(/Build a borrower population/i).first()).toBeVisible({ timeout: 10_000 });
    await routeNavLink(page, /^Leads$/i).click();
    await expect(page.getByText(/Ranked borrowers|Lead queue/i).first()).toBeVisible({ timeout: 10_000 });

    expect(configReads, 'AppContext, Portfolio Builder, and Lead Queue should share one config-options query').toEqual([
      'GET /api/v1/config/options',
    ]);
  });

  test('Lead Queue hover/focus never reads governed borrower dossiers before navigation', async ({ page }) => {
    await page.goto('/lead-queue');
    const firstButton = page.locator('.lead-table__borrower-btn').first();
    await expect(firstButton).toBeVisible({ timeout: 30_000 });
    const firstRow = firstButton.locator('xpath=ancestor::tr[1]');

    const borrowerId = (await firstButton.locator('.lead-table__borrower').innerText()).trim();
    expect(borrowerId, 'first visible Lead Queue row should expose a borrower id').toMatch(/^B-[0-9A-Z]+$/);
    const borrowerApiPath = `/api/borrowers/${borrowerId}`;
    const borrowerReads: string[] = [];
    page.on('request', (request) => {
      const path = normalizedApiPath(request.url());
      if (path === borrowerApiPath) borrowerReads.push(`${request.method()} ${path}`);
    });

    await firstRow.hover();
    await page.waitForTimeout(500);
    await firstButton.focus();
    await page.waitForTimeout(500);
    expect(
      borrowerReads,
      'hover/focus intent must stay static-module-only because /api/borrowers records VIEW_BORROWER audit events',
    ).toEqual([]);

    if ((await firstButton.getAttribute('aria-expanded')) !== 'true') {
      await firstButton.click();
    }
    await expect(page.locator('.tbl__expand').first()).toBeVisible({ timeout: 10_000 });
    expect(borrowerReads, 'row expansion preview must not read the governed borrower dossier').toEqual([]);

    const navigationResponse = page.waitForResponse((response) => {
      return normalizedApiPath(response.url()) === borrowerApiPath && response.status() === 200;
    }, { timeout: 30_000 });
    await page.getByRole('link', { name: /Open Borrower 360/i }).first().click();
    await navigationResponse;
    await expect(page.getByText(/Borrower dossier|Refi economics check/i).first()).toBeVisible({ timeout: 30_000 });
    await page.waitForTimeout(500);
    expect(
      borrowerReads,
      'Borrower 360 should be the first point that reads and audits the full borrower dossier',
    ).toEqual([`GET ${borrowerApiPath}`]);
  });

  test('static prefetch never reads borrower, lead, audit, or evidence APIs', async ({ page }) => {
    const protectedReads: string[] = [];
    page.on('request', (request) => {
      const path = normalizedApiPath(request.url());
      if (
        path.startsWith('/api/borrowers') ||
        path.startsWith('/api/leads') ||
        path.startsWith('/api/audit') ||
        path.includes('/evidence')
      ) {
        protectedReads.push(`${request.method()} ${path}`);
      }
    });

    const initialAuditRequest = page
      .waitForRequest((request) => normalizedApiPath(request.url()).startsWith('/api/audit'), { timeout: 5_000 })
      .catch(() => null);

    await page.goto('/');
    await expect(page.getByText(/Who should we contact, why now, and with what offer/i).first()).toBeVisible({ timeout: 10_000 });
    await initialAuditRequest;
    protectedReads.length = 0;

    for (const name of [/^Portfolio$/i, /^Segments$/i, /^Leads$/i, /^Borrower 360$/i, /^Offer$/i, /^Ask Genie$/i, /^Admin$/i]) {
      await routeNavLink(page, name).hover();
    }
    await page.getByLabel(/Toggle Genie chat/i).hover();
    await page.waitForTimeout(1200);

    expect(protectedReads, 'hover/idle prefetch must stay static-module-only').toEqual([]);
  });
});
