import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const ENABLED = process.env.E2E_LIVE === '1' && process.env.E2E_BROWSER_MATRIX === '1';
test.skip(!ENABLED, 'Set E2E_LIVE=1 and E2E_BROWSER_MATRIX=1 to run cross-browser matrix.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const API_URL = process.env.MIP_API_URL || APP_URL.replace(':5173', ':8000');
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER ? { Authorization: `Bearer ${BEARER}` } : {};

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

type RouteProbe = {
  label: string;
  path: string | ((request: APIRequestContext) => Promise<string>);
  ready: RegExp;
  deviceCanary?: boolean;
  assertReady?: (page: Page) => Promise<void>;
};

const ROUTES: RouteProbe[] = [
  {
    label: 'Home',
    path: '/',
    ready: /Who should we contact, why now, and with what offer/i,
    deviceCanary: true,
  },
  {
    label: 'Portfolio',
    path: '/portfolio-builder',
    ready: /Build a borrower population/i,
  },
  {
    label: 'Segments',
    path: '/segment-intelligence',
    ready: /borrower segments/i,
    deviceCanary: true,
    assertReady: async (page) => {
      await expect(page.locator('.seg-card').first()).toBeVisible();
      await expect(page.locator('.geo-map, .us-map, svg').first()).toBeVisible();
    },
  },
  {
    label: 'Lead Queue',
    path: '/lead-queue',
    ready: /Ranked borrowers|Lead queue/i,
    deviceCanary: true,
    assertReady: async (page) => {
      await expect(page.locator('.lead-table__table').first()).toBeVisible({ timeout: 30_000 });
    },
  },
  {
    label: 'Borrower 360',
    path: liveBorrowerPath('/borrower-360'),
    ready: /Borrower dossier|Refi economics check/i,
  },
  {
    label: 'Offer',
    path: liveBorrowerPath('/offer-orchestrator'),
    ready: /Draft outreach|Recommended offer/i,
  },
  {
    label: 'Ask Genie',
    path: '/ask-genie',
    ready: /Ask Genie|Ready for governed analysis/i,
    deviceCanary: true,
  },
  {
    label: 'Admin',
    path: '/admin-config',
    ready: /Offer rules|Audit explorer|Admin/i,
    deviceCanary: true,
  },
];

const NAV_SEQUENCE = [
  { name: /^Home$/i, ready: /Who should we contact, why now, and with what offer/i },
  { name: /^Portfolio$/i, ready: /Build a borrower population/i },
  { name: /^Segments$/i, ready: /borrower segments/i },
  { name: /^Leads$/i, ready: /Ranked borrowers|Lead queue/i },
  { name: /^Ask Genie$/i, ready: /Ask Genie|Ready for governed analysis/i },
  { name: /^Admin$/i, ready: /Offer rules|Audit explorer|Admin/i },
] as const;

function liveBorrowerPath(prefix: string): (request: APIRequestContext) => Promise<string> {
  return async (request) => {
    const resp = await request.get(`${API_URL}/api/leads?limit=1`, { headers: AUTH_HEADERS });
    expect(resp.status(), `GET /api/leads for ${prefix}`).toBe(200);
    const rows = (await resp.json()) as Array<{ borrower_id?: string }>;
    const borrowerId = rows[0]?.borrower_id;
    expect(borrowerId, `need live borrower id for ${prefix}`).toBeTruthy();
    return `${prefix}/${borrowerId}`;
  };
}

function isPhoneProject(projectName: string): boolean {
  return projectName.startsWith('mobile-');
}

function isDeviceProject(projectName: string): boolean {
  return projectName.startsWith('mobile-') || projectName.startsWith('tablet-');
}

async function resolvePath(request: APIRequestContext, route: RouteProbe): Promise<string> {
  return typeof route.path === 'string' ? route.path : await route.path(request);
}

async function assertShellHealthy(page: Page, label: string) {
  await expect(page.locator('#main-content')).toBeVisible({ timeout: 20_000 });
  const overflow = await page.evaluate(() => {
    const doc = document.documentElement;
    return Math.max(0, doc.scrollWidth - doc.clientWidth);
  });
  expect(overflow, `${label}: no horizontal document overflow`).toBeLessThanOrEqual(2);
  const brokenRuntimeToken = await page.evaluate(() => document.body.innerText.match(/\b(undefined|null|NaN)\b/)?.[0] ?? null);
  expect(brokenRuntimeToken, `${label}: no broken runtime text`).toBeNull();
}

test.describe('cross-browser/device shell matrix', () => {
  for (const route of ROUTES) {
    test(`${route.label} renders with stable shell @desktop`, async ({ page, request }, testInfo) => {
      test.skip(isDeviceProject(testInfo.project.name), 'Full route matrix runs on desktop browser engines.');
      const path = await resolvePath(request, route);
      await page.goto(path);
      await expect(page.getByText(route.ready).first()).toBeVisible({ timeout: 30_000 });
      await route.assertReady?.(page);
      await assertShellHealthy(page, `${testInfo.project.name} ${route.label}`);
    });
  }

  for (const route of ROUTES.filter((item) => item.deviceCanary)) {
    test(`${route.label} keeps the shell usable on phone/tablet @device`, async ({ page, request }, testInfo) => {
      test.skip(!isDeviceProject(testInfo.project.name), 'Device canaries run only on phone/tablet projects.');
      if (isPhoneProject(testInfo.project.name) && route.label === 'Lead Queue') {
        test.info().annotations.push({
          type: 'note',
          description: 'Lead Queue validates mobile shell and scroll containment, not full table ergonomics.',
        });
      }
      const path = await resolvePath(request, route);
      await page.goto(path);
      await expect(page.getByText(route.ready).first()).toBeVisible({ timeout: 35_000 });
      await route.assertReady?.(page);
      await assertShellHealthy(page, `${testInfo.project.name} ${route.label}`);
    });
  }

  test('primary navigation links route cleanly across browser engines @desktop', async ({ page }, testInfo) => {
    test.skip(isDeviceProject(testInfo.project.name), 'Full nav sequence runs on desktop browser engines.');
    await page.goto('/');
    await expect(page.getByText(NAV_SEQUENCE[0].ready).first()).toBeVisible({ timeout: 30_000 });

    for (const item of NAV_SEQUENCE) {
      await page.getByRole('link', { name: item.name }).click();
      await expect(page.getByText(item.ready).first()).toBeVisible({ timeout: 30_000 });
      await assertShellHealthy(page, `${testInfo.project.name} nav ${item.name}`);
    }
  });

  test('theme and density controls update the shared shell state @desktop', async ({ page }, testInfo) => {
    test.skip(isPhoneProject(testInfo.project.name), 'Phone canary coverage stays route-focused.');
    await page.goto('/');
    await expect(page.locator('#main-content')).toBeVisible({ timeout: 30_000 });

    const initialTheme = await page.locator('html').getAttribute('data-theme');
    await page.getByLabel('Toggle theme').click();
    await expect
      .poll(() => page.locator('html').getAttribute('data-theme'), {
        message: `${testInfo.project.name}: theme toggle should update <html>`,
      })
      .not.toBe(initialTheme);

    await page.getByLabel('Toggle console').click();
    await expect(page.getByRole('complementary', { name: /Workspace console/i })).toHaveClass(/is-open/);
    await page.getByRole('button', { name: /^Compact$/i }).click();
    await expect(page.locator('html')).toHaveAttribute('data-density', 'compact');
    await page.getByRole('button', { name: /^Comfortable$/i }).click();
    await expect(page.locator('html')).toHaveAttribute('data-density', 'comfortable');
    await assertShellHealthy(page, `${testInfo.project.name} theme-density`);
  });
});
