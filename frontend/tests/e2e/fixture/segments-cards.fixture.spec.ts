/**
 * Segments lane (audit 2026-09-21 wave 1b): segment cards at prototype
 * proportions on a shared row grid (visual-04), and every Segments filter in
 * the URL (flow-09). Rendered-layer proof at 1440x900.
 */
import AxeBuilder from '@axe-core/playwright';
import type { Locator, Page } from '@playwright/test';
import { registerVariedSegments } from './data/segmentsCards';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const ROUTE = '/segment-intelligence';
/** One element per subgrid row, top to bottom. */
const CARD_ROWS = [
  '.seg-card__hdr',
  '.seg-card__count',
  '.seg-card__reconcile-slot',
  '.seg-card__sub',
  '.seg-card__meta',
  '.seg-card__facets',
] as const;
/** Prototype card is ~195px (module_0_prototype_2.png); the app-added rows fit in 260. */
const MAX_CARD_HEIGHT = 260;

interface CardGeometry {
  code: string;
  top: number;
  height: number;
  rows: Record<string, number | null>;
}

async function cardGeometry(page: Page): Promise<CardGeometry[]> {
  return page.locator('.seg-grid .seg-card').evaluateAll(
    (cards, selectors) =>
      cards.map((card) => {
        const box = card.getBoundingClientRect();
        const rows: Record<string, number | null> = {};
        for (const selector of selectors) {
          const element = card.querySelector(selector);
          rows[selector] = element ? element.getBoundingClientRect().top : null;
        }
        const label = card.querySelector('.seg-card__title')?.textContent ?? '';
        return { code: label, top: box.top, height: box.height, rows };
      }),
    [...CARD_ROWS],
  );
}

/** Every present row element sits at the same y in every card of the grid row. */
function expectRowsAligned(cards: CardGeometry[]): void {
  for (const selector of CARD_ROWS) {
    const tops = cards
      .map((card) => ({ card: card.code, y: card.rows[selector] }))
      .filter((entry): entry is { card: string; y: number } => entry.y !== null);
    expect(tops.length, `${selector} renders in at least two cards`).toBeGreaterThan(1);
    for (const entry of tops) {
      expect(Math.abs(entry.y - tops[0].y), `${selector} of "${entry.card}" vs "${tops[0].card}"`).toBeLessThanOrEqual(0.5);
    }
  }
}

function facetTrigger(card: Locator, kind: 'product' | 'channel'): Locator {
  return card.locator(`.seg-card__facet-chip--${kind}`).getByRole('button');
}

for (const theme of FIXTURE_THEMES) {
  test.describe(`segment cards · ${theme}`, () => {
    test.beforeEach(async ({ app }) => {
      await app.setTheme(theme);
    });

    test('six cards share one row grid: equal tops and heights, counts on one line, at most 260px', async ({ app, page }) => {
      await app.gotoRoute(ROUTE);
      const cards = await cardGeometry(page);
      expect(cards).toHaveLength(6);
      for (const card of cards) {
        expect(Math.abs(card.top - cards[0].top), `top of "${card.code}"`).toBeLessThanOrEqual(0.5);
        expect(Math.abs(card.height - cards[0].height), `height of "${card.code}"`).toBeLessThanOrEqual(0.5);
        expect(card.height, `height of "${card.code}"`).toBeLessThanOrEqual(MAX_CARD_HEIGHT);
      }
      expectRowsAligned(cards);
    });

    test('rows stay aligned when one card has no reconcile note, a wrapped meta row or a single facet', async ({ app, mockApi, page }) => {
      registerVariedSegments(mockApi);
      await app.gotoRoute(ROUTE);
      const cards = await cardGeometry(page);
      expect(cards).toHaveLength(6);
      // The payload really varies: one card has no reconcile note and one
      // shows a single facet, so an unshared row grid would drift.
      await expect(page.locator('.seg-card__reconcile')).toHaveCount(5);
      await expect(page.locator('.seg-card__facet-chip')).toHaveCount(11);
      expectRowsAligned(cards);
    });

    test('the segment cards, facet share bars included, pass axe WCAG A/AA', async ({ app, page }) => {
      await app.gotoRoute(ROUTE);
      const results = await new AxeBuilder({ page })
        .include('.seg-grid')
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
        .analyze();
      expect(results.violations.map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(' ')).join(' ; ')}`)).toEqual([]);
    });
  });
}

test.describe('segment card facets keep one evidence trigger each', () => {
  test('every card exposes exactly one product and one channel trigger, named for the facet and its top values', async ({ app, page }) => {
    await app.gotoRoute(ROUTE);
    const cards = page.locator('.seg-grid .seg-card');
    await expect(cards).toHaveCount(6);
    for (let index = 0; index < 6; index += 1) {
      const card = cards.nth(index);
      await expect(facetTrigger(card, 'product')).toHaveCount(1);
      await expect(facetTrigger(card, 'channel')).toHaveCount(1);
      await expect(facetTrigger(card, 'product')).toHaveAccessibleName(/^Loan product mix: Conventional 58%, FHA 24%, Jumbo 12%/);
      await expect(facetTrigger(card, 'channel')).toHaveAccessibleName(/^Origination channel mix: Loan officer 62%, Digital 25%, Unknown 13%/);
    }
  });

  test('a facet trigger is keyboard-operable and opens its own drawer source without selecting the card', async ({ app, page }) => {
    await app.gotoRoute(ROUTE);
    const card = page.locator('.seg-grid .seg-card').first();
    const select = card.locator('.seg-card__select');
    await expect(select).toHaveAttribute('aria-pressed', 'false');

    // Sequential focus order: the meta row's Ask Genie entry, then the
    // product facet, then the channel facet.
    const product = facetTrigger(card, 'product');
    await card.locator('.seg-card__ask').getByRole('button').focus();
    await page.keyboard.press('Tab');
    await expect(product).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(facetTrigger(card, 'channel')).toBeFocused();
    await page.keyboard.press('Shift+Tab');
    await expect(product).toBeFocused();
    await page.keyboard.press('Enter');
    const drawer = app.evidenceDrawer();
    await expect(drawer).toHaveClass(/is-open/);
    await expect(drawer.locator('.drawer__title')).toHaveText('Loan product type evidence');
    await drawer.getByRole('button', { name: 'Close drawer' }).click();
    await expect(drawer).not.toHaveClass(/is-open/);

    const channel = facetTrigger(card, 'channel');
    // The trigger is hit-tested above the card's stretched select button.
    await channel.click();
    await expect(drawer).toHaveClass(/is-open/);
    await expect(drawer.locator('.drawer__title')).toHaveText('Origination channel evidence');
    await expect(select).toHaveAttribute('aria-pressed', 'false');
  });
});
