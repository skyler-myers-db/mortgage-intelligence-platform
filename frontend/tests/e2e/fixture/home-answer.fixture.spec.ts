/**
 * Home answers its own question (lane home-answer: 2026-09-21 audit flow-05,
 * visual-06, flow-07 CTA copy). Rendered-layer proofs at 1440x900, both
 * themes, against the production build:
 *
 *  - the answer band sits fully above the fold with the top five ranked
 *    borrowers (WHO), one evidence-chipped trigger per last-login highlight
 *    (WHY NOW) and an offer-mix bar whose segments make 100% (WHAT TO OFFER);
 *  - it still does with the TALLEST payload the vocabulary allows (all seven
 *    actionable offer codes plus "Monitor for later", five WHO rows, three
 *    triggers and the unverified-figures warning), and the map and its
 *    heading still clear the fold: a layout property, not a fixture shape;
 *  - WHAT TO OFFER reconciles with the "Primary offer paths" KPI on a
 *    live-shaped, nurture-dominant book: no "Monitor for later" segment, the
 *    note's total is the KPI's number, the printed percents make 100;
 *  - the hero's evidence claim is true: each evidence chip opens its source;
 *  - the geography map starts above the fold, paired with a side panel;
 *  - KPI values are at least the size of the page title;
 *  - no two cards touch: every measured gap is at least --gap-grid;
 *  - exactly one primary button above the fold, into the ranked queue;
 *  - every WHO row opens the Lead Queue narrowed to that borrower, and Home
 *    itself never reads GET /api/leads (that read writes a VIEW_LEADS audit
 *    row), not on load and not on hover.
 *
 * Mutation checks (reported in the lane summary): restoring the KPI clamp at
 * 1440 fails "KPI values are at least the size of the page title"; removing
 * the Home grid gap fails "no two cards touch"; drawing every offer as a
 * legend row fails "the tallest answer band"; putting "Monitor for later"
 * back in the bar fails "WHAT TO OFFER reconciles".
 */
import type { Locator, Page } from '@playwright/test';
import { BORROWERS } from './data/borrowers';
import {
  LIVE_ACTIONABLE_MIX,
  LIVE_MONITOR_COUNT,
  LIVE_SHAPED_HOME_PREVIEW,
  MAX_HOME_PREVIEW,
  MAX_HOME_SUMMARY,
  homePreviewHandler,
} from './data/homeAnswer';
import { HOME_SUMMARY, PORTFOLIO_PREVIEW } from './data/portfolio';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const FOLD = 900;
const LEAD_LIST_READ = /^\/api(?:\/v\d+)?\/leads(?:\/|$)/;
const BORROWER_READ = /^\/api(?:\/v\d+)?\/borrowers\//;

/** The economics fixture ranks BORROWERS in order; Home lists the first five. */
const TOP_FIVE = BORROWERS.slice(0, 5).map((borrower) => borrower.borrower_id);

interface Box {
  name: string;
  top: number;
  bottom: number;
  left: number;
  right: number;
}

/**
 * Every top-level card in the page body (`.kpi`, `.surface`, `.approval`,
 * `.map-wrap`) — a card nested inside another card is part of that card.
 */
async function cardBoxes(page: Page): Promise<Box[]> {
  return page.evaluate(() => {
    const selector = '.kpi, .surface, .approval, .map-wrap';
    const main = document.querySelector('#main-content');
    if (!main) return [];
    const cards = Array.from(main.querySelectorAll<HTMLElement>(selector)).filter(
      (card) => !card.parentElement?.closest(selector) && card.getBoundingClientRect().width > 0,
    );
    return cards.map((card) => {
      const rect = card.getBoundingClientRect();
      return {
        name: `${card.className.split(' ')[0]}${card.getAttribute('aria-label') ? `[${card.getAttribute('aria-label')}]` : ''}`,
        top: rect.top,
        bottom: rect.bottom,
        left: rect.left,
        right: rect.right,
      };
    });
  });
}

/** The printed percents of the legend rows and the "Also" line, in order. */
async function printedPercents(band: Locator): Promise<{ values: number[]; texts: string[] }> {
  const pct = band.locator('.offer-mix__pct');
  const values = await pct.evaluateAll((nodes) => nodes.map((node) => Number(node.getAttribute('data-percent'))));
  const texts = await pct.allTextContents();
  return { values, texts };
}

/**
 * The map's own heading ("Geography drill-down"): a map whose top edge merely
 * grazes the fold shows a border, not a map. Its heading clearing the fold is
 * what tells a reader the geography hero is there.
 */
async function mapHeadingBottom(page: Page): Promise<number> {
  const heading = page.locator('#main-content .map-wrap').getByText('Geography drill-down', { exact: true });
  await expect(heading).toBeVisible();
  const box = (await heading.boundingBox())!;
  return box.y + box.height;
}

async function gapGridPx(page: Page): Promise<number> {
  const raw = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue('--gap-grid').trim(),
  );
  expect(raw, '--gap-grid resolves to a pixel length').toMatch(/^\d+(\.\d+)?px$/);
  return Number.parseFloat(raw);
}

for (const theme of FIXTURE_THEMES) {
  test.describe(`Home answer band (${theme})`, () => {
    test.beforeEach(async ({ app }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/');
    });

    test('the answer band is fully above the fold with five WHO rows, triggers and a 100% offer mix', async ({ page }) => {
      const band = page.locator('.home-answer');
      await expect(band).toBeVisible();
      const box = await band.boundingBox();
      expect(box, 'answer band has a box').not.toBeNull();
      expect(box!.y).toBeGreaterThanOrEqual(0);
      expect(box!.y + box!.height, 'answer band bottom edge').toBeLessThanOrEqual(FOLD);

      // WHO: the first five of the governed ranking, in rank order.
      const rows = band.locator('.home-answer__who-row');
      await expect(rows).toHaveCount(5);
      await expect(rows.locator('.home-answer__who-id')).toHaveText(TOP_FIVE);
      for (const row of await rows.all()) {
        await expect(row.locator('.score')).toHaveText(/\d+$/);
        await expect(row.locator('.chip')).not.toBeEmpty();
        await expect(row.locator('.home-answer__who-place')).toHaveText(/, [A-Z]{2}$/);
      }

      // WHY NOW: one trigger per server highlight, each token verbatim on an evidence chip.
      const triggers = band.locator('.home-answer__trigger');
      await expect(triggers).toHaveCount(HOME_SUMMARY.highlights.length);
      await expect(triggers.locator('.evidence-chip')).toHaveText(HOME_SUMMARY.highlights.map((h) => h.display));
      await expect(band.locator('.login-summary')).toContainText('Since your last login');

      // WHAT TO OFFER: exact shares sum to 100 and the drawn segments fill the bar.
      const bar = band.locator('.offer-mix');
      const segments = bar.locator('.offer-mix__seg');
      await expect(segments).toHaveCount(PORTFOLIO_PREVIEW.offer_mix?.length ?? 0);
      const shares = (await segments.evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-share'))))
        .map(Number);
      expect(shares.reduce((sum, share) => sum + share, 0)).toBeCloseTo(100, 2);
      const widths = await segments.evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().width));
      const barWidth = (await bar.boundingBox())!.width;
      expect(Math.abs(widths.reduce((sum, width) => sum + width, 0) - barWidth)).toBeLessThanOrEqual(1);
      const percents = await printedPercents(band);
      expect(percents.values.reduce((sum, value) => sum + value, 0)).toBe(100);
      expect(percents.texts).toEqual(percents.values.map((value) => (value === 0 ? '<1%' : `${value}%`)));
      await expect(bar).toHaveAttribute('aria-label', /^Primary offer mix: .+%\.$/);

      await page.screenshot({ path: test.info().outputPath(`home-${theme}.png`) });
    });

    test('the tallest answer band still clears the fold and leaves the map above it', async ({ app, mockApi, page }) => {
      mockApi.register('POST', '/api/portfolio/preview', homePreviewHandler(MAX_HOME_PREVIEW));
      mockApi.register('GET', '/api/home/summary', () => ({ body: MAX_HOME_SUMMARY }));
      await app.gotoRoute('/');
      const band = page.locator('.home-answer');
      // The payload really is the maximum: every actionable offer, the
      // unverified-figures warning, five WHO rows, three triggers.
      await expect(band.locator('.offer-mix__seg')).toHaveCount(LIVE_ACTIONABLE_MIX.length);
      await expect(band.getByRole('status').filter({ hasText: 'could not be verified' })).toBeVisible();
      await expect(band.locator('.home-answer__who-row')).toHaveCount(5);
      await expect(band.locator('.home-answer__trigger')).toHaveCount(MAX_HOME_SUMMARY.highlights.length);

      const bandBox = (await band.boundingBox())!;
      const mapBox = (await page.locator('#main-content .map-wrap').boundingBox())!;
      const headingBottom = await mapHeadingBottom(page);
      test.info().annotations.push({
        type: 'geometry',
        description:
          `${theme}: band ${bandBox.y.toFixed(1)}-${(bandBox.y + bandBox.height).toFixed(1)}, ` +
          `map top ${mapBox.y.toFixed(1)}, map heading bottom ${headingBottom.toFixed(1)}`,
      });
      expect(bandBox.y + bandBox.height, 'answer band bottom edge').toBeLessThanOrEqual(FOLD);
      expect(mapBox.y, 'map top edge').toBeLessThan(FOLD);
      expect(headingBottom, 'map heading bottom edge').toBeLessThanOrEqual(FOLD);
      // How it fits: four legend rows, the other three offers on one "Also" line.
      await expect(band.locator('.offer-mix__item')).toHaveCount(4);
      await expect(band.locator('.offer-mix__more .offer-mix__more-label')).toHaveCount(LIVE_ACTIONABLE_MIX.length - 4);
      // Seven-digit live magnitudes still fit the --fs-36 KPI values.
      const overflowing = await page.locator('#main-content .kpi__value').evaluateAll((nodes) =>
        nodes.filter((node) => node.scrollWidth > node.clientWidth + 0.5).map((node) => node.textContent),
      );
      expect(overflowing).toEqual([]);
      await page.screenshot({ path: test.info().outputPath(`home-max-${theme}.png`) });
    });

    test('the geography map starts above the fold beside a side panel', async ({ page }) => {
      const map = page.locator('#main-content .map-wrap');
      const mapBox = await map.boundingBox();
      expect(mapBox, 'map has a box').not.toBeNull();
      expect(mapBox!.y, 'map top edge').toBeLessThan(FOLD);
      expect(await mapHeadingBottom(page), 'map heading bottom edge').toBeLessThanOrEqual(FOLD);
      // The prototype's layoutA pairing: the map on the left, a side panel on the right.
      const side = page.locator('.home-geo .home-side');
      const sideBox = await side.boundingBox();
      expect(sideBox!.x).toBeGreaterThan(mapBox!.x + mapBox!.width);
      expect(Math.abs(sideBox!.y - mapBox!.y)).toBeLessThanOrEqual(1);
      await expect(side.getByRole('region', { name: 'Approval queue' })).toBeVisible();
    });

    test('KPI values are at least the size of the page title', async ({ page }) => {
      const sizes = await page.evaluate(() => {
        const px = (el: Element | null) => (el ? Number.parseFloat(getComputedStyle(el).fontSize) : Number.NaN);
        return {
          h1: px(document.querySelector('#main-content h1')),
          kpis: Array.from(document.querySelectorAll('#main-content .kpi__value')).map(px),
        };
      });
      expect(sizes.kpis).toHaveLength(4);
      for (const size of sizes.kpis) {
        expect(size, `KPI ${size}px vs H1 ${sizes.h1}px`).toBeGreaterThanOrEqual(sizes.h1);
        expect(size, 'the prototype KPI value is --fs-36').toBe(36);
      }
    });

    test('no two cards touch: every gap is at least --gap-grid', async ({ page }) => {
      const gap = await gapGridPx(page);
      const cards = await cardBoxes(page);
      expect(cards.length, 'KPI cards, the answer band, the map and the approval queue').toBeGreaterThanOrEqual(7);
      const tight: string[] = [];
      for (let i = 0; i < cards.length; i += 1) {
        for (let j = i + 1; j < cards.length; j += 1) {
          const [a, b] = [cards[i], cards[j]];
          const sideBySide = a.top < b.bottom && b.top < a.bottom;
          const stacked = a.left < b.right && b.left < a.right;
          if (sideBySide && stacked) {
            tight.push(`${a.name} overlaps ${b.name}`);
            continue;
          }
          const measured = stacked
            ? Math.max(b.top - a.bottom, a.top - b.bottom)
            : sideBySide
              ? Math.max(b.left - a.right, a.left - b.right)
              : Number.POSITIVE_INFINITY;
          if (measured < gap - 0.5) tight.push(`${a.name} / ${b.name}: ${measured.toFixed(1)}px < ${gap}px`);
        }
      }
      expect(tight).toEqual([]);
    });

    test('exactly one primary button above the fold, into the ranked queue', async ({ page }) => {
      const primaries = await page.locator('#main-content .btn--primary').evaluateAll((nodes) =>
        nodes
          .filter((node) => {
            const rect = node.getBoundingClientRect();
            return rect.width > 0 && rect.top < 900;
          })
          .map((node) => ({ text: node.textContent?.trim(), href: node.getAttribute('href') })),
      );
      expect(primaries).toEqual([{ text: "Review today's top leads", href: '/lead-queue' }]);
      // The other actions stay reachable, as secondary buttons.
      for (const name of ['Build a portfolio', 'Explore segments', 'Open review queue']) {
        const link = page.locator('#main-content').getByRole('link', { name });
        await expect(link).toBeVisible();
        await expect(link).not.toHaveClass(/btn--primary/);
      }
    });
  });
}

test.describe('Home answer band links and reads', () => {
  test('every WHO row opens the Lead Queue narrowed to that borrower', async ({ app, page }) => {
    await app.gotoRoute('/');
    const rows = page.locator('.home-answer__who-row');
    await expect(rows).toHaveCount(5);
    const hrefs = await rows.evaluateAll((nodes) => nodes.map((node) => node.getAttribute('href')));
    expect(hrefs).toEqual(TOP_FIVE.map((id) => `/lead-queue?borrower_ids=${id}`));
    for (const href of hrefs) expect(href).toMatch(/^\/lead-queue\?(?:borrower_ids=B-[0-9A-Z]{13}|segment=[a-z_]+)$/);

    await rows.nth(1).click();
    await expect(page).toHaveURL(new RegExp(`/lead-queue\\?borrower_ids=${TOP_FIVE[1]}$`));
    await app.settle();
    const queueRows = page.locator('table.tbl tbody tr:not(.tbl__expand)');
    await expect(queueRows).toHaveCount(1);
    await expect(queueRows.first()).toContainText(TOP_FIVE[1]);
  });

  test('WHY NOW and WHAT TO OFFER deep-link through the Lead Queue URL filter contract', async ({ app, page }) => {
    await app.gotoRoute('/');
    const why = page.locator('.home-answer .login-summary');
    await expect(why.getByRole('link', { name: 'borrowers whose rate and equity pass the refinance screen' }))
      .toHaveAttribute('href', '/lead-queue?segment=itm');
    await expect(why.getByRole('link', { name: /opportunity score of 75\+/ }))
      .toHaveAttribute('href', '/lead-queue?funnel_stage=high_opportunity');
    // "offers available" counts "Monitor for later" too; no queue filter is that population.
    await expect(why.getByRole('link', { name: 'borrowers with an offer decision' })).toHaveCount(0);

    const offers = page.locator('.offer-mix__legend');
    await expect(offers.getByRole('link', { name: 'Refinance review', exact: true })).toHaveAttribute('href', '/lead-queue?product=Refi');
    await expect(offers.getByRole('link', { name: 'Cash-out refinance review' })).toHaveAttribute('href', '/lead-queue?product=Cash-out');
    // Offers past the four legend rows keep their own filter link on the "Also" line.
    const also = page.locator('.offer-mix__more');
    await expect(also.getByRole('link', { name: 'Next-home purchase loan' })).toHaveAttribute('href', '/lead-queue?product=Purchase');
    await expect(also.getByRole('link', { name: 'Customer retention review' })).toHaveAttribute('href', '/lead-queue?product=Retention');
    await offers.getByRole('link', { name: 'Home-equity line review' }).click();
    await expect(page).toHaveURL(/\/lead-queue\?product=HELOC$/);
    await app.settle();
  });

  test('Home never reads the lead list or a borrower, on load or on hover', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/');
    await expect(page.locator('.home-answer__who-row')).toHaveCount(5);
    for (const row of await page.locator('.home-answer__who-row, .home-answer__trigger a, .offer-mix__label[href], .offer-mix__more-label').all()) {
      await row.hover();
    }
    await app.settle();
    const reads = mockApi.calls.filter((call) => call.method === 'GET');
    expect(reads.filter((call) => LEAD_LIST_READ.test(call.path)), 'GET /api/leads writes VIEW_LEADS').toEqual([]);
    expect(reads.filter((call) => BORROWER_READ.test(call.path)), 'borrower reads write VIEW_BORROWER').toEqual([]);
    // WHO comes from the audit-free economics ranking instead.
    expect(reads.some((call) => /\/analytics\/economics$/.test(call.path))).toBe(true);
  });

  test('WHAT TO OFFER reconciles with the Primary offer paths KPI on a live-shaped book', async ({ app, mockApi, page }) => {
    mockApi.register('POST', '/api/portfolio/preview', homePreviewHandler(LIVE_SHAPED_HOME_PREVIEW));
    await app.gotoRoute('/');
    const band = page.locator('.home-answer');
    // "Monitor for later" is ~97% of this book's offer decisions and no offer:
    // it is never a segment, a legend row or a link.
    await expect(band.locator('.offer-mix__seg')).toHaveCount(LIVE_ACTIONABLE_MIX.length);
    await expect(band.locator('.offer-mix__seg[data-offer="nurture"], .offer-mix__swatch[data-offer="nurture"]')).toHaveCount(0);
    await expect(band.locator('.offer-mix__label, .offer-mix__more-label').filter({ hasText: 'Monitor for later' })).toHaveCount(0);

    // The bar's 100% is the KPI's population, stated with the KPI's own number.
    const kpi = page.locator('.kpi', { hasText: 'Primary offer paths' }).locator('.kpi__value');
    await expect(kpi).toHaveText(LIVE_SHAPED_HOME_PREVIEW.offers_recommended!.toLocaleString('en-US'));
    const kpiText = ((await kpi.textContent()) ?? '').trim();
    const note = band.locator('.home-answer__col--offer .home-answer__note');
    const noteTotal = /Share of the ([\d,]+) borrowers with a primary offer path/.exec((await note.textContent()) ?? '')?.[1];
    expect(noteTotal, 'the note names the KPI number').toBe(kpiText);
    await expect(note).toContainText(`${LIVE_MONITOR_COUNT.toLocaleString('en-US')} more are on Monitor for later`);
    // The briefing states the same number.
    await expect(band.locator('.home-answer__briefing')).toContainText(`${kpiText} have a primary offer path`);

    // Printed percents (legend rows plus the "Also" line) make 100, and the
    // leading offer reads its real share, not the 1-2% a nurture bar left it.
    const percents = await printedPercents(band);
    expect(percents.values).toHaveLength(LIVE_ACTIONABLE_MIX.length);
    expect(percents.values.reduce((sum, value) => sum + value, 0)).toBe(100);
    expect(percents.texts[0], 'Refinance review: 55,871 of 122,389 offer paths').toBe('45%');
    const shares = (await band.locator('.offer-mix__seg').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('data-share'))))
      .map(Number);
    expect(shares.reduce((sum, share) => sum + share, 0)).toBeCloseTo(100, 2);
  });

  test("the hero's evidence claim holds: each evidence chip in the band opens its source", async ({ app, page }) => {
    await app.gotoRoute('/');
    await expect(page.locator('#main-content .lede')).toHaveText(
      "Today's briefing answers all three; each evidence chip opens the source behind its figure.",
    );
    // The briefing's figures are prose, not controls: the lede must not say they open anything.
    await expect(page.locator('.home-answer__briefing').locator('a, button')).toHaveCount(0);
    const chips = page.locator('.home-answer .evidence-chip');
    await expect(chips).toHaveCount(2 + HOME_SUMMARY.highlights.length);
    for (const name of ['Governed ranking', HOME_SUMMARY.highlights[0].display, 'Offer rules']) {
      const drawer = await app.openEvidenceDrawer(page.locator('.home-answer .evidence-chip', { hasText: name }).first());
      await drawer.getByRole('button', { name: 'Close drawer' }).click();
      await expect(drawer).not.toHaveClass(/is-open/);
    }
  });

  test('a failed ranking read leaves a quiet status in WHO, never an alert', async ({ app, page }) => {
    app.degrade('/api/analytics/economics', { status: 500, body: { detail: 'fixture: economics is down' } });
    await app.gotoRoute('/');
    const who = page.locator('.home-answer__col--who');
    await expect(who.getByRole('status')).toContainText('could not be loaded');
    await expect(who.getByRole('link', { name: 'Open the Lead Queue' })).toHaveAttribute('href', '/lead-queue');
    await expect(page.locator('.home-answer [role="alert"]')).toHaveCount(0);
    // The other two answers do not depend on the ranking.
    await expect(page.locator('.home-answer__trigger')).toHaveCount(HOME_SUMMARY.highlights.length);
    await expect(page.locator('.offer-mix__seg')).not.toHaveCount(0);
  });

  test('Home and the Economics tab share one ranking read', async ({ app, mockApi, page }) => {
    const economicsReads = () => mockApi.calls.filter((call) => /\/analytics\/economics$/.test(call.path)).length;
    await app.gotoRoute('/');
    expect(economicsReads()).toBe(1);
    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Analytics' }).click();
    await app.settle();
    await page.getByRole('tab', { name: 'Economics' }).click();
    await app.settle();
    // Same cache entry: the Top Borrowers table renders the ranking Home
    // already read, without a second request.
    const topRow = page.locator('.surface', { hasText: 'Top Borrowers' }).locator('tbody tr').first();
    await expect(topRow).toContainText(TOP_FIVE[0].slice(-4));
    expect(economicsReads()).toBe(1);
  });
});
