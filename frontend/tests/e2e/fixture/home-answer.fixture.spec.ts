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
 *  - the geography map starts above the fold, paired with a side panel, and
 *    the live responsive matrix's pairing contract (tests/e2e/homeGeography.ts)
 *    holds at 1150px and at every one of its eight anchors;
 *  - a narrow WHO column (a 1366 laptop, the Console open) shows every id and
 *    city whole, and whatever still truncates keeps its text in a tooltip;
 *  - the hero's actions keep the two-row stack beside the title at full
 *    width, and where the main column wraps them under the title (the
 *    Console open, the 1150px canary) they are one start-aligned row, so the
 *    hero is no taller than its title and one control row (home.css);
 *  - KPI values are at least the size of the page title;
 *  - no two cards touch: every measured gap is at least --gap-grid;
 *  - exactly one primary button above the fold, into the ranked queue;
 *  - the offer slices and legend swatches paint pairwise-distinct segment
 *    hues, refi + HELOC as the refi / HELOC stripe (D-dataviz-geo-c2), axe
 *    clean;
 *  - every WHO row opens the Lead Queue narrowed to that borrower, and Home
 *    itself never reads GET /api/leads (that read writes a VIEW_LEADS audit
 *    row), not on load and not on hover.
 *
 * Mutation checks (reported in the lane summary): restoring the KPI clamp at
 * 1440 fails "KPI values are at least the size of the page title"; removing
 * the Home grid gap fails "no two cards touch"; drawing every offer as a
 * legend row fails "the tallest answer band"; putting "Monitor for later"
 * back in the bar fails "WHAT TO OFFER reconciles"; dropping the WHO
 * column's narrow layout fails "a narrow WHO column keeps every id and city
 * whole"; dropping home.css's wrapped-hero query, or its 42vw-cap lift, fails
 * "the hero actions" (Console open, and the 1150px canary).
 */
import type { Locator, Page } from '@playwright/test';
import { expectAxeClean } from './axe';
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
import { expectHomeGeographyPaired } from '../homeGeography';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const FOLD = 900;
/** HomeAnswerBand.css: below this WHO list width the place moves under the id. */
const WHO_ONE_LINE_MIN = 480;
const LEAD_LIST_READ = /^\/api(?:\/v\d+)?\/leads(?:\/|$)/;
const BORROWER_READ = /^\/api(?:\/v\d+)?\/borrowers\//;

/**
 * responsive.spec.ts's narrow canary (1150x900) and its eight anchors: the
 * live matrix asserts the geography pairing at each, credential-gated.
 */
const RESPONSIVE_VIEWPORTS = [
  { width: 1150, height: 900 },
  { width: 1280, height: 720 },
  { width: 1366, height: 768 },
  { width: 1440, height: 900 },
  { width: 1600, height: 900 },
  { width: 1920, height: 1080 },
  { width: 2560, height: 1440 },
  { width: 3440, height: 1440 },
  { width: 3840, height: 2160 },
] as const;

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

/** The block-axis space between each item of a list and the next, in px. */
async function itemGaps(list: Locator): Promise<number[]> {
  return list.evaluate((node) => {
    const items = Array.from(node.children).map((child) => child.getBoundingClientRect());
    return items.slice(1).map((rect, index) => rect.top - items[index].bottom);
  });
}

async function tokenPx(page: Page, token: string): Promise<number> {
  const raw = await page.evaluate(
    (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim(),
    token,
  );
  expect(raw, `${token} resolves to a pixel length`).toMatch(/^\d+(\.\d+)?px$/);
  return Number.parseFloat(raw);
}

async function gapGridPx(page: Page): Promise<number> {
  const raw = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue('--gap-grid').trim(),
  );
  expect(raw, '--gap-grid resolves to a pixel length').toMatch(/^\d+(\.\d+)?px$/);
  return Number.parseFloat(raw);
}

/**
 * Let the shell's finite transitions end, so a sample taken while the Console
 * slides in (and .main's gutter snaps) proves nothing about the settled layout.
 */
async function finishTransitions(page: Page): Promise<void> {
  await page.evaluate(() =>
    Promise.all(
      document
        .getAnimations()
        .filter((animation) => animation.effect?.getComputedTiming().endTime !== Infinity)
        .map((animation) => animation.finished.catch(() => undefined)),
    ),
  );
}

type HeroBox = Box & { height: number };

interface HeroGeometry {
  hero: HeroBox;
  title: HeroBox;
  actions: HeroBox;
  /** The hero's row gap: the space between the title line and a wrapped actions line. */
  rowGap: number;
  /** The gold "Refreshed" chip, FetchedAt and the primary action, in DOM order. */
  items: HeroBox[];
}

/** The Home hero: its title block, its actions slot and the three actions in it. */
async function heroGeometry(page: Page): Promise<HeroGeometry> {
  await expect(page.locator('.proto-hero__actions [data-testid="fetched-at"] .fetched-at__label')).toBeVisible();
  return page.locator('#main-content .proto-hero').evaluate((hero) => {
    const box = (element: Element, name: string) => {
      const rect = element.getBoundingClientRect();
      return { name, top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right, height: rect.height };
    };
    const actions = hero.querySelector('.proto-hero__actions')!;
    const items = Array.from(actions.querySelectorAll('.chip, [data-testid="fetched-at"], .btn--primary'), (element) =>
      box(element, element.className.split(' ')[0]),
    );
    return {
      hero: box(hero, 'proto-hero'),
      title: box(hero.firstElementChild!, 'title'),
      actions: box(actions, 'proto-hero__actions'),
      rowGap: Number.parseFloat(getComputedStyle(hero).rowGap),
      items,
    };
  });
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
      // unverified-figures warning, five WHO rows, the rate move since the
      // visit plus five triggers (flow-05).
      await expect(band.locator('.offer-mix__seg')).toHaveCount(LIVE_ACTIONABLE_MIX.length);
      await expect(band.getByRole('status').filter({ hasText: 'could not be verified' })).toBeVisible();
      await expect(band.locator('.home-answer__who-row')).toHaveCount(5);
      await expect(band.locator('[data-testid="why-now-rate-move"]')).toBeVisible();
      await expect(band.locator('.home-answer__trigger')).toHaveCount(MAX_HOME_SUMMARY.highlights.length + 1);

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

    test('only the WHY NOW list drops its row gap: the WHO rows keep --sp-1 between them', async ({ app, mockApi, page }) => {
      // The fold fix for the tallest band (flow-05) removes the trigger list's
      // row gap. The WHO list and the WHY NOW loading skeleton share that
      // list rule, so this pins that the five ranked rows and the skeleton's
      // bars keep the prototype's --sp-1 spacing.
      let releaseSummary = () => {};
      const summaryHeld = new Promise<void>((resolve) => {
        releaseSummary = resolve;
      });
      mockApi.register('POST', '/api/portfolio/preview', homePreviewHandler(MAX_HOME_PREVIEW));
      mockApi.register('GET', '/api/home/summary', async () => {
        await summaryHeld;
        return { body: MAX_HOME_SUMMARY };
      });
      // A fresh document with no restored briefing, so WHY NOW starts loading.
      await page.addInitScript(() => window.sessionStorage.removeItem('mip.queryCache.v1'));
      await page.goto('/', { waitUntil: 'domcontentloaded' });
      const band = page.locator('.home-answer');
      const sp1 = await tokenPx(page, '--sp-1');
      expect(sp1, '--sp-1 is a non-zero gap').toBeGreaterThan(0);

      const skeleton = band.locator('ul.home-answer__triggers[aria-hidden="true"]');
      await expect(skeleton.locator('.skeleton')).toHaveCount(3);
      const skeletonGaps = await itemGaps(skeleton);
      expect(skeletonGaps, 'one gap between each pair of skeleton bars').toHaveLength(2);
      for (const gap of skeletonGaps) expect(gap, 'WHY NOW skeleton bar gap').toBeCloseTo(sp1, 0);
      releaseSummary();
      await app.settle();

      await expect(band.locator('.home-answer__who-row')).toHaveCount(5);
      await expect(band.locator('.home-answer__trigger')).toHaveCount(MAX_HOME_SUMMARY.highlights.length + 1);

      const whoGaps = await itemGaps(band.locator('ol.home-answer__who'));
      expect(whoGaps, 'one gap between each pair of the five WHO rows').toHaveLength(4);
      for (const gap of whoGaps) expect(gap, 'WHO row gap').toBeCloseTo(sp1, 0);

      const triggerGaps = await itemGaps(band.locator('ul.home-answer__triggers'));
      expect(triggerGaps, 'one gap between each pair of WHY NOW rows').toHaveLength(MAX_HOME_SUMMARY.highlights.length);
      for (const gap of triggerGaps) expect(gap, 'WHY NOW row gap').toBeCloseTo(0, 0);
    });

    test('offer slices and swatches paint distinct segment hues, refi + HELOC striped (dataviz-09)', async ({ app, mockApi, page }) => {
      mockApi.register('POST', '/api/portfolio/preview', homePreviewHandler(MAX_HOME_PREVIEW));
      mockApi.register('GET', '/api/home/summary', () => ({ body: MAX_HOME_SUMMARY }));
      await app.gotoRoute('/');
      const band = page.locator('.home-answer');
      await expect(band.locator('.offer-mix__seg')).toHaveCount(LIVE_ACTIONABLE_MIX.length);
      for (const part of ['.offer-mix__seg', '.offer-mix__swatch']) {
        const paints = await band.locator(part).evaluateAll((nodes) =>
          nodes.map((node) => {
            const style = getComputedStyle(node);
            return { offer: node.getAttribute('data-offer'), color: style.backgroundColor, image: style.backgroundImage };
          }),
        );
        const striped = paints.filter((paint) => paint.offer === 'refi_plus_heloc');
        expect(striped.length, `${part}: refi + HELOC is drawn`).toBeGreaterThan(0);
        for (const paint of striped) expect(paint.image, `${part} refi + HELOC`).toContain('repeating-linear-gradient');
        const solids = [...new Map(paints.filter((paint) => paint.offer !== 'refi_plus_heloc').map((paint) => [paint.offer, paint])).values()];
        for (const paint of solids) expect(paint.image, `${part} ${paint.offer} is a solid hue`).toBe('none');
        expect(new Set(solids.map((paint) => paint.color)).size, `${part}: ${JSON.stringify(solids)}`).toBe(solids.length);
      }
      // The slice gap is the 2px card surface, drawn inside the next slice.
      const gap = await band.locator('.offer-mix__seg').nth(1).evaluate((node) => getComputedStyle(node).boxShadow);
      expect(gap).toMatch(/inset 2px 0px 0px 0px$|^rgb.* 2px 0px 0px 0px inset$/);
      await expectAxeClean(page, { key: { route: 'home', state: 'offer-mix-max' }, theme, known: {}, include: '.home-answer' });
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
      await expect(side.getByRole('region', { name: 'Refinance review queue' })).toBeVisible();
    });

    test('a narrow WHO column keeps every id and city whole: a 1366 laptop, the Console open', async ({ app, page }) => {
      const who = page.locator('.home-answer__who');
      const rows = page.locator('.home-answer__who-row');
      for (const narrow of [
        { label: '1366x900', width: 1366, console: false },
        { label: '1440x900 with the Console open', width: 1440, console: true },
        { label: '1536x900 with the Console open', width: 1536, console: true },
      ]) {
        await page.setViewportSize({ width: narrow.width, height: FOLD });
        if (narrow.console) await app.openConsole();
        // Then check the column really is narrow.
        await finishTransitions(page);
        await expect
          .poll(() => who.evaluate((el) => el.getBoundingClientRect().width), { message: `${narrow.label}: WHO list width` })
          .toBeLessThan(WHO_ONE_LINE_MIN);
        await expect(rows).toHaveCount(5);
        const cut = await rows.evaluateAll((nodes) =>
          nodes.flatMap((row) =>
            Array.from(row.querySelectorAll<HTMLElement>('.home-answer__who-id, .home-answer__who-place'))
              .filter((part) => part.clientWidth === 0 || part.scrollWidth > part.clientWidth + 0.5)
              .map((part) => `${part.textContent}: ${part.clientWidth}/${part.scrollWidth}px`),
          ),
        );
        expect(cut, `${narrow.label}: ids and places shown whole`).toEqual([]);
      }
      // Whatever still truncates (a long city at a wide column, the offer chip
      // here) keeps its full text in a tooltip.
      for (const row of await rows.all()) {
        const place = row.locator('.home-answer__who-place');
        await expect(place).toHaveAttribute('title', (await place.textContent()) ?? '');
        const chip = row.locator('.chip');
        await expect(chip).toHaveAttribute('title', (await chip.locator('.chip__label').textContent()) ?? '');
      }
    });

    test('the hero actions: the stack beside the title at full width, one start-aligned row under it with the Console open', async ({ app, page }) => {
      // Full width (the Console closed): the gold chip over FetchedAt and the
      // action, beside the title, and the hero is its title block's height.
      const wide = await heroGeometry(page);
      expect(wide.items.map((item) => item.name)).toEqual(['chip', 'fetched-at', 'btn']);
      expect(wide.actions.left, 'full width: the actions sit beside the title block').toBeGreaterThan(wide.title.right);
      expect(wide.items[0].bottom, 'full width: the gold chip sits over the controls').toBeLessThanOrEqual(
        Math.min(wide.items[1].top, wide.items[2].top),
      );
      expect(wide.hero.height, 'full width: the hero is as tall as its title block').toBeLessThanOrEqual(wide.title.height + 0.5);

      // A main column too narrow for the stack beside the title wraps the
      // actions under it. There the stack's second row grew the hero 32px and
      // pushed the answer band down (W5b home-hero-wrap): the actions must be
      // one start-aligned row, the hero the title plus one control row, as
      // before FetchedAt joined the hero (the pre-W5b console capture).
      for (const narrow of [
        { label: '1440x900 with the Console open', width: 1440, console: true },
        // responsive.spec.ts's narrow canary: the 42vw cap (483px) is narrower
        // than the 528px row, so the row only holds if the wrapped slot lifts it.
        { label: '1150x900, the Console closed', width: 1150, console: false },
      ]) {
        await page.setViewportSize({ width: narrow.width, height: FOLD });
        if (narrow.console) await app.openConsole();
        await finishTransitions(page);
        const geometry = await heroGeometry(page);
        const { title, actions, items } = geometry;
        expect(actions.top, `${narrow.label}: the actions wrap under the title`).toBeGreaterThanOrEqual(title.bottom);
        expect(Math.abs(items[0].left - title.left), `${narrow.label}: the row starts at the title's edge`).toBeLessThanOrEqual(0.5);
        const centres = items.map((item) => item.top + item.height / 2);
        const minHeight = Math.min(...items.map((item) => item.height));
        expect(Math.max(...centres) - Math.min(...centres), `${narrow.label}: chip, FetchedAt and action centre on one row`).toBeLessThan(
          minHeight / 2,
        );
        for (let index = 1; index < items.length; index += 1) {
          expect(items[index].left, `${narrow.label}: ${items[index].name} follows ${items[index - 1].name}`).toBeGreaterThanOrEqual(
            items[index - 1].right,
          );
        }
        const controlRow = Math.max(...items.map((item) => item.height));
        expect(geometry.hero.height, `${narrow.label}: the hero is its title and one control row`).toBeLessThanOrEqual(
          title.height + geometry.rowGap + controlRow + 0.5,
        );
      }
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

test.describe('Home geography across the responsive anchors', () => {
  test("the live matrix's pairing contract holds: map beside the side panel, the pair full-width", async ({ app, page }) => {
    await app.gotoRoute('/');
    for (const viewport of RESPONSIVE_VIEWPORTS) {
      await page.setViewportSize(viewport);
      await expectHomeGeographyPaired(page, 2, `${viewport.width}x${viewport.height}`);
    }
  });
});

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
    await expect(why.getByRole('link', { name: 'borrowers who pass the refi screen' }))
      .toHaveAttribute('href', '/lead-queue?segment=itm');
    await expect(why.getByRole('link', { name: /opportunity score 75\+/ }))
      .toHaveAttribute('href', '/lead-queue?funnel_stage=high_opportunity');
    // flow-05: the event measures and the primary offer paths open the queue
    // filter with the same predicate (offers_available, which had none, is gone).
    await expect(why.getByRole('link', { name: 'borrowers with a listed home' }))
      .toHaveAttribute('href', '/lead-queue?purchase_intent=Listed+for+sale');
    await expect(why.getByRole('link', { name: 'borrowers with a competitor lien' }))
      .toHaveAttribute('href', '/lead-queue?lender_relationship=Competitor+customer');
    await expect(why.getByRole('link', { name: 'borrowers with a primary offer path' }))
      .toHaveAttribute('href', '/lead-queue?funnel_stage=offer_recommended');
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
