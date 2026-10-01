/**
 * Rendered-layer proofs for the w5-platform-backend lane at 1440x900, both
 * themes:
 *
 *  - critic-08, the Topbar tooltips (ui/Tooltip + ui/tooltipController):
 *    hover opens only after the delay; keyboard focus opens at once; Escape
 *    closes it and focus stays; the next control opens at once inside the
 *    reopen grace (skip-delay); the command palette shows its shortcut in a
 *    `<kbd>`; `aria-describedby` resolves to the tooltip text; no migrated
 *    control carries a native `title`; opening a tooltip moves no topbar
 *    box; axe is clean with a tooltip open.
 *  - 12.3 (gold-only readiness): an Admin sources answer whose readiness
 *    summary is absent renders "readiness unavailable" with a warn dot,
 *    axe-clean.
 *
 * Timings are measured inside the page (a pointerover timestamp against the
 * popup's own mutation), so they hold under load.
 */
import type { Locator, Page } from '@playwright/test';
import { expectAxeClean } from './axe';
import { json } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const SHOW_DELAY_MS = 350;
const POPUP = '#mip-tooltip';

function popup(page: Page): Locator {
  return page.locator(`${POPUP}[role="tooltip"]:not([hidden])`);
}

/** The Topbar controls migrated off native `title`. */
function migrated(page: Page): Record<string, Locator> {
  return {
    palette: page.locator('.topbar__search-kbd'),
    theme: page.getByRole('button', { name: 'Toggle theme' }),
    genie: page.getByRole('button', { name: 'Toggle Genie chat' }),
    console: page.getByRole('button', { name: 'Toggle console' }),
    tenant: page.locator('.topbar__actions > .topbar__pill').first(),
    status: page.getByTestId('system-status-pill'),
  };
}

/** The ui/tooltipController chunk loads after the first paint; its install creates the hidden popup. */
async function controllerReady(page: Page): Promise<void> {
  await expect(page.locator(POPUP)).toBeAttached();
}

/** Resolve an element's aria-describedby to text. */
async function describedText(target: Locator): Promise<string> {
  return target.evaluate((node) =>
    (node.getAttribute('aria-describedby') ?? '')
      .split(/\s+/)
      .filter(Boolean)
      .map((id) => document.getElementById(id)?.textContent ?? '')
      .join(' | '),
  );
}

/** Milliseconds from the pointer entering `target` to the popup showing its text. */
async function armShowTimer(page: Page, target: Locator, text: string): Promise<void> {
  await target.evaluate(
    (node, expected) => {
      const record = window as unknown as { __tipEnteredAt?: number; __tipShownAt?: number };
      record.__tipEnteredAt = undefined;
      record.__tipShownAt = undefined;
      node.addEventListener('pointerover', () => { record.__tipEnteredAt ??= performance.now(); }, { once: true });
      const observer = new MutationObserver(() => {
        const tip = document.getElementById('mip-tooltip');
        if (tip && !tip.hidden && tip.textContent?.startsWith(expected)) {
          record.__tipShownAt ??= performance.now();
          observer.disconnect();
        }
      });
      observer.observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
    },
    text,
  );
}

async function shownAfterMs(page: Page): Promise<number> {
  await page.waitForFunction(() => (window as unknown as { __tipShownAt?: number }).__tipShownAt !== undefined);
  return page.evaluate(() => {
    const record = window as unknown as { __tipEnteredAt: number; __tipShownAt: number };
    return record.__tipShownAt - record.__tipEnteredAt;
  });
}

interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

async function topbarBoxes(page: Page): Promise<Box[]> {
  return page.locator('.topbar > *, .topbar__actions > *').evaluateAll((nodes) =>
    nodes.map((node) => {
      const rect = node.getBoundingClientRect();
      return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
    }),
  );
}

test.use({ viewport: { width: 1440, height: 900 } });

for (const theme of FIXTURE_THEMES) {
  test.describe(`${theme}`, () => {
    test('topbar tooltips replace native titles and keep names and descriptions', async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/');
      await controllerReady(page);
      const controls = migrated(page);

      for (const [name, control] of Object.entries(controls)) {
        await expect(control, name).toBeVisible();
        await expect(control, `${name} carries no native title`).not.toHaveAttribute('title', /.*/);
      }
      await expect(controls.theme).toHaveAccessibleName('Toggle theme');
      await expect(controls.palette).toHaveAccessibleName(/^Open command palette \((⌘K|Ctrl K)\)$/);
      expect(await describedText(controls.theme)).toBe(`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`);
      expect(await describedText(controls.palette)).toMatch(/^Command palette \((⌘K|Ctrl K)\)$/);
      expect(await describedText(controls.status)).toContain('System status · live');
    });

    test('hover waits for the delay, the neighbour skips it, and nothing in the topbar moves', async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/');
      await controllerReady(page);
      const { theme: themeButton, console: consoleButton } = migrated(page);
      const before = await topbarBoxes(page);

      await armShowTimer(page, themeButton, 'Switch to');
      await themeButton.hover();
      expect(await shownAfterMs(page), 'hover waits out the delay').toBeGreaterThanOrEqual(SHOW_DELAY_MS - 10);
      await expect(popup(page)).toBeVisible();
      expect(await popup(page).evaluate((node) => node.matches(':popover-open')), 'the tooltip is in the top layer').toBe(
        true,
      );
      await expect(themeButton).toHaveAttribute('aria-describedby', 'mip-tooltip');
      expect(await topbarBoxes(page), 'an open tooltip moves no topbar box').toEqual(before);
      await expectAxeClean(page, {
        key: { route: 'platform-backend', state: 'topbar-tooltip-open' },
        theme,
        known: {},
        include: `.topbar, ${POPUP}`,
      });

      await armShowTimer(page, consoleButton, 'Console');
      await consoleButton.hover();
      expect(await shownAfterMs(page), 'the neighbour opens inside the reopen grace').toBeLessThan(SHOW_DELAY_MS / 2);
      await expect(popup(page)).toHaveText('Console (theme, density, accent)');
    });

    test('keyboard focus opens at once, shows the shortcut, and Escape closes it with focus kept', async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/');
      await controllerReady(page);
      const palette = migrated(page).palette;

      await page.getByRole('combobox', { name: 'Search borrowers' }).click();
      await page.keyboard.press('Tab');
      await expect(palette).toBeFocused();
      await expect(popup(page)).toBeVisible();
      await expect(popup(page).locator('kbd.tooltip__kbd')).toHaveText(/^(⌘K|Ctrl K)$/);
      await expect(popup(page)).toHaveText(/^Command palette(⌘K|Ctrl K)$/);

      await page.keyboard.press('Escape');
      await expect(popup(page)).toHaveCount(0);
      await expect(palette).toBeFocused();
      expect(await describedText(palette)).toMatch(/^Command palette \((⌘K|Ctrl K)\)$/);
    });

    test('an absent readiness summary reads "readiness unavailable" with a warn dot', async ({ app, page, mockApi }) => {
      mockApi.register('GET', '/api/admin/sources', () =>
        json([
          { name: 'Voluntary Lien', status: 'unavailable', rows: null, last_updated: null, note: 'Delta Share · nightly · readiness summary has no row for this source' },
          { name: 'MLS Listings', status: 'unavailable', rows: null, last_updated: null, note: 'Cotality MLS listing feed · readiness summary has no row for this source' },
          { name: 'Building Permits', status: 'roadmap', rows: null, last_updated: null, note: 'Contracted · pending load' },
        ]),
      );
      await app.setTheme(theme);
      await app.gotoRoute('/admin-config');

      const row = page.locator('.source-status-row').filter({ hasText: 'MLS Listings' });
      await expect(row.locator('.source-status-meta')).toHaveText('readiness unavailable');
      await expect(row.locator('.status-dot')).toHaveClass(/status-dot--warn/);
      await expect(page.locator('.source-status-row').filter({ hasText: 'Building Permits' }).locator('.source-status-meta')).toHaveText(
        'roadmap',
      );
      await expectAxeClean(page, {
        key: { route: 'platform-backend', state: 'admin-sources-unavailable' },
        theme,
        known: {},
        include: '.source-status-row',
      });
    });
  });
}
