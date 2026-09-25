/**
 * Delivery boot (wave 4a, lane w4-delivery-boot), proven in the built app:
 *
 *   - the boot module (audit bundle-02) starts the four non-audited boot reads
 *     beside the entry chunk, each once, and no audit-writing read;
 *   - the served HTML loads it just before the entry, with no inline script
 *     and no fetch preload;
 *   - the text-free shell skeleton (bundle-10) paints the rail and topbar
 *     exactly where, and in the colour, the mounted shell does;
 *   - the tenant label rides the session call (delivery-07);
 *   - a deep link carries no Home modulepreload (the `/` variant is served by
 *     the backend and pinned in tests/unit/test_frontend_build_artifacts.py:
 *     `vite preview` serves plain index.html for every path);
 *   - route-data prefetch on RouteNav intent (delivery-03) and saveData
 *     (bundle-09 item 3);
 *   - the identity boundary (genie-02 item 1): only a trusted health probe
 *     moves the actor;
 *   - a failed boot prime falls through to the real request.
 *
 * Layout assertions compare the same renderer before and after mount and
 * assert no text, so Linux Geist Mono metrics cannot move them.
 */
import type { Page } from '@playwright/test';
import type { ConfigOptions } from '../../../src/types';
import type { HealthPayload } from '../../../src/lib/apiTypes';
import { KNOWN_VIOLATIONS, expectAxeClean } from './axe';
import { GENIE_QUESTION, registerGenieTurn } from './data/genieTurn';
import { LENDER_NAME } from './data/reference';
import { CONFIG_OPTIONS, HEALTH_OK } from './data/shell';
import { json, type ApiCall } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const IN_FLIGHT_KEY = 'mip.genie.inFlightTurn';
const HEALTH_PATH = /^\/api(\/v1)?\/health$/;
const AUDITED_READS = /^\/api(\/v1)?\/(leads(\/|$)|borrowers\/|outreach\/draft|offers\/recommend)/;
const IDLE_CHUNK = /\/assets\/(portfolio-builder|analytics|segment-intelligence|lead-queue|Console|GenieChat)-[\w-]{8}\.js$/;

const key = (call: ApiCall) => `${call.method} ${call.path}${call.search ? `?${call.search}` : ''}`;

function nav(page: Page, name: RegExp) {
  return page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name });
}

test.describe('boot reads', () => {
  test('(1) a cold / starts the four non-audited boot reads once each, before the entry does any work, and no audited read', async ({ app, page, mockApi }) => {
    const requests: Array<{ path: string; search: string; at: number }> = [];
    page.on('request', (request) => {
      const url = new URL(request.url());
      requests.push({ path: url.pathname, search: url.search, at: Date.now() });
    });
    await app.gotoRoute('/');

    const calls = mockApi.calls.map(key);
    for (const read of ['GET /api/session', 'GET /api/config/options', 'GET /api/config/footprint']) {
      expect(calls.filter((call) => call === read), read).toHaveLength(1);
    }
    const health = requests.filter((request) => HEALTH_PATH.test(request.path));
    expect(health[0]?.search, 'the first probe is the prime, with the first poll\'s hint').toBe('?idle_s=0');
    expect(
      health.filter((request) => request.at - health[0].at < 7_000),
      'the prime replaced the first poll: one probe at boot, not two',
    ).toHaveLength(1);
    expect(mockApi.calls.filter((call) => AUDITED_READS.test(call.path)).map(key), 'no audit-writing read on Home').toEqual([]);

    const firstSession = requests.findIndex((request) => /^\/api\/v1\/session$/.test(request.path));
    const firstHomeChunk = requests.findIndex((request) => /^\/assets\/home-/.test(request.path));
    expect(firstSession).toBeGreaterThanOrEqual(0);
    expect(firstHomeChunk, 'the entry preloads the Home chunk').toBeGreaterThan(-1);
    expect(firstSession, 'the session read starts before the entry runs').toBeLessThan(firstHomeChunk);
  });

  test('(2) the served HTML loads the boot module just before the entry: no inline script, no fetch preload', async ({ app, page }) => {
    const response = await page.goto('/', { waitUntil: 'domcontentloaded' });
    const html = (await response?.text()) ?? '';
    const scripts = [...html.matchAll(/<script type="module" crossorigin src="\/assets\/([^"]+)"><\/script>/g)].map((m) => m[1]);
    expect(scripts).toHaveLength(2);
    expect(scripts[0]).toMatch(/^boot-[\w-]+\.js$/);
    expect(scripts[1]).toMatch(/^index-[\w-]+\.js$/);
    expect(html).not.toMatch(/<script(?![^>]*\bsrc=)[^>]*>/);
    expect(html).not.toContain('as="fetch"');
    await app.settle();
  });

  test('(4) the tenant pill shows the configured lender while /config/options is still unanswered', async ({ app, page, mockApi }) => {
    let answerOptions: () => void = () => undefined;
    const optionsHeld = new Promise<void>((resolve) => {
      answerOptions = resolve;
    });
    mockApi.register('GET', '/api/config/options', async () => {
      await optionsHeld;
      return json<ConfigOptions>(CONFIG_OPTIONS);
    });
    await page.goto('/glossary', { waitUntil: 'domcontentloaded' });

    await expect(page.getByLabel(`Configured tenant: ${LENDER_NAME}`)).toBeVisible();
    expect(mockApi.calls.filter((call) => call.path === '/api/config/options'), 'options not answered yet').toEqual([]);
    answerOptions();
    await app.settle();
    expect(mockApi.calls.filter((call) => call.path === '/api/config/options')).toHaveLength(1);
  });

  test('(9) a boot options prime answered 503 falls through to one real request, and the app renders', async ({ app, page, mockApi, hygiene }) => {
    hygiene.allow('console.error', /Failed to load resource.*503.*\/api\/v1\/config\/options/);
    let optionsCalls = 0;
    mockApi.register<unknown>('GET', '/api/config/options', () => {
      optionsCalls += 1;
      return optionsCalls === 1
        ? { status: 503, body: { detail: 'Warehouse warming up', retryable: true, dependency: 'warehouse', reason: 'warming_up' } }
        : json<ConfigOptions>(CONFIG_OPTIONS);
    });
    await app.gotoRoute('/');

    expect(optionsCalls, 'the prime, then exactly one real request').toBe(2);
    await expect(page.locator('#main-content h1')).toBeVisible();
    await expect(page.getByLabel(`Configured tenant: ${LENDER_NAME}`)).toBeVisible();
  });
});

test.describe('shell skeleton', () => {
  for (const theme of FIXTURE_THEMES) {
    for (const consoleOpen of [false, true]) {
      test(`(3) paints the text-free skeleton with the mounted rail and topbar geometry and colour (${theme}, Console ${consoleOpen ? 'open' : 'closed'})`, async ({ app, page }) => {
        await app.setTheme(theme);
        if (consoleOpen) {
          await page.addInitScript(() => {
            try {
              window.localStorage.setItem('mip.consoleOpen', 'true');
            } catch {
              // about:blank has no storage; the app document seeds it.
            }
          });
        }
        let releaseEntry: () => void = () => undefined;
        const entryHeld = new Promise<void>((resolve) => {
          releaseEntry = resolve;
        });
        await page.route(/\/assets\/index-[\w-]+\.js$/, async (route) => {
          await entryHeld;
          await route.continue();
        });
        await page.goto('/glossary', { waitUntil: 'commit' });

        const skeleton = page.locator('#root > .app-shell[aria-hidden="true"]');
        await expect(skeleton).toBeAttached();
        await expect.poll(() => page.evaluate(() => getComputedStyle(document.querySelector('.app-shell') as Element).display)).toBe('grid');
        expect(await skeleton.evaluate((node) => node.textContent)).toBe('');
        expect(await page.evaluate(() => document.documentElement.dataset.console)).toBe(consoleOpen ? 'open' : 'closed');
        const measure = () => page.evaluate(() => ['rail', 'topbar', 'main'].map((name) => {
          const element = document.querySelector(`#root > .app-shell > .${name}`) as HTMLElement;
          const box = element.getBoundingClientRect();
          const style = getComputedStyle(element);
          return { name, x: box.x, y: box.y, width: box.width, height: box.height, background: style.backgroundColor, paddingRight: style.paddingRight };
        }));
        const before = await measure();

        releaseEntry();
        await expect(page.locator('#root > .app-shell:not([aria-hidden])')).toBeAttached();
        await app.settle();
        expect(await measure()).toEqual(before);
      });
    }
  }
});

test.describe('deep links and intent', () => {
  test('(5) a /lead-queue load carries no Home modulepreload', async ({ app, page }) => {
    const response = await page.goto('/lead-queue', { waitUntil: 'domcontentloaded' });
    expect(await response?.text()).not.toMatch(/rel="modulepreload"[^>]*href="\/assets\/home-/);
    await app.settle();
    await expect(page.locator('link[rel="modulepreload"][href*="/assets/home-"]')).toHaveCount(0);
  });

  test('(6) hovering Analytics reads only the executive and rate-window aggregates; hovering Leads reads no /api/leads', async ({ app, page, mockApi }) => {
    await app.gotoRoute('/glossary');
    const before = mockApi.calls.length;
    await nav(page, /^Analytics/).hover();
    await expect
      .poll(() => mockApi.calls.slice(before).map((call) => `${call.method} ${call.path}`).sort())
      .toEqual(['GET /api/analytics/executive', 'GET /api/analytics/rate-window']);

    const afterAnalytics = mockApi.calls.length;
    await nav(page, /^Leads/).hover();
    await page.waitForTimeout(1_000);
    expect(mockApi.calls.slice(afterAnalytics).map(key), 'a Lead Queue hover asks the API for nothing').toEqual([]);
  });

  for (const saveData of [false, true]) {
    test(`(7) idle chunk preloads ${saveData ? 'are suppressed under saveData' : 'run without saveData (control)'}`, async ({ app, page }) => {
      if (saveData) {
        await page.addInitScript(() => {
          Object.defineProperty(navigator, 'connection', { configurable: true, value: { saveData: true } });
        });
      }
      const chunks = new Set<string>();
      page.on('request', (request) => {
        const match = IDLE_CHUNK.exec(new URL(request.url()).pathname);
        if (match) chunks.add(match[1]);
      });
      await app.gotoRoute('/glossary');
      if (saveData) {
        await page.waitForTimeout(6_000);
        expect([...chunks]).toEqual([]);
      } else {
        await expect
          .poll(() => [...chunks].sort(), { timeout: 15_000 })
          .toEqual(['Console', 'GenieChat', 'analytics', 'lead-queue', 'portfolio-builder', 'segment-intelligence']);
      }
    });
  }
});

test.describe('identity boundary', () => {
  function thread(page: Page) {
    return page.locator('#main-content .genie-thread');
  }

  async function inFlightRecord(page: Page): Promise<string | null> {
    return page.evaluate((storageKey) => window.sessionStorage.getItem(storageKey), IN_FLIGHT_KEY);
  }

  /** Ask on /ask-genie as actor `fixture-actor-a` until the turn is polling. */
  async function turnInFlight(page: Page, app: { gotoRoute(path: string): Promise<void> }, mockApi: Parameters<typeof registerGenieTurn>[0]) {
    const actor = { key: 'fixture-actor-a' };
    mockApi.register('GET', '/api/health', () => json<HealthPayload>({ ...HEALTH_OK, actor_cache_key: actor.key }));
    const turn = registerGenieTurn(mockApi);
    await app.gotoRoute('/ask-genie');
    await page.locator('#main-content').getByRole('textbox', { name: 'Ask Genie — question' }).fill(GENIE_QUESTION);
    await page.locator('#main-content').getByRole('button', { name: 'Ask Genie', exact: true }).click();
    await expect.poll(() => turn.progressPolls).toBeGreaterThan(0);
    expect(await inFlightRecord(page)).not.toBeNull();
    const healthCalls = () => mockApi.calls.filter((call) => HEALTH_PATH.test(call.path)).length;
    return { actor, turn, healthCalls };
  }

  async function nextProbe(page: Page, healthCalls: () => number, ms: number): Promise<void> {
    const before = healthCalls();
    await page.clock.runFor(ms);
    await expect.poll(healthCalls, { timeout: 15_000 }).toBeGreaterThan(before);
  }

  async function keeps(page: Page, why: string): Promise<void> {
    expect(await inFlightRecord(page), why).not.toBeNull();
    await expect(thread(page).locator('.genie__msg--user')).toHaveText([GENIE_QUESTION]);
  }

  test('(8a) a mid-session /api/health 502 for two polls keeps the transcript and the in-flight record', async ({ app, page, mockApi }) => {
    const { healthCalls } = await turnInFlight(page, app, mockApi);
    const restore = app.degrade('/api/health', { status: 502, body: { detail: 'Bad gateway' } });
    await nextProbe(page, healthCalls, 8_000);
    await keeps(page, 'one 502');
    await nextProbe(page, healthCalls, 3_000);
    await keeps(page, 'two 502s');
    restore();
    await nextProbe(page, healthCalls, 3_000);
    await keeps(page, 'the same actor again');
  });

  test('(8b) a reload whose first probes 502 keeps the state, then the same key keeps it; another key clears it', async ({ app, page, mockApi }) => {
    const { actor, healthCalls } = await turnInFlight(page, app, mockApi);
    const restore = app.degrade('/api/health', { status: 502, body: { detail: 'Bad gateway' } });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await app.settle();
    await keeps(page, 'the reload\'s first probe 502ed: nothing clears');
    restore();
    await nextProbe(page, healthCalls, 3_000);
    await keeps(page, 'the first trusted key is the stored one');

    actor.key = 'fixture-actor-b';
    await page.reload({ waitUntil: 'domcontentloaded' });
    await app.settle();
    await expect.poll(() => inFlightRecord(page)).toBeNull();
    await expect(thread(page).locator('.genie__msg--user')).toHaveCount(0);
  });

  test('(8c) a 401 on the health poll clears the actor\'s state', async ({ app, page, mockApi }) => {
    const { healthCalls } = await turnInFlight(page, app, mockApi);
    app.degrade('/api/health', { status: 401, body: {} });
    await nextProbe(page, healthCalls, 8_000);
    await expect.poll(() => inFlightRecord(page)).toBeNull();
  });
});

test.describe('accessibility', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`(10) / is axe-clean after mount (${theme})`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/');
      await expectAxeClean(page, { key: { route: 'home', state: 'default' }, theme, known: KNOWN_VIOLATIONS });
    });
  }
});
