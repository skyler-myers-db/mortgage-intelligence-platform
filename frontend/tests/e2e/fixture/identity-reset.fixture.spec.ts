/**
 * The shell toast region's keyboard path (W5b w5-identity-reset, wave-3
 * review #13; deviation:toast-actions-and-path), in the built app at
 * 1440x900. The region is the named 'Notifications' landmark; F8 moves focus
 * to the newest toast (its live action, else its Close) from anywhere, also
 * while typing, and the `?` sheet lists the binding with the rest of the
 * page's shortcuts. (T1, the former P5 of the identity-reset record; the
 * identity reset itself is proven in identity-boundary.fixture.spec.ts.)
 */
import type { Locator, Page } from '@playwright/test';
import { expectAxeClean } from './axe';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const F8_DESCRIPTION = 'Move focus to the newest notification';

function toastRegion(page: Page): Locator {
  return page.locator('section.toast-region[aria-label="Notifications"]');
}

/** A clipboard that accepts the build link, so Share this build raises a success toast. */
async function grantClipboard(page: Page): Promise<void> {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: () => Promise.resolve() },
    });
  });
}

test.describe('the toast region keyboard path (wave-3 review #13)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`(T1) ${theme}: F8 focuses the newest toast on /portfolio-builder, and the ? sheet lists it`, async ({ app, page }) => {
      await grantClipboard(page);
      await app.setTheme(theme);
      await app.gotoRoute('/portfolio-builder');
      const share = page.getByTestId('portfolio-copy-link');
      await share.click();
      const card = toastRegion(page).locator('.toast');
      await expect(card).toHaveCount(1);

      await share.focus();
      await page.keyboard.press('F8');
      await expect(card.locator('.toast__close'), 'no action on this toast: its Close takes focus').toBeFocused();
      await expectAxeClean(page, {
        key: { route: 'identity-reset', state: 'f8-toast' },
        theme,
        known: {},
        include: 'section.toast-region',
      });

      await page.keyboard.press('?');
      const sheet = page.getByTestId('shortcut-sheet');
      await expect(sheet).toContainText(F8_DESCRIPTION);
      await expect(sheet.locator('kbd', { hasText: /^F8$/ })).not.toHaveCount(0);
      await page.keyboard.press('Escape');
      await expect(sheet).toHaveCount(0);
    });
  }
});
