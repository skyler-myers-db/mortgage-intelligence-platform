/**
 * Segments lane (audit 2026-09-21 wave 1b): segment cards at prototype
 * proportions on a shared row grid (visual-04), and every Segments filter in
 * the URL (flow-09). Rendered-layer proof at 1440x900.
 */
import AxeBuilder from '@axe-core/playwright';
import type { Locator, Page } from '@playwright/test';
import type { SegmentSummary } from '../../../src/types';
import type { AppDriver } from './app';
import { PRIMARY_BORROWER } from './data/borrowers';
import {
  ALL_CODE_ELIGIBLE_SEGMENTS,
  BIG_SEGMENT_COUNTS,
  GATED_SEGMENTS,
  registerBigCountSegments,
  registerGatedSegments,
  registerVariedSegments,
} from './data/segmentsCards';
import { json } from './mockApi';
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
/** `.seg-grid` lays six columns from a 1281px `main` container (10-native-analytics.css). */
const SIX_COLUMN_MIN_CONTAINER = 1281;
/** The design width, and the narrowest viewport still in the six-column band. */
const CARD_WIDTHS = [1440, 'narrowest'] as const;
type CardWidth = (typeof CARD_WIDTHS)[number];

function widthLabel(width: CardWidth): string {
  return width === 1440 ? '1440px' : 'the narrowest six-column width';
}

async function segGridColumns(page: Page): Promise<number> {
  return page.locator('.seg-grid').evaluate((grid) => getComputedStyle(grid).gridTemplateColumns.split(' ').length);
}

/**
 * Open the route at one of the card widths. The narrowest six-column
 * viewport is MEASURED, not assumed: everything in the viewport outside
 * `.main`'s content box (the rail, and the scrollbar lane `.main` reserves
 * with `scrollbar-gutter: stable`, audit css-09) is read at 1440 and added to
 * the band's floor. A literal 1356 fell out of the band, into three columns,
 * once the reserved lane took 11px of the container. The band edge is
 * checked both ways: six columns there, fewer one pixel narrower.
 */
async function openAtCardWidth(app: AppDriver, page: Page, width: CardWidth): Promise<number> {
  await page.setViewportSize({ width: 1440, height: 900 });
  await app.gotoRoute(ROUTE);
  if (width === 1440) return 1440;
  const chrome = await page.locator('.main').evaluate((main) => {
    const style = getComputedStyle(main);
    return window.innerWidth - (main.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight));
  });
  const narrowest = SIX_COLUMN_MIN_CONTAINER + chrome;
  await page.setViewportSize({ width: narrowest - 1, height: 900 });
  await layoutTracksViewport(page, chrome);
  expect(await segGridColumns(page), `one pixel under ${narrowest}px leaves the six-column band`).toBeLessThan(6);
  await page.setViewportSize({ width: narrowest, height: 900 });
  await layoutTracksViewport(page, chrome);
  expect(await segGridColumns(page), `${narrowest}px is inside the six-column band`).toBe(6);
  return narrowest;
}

/**
 * Wait until layout has caught up with the viewport: right after
 * setViewportSize, Chromium can report the new innerWidth while .main still
 * has the previous width, so a column count read at once belongs to the old
 * layout (it failed 6 in 20 under load; wave-2 css-hygiene review).
 */
async function layoutTracksViewport(page: Page, chrome: number): Promise<void> {
  await expect
    .poll(() => page.locator('.main').evaluate((main, chromeWidth) => {
      const style = getComputedStyle(main);
      const content = main.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
      return Math.abs(window.innerWidth - chromeWidth - content);
    }, chrome), { message: '.main content box tracks the new viewport width' })
    .toBeLessThanOrEqual(0.5);
}

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

interface CountGeometry {
  text: string;
  height: number;
  lineHeight: number;
  right: number;
  /** Right edge of the card's `avg`, or null when the card shows none. */
  avgRight: number | null;
  /** Right edge of the card's content box: neither may run past it. */
  cardContentRight: number;
}

async function countGeometry(page: Page): Promise<CountGeometry[]> {
  return page.locator('.seg-grid .seg-card__count').evaluateAll((counts) =>
    counts.map((count) => {
      const box = count.getBoundingClientRect();
      const card = count.closest('.seg-card');
      const cardBox = card?.getBoundingClientRect();
      const cardStyle = card ? getComputedStyle(card) : null;
      const cardContentRight = cardBox && cardStyle
        ? cardBox.right - parseFloat(cardStyle.paddingRight) - parseFloat(cardStyle.borderRightWidth)
        : Number.NaN;
      const avg = card?.querySelector('.seg-card__avg');
      return {
        text: count.textContent ?? '',
        height: box.height,
        lineHeight: parseFloat(getComputedStyle(count).lineHeight),
        right: box.right,
        avgRight: avg ? avg.getBoundingClientRect().right : null,
        cardContentRight,
      };
    }),
  );
}

function facetTrigger(card: Locator, kind: 'product' | 'channel'): Locator {
  return card.locator(`.seg-card__facet-chip--${kind}`).getByRole('button');
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
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

    test('rows stay aligned and within 260px when cards have no reconcile note, a pending delta, a single facet or no borrowers', async ({ app, mockApi, page }) => {
      registerVariedSegments(mockApi);
      await app.gotoRoute(ROUTE);
      const cards = await cardGeometry(page);
      expect(cards).toHaveLength(6);
      // The payload really varies: one card has no reconcile note, one shows
      // a single facet and one has no borrowers (no avg, no facets), so an
      // unshared row grid would drift.
      await expect(page.locator('.seg-card__reconcile:not(.seg-card__reconcile--empty)')).toHaveCount(4);
      await expect(page.locator('.seg-card__facet-chip')).toHaveCount(9);
      expectRowsAligned(cards);

      // The fresh-deploy status and the zero-count reason each keep their
      // card inside the target: neither wraps the meta row (review round 1).
      for (const card of cards) {
        expect(card.height, `height of "${card.code}"`).toBeLessThanOrEqual(MAX_CARD_HEIGHT);
      }
      const pending = page.locator('.seg-card', { hasText: 'HELOC Intent' });
      await expect(pending.locator('.seg-card__meta')).toContainText('Δ —');
      await expect(pending.locator('.seg-card__meta .sr-only')).toHaveText('first snapshot · deltas pending');
      const empty = page.locator('.seg-card', { hasText: 'Retention Risk' });
      await expect(empty.locator('.seg-card__reconcile--empty')).toHaveText('no borrowers in current view');
    });

    // A national footprint puts headline counts at six to eight digits. The
    // count must stay one unbroken line (never `1,234,56` / `7`) and inside
    // its card, with `avg` giving way instead, down to the narrowest card of
    // the six-column band.
    for (const width of CARD_WIDTHS) {
      test(`six- to eight-digit counts stay on one line inside the card at ${widthLabel(width)}, rows aligned`, async ({ app, mockApi, page }) => {
        registerBigCountSegments(mockApi);
        await openAtCardWidth(app, page, width);
        await expect(page.locator('.seg-grid .seg-card__count')).toHaveText(
          BIG_SEGMENT_COUNTS.map((count) => count.toLocaleString('en-US')),
        );
        const counts = await countGeometry(page);
        expect(counts).toHaveLength(6);
        for (const count of counts) {
          expect(count.lineHeight, `line-height of "${count.text}"`).toBeGreaterThan(0);
          expect(Math.abs(count.height - count.lineHeight), `"${count.text}" renders on one line`).toBeLessThanOrEqual(0.5);
          expect(count.right, `"${count.text}" ends inside its card`).toBeLessThanOrEqual(count.cardContentRight + 0.5);
          // `avg` gives way by wrapping below the count, never by running out of the card.
          expect(count.avgRight, `avg beside "${count.text}"`).not.toBeNull();
          expect(count.avgRight ?? Number.POSITIVE_INFINITY, `avg beside "${count.text}" ends inside its card`).toBeLessThanOrEqual(count.cardContentRight + 0.5);
        }
        expectRowsAligned(await cardGeometry(page));
      });
    }

    // Gated segments (review round 2): a gated card's meta row used to wrap
    // the state chip, the source name and the evidence chip to three lines,
    // and the shared row stretched its connected neighbours with it (302px
    // at 1440). Each of the three now takes one line of a row it shares.
    for (const width of CARD_WIDTHS) {
      test(`gated cards put the state chip beside the count, the source on one line and the evidence chip alone in the meta row, rows aligned, at ${widthLabel(width)}`, async ({ app, mockApi, page }) => {
        registerGatedSegments(mockApi);
        await openAtCardWidth(app, page, width);
        const cards = await cardGeometry(page);
        expect(cards).toHaveLength(GATED_SEGMENTS.length);
        const gatedRows = GATED_SEGMENTS.filter((row) => row.source_status !== 'connected');
        await expect(page.locator('.seg-grid .seg-card--gated')).toHaveCount(gatedRows.length);
        const gridRows = new Map<number, CardGeometry[]>();
        for (const card of cards) {
          // The 260px target is the 1440 design width's (at the narrowest
          // six-column width a connected card's own meta row wraps its Ask
          // Genie entry, gated or not).
          if (width === 1440) expect(card.height, `height of "${card.code}"`).toBeLessThanOrEqual(MAX_CARD_HEIGHT);
          const key = Math.round(card.top);
          gridRows.set(key, [...(gridRows.get(key) ?? []), card]);
        }
        expect(gridRows.size, 'twelve cards fill two six-card grid rows').toBe(2);
        for (const row of gridRows.values()) expectRowsAligned(row);

        const gated = await page.locator('.seg-grid .seg-card--gated').evaluateAll((elements) =>
          elements.map((card) => {
            const middle = (element: Element | null) => {
              const box = element?.getBoundingClientRect();
              return box ? box.top + box.height / 2 : Number.NaN;
            };
            const source = card.querySelector<HTMLElement>('.seg-card__source');
            const chip = card.querySelector('.seg-card__meta .evidence-chip');
            return {
              title: card.querySelector('.seg-card__title')?.textContent ?? '',
              state: card.querySelector('.seg-card__count-row .seg-card__gate')?.textContent ?? null,
              stateOffset: Math.abs(middle(card.querySelector('.seg-card__gate')) - middle(card.querySelector('.seg-card__count'))),
              metaItems: card.querySelector('.seg-card__meta')?.childElementCount ?? Number.NaN,
              metaHeight: card.querySelector('.seg-card__meta')?.getBoundingClientRect().height ?? Number.NaN,
              chipHeight: chip?.getBoundingClientRect().height ?? Number.NaN,
              source: source?.textContent ?? null,
              sourceHeight: source?.getBoundingClientRect().height ?? Number.NaN,
              sourceLineHeight: source ? parseFloat(getComputedStyle(source).lineHeight) : Number.NaN,
              sourceFits: source ? source.scrollWidth <= source.clientWidth : false,
            };
          }),
        );
        expect(gated.map((card) => card.state)).toEqual(
          gatedRows.map((row) => (row.source_status === 'not_licensed' ? 'not licensed' : 'not connected')),
        );
        expect(gated.map((card) => card.source)).toEqual(gatedRows.map((row) => row.source_name));
        for (const card of gated) {
          expect(card.stateOffset, `state chip of "${card.title}" sits on the count's line`).toBeLessThanOrEqual(2);
          expect(card.sourceHeight, `source of "${card.title}" is one line`).toBeLessThanOrEqual(card.sourceLineHeight + 0.5);
          expect(card.sourceFits, `source of "${card.title}" is not truncated`).toBe(true);
          expect(card.metaItems, `meta row of "${card.title}" holds only the evidence chip`).toBe(1);
          expect(card.metaHeight, `meta row of "${card.title}" is one line`).toBeLessThanOrEqual(card.chipHeight + 0.5);
        }
      });
    }

    test('loading cards hold the loaded grid height, so nothing below the grid moves when the data arrives', async ({ app, mockApi, page }) => {
      let release = (): void => undefined;
      const held = new Promise<void>((resolve) => {
        release = resolve;
      });
      mockApi.register('GET', '/api/segments', async () => {
        await held;
        return json<SegmentSummary[]>([...ALL_CODE_ELIGIBLE_SEGMENTS]);
      });
      await page.goto(ROUTE, { waitUntil: 'domcontentloaded' });
      const grid = page.locator('.seg-grid');
      // One loading card per registered segment code, the payload's length.
      await expect(grid.locator('.seg-card--skeleton')).toHaveCount(ALL_CODE_ELIGIBLE_SEGMENTS.length);
      await page.evaluate(() => document.fonts.ready.then(() => undefined));
      const heights = () => grid.locator('.seg-card').evaluateAll((cards) => cards.map((card) => card.getBoundingClientRect().height));
      const gridHeight = () => grid.evaluate((element) => element.getBoundingClientRect().height);
      const loadingCards = await heights();
      const loadingGrid = await gridHeight();

      release();
      await expect(grid.locator('.seg-card--skeleton')).toHaveCount(0);
      await app.settle();
      const loadedCards = await heights();
      expect(loadedCards).toHaveLength(ALL_CODE_ELIGIBLE_SEGMENTS.length);
      for (const [index, height] of loadedCards.entries()) {
        expect(Math.abs(height - loadingCards[index]), `card ${index}: loaded ${height}px vs loading ${loadingCards[index]}px`).toBeLessThanOrEqual(1);
      }
      expect(Math.abs((await gridHeight()) - loadingGrid), 'grid height across the load').toBeLessThanOrEqual(1);
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
  // WCAG 2.5.3 Label in Name (Level A): each trigger's accessible name starts
  // with the legend a sighted user sees, so a speech-input user who says it
  // reaches the trigger; a hidden continuation then names the facet and its
  // top values. axe cannot see this (its label-content-name-mismatch rule
  // only reads aria-label), so the name is asserted against the rendered
  // legend text here.
  test('every card exposes exactly one product and one channel trigger, named by its visible legend, then the facet and its top values', async ({ app, page }) => {
    await app.gotoRoute(ROUTE);
    const cards = page.locator('.seg-grid .seg-card');
    await expect(cards).toHaveCount(6);
    const facets = [
      { kind: 'product', legend: 'Conv 58%', summary: 'Loan product mix: Conventional 58%, FHA 24%, Jumbo 12%' },
      { kind: 'channel', legend: 'LO 62%', summary: 'Origination channel mix: Loan officer 62%, Digital 25%, Unknown 13%' },
    ] as const;
    for (let index = 0; index < 6; index += 1) {
      const card = cards.nth(index);
      for (const facet of facets) {
        const trigger = facetTrigger(card, facet.kind);
        await expect(trigger).toHaveCount(1);
        const legend = trigger.locator('.seg-card__facet-legend');
        await expect(legend).toBeVisible();
        const visible = (await legend.innerText()).trim();
        expect(visible, `${facet.kind} legend of card ${index}`).toBe(facet.legend);
        await expect(trigger).toHaveAccessibleName(
          new RegExp(`^${escapeRegExp(visible)} \\(${escapeRegExp(facet.summary)}\\)`),
        );
      }
    }
    // Chromium's own accessibility tree, which assistive tech reads, agrees
    // with Playwright's name computation: all twelve triggers.
    const cdp = await page.context().newCDPSession(page);
    const { nodes } = (await cdp.send('Accessibility.getFullAXTree')) as {
      nodes: Array<{ role?: { value?: unknown }; name?: { value?: unknown } }>;
    };
    const chromeNames = nodes
      .filter((node) => node.role?.value === 'button')
      .map((node) => String(node.name?.value ?? ''))
      .filter((name) => / mix: /.test(name));
    expect(chromeNames).toHaveLength(12);
    for (const name of chromeNames) {
      expect(name).toMatch(
        new RegExp(`^(?:${facets.map((facet) => `${escapeRegExp(facet.legend)} \\(${escapeRegExp(facet.summary)}\\)`).join('|')})`),
      );
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

/** The FilterSelect trigger for one Segments filter (its label is `LABEL: value`). */
function filterTrigger(page: Page, label: string): Locator {
  return page.locator(`button[aria-haspopup="listbox"][aria-label^="${label}:"]`);
}

function cardSelect(page: Page, title: string): Locator {
  return page.locator('.seg-grid .seg-card', { hasText: title }).locator('.seg-card__select');
}

function modeButton(page: Page, label: 'Any selected' | 'All selected'): Locator {
  return page.getByRole('group', { name: 'Segment match mode' }).getByRole('button', { name: new RegExp(`^${label}`) });
}

test.describe('Segments filters live in the URL (flow-09)', () => {
  test('a deep link restores the selected cards, the match mode and every secondary filter', async ({ app, mockApi, page }) => {
    const deepLink = new URLSearchParams({
      segment_codes: 'itm,equity',
      segment_mode: 'all',
      state: 'TX',
      occupancy: 'Owner-occupied',
      lien_status: 'Open HELOC',
      min_equity_pct_label: '≥ 25%',
      marketing_eligibility: 'Any',
      recency: 'Untouched 30d',
    });
    await app.gotoRoute(`${ROUTE}?${deepLink.toString()}`);

    await expect(cardSelect(page, 'Prime Refi Candidates')).toHaveAttribute('aria-pressed', 'true');
    await expect(cardSelect(page, 'Home Equity Candidate')).toHaveAttribute('aria-pressed', 'true');
    await expect(cardSelect(page, 'Listed for Sale')).toHaveAttribute('aria-pressed', 'false');
    await expect(modeButton(page, 'All selected')).toHaveAttribute('aria-pressed', 'true');
    await expect(filterTrigger(page, 'LOCATION')).toHaveAttribute('aria-label', 'LOCATION: Texas');
    await expect(filterTrigger(page, 'OCCUPANCY')).toHaveAttribute('aria-label', 'OCCUPANCY: Owner-occupied');
    await expect(filterTrigger(page, 'LIEN')).toHaveAttribute('aria-label', 'LIEN: Open 2nd lien / HELOC');
    await expect(filterTrigger(page, 'CASH-OUT')).toHaveAttribute('aria-label', 'CASH-OUT: Equity ≥ 25%');
    await expect(filterTrigger(page, 'CONTACTABILITY')).toHaveAttribute('aria-label', 'CONTACTABILITY: Any');
    await expect(filterTrigger(page, 'RECENCY')).toHaveAttribute('aria-label', 'RECENCY: Untouched 30d');

    // The data is the restored view, not just the controls: the cards were
    // counted for the All-selected intersection with the restored criteria.
    const segmentsCall = mockApi.calls.filter((call) => call.path === '/api/segments').at(-1);
    const sent = new URLSearchParams(segmentsCall?.search ?? '');
    expect(sent.get('segment_codes')).toBe('itm,equity');
    expect(sent.get('segment_mode')).toBe('all');
    expect(sent.get('occupancy')).toBe('Owner-occupied');
    expect(sent.get('lien_status')).toBe('Open HELOC');
    expect(sent.get('min_equity_pct_label')).toBe('≥ 25%');
    expect(sent.get('recency')).toBe('Untouched 30d');
    expect(sent.has('marketing_eligibility')).toBe(false);
    const leadsCall = mockApi.calls.filter((call) => call.path === '/api/leads').at(-1);
    expect(new URLSearchParams(leadsCall?.search ?? '').get('state')).toBe('TX');
  });

  test('changing a filter writes the URL; Back and Forward restore the previous view', async ({ app, page }) => {
    await app.gotoRoute(ROUTE);
    expect(new URL(page.url()).search).toBe('');

    await (await app.openFilterMenu('OCCUPANCY')).getByRole('option', { name: 'Owner-occupied', exact: true }).click();
    await expect(page).toHaveURL(/[?&]occupancy=Owner-occupied(&|$)/);
    await app.settle();
    await (await app.openFilterMenu('LIEN')).getByRole('option', { name: 'Free & clear', exact: true }).click();
    await expect(page).toHaveURL(/[?&]lien_status=Free\+%26\+clear(&|$)/);
    await expect(filterTrigger(page, 'LIEN')).toHaveAttribute('aria-label', 'LIEN: Free & clear');
    await app.settle();

    await page.goBack();
    await expect(page).not.toHaveURL(/lien_status=/);
    await expect(filterTrigger(page, 'LIEN')).toHaveAttribute('aria-label', 'LIEN: Any');
    await expect(filterTrigger(page, 'OCCUPANCY')).toHaveAttribute('aria-label', 'OCCUPANCY: Owner-occupied');

    await page.goBack();
    await expect.poll(() => new URL(page.url()).search).toBe('');
    await expect(filterTrigger(page, 'OCCUPANCY')).toHaveAttribute('aria-label', 'OCCUPANCY: All');

    await page.goForward();
    await expect(filterTrigger(page, 'OCCUPANCY')).toHaveAttribute('aria-label', 'OCCUPANCY: Owner-occupied');
    await app.settle();

    // Choosing the default removes the key rather than writing it.
    await (await app.openFilterMenu('OCCUPANCY')).getByRole('option', { name: 'All', exact: true }).click();
    await expect.poll(() => new URL(page.url()).search).toBe('');
  });

  test('the All-selected mode is kept before any card is selected and survives a reload', async ({ app, page }) => {
    await app.gotoRoute(ROUTE);
    await modeButton(page, 'All selected').click();
    await expect(page).toHaveURL(/[?&]segment_mode=all(&|$)/);
    await expect(modeButton(page, 'All selected')).toHaveAttribute('aria-pressed', 'true');
    await page.reload();
    await app.settle();
    await expect(modeButton(page, 'All selected')).toHaveAttribute('aria-pressed', 'true');
    await cardSelect(page, 'Listed for Sale').click();
    await expect(page).toHaveURL(/[?&]segment=listed(&|$)/);
    await expect(page).toHaveURL(/[?&]segment_mode=all(&|$)/);
  });

  test('Clear filters removes every Segments filter from the URL in one step', async ({ app, page }) => {
    await app.gotoRoute(`${ROUTE}?segment=itm&occupancy=Owner-occupied&consent_status=Opt-in&target_lender_ref=Competitor+B`);
    await expect(filterTrigger(page, 'TARGET LIEN HOLDER')).toHaveAttribute('aria-label', 'TARGET LIEN HOLDER: Competitor B');
    await expect(filterTrigger(page, 'CONSENT')).toHaveAttribute('aria-label', 'CONSENT: Opt-in');
    await page.getByRole('button', { name: 'Clear filters' }).click();
    await expect.poll(() => new URL(page.url()).search).toBe('');
    await expect(filterTrigger(page, 'CONSENT')).toHaveAttribute('aria-label', 'CONSENT: Any');
    await expect(cardSelect(page, 'Prime Refi Candidates')).toHaveAttribute('aria-pressed', 'false');
    await expect(page.getByRole('button', { name: 'Clear filters' })).toBeDisabled();
  });

  // Review round 2: on the footprint's fallback a `?state=TX` deep link
  // showed 'LOCATION: TX' while the control's options listed only 'All'.
  test('a state code the footprint cannot verify stays the LOCATION value and is one of its options', async ({ app, mockApi, page }) => {
    mockApi.register('GET', '/api/config/footprint', () => json({ states: [], geography_scope: null, using_fallback: true }));
    await app.gotoRoute(`${ROUTE}?state=TX`);
    const trigger = filterTrigger(page, 'LOCATION');
    await expect(trigger).toHaveAttribute('aria-label', 'LOCATION: TX');
    await trigger.click();
    const listbox = page.getByRole('listbox', { name: 'LOCATION' });
    await expect(listbox.getByRole('option')).toHaveText(['All', 'TX']);
    await expect(listbox.getByRole('option', { name: 'TX' })).toHaveAttribute('aria-selected', 'true');
    await listbox.getByRole('option', { name: 'All' }).click();
    await expect(trigger).toHaveAttribute('aria-label', 'LOCATION: All');
    expect(new URL(page.url()).searchParams.has('state')).toBe(false);
  });
});

/**
 * Linux Chromium rounds each Geist Mono advance to a whole pixel (6.6 -> 7px
 * at fs-11) and draws the delta arrow from a wider fallback face, so a meta
 * row that fits macOS by a few pixels wraps there and makes the card 28px
 * taller (wave-2 fixture job: 281px against the 260 cap). The spec widens the
 * mono text past Linux's rounding (+0.5px per glyph; the evidence chip sets
 * its own letter-spacing, so it is named) and the delta by 4px for the arrow,
 * then asks every row for one line with a margin left over.
 */
const LINUX_TEXT_EMULATION = `
  .seg-card__meta, .seg-card__meta .evidence-chip { letter-spacing: 0.5px; }
  .seg-card__meta > .up, .seg-card__meta > .down { padding-inline-start: 4px; }
`;
/** Spare width each meta row keeps under the emulation, for renderers wider still. */
const META_ROW_MARGIN = 4;

test('each meta row keeps one line, with a margin, when its text renders at Linux widths', async ({ app, page }) => {
  await app.gotoRoute(ROUTE);
  await page.addStyleTag({ content: LINUX_TEXT_EMULATION });
  const rows = await page.locator('.seg-grid .seg-card__meta').evaluateAll((metas) =>
    metas.map((meta) => {
      const children = [...meta.children].map((child) => child.getBoundingClientRect());
      const gap = parseFloat(getComputedStyle(meta).columnGap);
      const needed = children.reduce((sum, box) => sum + box.width, 0) + gap * (children.length - 1);
      return {
        text: meta.textContent ?? '',
        children: children.length,
        spare: meta.clientWidth - needed,
        oneLine: children.every((box) => Math.abs(box.top + box.height / 2 - (children[0].top + children[0].height / 2)) <= 1),
      };
    }),
  );
  expect(rows).toHaveLength(6);
  for (const row of rows) {
    expect(row.children, `"${row.text}" carries a delta, the evidence chip and Ask Genie`).toBe(3);
    expect(row.oneLine, `"${row.text}" stays on one line`).toBe(true);
    expect(row.spare, `"${row.text}" keeps ${META_ROW_MARGIN}px spare`).toBeGreaterThanOrEqual(META_ROW_MARGIN);
  }
  const heights = await page.locator('.seg-grid .seg-card').evaluateAll((cards) => cards.map((card) => card.getBoundingClientRect().height));
  for (const height of heights) expect(height).toBeLessThanOrEqual(MAX_CARD_HEIGHT);
});

test.describe('Borrower 360 proof drawer (flow-09)', () => {
  test('the dossier has one control that opens the proof drawer, not two', async ({ app, page }) => {
    const borrowerId = PRIMARY_BORROWER.borrower_id;
    await app.gotoRoute(`/borrower-360/${borrowerId}`);
    const proofControls = page.locator('#main-content').getByRole('button', { name: /proof|math/i });
    await expect(proofControls).toHaveCount(1);
    await expect(proofControls).toHaveAccessibleName(`Show scoring math for borrower ${borrowerId}`);
    await proofControls.click();
    await expect(page.locator('aside.proof-drawer')).toHaveClass(/is-open/);
    await expect(page.getByRole('dialog', { name: `Proof for borrower ${borrowerId}` })).toBeVisible();
  });
});
