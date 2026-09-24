/**
 * Rendered-layer proofs for lane w2-error-telemetry at 1440x900 (audit
 * stack-01, shell-01, states-01, quality-01, shell-05, delivery-v3 and the
 * wave-1c follow-ups #6 / #7):
 *
 *   A. a render throw in the evidence drawer stays inside an open drawer
 *      frame (both themes): Genie, the route and the rail stay mounted;
 *   B. a Genie chat chunk that will not load offers Reload inside the Genie
 *      frame; C. the same for the Console, inside #workspace-console;
 *   D. an in-app navigation to an unloaded route holds the painted page
 *      (no fallback) and then focuses the new heading;
 *   E. what reaches POST /api/telemetry/rum: one closed client_error and
 *      templated api_call events, nothing message- or id-bearing;
 *   F. a Back the unsaved-changes guard holds records no route_change, and a
 *      save that finishes behind the dialog lets the held Back through.
 *
 * Only the API (and, for B, C and D, one hashed chunk) is faked; the
 * scenarios live in data/errorTelemetry.ts.
 */
import AxeBuilder from '@axe-core/playwright';
import type { Locator, Page } from '@playwright/test';
import { PRIMARY_BORROWER } from './data/borrowers';
import {
  crashTheEvidenceDrawer,
  enableRum,
  holdChunk,
  retireChunk,
  routeRumThroughFetch,
  spendStaleChunkReload,
} from './data/errorTelemetry';
import { portfolioCreated } from './data/feedbackGuard';
import type { Hygiene } from './hygiene';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const HOME_H1 = 'Who should we contact, why now, and with what offer?';
const GLOSSARY_H1 = 'Mortgage intelligence glossary';

/**
 * The only console lines these crashes may print: the app's message-free
 * client error report, the browser's line for a retired chunk, and React's
 * own caught-error lines (as crashproof.fixture.spec.ts allows).
 */
function allowNamedErrorLines(hygiene: Hygiene): void {
  hygiene.allow('console.error', /\[mip\] client error/);
  hygiene.allow('console.error', /Failed to load resource/);
  hygiene.allow('console.error', /above error occurred|recreate this component tree/);
}

function navLink(page: Page, name: string): Locator {
  return page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name, exact: true });
}

async function axeViolations(page: Page, selector: string): Promise<string[]> {
  const results = await new AxeBuilder({ page }).include(selector).withTags(WCAG_TAGS).analyze();
  return results.violations.map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(' ')).join(' ; ')}`);
}

/** Horizontal overflow of an element: scrollWidth beyond clientWidth. */
async function horizontalOverflow(locator: Locator): Promise<number> {
  return locator.evaluate((element) => element.scrollWidth - element.clientWidth);
}

/** The surface's rendered border colour and what the danger token resolves to beside it. */
async function borderAgainstToken(surface: Locator): Promise<{ rendered: string; token: string }> {
  return surface.evaluate((element) => {
    const probe = document.createElement('div');
    probe.style.borderTop = '1px solid var(--status-danger-line-strong)';
    element.parentElement?.appendChild(probe);
    const token = getComputedStyle(probe).borderTopColor;
    probe.remove();
    return { rendered: getComputedStyle(element).borderTopColor, token };
  });
}

test.describe('A. evidence drawer render throw', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`stays inside the open drawer frame; Genie, the route and the rail stay mounted (${theme})`, async ({ app, hygiene, mockApi, page }) => {
      allowNamedErrorLines(hygiene);
      crashTheEvidenceDrawer(mockApi);
      await app.setTheme(theme);
      await app.gotoRoute('/');

      const genie = await app.openGenie();
      const genieNode = await genie.elementHandle();
      // The first KPI chip on Home is the population source (lineageFamily
      // 'marketable_population', lib/drawerSourceRegistry.ts): opening it
      // reads the lineage manifest, whose unproducible payload throws.
      const drawer = await app.openEvidenceDrawer();
      const surface = drawer.locator('[data-error-boundary="drawer"][data-error-kind="render"]');
      await expect(surface).toBeVisible();
      await expect(drawer).toHaveClass(/is-open/);
      await expect(drawer).toHaveAttribute('aria-modal', 'true');
      await expect(surface.getByRole('button')).toHaveText(['Try again', 'Reload']);
      await expect(surface.getByRole('heading', { level: 2 })).toHaveText('The evidence drawer hit an unexpected error');
      await expect(page.locator('body')).not.toContainText('Cannot read');

      expect(await genieNode?.evaluate((node) => node.isConnected), 'the Genie chat was never unmounted').toBe(true);
      await expect(page.locator('#main-content h1')).toHaveText(HOME_H1);
      await expect(page.getByRole('navigation', { name: 'Primary navigation' })).toBeVisible();

      expect(await horizontalOverflow(drawer), 'no horizontal overflow in the drawer').toBeLessThanOrEqual(0);
      expect(await horizontalOverflow(surface), 'no horizontal overflow in the surface').toBeLessThanOrEqual(0);
      const border = await borderAgainstToken(surface);
      expect(border.rendered, `border is --status-danger-line-strong (${theme})`).toBe(border.token);
      expect(await axeViolations(page, '.drawer'), `WCAG A/AA inside the drawer frame (${theme})`).toEqual([]);

      await drawer.getByRole('button', { name: 'Close drawer' }).click();
      await expect(app.evidenceDrawer()).not.toHaveClass(/is-open/);
      await expect(page.locator('[data-error-boundary="drawer"]')).toHaveCount(0);
      await navLink(page, 'Glossary').click();
      await expect(page).toHaveURL(/\/glossary$/);
      await expect(page.locator('#main-content h1')).toHaveText(GLOSSARY_H1);

      // Non-vacuity and the audit posture: the unaudited manifest read did
      // happen; no proof drawer, no draft, no write.
      expect(mockApi.calls.some((call) => call.path === '/api/lineage/manifest')).toBe(true);
      expect(mockApi.calls.filter((call) => /proof|draft|outreach|approve/.test(call.path))).toEqual([]);
    });
  }
});

test.describe('B/C. a panel chunk that will not load', () => {
  test('B: Genie offers Reload only inside its frame; the route stays usable; Close hides it and reopening shows it again', async ({ app, hygiene, page }) => {
    allowNamedErrorLines(hygiene);
    await retireChunk(page, 'GenieChat');
    await app.gotoRoute('/');
    await spendStaleChunkReload(page);

    await app.genieToggle().click();
    const panel = app.geniePanel();
    await expect(panel).toHaveClass(/is-open/);
    const surface = panel.locator('[data-error-boundary="genie"][data-error-kind="chunk"]');
    await expect(surface).toBeVisible();
    await expect(surface.getByRole('button')).toHaveText(['Reload']);
    await expect(surface.getByRole('heading', { level: 2 })).toHaveText('A new version is available');
    expect(await horizontalOverflow(surface)).toBeLessThanOrEqual(0);
    expect(await axeViolations(page, '.genie'), 'WCAG A/AA inside the Genie frame').toEqual([]);
    await expect(page.locator('#main-content h1')).toHaveText(HOME_H1);

    await panel.getByRole('button', { name: 'Close Genie' }).click();
    await expect(panel).not.toHaveClass(/is-open/);
    await app.genieToggle().click();
    await expect(panel).toHaveClass(/is-open/);
    await expect(surface).toBeVisible();
    await panel.getByRole('button', { name: 'Close Genie' }).click();

    await navLink(page, 'Glossary').click();
    await expect(page.locator('#main-content h1')).toHaveText(GLOSSARY_H1);
  });

  test('C: the Console offers Reload only inside #workspace-console; the route stays usable', async ({ app, hygiene, page }) => {
    allowNamedErrorLines(hygiene);
    await retireChunk(page, 'Console');
    await app.gotoRoute('/');
    await spendStaleChunkReload(page);

    const consolePanel = await app.openConsole();
    const surface = page.locator('#workspace-console [data-error-boundary="console"][data-error-kind="chunk"]');
    await expect(surface).toBeVisible();
    await expect(consolePanel).toHaveClass(/is-open/);
    await expect(surface.getByRole('button')).toHaveText(['Reload']);
    expect(await horizontalOverflow(consolePanel), 'the surface fits the 300px Console').toBeLessThanOrEqual(0);
    expect(await horizontalOverflow(surface)).toBeLessThanOrEqual(0);
    expect(await axeViolations(page, '#workspace-console'), 'WCAG A/AA inside the Console frame').toEqual([]);
    await expect(page.locator('#main-content h1')).toHaveText(HOME_H1);

    await consolePanel.getByRole('button', { name: 'Close console' }).click();
    // Closed, the landmark is aria-hidden, so find it by id rather than role.
    const landmark = page.locator('aside#workspace-console');
    await expect(landmark).not.toHaveClass(/is-open/);
    await expect(landmark).toHaveAttribute('aria-hidden', 'true');
    await app.openConsole();
    await expect(surface).toBeVisible();

    await navLink(page, 'Glossary').click();
    await expect(page.locator('#main-content h1')).toHaveText(GLOSSARY_H1);
  });
});

test.describe('D. route hold (shell-05)', () => {
  test('an in-app navigation to an unloaded route keeps the painted page, never mounts the fallback, then focuses the new heading', async ({ app, page }) => {
    await app.gotoRoute('/');
    const release = await holdChunk(page, 'glossary');
    await page.evaluate(() => {
      const win = window as Window & { __fallbackMounts?: number };
      win.__fallbackMounts = 0;
      new MutationObserver((records) => {
        for (const record of records) {
          for (const added of record.addedNodes) {
            if (!(added instanceof Element)) continue;
            if (added.matches('[data-route-fallback]')) win.__fallbackMounts = (win.__fallbackMounts ?? 0) + 1;
            win.__fallbackMounts = (win.__fallbackMounts ?? 0) + added.querySelectorAll('[data-route-fallback]').length;
          }
        }
      }).observe(document.getElementById('main-content')!, { childList: true, subtree: true });
    });
    const fallbackMounts = () => page.evaluate(() => (window as Window & { __fallbackMounts?: number }).__fallbackMounts);

    await navLink(page, 'Glossary').click();
    await expect(page).toHaveURL(/\/glossary$/);
    // Held: the chunk is still on the wire, and Home is what is painted.
    await expect(page.locator('#main-content h1')).toHaveText(HOME_H1);
    expect(await fallbackMounts(), 'no fallback while the chunk is held').toBe(0);

    release();
    const heading = page.locator('#main-content h1');
    await expect(heading).toHaveText(GLOSSARY_H1);
    await expect(heading).toBeFocused();
    expect(await fallbackMounts(), 'the route fallback never mounted').toBe(0);
  });
});

test.describe('E. telemetry on the wire', () => {
  test('one closed client_error and templated api_call events; no message, id, query, email or stack', async ({ app, hygiene, mockApi, page }) => {
    allowNamedErrorLines(hygiene);
    await routeRumThroughFetch(page);
    const rum = enableRum(mockApi);
    crashTheEvidenceDrawer(mockApi);
    await app.gotoRoute('/');

    const drawer = await app.openEvidenceDrawer();
    await expect(drawer.locator('[data-error-boundary="drawer"]')).toBeVisible();
    // The RUM flush timer (2 s) sends the batch; poll for it.
    await expect.poll(() => rum.events().filter((event) => event.metric === 'client_error').length, { timeout: 20_000 }).toBe(1);
    await expect
      .poll(() => rum.events().some((event) => event.metric === 'api_call' && event.details?.api_route === '/api/config/options'), { timeout: 20_000 })
      .toBe(true);

    // The borrower route's natural load, in a fresh document.
    await app.gotoRoute(`/borrower-360/${PRIMARY_BORROWER.borrower_id}`);
    await expect
      .poll(() => rum.events().some((event) => event.metric === 'api_call' && event.details?.api_route === '/api/borrowers/:id'), { timeout: 20_000 })
      .toBe(true);

    const events = rum.events();
    const clientErrors = events.filter((event) => event.metric === 'client_error');
    expect(clientErrors).toEqual([
      {
        metric: 'client_error',
        value: 1,
        rating: 'info',
        route: '/',
        details: { error_name: 'TypeError', error_kind: 'render', error_source: 'caught', boundary: 'drawer' },
      },
    ]);
    const config = events.find((event) => event.metric === 'api_call' && event.details?.api_route === '/api/config/options');
    expect(config?.details).toMatchObject({ cache: 'hit', warehouse_ms: 0, total_ms: 4 });
    const apiRoutes = events.filter((event) => event.metric === 'api_call').map((event) => String(event.details?.api_route));
    expect(apiRoutes.filter((route) => route.startsWith('/api/telemetry') || route === '/api/health')).toEqual([]);
    for (const route of apiRoutes) expect(route).toMatch(/^\/api(\/[a-z-]+|\/:id)+$/);

    const payload = rum.bodies.join('\n');
    expect(payload).not.toContain('B-');
    expect(payload).not.toContain('Cannot read');
    expect(payload).not.toContain('?');
    expect(payload).not.toContain('@');
    expect(payload).not.toContain('    at ');
  });
});

test.describe('F. the unsaved-changes guard and route_change (follow-ups #6, #7)', () => {
  test('a held Back records no route_change; a save that finishes behind the dialog lets the next Back through', async ({ app, mockApi, page }) => {
    await routeRumThroughFetch(page);
    const rum = enableRum(mockApi);
    const leaveDialog = page.getByRole('dialog', { name: 'Leave without saving?' });
    const budget = page.getByRole('spinbutton', { name: 'Budget', exact: true });

    await app.gotoRoute('/');
    await navLink(page, 'Portfolio').click();
    await app.settle();
    await budget.click();
    await budget.fill('25000');
    await budget.blur();

    await page.goBack();
    await expect(leaveDialog).toBeVisible();
    await leaveDialog.getByRole('button', { name: 'Stay' }).click();
    await expect(leaveDialog).toHaveCount(0);
    await expect(page).toHaveURL(/\/portfolio-builder$/);

    // A sentinel report, then a pagehide flush: this batch is sent after the
    // held Back, so "no route_change in it" is read from a real batch.
    await page.evaluate(() => {
      window.dispatchEvent(new ErrorEvent('error', { error: new RangeError('sentinel') }));
      window.dispatchEvent(new Event('pagehide'));
    });
    await expect
      .poll(() => rum.events().some((event) => event.metric === 'client_error' && event.details?.error_name === 'RangeError'), { timeout: 20_000 })
      .toBe(true);
    const routeChanges = () => rum.events().filter((event) => event.metric === 'route_change');
    expect(routeChanges().map((event) => [event.details?.from_route, event.route])).toEqual([['/', '/portfolio-builder']]);

    // #7: the page turns clean while "Leave without saving?" holds a Back.
    let releaseSave: () => void = () => undefined;
    const saveGate = new Promise<void>((resolve) => {
      releaseSave = resolve;
    });
    let saveRequested = false;
    mockApi.register('POST', '/api/portfolio/create', async ({ body }) => {
      saveRequested = true;
      await saveGate;
      const name = (body as { name?: unknown } | null)?.name;
      return portfolioCreated(typeof name === 'string' ? name : '');
    });
    await page.getByTestId('portfolio-save-build').click();
    await page.getByTestId('portfolio-save-name').fill('Summit IL refi cohort');
    await page.getByTestId('portfolio-save-confirm').click();
    await expect.poll(() => saveRequested).toBe(true);

    await page.goBack();
    await expect(leaveDialog).toBeVisible();
    releaseSave();
    await expect(leaveDialog).toHaveCount(0);
    await expect(page).toHaveURL(/\/$/);
    await expect(page.locator('#main-content h1')).toHaveText(HOME_H1);
  });
});
