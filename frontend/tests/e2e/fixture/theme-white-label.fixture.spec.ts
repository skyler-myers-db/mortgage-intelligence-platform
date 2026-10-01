/**
 * Rendered-layer proofs for the w5-theme-white-label lane (W5b), in the built
 * app at 1440x900:
 *
 *  - the boot watchdog (12.3 review leftover): a normal boot signals ready and
 *    never swaps; an aborted entry chunk shows the reload prompt at once,
 *    focused and axe-clean; a hung entry keeps the skeleton until 20 s; Reload
 *    with the chunk released boots the app;
 *  - the palette (shell-07 item 4, bundle-09 item 2): while the borrower
 *    search is held only its status speaks; the active route row's chunk is
 *    requested and nothing else reaches the API;
 *  - the Button loading CSS contract (motion-08 slice 2): same width, label at
 *    opacity 0 but still the accessible name, a visible spinner that stops
 *    under reduced motion;
 *  - the success CTA fill (a11y-01 carryover): white on the dark fill >= 4.5:1.
 */
import type { Page, Route } from '@playwright/test';
import { expectAxeClean } from './axe';
import { contrastRatio, parseRgb } from './renderedColor';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const ENTRY_CHUNK = /\/assets\/index-[\w-]+\.js$/;
const SKELETON = '#root > .app-shell[aria-hidden="true"]';

function bootAlert(page: Page) {
  return page.getByRole('alert').filter({ hasText: 'The workspace did not finish loading' });
}

test.describe('boot watchdog', () => {
  test('a normal boot signals ready and never shows the reload prompt', async ({ app, page }) => {
    await app.gotoRoute('/glossary');
    await expect(page.locator('html')).toHaveAttribute('data-mip-boot', 'ready');
    await expect(page.locator('[data-boot-watchdog]')).toHaveCount(0);
  });

  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: an aborted entry chunk shows a focused reload prompt at once, and Reload boots the app`, async ({ app, page, hygiene }) => {
      // The one request this test fails on purpose, and the browser's log line for it.
      hygiene.allow('request-failed', /\/assets\/index-[\w-]+\.js failed/);
      hygiene.allow('console.error', /\/assets\/index-[\w-]+\.js/);
      await app.setTheme(theme);
      let abort = true;
      await page.route(ENTRY_CHUNK, (route) => (abort ? route.abort('failed') : route.fallback()));
      await page.goto('/glossary', { waitUntil: 'commit' });

      await expect(bootAlert(page)).toBeVisible();
      await expect(page.getByRole('button', { name: 'Reload' })).toBeFocused();
      await expect(page.locator(SKELETON)).toHaveCount(0);
      await expect(bootAlert(page)).not.toContainText(/assets|index-|\.js/);
      await expectAxeClean(page, { key: { route: 'glossary', state: 'boot-watchdog' }, theme, known: {} });

      abort = false;
      await page.getByRole('button', { name: 'Reload' }).click();
      await app.settle();
      await expect(page.locator('html')).toHaveAttribute('data-mip-boot', 'ready');
      await expect(page.locator('[data-boot-watchdog]')).toHaveCount(0);
    });
  }

  test.describe('a hung entry', () => {
    // A fake clock of our own: the harness's fixed Date would pre-install one.
    test.use({ fixtureNow: null });

    test('keeps the skeleton at 19 s and shows the prompt at 20 s', async ({ page }) => {
      await page.clock.install({ time: new Date('2026-07-15T12:00:00Z') });
      let held: Route | null = null;
      await page.route(ENTRY_CHUNK, (route) => {
        held = route;
      });
      try {
        await page.goto('/glossary', { waitUntil: 'commit' });
        await expect(page.locator(SKELETON)).toBeAttached();
        await page.clock.runFor(19_000);
        await expect(page.locator('[data-boot-watchdog]')).toHaveCount(0);
        await expect(page.locator(SKELETON)).toBeAttached();
        await page.clock.runFor(1_000);
        await expect(bootAlert(page)).toBeVisible();
        await expect(page.locator(SKELETON)).toHaveCount(0);
      } finally {
        await (held as Route | null)?.abort('aborted').catch(() => undefined);
      }
    });
  });
});

test.describe('command palette', () => {
  test('while the borrower search is held, only "Searching borrowers…" speaks', async ({ app, mockApi, page }) => {
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    mockApi.register('GET', '/api/borrowers/search', async () => {
      await held;
      return { body: [] };
    });
    try {
      await app.gotoRoute('/glossary');
      const palette = await app.openCommandPalette();
      await palette.getByRole('combobox').fill('zz');
      // One visible line, and the palette's live region outside the listbox says the same.
      await expect(palette.locator('.cmdk__list').locator('.cmdk__status, .cmdk__empty')).toHaveText(['Searching borrowers…']);
      await expect(palette.locator('.cmdk__panel > [role="status"]')).toHaveText('Searching borrowers…');
      await expectAxeClean(page, { key: { route: 'glossary', state: 'palette-searching' }, theme: 'dark', known: {}, include: '.cmdk' });
    } finally {
      release();
    }
  });

  test('ArrowDown onto a route row requests that route chunk and no API call', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/glossary');
    const chunks: string[] = [];
    page.on('request', (request) => {
      const match = /\/assets\/([\w-]+?)-[\w-]{8}\.js$/.exec(new URL(request.url()).pathname);
      if (match) chunks.push(match[1]);
    });
    const apiCallsBefore = mockApi.calls.length;
    const palette = await app.openCommandPalette();
    const input = palette.getByRole('combobox');
    // Rows whose chunks neither the glossary page nor the idle preloader load.
    const targets: Record<string, string> = { '/offer-orchestrator': 'offer-orchestrator', '/ask-genie': 'ask-genie', '/borrower-360': 'borrower-360' };
    let hint = '';
    for (let step = 0; step < 16 && !(hint in targets); step += 1) {
      await input.press('ArrowDown');
      hint = (await palette.locator('.cmdk__row.is-active .cmdk__row-hint').textContent()) ?? '';
    }
    expect(Object.keys(targets), 'the palette reached a cold route row').toContain(hint);
    await expect.poll(() => chunks.includes(targets[hint]), { message: `the ${targets[hint]} chunk is requested` }).toBe(true);
    expect(mockApi.calls.slice(apiCallsBefore), 'chunks only: no API call').toEqual([]);
  });
});

/** An injected primary button and its loading twin, as Button renders them. */
async function injectButtons(page: Page): Promise<void> {
  await page.locator('#main-content').evaluate((main) => {
    const host = document.createElement('div');
    host.dataset.buttonProbe = '';
    host.innerHTML =
      '<button type="button" class="btn btn--primary" data-plain>Approve outreach</button> ' +
      '<button type="button" class="btn btn--primary btn--loading" aria-busy="true" aria-disabled="true" data-loading>' +
      '<span class="btn__label">Approve outreach</span><span class="btn__spinner" aria-hidden="true"></span></button>';
    main.prepend(host);
  });
}

test.describe('Button loading CSS contract', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: same width, invisible label that still names the button, a spinner that reduced motion stops`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/glossary');
      await injectButtons(page);
      const plain = page.locator('[data-button-probe] [data-plain]');
      const loading = page.locator('[data-button-probe] [data-loading]');
      const [plainBox, loadingBox] = [await plain.boundingBox(), await loading.boundingBox()];
      expect(Math.abs((plainBox?.width ?? 0) - (loadingBox?.width ?? -1)), 'the loading twin keeps the width').toBeLessThanOrEqual(0.5);
      expect(Math.abs((plainBox?.height ?? 0) - (loadingBox?.height ?? -1)), 'and the height').toBeLessThanOrEqual(0.5);
      expect(await loading.locator('.btn__label').evaluate((el) => getComputedStyle(el).opacity)).toBe('0');
      const spinner = loading.locator('.btn__spinner');
      const spinnerBox = await spinner.boundingBox();
      expect(spinnerBox?.width ?? 0).toBeGreaterThan(0);
      expect(spinnerBox?.height ?? 0).toBeGreaterThan(0);
      // The label still names the button (visibility: hidden would drop it).
      await expect(page.locator('[data-button-probe]').getByRole('button', { name: 'Approve outreach', exact: true })).toHaveCount(2);
      // The harness pins reduced motion: the spinner stands still.
      expect(await spinner.evaluate((el) => getComputedStyle(el).animationName)).toBe('none');
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      expect(await spinner.evaluate((el) => getComputedStyle(el).animationName)).toBe('btn-spin');
    });
  }
});

test('dark: the success CTA paints white on a fill at or above 4.5:1', async ({ app, page }) => {
  await app.setTheme('dark');
  await app.gotoRoute('/glossary');
  const colors = await page.locator('#main-content').evaluate((main) => {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'btn btn--success';
    button.textContent = 'Open eligible refi subset';
    main.prepend(button);
    const style = getComputedStyle(button);
    return { fill: style.backgroundColor, ink: style.color };
  });
  expect(contrastRatio(parseRgb(colors.fill), parseRgb(colors.ink))).toBeGreaterThanOrEqual(4.5);
});
