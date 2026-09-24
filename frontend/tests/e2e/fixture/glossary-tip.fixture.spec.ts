/**
 * Glossary tips stay out of layout until shown (wave-2 integration; the
 * visual gate's slug layout-w2-glossary-tip). A tip hidden only by opacity
 * still counted toward its card's scrollable overflow: with the Console open
 * the Borrower 360 dossier card scrolled sideways (scrollWidth 574 >
 * clientWidth 531). Hidden tips are now display: none; hover still shows one.
 */
import { PRIMARY_BORROWER } from './data/borrowers';
import { expect, test } from './test';

const DOSSIER = `/borrower-360/${PRIMARY_BORROWER.borrower_id}`;

test('with the Console open, no card holding glossary terms scrolls sideways', async ({ app, page }) => {
  await app.gotoRoute(DOSSIER);
  await app.openConsole();
  await app.settle();
  const cards = page.locator('.surface:has(.glossary-term)');
  expect(await cards.count(), 'the dossier renders glossary terms inside cards').toBeGreaterThan(0);
  const overflowing = await cards.evaluateAll((nodes) =>
    nodes
      .filter((node) => node.scrollWidth > node.clientWidth)
      .map((node) => `${node.scrollWidth} > ${node.clientWidth}`),
  );
  expect(overflowing, 'cards that scroll sideways because of a hidden tip').toEqual([]);
});

test('hovering a glossary term still shows its tip, and leaving hides it', async ({ app, page }) => {
  await app.gotoRoute(DOSSIER);
  const term = page.locator('#main-content .glossary-term').first();
  const tip = term.locator('.glossary-term__tip');
  await expect(tip).toBeHidden();
  await term.hover();
  await expect(tip).toBeVisible();
  await expect.poll(() => tip.evaluate((node) => getComputedStyle(node).opacity)).toBe('1');
  await page.mouse.move(5, 5);
  await expect(tip).toBeHidden();
});
