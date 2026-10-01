/**
 * Deploy-time co-branding in the built shell (audit responsive-10, report
 * 12.4 #9; D-theme-nav-d), at 1440x900.
 *
 * The shell document is served with the metas a co-branded build writes
 * (lib/tenantAppearancePlugin): a navy tenant accent and a lender mark for
 * the synthetic 'Fixture Lending' (never Summit Mortgage, never a real
 * brand). The pytest identity 'Fixture Test Lending' is not used here: at
 * 1440x900 that 20-character name already ellipsizes in the tenant pill
 * WITHOUT a mark (label 109 px in a 106 px slot, measured 2026-10-01), the
 * pill's designed behaviour for a long name, so a 'whole name' check could
 * not isolate the mark. The mark is an abstract two-tone 64x64 PNG generated
 * here. The
 * document route KEEPS the production CSP (hygiene.ts attaches it on the same
 * route; a later route that fulfils would otherwise bypass that check).
 */
import crypto from 'node:crypto';
import path from 'node:path';
import zlib from 'node:zlib';
import type { Page, TestInfo } from '@playwright/test';
import { expectAxeClean } from './axe';
import { SESSION } from './data/shell';
import { json } from './mockApi';
import { readProductionCsp } from './productionCsp';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const LENDER = 'Fixture Lending';

function pngChunk(kind: string, payload: Buffer): Buffer {
  const head = Buffer.alloc(4);
  head.writeUInt32BE(payload.length);
  const body = Buffer.concat([Buffer.from(kind, 'latin1'), payload]);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(zlib.crc32(body) >>> 0);
  return Buffer.concat([head, body, crc]);
}

/** An abstract 64x64 two-tone square: never a brand. */
function syntheticMark(): Buffer {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(64, 0);
  header.writeUInt32BE(64, 4);
  header.set([8, 6, 0, 0, 0], 8);
  const row = Buffer.concat([Buffer.from([0]), Buffer.alloc(32 * 4, Buffer.from([0x1f, 0x6f, 0xb4, 0xff])), Buffer.alloc(32 * 4, Buffer.from([0xe8, 0xee, 0xf3, 0xff]))]);
  const pixels = zlib.deflateSync(Buffer.concat(Array.from({ length: 64 }, () => row)));
  return Buffer.concat([Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]), pngChunk('IHDR', header), pngChunk('IDAT', pixels), pngChunk('IEND', Buffer.alloc(0))]);
}

const MARK = syntheticMark();
const MARK_URL = `/branding/lender-mark.png?v=${crypto.createHash('sha256').update(MARK).digest('hex').slice(0, 8)}`;
const CO_BRANDED = { 'mip-default-accent': 'navy', 'mip-lender-mark': MARK_URL, 'mip-lender-mark-lender': LENDER };

/** Serve the shell with these metas before the theme-boot tag (production CSP kept) and the mark image. */
async function serveBuildMetas(page: Page, testInfo: TestInfo, metas: Record<string, string>): Promise<void> {
  const configFile = testInfo.config.configFile;
  if (!configFile) throw new Error('needs frontend/playwright.config.ts');
  const csp = readProductionCsp(path.dirname(configFile));
  await page.route(
    (url) => !url.pathname.startsWith('/api/') && !url.pathname.startsWith('/assets/'),
    async (route) => {
      if (route.request().resourceType() !== 'document') {
        await route.fallback();
        return;
      }
      const response = await route.fetch();
      const tags = Object.entries(metas).map(([name, content]) => `<meta name="${name}" content="${content}">\n    `).join('');
      const html = await response.text();
      expect(html, 'the shell has the theme-boot anchor').toContain('<script src="/theme-boot.js');
      await route.fulfill({
        response,
        body: html.replace('<script src="/theme-boot.js', `${tags}<script src="/theme-boot.js`),
        headers: { ...response.headers(), 'content-security-policy': csp },
      });
    },
  );
  await page.route(/\/branding\/lender-mark\.png\?v=[0-9a-f]{8}$/, (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: MARK }),
  );
}

/** Every data-accent value <html> takes, and how many paints existed at the first one. */
async function recordAccent(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const record = { accent: [] as string[], paintsBeforeFirstAccent: null as number | null, shift: 0 };
    (window as unknown as { __mipBrand: typeof record }).__mipBrand = record;
    new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.target !== document.documentElement || mutation.attributeName !== 'data-accent') continue;
        const value = document.documentElement.getAttribute('data-accent');
        if (!value) continue;
        if (record.paintsBeforeFirstAccent === null) record.paintsBeforeFirstAccent = performance.getEntriesByType('paint').length;
        record.accent.push(value);
      }
    }).observe(document, { attributes: true, subtree: true, attributeFilter: ['data-accent'] });
    new PerformanceObserver((list) => {
      for (const entry of list.getEntries() as Array<PerformanceEntry & { value: number; hadRecentInput: boolean }>) {
        if (!entry.hadRecentInput) record.shift += entry.value;
      }
    }).observe({ type: 'layout-shift', buffered: true });
  });
}

type Rect = { left: number; right: number; top: number; bottom: number };
const overlaps = (a: Rect, b: Rect) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;

function pill(page: Page) {
  return page.getByRole('banner').locator('.topbar__pill', { has: page.locator('.topbar__pill-tenant') });
}

for (const theme of FIXTURE_THEMES) {
  for (const consoleOpen of [false, true]) {
    test(`${theme}, Console ${consoleOpen ? 'open' : 'closed'}: the tenant accent pre-paints and the mark sits whole in the pill and the Console chip`, async ({ app, mockApi, page }, testInfo) => {
      mockApi.register('GET', '/api/session', () => json({ ...SESSION, lender_name: LENDER }));
      await recordAccent(page);
      await serveBuildMetas(page, testInfo, CO_BRANDED);
      await app.setTheme(theme);
      await app.gotoRoute('/');

      const record = await page.evaluate(() => (window as unknown as { __mipBrand: { accent: string[]; paintsBeforeFirstAccent: number | null; shift: number } }).__mipBrand);
      expect(record.accent[0], 'theme-boot set the tenant accent first').toBe('navy');
      expect(record.paintsBeforeFirstAccent, 'before any paint').toBe(0);
      expect(record.accent.every((value) => value === 'navy')).toBe(true);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      expect(await page.evaluate(() => localStorage.getItem('mip.accent')), 'the tenant default is never stored').toBeNull();

      const mark = pill(page).locator('img.lender-mark');
      await expect(mark).toBeVisible();
      await expect(mark).toHaveAttribute('alt', '');
      const markBox = await mark.boundingBox();
      expect(Math.abs((markBox?.height ?? 0) - 16), 'the mark box is 16 px tall').toBeLessThanOrEqual(0.5);
      expect(await pill(page).locator('.topbar__pill-tenant').evaluate((el) => el.scrollWidth <= el.clientWidth), 'the tenant name is whole').toBe(true);
      await expect(pill(page).locator('.topbar__pill-tenant')).toHaveText(LENDER);
      const rect = async (selector: ReturnType<Page['locator']>) => {
        const box = await selector.boundingBox();
        if (!box) throw new Error('not rendered');
        return { left: box.x, right: box.x + box.width, top: box.y, bottom: box.y + box.height };
      };
      const [pillRect, searchRect, statusRect] = [
        await rect(pill(page)),
        await rect(page.getByRole('banner').getByRole('search')),
        await rect(page.getByTestId('system-status-pill')),
      ];
      expect(overlaps(pillRect, searchRect), 'pill x search').toBe(false);
      expect(overlaps(pillRect, statusRect), 'pill x status pill').toBe(false);
      // The 16 px mark replaces the 12 px glyph once the session lands: 0.0002 measured (2026-10-01).
      testInfo.annotations.push({ type: 'cls', description: `cumulative layout shift with the mark: ${record.shift.toFixed(4)}` });
      expect(record.shift, 'layout shift with the mark').toBeLessThan(0.01);

      if (consoleOpen) {
        const panel = await app.openConsole();
        await expect(panel.locator('.chip', { hasText: LENDER }).locator('img.lender-mark')).toBeVisible();
      }
      // Closed: the whole page. Open: the banner and the tenant chip's row, because
      // the Console's Recent activity list (.audit-panel) already fails
      // scrollable-region-focusable on the base build, with or without a mark
      // (measured 2026-10-01; outside this lane, reported to the integrator).
      await expectAxeClean(page, {
        key: { route: 'home', state: consoleOpen ? 'co-branded-console' : 'co-branded' },
        theme,
        accent: 'navy',
        known: {},
        include: consoleOpen ? '.topbar, .tweaks .tweak-row > .stack-sm' : undefined,
      });
    });
  }
}

test('a mark built for another lender never shows, in the pill or the Console chip', async ({ app, page }, testInfo) => {
  // The default fixture session reports Summit Mortgage.
  await serveBuildMetas(page, testInfo, CO_BRANDED);
  await app.gotoRoute('/');
  await expect(pill(page).locator('.topbar__pill-tenant')).toHaveText('Summit Mortgage');
  const panel = await app.openConsole();
  await expect(panel.locator('.chip', { hasText: 'Summit Mortgage' })).toBeVisible();
  await expect(page.locator('img')).toHaveCount(await page.locator('img:not(.lender-mark)').count());
  await expect(page.locator('img.lender-mark')).toHaveCount(0);
});

test('a failed lender-mark chunk leaves the shell up with the building glyph in the pill', async ({ app, hygiene, mockApi, page }, testInfo) => {
  // The one chunk this test fails on purpose (a network blip, or a stale chunk
  // after a redeploy), and the browser's and the client error log's lines for it.
  hygiene.allow('request-failed', /\/assets\/LenderMark-[\w-]+\.js failed/);
  hygiene.allow('console.error', /\/assets\/LenderMark-[\w-]+\.js/);
  mockApi.register('GET', '/api/session', () => json({ ...SESSION, lender_name: LENDER }));
  await serveBuildMetas(page, testInfo, CO_BRANDED);
  const aborted: string[] = [];
  await page.route(/\/assets\/LenderMark-[\w-]+\.js$/, async (route) => {
    aborted.push(route.request().url());
    await route.abort('failed');
  });
  await app.gotoRoute('/');

  await expect(pill(page).locator('.topbar__pill-tenant')).toBeVisible();
  await expect(pill(page).locator('.topbar__pill-tenant')).toHaveText(LENDER);
  // Non-vacuity: the co-branded build really asked for the chunk this test failed.
  await expect.poll(() => aborted.length, { message: 'the LenderMark chunk was requested and aborted' }).toBeGreaterThan(0);
  await expect(pill(page).locator('svg')).toHaveCount(1);
  await expect(pill(page).locator('img.lender-mark')).toHaveCount(0);
  await expect(page.locator('.error-surface--page')).toHaveCount(0);
  await expect(page.getByRole('main')).toBeVisible();
});

test('print shows no lender mark', async ({ app, mockApi, page }, testInfo) => {
  mockApi.register('GET', '/api/session', () => json({ ...SESSION, lender_name: LENDER }));
  await serveBuildMetas(page, testInfo, CO_BRANDED);
  await app.gotoRoute('/');
  await expect(pill(page).locator('img.lender-mark')).toBeVisible();
  await page.emulateMedia({ media: 'print' });
  await expect(page.locator('img.lender-mark:visible')).toHaveCount(0);
});

test('a default build draws the building glyph and never requests a mark', async ({ app, mockApi, page }) => {
  mockApi.register('GET', '/api/session', () => json({ ...SESSION, lender_name: LENDER }));
  const branding: string[] = [];
  page.on('request', (request) => {
    if (new URL(request.url()).pathname.startsWith('/branding/')) branding.push(request.url());
  });
  await app.gotoRoute('/');
  await expect(page.locator('meta[name^="mip-"]')).toHaveCount(0);
  await expect(pill(page).locator('svg')).toHaveCount(1);
  await expect(page.locator('img.lender-mark')).toHaveCount(0);
  await expect(page.locator('html')).toHaveAttribute('data-accent', 'bright');
  expect(branding).toEqual([]);
});
