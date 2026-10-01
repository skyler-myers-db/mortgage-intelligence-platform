/**
 * Design-contract lane (w5-design-contract, 2026-09-30 rulings), proven on
 * the built app at 1440x900 in both themes:
 *
 *  - sparkle marks Genie and model output only (critic-12 remainder): the
 *    deterministic Home briefing and Borrower 360 story no longer draw it,
 *    while the topbar Genie toggle still does (design_files/Module 0
 *    Prototype.html:1238);
 *  - the focus ring no longer reshapes the focused element (css-06 item 4):
 *    a keyboard-focused Lead Queue scroll region and sort header keep their
 *    own square corners (the prototype rule, design_files/index.html:214,
 *    gave both 4px corners on focus) and still paint the token ring.
 *
 * The other rulings are proven where their surfaces already are:
 * theme.fixture.spec.ts (dark first visit, CTA fill, fixed data ink),
 * home-answer / segments-cards / css-hygiene.modes (segment palette) and
 * shell-wayfinding (account avatar).
 */
import type { Locator, Page } from '@playwright/test';
import { PRIMARY_BORROWER } from './data/borrowers';
import { asComputedRgb, settleTransitions } from './renderedColor';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

/** Icon.tsx `sparkle` path data (the prototype's Genie glyph). */
const SPARKLE_PATH = 'M12 2v5M12 17v5M2 12h5M17 12h5M5 5l3 3M16 16l3 3M19 5l-3 3M8 16l-3 3';

async function glyphPaths(icon: Locator): Promise<string[]> {
  return icon.locator('svg path').evaluateAll((paths) => paths.map((path) => path.getAttribute('d') ?? ''));
}

for (const theme of FIXTURE_THEMES) {
  test.describe(`sparkle is reserved for Genie (${theme})`, () => {
    test.beforeEach(async ({ app }) => {
      await app.setTheme(theme);
    });

    test('the Home briefing draws a document glyph; the Genie toggle keeps the sparkle', async ({ app, page }) => {
      await app.gotoRoute('/');
      const briefing = page.locator('.home-answer .surface__icon');
      await expect(briefing.locator('svg')).toHaveCount(1);
      expect(await glyphPaths(briefing)).not.toContain(SPARKLE_PATH);
      const genie = page.getByRole('banner').getByRole('button', { name: 'Toggle Genie chat' });
      expect(await glyphPaths(genie)).toEqual([SPARKLE_PATH]);
    });

    test('the Borrower 360 story draws a document glyph', async ({ app, page }) => {
      await app.gotoRoute(`/borrower-360/${PRIMARY_BORROWER.borrower_id}`);
      const story = page.locator('.borrower-story .surface__icon');
      await expect(story.locator('svg')).toHaveCount(1);
      expect(await glyphPaths(story)).not.toContain(SPARKLE_PATH);
    });
  });
}

interface Shape {
  radii: string[];
  outlineStyle: string;
  outlineColor: string;
  ring: string;
  focusVisible: boolean;
}

async function shape(target: Locator): Promise<Shape> {
  return target.evaluate((el) => {
    const style = getComputedStyle(el);
    return {
      radii: [style.borderTopLeftRadius, style.borderTopRightRadius, style.borderBottomRightRadius, style.borderBottomLeftRadius],
      outlineStyle: style.outlineStyle,
      outlineColor: style.outlineColor,
      ring: style.getPropertyValue('--focus-ring-color').trim(),
      focusVisible: el.matches(':focus-visible'),
    };
  });
}

/** Keyboard first, so the script focus that follows keeps :focus-visible. */
async function keyboardFocus(page: Page, target: Locator): Promise<Shape> {
  await page.keyboard.press('Tab');
  await target.focus();
  await settleTransitions(target);
  return shape(target);
}

for (const theme of FIXTURE_THEMES) {
  test(`${theme}: the focus ring keeps a square element square on /lead-queue (css-06)`, async ({ app, page }) => {
    await app.setTheme(theme);
    await app.gotoRoute('/lead-queue');
    const targets = [
      ['the table scroll region', page.locator('#main-content .tbl-wrap').first()],
      ['a sort header button', page.locator('#main-content .tbl__sort').first()],
    ] as const;
    for (const [label, target] of targets) {
      await expect(target, label).toBeVisible();
      const rest = await shape(target);
      expect(rest.radii, `${label} is square at rest (precondition)`).toEqual(['0px', '0px', '0px', '0px']);
      const focused = await keyboardFocus(page, target);
      expect(focused.focusVisible, `${label} matches :focus-visible`).toBe(true);
      expect(focused.radii, `${label} keeps its corners when focused`).toEqual(rest.radii);
      expect(focused.outlineStyle, `${label} still paints the ring`).toBe('solid');
      expect(focused.outlineColor, `${label} ring is the token colour`).toBe(await asComputedRgb(page, focused.ring));
      await target.evaluate((el) => (el as HTMLElement).blur());
    }
  });
}
