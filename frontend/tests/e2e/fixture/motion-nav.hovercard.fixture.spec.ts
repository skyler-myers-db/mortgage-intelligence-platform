/**
 * The evidence hover card at the rendered layer (2026-09-21 audit motion-10
 * and css-10's anchored popovers), at 1440x900 on the Lead Queue, where the
 * chip sits inside the table's own overflow clip (`.tbl-wrap`):
 *
 *  - with CSS anchor positioning: the card is in the top layer, wholly
 *    inside the viewport and flush above (or below) its chip; it follows the
 *    chip when the table scrolls, and is not painted once the chip is
 *    scrolled out of the clip;
 *  - with anchor positioning forced off: the fixed-coordinate path chooses
 *    above / below from the card's MEASURED height;
 *  - with motion allowed, pointer-out keeps the card for its --dur-instant
 *    opacity fade, then removes it;
 *  - nothing asks the API between hover and exit (a passive preview).
 * Both themes. The chip is focused for the scroll cases: a table scrolled
 * under a resting pointer moves another chip under it.
 */
import type { Locator, Page } from '@playwright/test';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const CARD_GAP_PX = 8;
/** The height the card used to be ASSUMED to have (audit motion-10), plus its margin. */
const OLD_ESTIMATE_THRESHOLD_PX = 144;

function tableChip(page: Page, index = 3): Locator {
  return page.locator('.tbl-wrap .evidence-chip').nth(index);
}

function card(page: Page): Locator {
  return page.locator('.evidence-hovercard');
}

async function box(target: Locator) {
  const rect = await target.evaluate((node) => {
    const r = node.getBoundingClientRect();
    return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height };
  });
  return rect;
}

function recordApiRequests(page: Page): string[] {
  const urls: string[] = [];
  page.on('request', (request) => {
    if (/\/api\//.test(new URL(request.url()).pathname)) urls.push(request.url());
  });
  return urls;
}

/** True when hiding the card changes no pixel of its box: it was not painted. */
async function isUnpainted(page: Page, target: Locator): Promise<boolean> {
  const rect = await box(target);
  const clip = { x: Math.max(0, rect.left), y: Math.max(0, rect.top), width: Math.max(1, rect.width), height: Math.max(1, rect.height) };
  const withCard = await page.screenshot({ clip, animations: 'disabled' });
  await target.evaluate((node) => {
    (node as HTMLElement).style.setProperty('display', 'none');
  });
  const without = await page.screenshot({ clip, animations: 'disabled' });
  await target.evaluate((node) => {
    (node as HTMLElement).style.removeProperty('display');
  });
  return withCard.equals(without);
}

test.use({ viewport: { width: 1440, height: 900 } });

for (const theme of FIXTURE_THEMES) {
  test.describe(`${theme}`, () => {
    test('an anchored card sits in the viewport by its chip, follows the table scroll, and is not painted once clipped', async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      const api = recordApiRequests(page);
      const chip = tableChip(page);
      await chip.hover();
      await expect(card(page)).toBeVisible();
      await expect(card(page)).toHaveAttribute('data-anchored', '');
      expect(await card(page).evaluate((node) => node.matches(':popover-open')), 'the card is in the top layer').toBe(true);

      const chipBox = await box(chip);
      const cardBox = await box(card(page));
      expect(cardBox.left).toBeGreaterThanOrEqual(0);
      expect(cardBox.top).toBeGreaterThanOrEqual(0);
      expect(cardBox.right).toBeLessThanOrEqual(1440);
      expect(cardBox.bottom).toBeLessThanOrEqual(900);
      const above = Math.abs(cardBox.bottom + CARD_GAP_PX - chipBox.top) <= 1;
      const below = Math.abs(chipBox.bottom + CARD_GAP_PX - cardBox.top) <= 1;
      expect(above || below, `card ${JSON.stringify(cardBox)} is flush above or below chip ${JSON.stringify(chipBox)}`).toBe(true);
      expect(Math.abs(cardBox.left + cardBox.width / 2 - (chipBox.left + chipBox.width / 2)), 'centred on the chip').toBeLessThanOrEqual(1);

      // Keep this card open without a resting pointer: focus shows it too.
      await page.mouse.move(0, 0);
      await expect(card(page)).toHaveCount(0);
      await chip.focus();
      await expect(card(page)).toBeVisible();
      const anchoredBox = await box(card(page));
      const wrap = page.locator('.tbl-wrap').first();
      await wrap.evaluate((node) => {
        node.scrollTop += 40;
      });
      await expect.poll(async () => (await box(card(page))).top, { message: 'the card moves with its chip' }).toBeCloseTo(anchoredBox.top - 40, 0);
      expect((await box(chip)).top).toBeCloseTo(chipBox.top - 40, 0);

      // Scroll the chip just out of the clip (above the sticky header), while
      // its row is still rendered: the card stays open, but is not painted.
      const wrapTop = await wrap.evaluate((node) => node.getBoundingClientRect().top);
      const chipTop = (await box(chip)).top;
      await wrap.evaluate((node, by) => {
        node.scrollTop += by;
      }, chipTop - wrapTop + 40);
      await expect(card(page)).toHaveCount(1);
      await expect.poll(() => isUnpainted(page, card(page)), { message: 'a clipped chip hides its card' }).toBe(true);

      expect(api, 'a passive preview asks the API for nothing').toEqual([]);
    });
  });
}

test('without anchor positioning, the card is placed by its measured height', async ({ app, page }) => {
  await page.addInitScript(() => {
    const supports = CSS.supports.bind(CSS);
    Object.defineProperty(CSS, 'supports', {
      configurable: true,
      value: (condition: string, value?: string) =>
        (/anchor-name/.test(condition) ? false : value === undefined ? supports(condition) : supports(condition, value)),
    });
  });
  await app.gotoRoute('/lead-queue');
  const chip = tableChip(page);
  await chip.hover();
  await expect(card(page)).toBeVisible();
  await expect(card(page)).not.toHaveAttribute('data-anchored', /.*/);
  const height = (await box(card(page))).height;
  await page.mouse.move(0, 0);
  await expect(card(page)).toHaveCount(0);

  // A chip top between the measured threshold and the old estimate's.
  const measuredThreshold = height + CARD_GAP_PX + 4;
  const target = (measuredThreshold + OLD_ESTIMATE_THRESHOLD_PX) / 2;
  const expected = target >= measuredThreshold ? 'above' : 'below';
  expect(Math.abs(measuredThreshold - OLD_ESTIMATE_THRESHOLD_PX), 'the two thresholds differ').toBeGreaterThan(4);
  const top = (await box(chip)).top;
  await page.locator('#main-content').evaluate((node, by) => {
    node.scrollTop += by;
  }, top - target);
  await expect.poll(async () => Math.abs((await box(chip)).top - target) <= 2).toBe(true);
  // Let the settled scroll pass before hovering (a fixed card hides on scroll).
  await page.waitForTimeout(200);
  await chip.hover();
  await expect(card(page)).toBeVisible();
  await expect(card(page)).toHaveClass(new RegExp(`evidence-hovercard--${expected}`));
  const chipBox = await box(chip);
  const cardBox = await box(card(page));
  if (expected === 'above') expect(Math.abs(cardBox.bottom + CARD_GAP_PX - chipBox.top)).toBeLessThanOrEqual(1);
  else expect(Math.abs(chipBox.bottom + CARD_GAP_PX - cardBox.top)).toBeLessThanOrEqual(1);
  expect(cardBox.top).toBeGreaterThanOrEqual(0);
});

test.describe('with motion allowed', () => {
  test.use({ contextOptions: { reducedMotion: 'no-preference' }, traceScreenshots: false });

  test('pointer-out keeps the card for its opacity fade, then removes it', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    const api = recordApiRequests(page);
    await tableChip(page).hover();
    await expect(card(page)).toBeVisible();
    // Let the entrance animation finish so the exit is a plain transition.
    await page.waitForTimeout(300);
    await page.evaluate(() => {
      const win = window as Window & { __cardExit?: { closingAt?: number; removedAt?: number; transitions?: Array<[string, number]> } };
      const exit: NonNullable<typeof win.__cardExit> = {};
      win.__cardExit = exit;
      const node = document.querySelector('.evidence-hovercard');
      if (!node) return;
      new MutationObserver(() => {
        if (exit.closingAt === undefined && node.classList.contains('is-closing')) {
          exit.closingAt = performance.now();
          exit.transitions = node.getAnimations()
            .filter((animation): animation is CSSTransition => animation instanceof CSSTransition)
            .map((transition) => [transition.transitionProperty, Number(transition.effect?.getComputedTiming().duration)]);
        }
      }).observe(node, { attributes: true, attributeFilter: ['class'] });
      new MutationObserver(() => {
        if (exit.removedAt === undefined && !node.isConnected) exit.removedAt = performance.now();
      }).observe(document.body, { childList: true });
    });
    await page.mouse.move(0, 0);
    await expect(card(page)).toHaveCount(0);
    const exit = await page.evaluate(
      () => (window as Window & { __cardExit?: { closingAt?: number; removedAt?: number; transitions?: Array<[string, number]> } }).__cardExit,
    );
    expect(exit?.closingAt, 'the card stayed mounted to fade').toBeDefined();
    expect(exit?.transitions).toEqual([['opacity', 80]]);
    expect((exit?.removedAt ?? 0) - (exit?.closingAt ?? 0), 'removed after its fade, not at once').toBeGreaterThanOrEqual(60);
    expect(api).toEqual([]);
  });
});
