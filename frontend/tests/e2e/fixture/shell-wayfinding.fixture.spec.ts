/**
 * Rendered-layer proofs for the wave-1c lane "shell-wayfinding" (audit
 * 2026-09-21 shell-04, shell-06, shell-08): the topbar identity menu and the
 * linked breadcrumbs with the queue context. Every assertion reads the built
 * app's DOM, geometry or computed style at 1440x900.
 */
import type { Locator, Page } from '@playwright/test';
import { expect, test, type FixtureTheme } from './test';
import { SIGNED_IN_APPROVER, sessionReply } from './data/shellWayfinding';

interface Box {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

async function boxOf(locator: Locator): Promise<Box> {
  const box = await locator.boundingBox();
  if (!box) throw new Error('element has no bounding box');
  return { left: box.x, top: box.y, right: box.x + box.width, bottom: box.y + box.height };
}

function overlaps(a: Box, b: Box): boolean {
  return a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
}

const THEMES: readonly FixtureTheme[] = ['dark', 'light'];

test.describe('identity menu (shell-06)', () => {
  test.beforeEach(({ mockApi }) => {
    mockApi.register('GET', '/api/session', () => sessionReply());
  });

  for (const theme of THEMES) {
    test(`${theme}: names the actor, opens a styled menu inside the viewport and keeps the topbar clear`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      const banner = page.getByRole('banner');
      const trigger = banner.getByRole('button', { name: 'Account menu, signed in as Jane Doe' });
      await expect(trigger).toBeVisible();
      await expect(trigger).toHaveAttribute('aria-haspopup', 'menu');

      // The fourth icon button fits beside the pills: nothing in the actions
      // cluster overlaps the centred search, and the tenant name is whole.
      const search = await boxOf(banner.getByRole('search'));
      for (const control of await banner.locator('.topbar__actions > *').all()) {
        expect(overlaps(await boxOf(control), search), 'an action overlaps the search').toBe(false);
      }
      const tenant = banner.locator('.topbar__pill-tenant');
      const clipped = await tenant.evaluate((el) => el.scrollWidth > el.clientWidth);
      expect(clipped, 'the tenant name is not ellipsized at 1440').toBe(false);

      await trigger.focus();
      await page.keyboard.press('ArrowDown');
      const menu = page.getByRole('menu', { name: 'Account' });
      await expect(menu).toBeVisible();
      await expect(page.getByRole('menuitemradio', { name: 'Dark' })).toBeFocused();
      const panel = banner.locator('.identity-menu__panel');
      await expect(panel).toContainText(SIGNED_IN_APPROVER.actor_email ?? '');
      await expect(panel.locator('.chip')).toHaveText(['Administrator', 'Approver']);
      // The lazy stylesheet applied: the popup anchors to the trigger's end
      // edge and stays inside the viewport instead of hanging off it.
      const viewport = page.viewportSize();
      if (!viewport) throw new Error('viewport size is unset');
      const panelBox = await boxOf(panel);
      const triggerBox = await boxOf(trigger);
      expect(Math.abs(panelBox.right - triggerBox.right)).toBeLessThanOrEqual(1);
      expect(panelBox.left).toBeGreaterThanOrEqual(0);
      expect(panelBox.right).toBeLessThanOrEqual(viewport.width);
      expect(panelBox.top).toBeGreaterThan(triggerBox.bottom);
      await expect(panel).toHaveCSS('display', 'grid');

      await page.keyboard.press('Escape');
      await expect(menu).toBeHidden();
      await expect(trigger).toBeFocused();
    });
  }

  test('a theme picked in the menu repaints the app and the menu marks it', async ({ app, page }) => {
    await app.setTheme('dark');
    await app.gotoRoute('/');
    const trigger = page.getByRole('banner').getByRole('button', { name: /^Account menu/ });
    await trigger.click();
    await page.getByRole('menuitemradio', { name: 'Light' }).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await expect(trigger).toBeFocused();
    await trigger.click();
    await expect(page.getByRole('menuitemradio', { name: 'Light' })).toHaveAttribute('aria-checked', 'true');
  });

  test('Keyboard shortcuts dispatches the overlay event; Glossary navigates', async ({ app, page }) => {
    await app.gotoRoute('/');
    await page.evaluate(() => {
      (window as unknown as { __shortcutEvents: number }).__shortcutEvents = 0;
      window.addEventListener('mip:open-shortcuts', () => {
        (window as unknown as { __shortcutEvents: number }).__shortcutEvents += 1;
      });
    });
    const trigger = page.getByRole('banner').getByRole('button', { name: /^Account menu/ });
    await trigger.click();
    await page.getByRole('menuitem', { name: 'Keyboard shortcuts' }).click();
    await expect.poll(() => page.evaluate(() => (window as unknown as { __shortcutEvents: number }).__shortcutEvents)).toBe(1);
    await trigger.click();
    await page.getByRole('menuitem', { name: 'Glossary' }).click();
    await expect(page).toHaveURL(/\/glossary$/);
  });
});

/** The masked ids of the ranked rows, in the order the queue shows them. */
async function queueIds(page: Page): Promise<string[]> {
  const rows = page.locator('table.tbl tbody tr:not(.tbl__expand)');
  await expect(rows.first()).toBeVisible();
  const texts = await rows.allInnerTexts();
  return texts.map((text) => /B-[0-9A-Z]{13}/.exec(text)?.[0]).filter((id): id is string => Boolean(id));
}

async function openDossierFromQueue(page: Page, rowIndex: number): Promise<void> {
  const toggle = page.locator('table.tbl tbody [aria-expanded]').nth(rowIndex);
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await page.locator('tr.tbl__expand').getByRole('link', { name: 'Open Borrower 360' }).click();
}

test.describe('queue-to-dossier wayfinding (shell-04)', () => {
  for (const theme of THEMES) {
    test(`${theme}: linked crumbs name the queue and the borrower, and fit the topbar`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue?state=IL');
      const ids = await queueIds(page);
      expect(ids.length).toBe(3);
      await openDossierFromQueue(page, 1);
      await expect(page).toHaveURL(new RegExp(`/borrower-360/${ids[1]}$`));
      await app.settle();

      const crumbs = page.getByRole('banner').getByRole('navigation', { name: 'Breadcrumb' });
      const queueLink = crumbs.getByRole('link', { name: 'Lead Queue · IL' });
      await expect(queueLink).toHaveAttribute('href', '/lead-queue?state=IL');
      const current = crumbs.locator('[aria-current="page"]');
      await expect(current).toHaveText(ids[1]);
      // The trail fits the left track: whole labels, clear of the search.
      const search = await boxOf(page.getByRole('banner').getByRole('search'));
      expect((await boxOf(crumbs)).right).toBeLessThanOrEqual(search.left);
      for (const crumb of [queueLink, current]) {
        const clipped = await crumb.evaluate((el) => el.scrollWidth > el.clientWidth);
        expect(clipped, `${await crumb.textContent()} is not ellipsized`).toBe(false);
      }
      // The crumb returns to the exact filtered queue.
      await queueLink.click();
      await expect(page).toHaveURL(/\/lead-queue\?state=IL$/);
      await expect(page.locator('[aria-label^="STATE:"]').first()).toHaveAttribute('aria-label', /^STATE: IL\b/);
      expect(await queueIds(page)).toEqual(ids);
    });
  }

  test('a dossier opened by URL keeps its queue crumb through the session fallback; Offer and asset crumbs link to real indexes', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue?state=IL');
    const ids = await queueIds(page);
    // A fresh document with no history state: the stored queue lists it.
    await app.gotoRoute(`/borrower-360/${ids[2]}`);
    const crumbs = page.getByRole('navigation', { name: 'Breadcrumb' });
    await expect(crumbs.getByRole('link', { name: 'Lead Queue · IL' })).toHaveAttribute('href', '/lead-queue?state=IL');

    await page.getByRole('link', { name: 'Build outreach draft' }).click();
    await expect(page).toHaveURL(new RegExp(`/offer-orchestrator/${ids[2]}$`));
    await app.settle();
    await expect(crumbs.getByRole('listitem')).toHaveText([/Lead Queue · IL/, new RegExp(ids[2]), /Offer Orchestrator/]);
    await expect(crumbs.locator('[aria-current="page"]')).toHaveText('Offer Orchestrator');
    await crumbs.getByRole('link', { name: ids[2] }).click();
    await expect(page).toHaveURL(new RegExp(`/borrower-360/${ids[2]}$`));
    await expect(crumbs.locator('[aria-current="page"]')).toHaveText(ids[2]);

    await app.gotoRoute('/data-estate/assets/borrower_360');
    const assetCrumbs = page.getByRole('navigation', { name: 'Breadcrumb' });
    await expect(assetCrumbs.getByRole('link', { name: 'Data estate' })).toHaveAttribute('href', '/admin-config');
    await expect(assetCrumbs.locator('[aria-current="page"]')).toHaveText('Governed asset');
  });
});
