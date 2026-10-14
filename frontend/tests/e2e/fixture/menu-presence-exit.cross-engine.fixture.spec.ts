/**
 * Presence exits across engines (motion-01 item 2e, W5d): a FilterSelect
 * menu, the account menu and the ? shortcut sheet stay mounted while they
 * fade, aria-hidden and inert, with focus already back on their trigger,
 * then unmount. Runs in fixture-chromium on every fixture run and in
 * fixture-webkit and fixture-firefox-forced in the e2e-cross-engine CI job
 * (MIP_CROSS_ENGINE=1; playwright.config.ts FIREFOX_FORCED_SPEC names it, as
 * it names genie-pagehide). The exits are lengthened by a test stylesheet so the
 * mid-exit frame can be read on a loaded runner: the hook waits for the
 * element's own transition, whatever its length.
 */
import type { Page } from '@playwright/test';
import { expect, test } from './test';

test.use({ contextOptions: { reducedMotion: 'no-preference' } });

const SLOW_EXITS = `
  .filter-menu.is-closing { transition-duration: 4s !important; }
  .cmdk:not([open]) { transition-duration: 4s !important; }
`;

const runningOwnTransition = (page: Page, selector: string) =>
  page.locator(selector).first().evaluate((element) => (
    element.getAnimations().some((animation) => animation instanceof CSSTransition && animation.playState === 'running')
  ));

test('a FilterSelect menu fades out mounted, aria-hidden and inert, then unmounts', async ({ app, page }) => {
  await app.gotoRoute('/lead-queue');
  await page.addStyleTag({ content: SLOW_EXITS });
  const trigger = page.locator('button[aria-haspopup="listbox"][aria-label^="STATE:"]').first();
  const menu = await app.openFilterMenu('STATE');
  // Close after the entry keyframes end: while filter-menu-in still animates
  // opacity, an engine may not start the exit transition at all (WebKit
  // doesn't), and the hook then releases the menu on its fallback timer.
  await menu.evaluate((element) => Promise.all(element.getAnimations().map((animation) => animation.finished)));
  await page.keyboard.press('Escape');

  const closing = page.locator('.filter-menu.is-closing');
  await expect(closing).toHaveCount(1);
  await expect(closing).toHaveAttribute('aria-hidden', 'true');
  await expect(closing).toHaveAttribute('inert', '');
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await expect(trigger).toBeFocused();
  expect(await runningOwnTransition(page, '.filter-menu.is-closing')).toBe(true);
  await expect(page.locator('.filter-menu')).toHaveCount(0, { timeout: 15_000 });
});

test('the account menu fades out mounted and inert, and a reopen mid-exit lands on the requested item', async ({ app, page }) => {
  await app.gotoRoute('/');
  await page.addStyleTag({ content: SLOW_EXITS });
  const trigger = page.getByTestId('identity-menu-trigger');
  await trigger.click();
  await expect(page.getByRole('menu', { name: 'Account' })).toBeVisible();
  await page.keyboard.press('Escape');

  const panel = page.locator('.identity-menu__panel');
  await expect(panel).toHaveClass(/is-closing/);
  await expect(panel).toHaveAttribute('aria-hidden', 'true');
  await expect(trigger).toBeFocused();
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');

  await page.keyboard.press('ArrowUp');
  await expect(page.locator('.identity-menu__panel')).toHaveCount(1);
  await expect(page.locator('.identity-menu__panel')).not.toHaveClass(/is-closing/);
  await expect(page.getByRole('menuitem', { name: 'Glossary' })).toBeFocused();

  await page.keyboard.press('Escape');
  await expect(page.locator('.identity-menu__panel')).toHaveCount(0, { timeout: 15_000 });
});

test('the ? sheet closes its dialog at exit start and unmounts after the fade', async ({ app, page }) => {
  await app.gotoRoute('/lead-queue');
  await page.addStyleTag({ content: SLOW_EXITS });
  await page.locator('body').press('Shift+?');
  const sheet = page.getByTestId('shortcut-sheet');
  await expect(sheet).toBeVisible();
  await page.keyboard.press('Escape');

  const dialog = page.locator('dialog.cmdk', { has: sheet });
  await expect(dialog).toHaveCount(1);
  await expect(dialog).not.toHaveAttribute('open', '');
  await expect(dialog).toHaveAttribute('aria-hidden', 'true');
  await expect(sheet).toHaveCount(0, { timeout: 15_000 });
});
