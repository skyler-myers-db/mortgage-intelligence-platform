/**
 * The route-nav focus clearance (38-focus-clearance.css, audit a11y-v2)
 * costs no style work beyond the elements it styles.
 *
 * Its first shape put `.main:has(.route-nav)` in the ANCESTOR compound of a
 * universal rule (`:where(.main:has(...) :not(...))`). Chromium then
 * re-styled the whole `.main` subtree for every node inserted into or
 * removed from `.main`: every virtual-window shift of the Lead Queue's rows,
 * about 8x the style-recalc time of a scroll. The shipped rules keep every
 * :has() on `.main` itself (the clearance is a custom property it inherits).
 *
 * One test compares three sides on the same page, so host load cancels out:
 * the clearance as shipped, its rules deleted through the CSSOM, and the
 * first shape put back (the non-vacuity leg, which must show the blow-up).
 * Each side runs the same two scripts:
 *   - scroll: the 160-row virtualized queue scrolled in fixed steps, the
 *     real case;
 *   - insert: a span inserted into and removed from `.main`, ten times, the
 *     mechanism itself.
 * What is asserted is how many elements Chromium re-styled (the
 * `elementCount` of its UpdateLayoutTree trace events): a count, not a
 * timing, so a loaded runner cannot flake it. It covers the style updates
 * of the frame lifecycle, not the ones a script forces by reading layout,
 * so the scroll's ratio understates the time ratio; the recalc time
 * (CDP RecalcStyleDuration) is logged beside it, never asserted.
 */
import type { Browser, Page } from '@playwright/test';
import { registerVirtualQueue } from './data/queueKeyboard';
import { expect, test } from './test';

/** The first shape of the rule: a :has() in a universal rule's ancestor compound. */
const ANCESTOR_HAS_RULE = '@media (min-height: 40rem) {'
  + ' :where(.main:has(.route-nav):not(:has(.genie-composer:not([hidden] *))) :not(.route-nav *, .tbl-wrap *))'
  + ' { scroll-margin-block-start: 61px; } }';

/**
 * The shipped clearance's rules carry this custom property (38's two, and
 * LeadTable.css's table rule that reads it); nothing else does.
 */
const CLEARANCE_MARKER = '--nav-clear';

const SCROLL_STEPS = 25;
const SCROLL_STEP_PX = 240;
const INSERTIONS = 10;

interface TraceEvent {
  name?: string;
  args?: { elementCount?: unknown };
}

interface StyleCost {
  /** Elements re-styled over the scripted scroll. */
  scroll: number;
  /** Elements re-styled over the insert-and-remove script. */
  insert: number;
  /** RecalcStyleDuration over the scroll, ms (logged only). */
  scrollMs: number;
}

/** Delete the top-level rules that carry the clearance; returns how many. */
async function deleteClearanceRules(page: Page): Promise<number> {
  return page.evaluate((marker) => {
    let deleted = 0;
    for (const sheet of [...document.styleSheets]) {
      for (let index = sheet.cssRules.length - 1; index >= 0; index -= 1) {
        if (sheet.cssRules[index].cssText.includes(marker)) {
          sheet.deleteRule(index);
          deleted += 1;
        }
      }
    }
    return deleted;
  }, CLEARANCE_MARKER);
}

/** Run `script` under a devtools.timeline trace; return the elements Chromium re-styled. */
async function restyledElements(browser: Browser, page: Page, script: () => Promise<void>): Promise<number> {
  await browser.startTracing(page, { categories: ['devtools.timeline'] });
  await script();
  const trace = JSON.parse((await browser.stopTracing()).toString('utf8')) as { traceEvents?: TraceEvent[] };
  return (trace.traceEvents ?? [])
    .filter((event) => event.name === 'UpdateLayoutTree')
    .reduce((sum, event) => sum + (typeof event.args?.elementCount === 'number' ? event.args.elementCount : 0), 0);
}

async function styleCost(browser: Browser, page: Page): Promise<StyleCost> {
  const wrap = page.getByRole('region', { name: 'Ranked borrowers table scroll region' });
  await wrap.evaluate(async (element) => {
    element.scrollTop = 0;
    await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');
  const recalcStyleSeconds = async () => (await cdp.send('Performance.getMetrics')).metrics
    .find((metric) => metric.name === 'RecalcStyleDuration')?.value ?? 0;
  const before = await recalcStyleSeconds();
  let reached = 0;
  const scroll = await restyledElements(browser, page, async () => {
    reached = await wrap.evaluate(async (element, { steps, step }) => {
      const frame = () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
      for (let index = 0; index < steps; index += 1) {
        element.scrollTop += step;
        await frame();
        await frame();
      }
      return element.scrollTop;
    }, { steps: SCROLL_STEPS, step: SCROLL_STEP_PX });
  });
  const scrollMs = Math.round((await recalcStyleSeconds() - before) * 1000);
  await cdp.detach();
  expect(reached, 'precondition: the scroll moved the virtual window').toBeGreaterThan(SCROLL_STEP_PX * 10);

  const insert = await restyledElements(browser, page, async () => {
    await page.locator('.main .surface__hdr').first().evaluate(async (host, count) => {
      const frames = () => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
      for (let index = 0; index < count; index += 1) {
        const span = document.createElement('span');
        host.append(span);
        await frames();
        span.remove();
        await frames();
      }
    }, INSERTIONS);
  });
  return { scroll, insert, scrollMs };
}

/**
 * The largest count of three passes per condition. A scroll's count depends on
 * how many frames the engine renders while it runs, so a loaded runner that
 * coalesces frames under-counts one pass (W5a CI: a control pass of 158 where
 * the same tree measures ~540); the maximum is each condition's full cost.
 */
async function stableStyleCost(browser: Browser, page: Page): Promise<StyleCost> {
  const passes: StyleCost[] = [];
  for (let pass = 0; pass < 3; pass += 1) passes.push(await styleCost(browser, page));
  return {
    scroll: Math.max(...passes.map((cost) => cost.scroll)),
    insert: Math.max(...passes.map((cost) => cost.insert)),
    scrollMs: Math.max(...passes.map((cost) => cost.scrollMs)),
  };
}

test('the route-nav clearance re-styles only what a scroll or an insertion changed, as no clearance does', async ({ app, browser, mockApi, page }) => {
  registerVirtualQueue(mockApi);
  await app.gotoRoute('/lead-queue');
  await expect(page.locator('table.tbl tbody tr[data-borrower-row]').first()).toBeVisible();
  const navMargin = () => page.locator('.lead-table__header-actions .filter').first()
    .evaluate((element) => getComputedStyle(element).scrollMarginBlockStart);
  expect(await navMargin(), 'precondition: the clearance applies on this route').toBe('61px');

  // A warm-up pass, so each measured pass starts from the same state.
  await styleCost(browser, page);
  const shipped = await stableStyleCost(browser, page);

  expect(await deleteClearanceRules(page), 'the clearance\'s rules were found and deleted').toBeGreaterThan(0);
  expect(await navMargin()).toBe('0px');
  const without = await stableStyleCost(browser, page);

  await page.addStyleTag({ content: ANCESTOR_HAS_RULE });
  expect(await navMargin(), 'the first shape applies too').toBe('61px');
  const ancestorHas = await stableStyleCost(browser, page);

  const report = (cost: StyleCost) => `scroll ${cost.scroll} (${cost.scrollMs} ms), insert ${cost.insert}`;
  const counts = `shipped ${report(shipped)}; without ${report(without)}; ancestor :has ${report(ancestorHas)}`;
  console.log(`[focus-clearance-cost] re-styled elements: ${counts}`);
  expect(without.scroll, `non-vacuity: the scroll re-styled rows (${counts})`).toBeGreaterThan(0);
  expect(without.insert, `non-vacuity: each insertion re-styled something (${counts})`).toBeGreaterThanOrEqual(INSERTIONS);
  expect(shipped.scroll / without.scroll, `the shipped clearance: scroll (${counts})`).toBeLessThanOrEqual(1.5);
  expect(shipped.insert / without.insert, `the shipped clearance: insert (${counts})`).toBeLessThanOrEqual(1.5);
  expect(ancestorHas.scroll / without.scroll, `non-vacuity: the first shape's scroll blow-up (${counts})`).toBeGreaterThan(2);
  expect(ancestorHas.insert / without.insert, `non-vacuity: the first shape's insert blow-up (${counts})`).toBeGreaterThan(20);
});

/**
 * The thead measurement's write (useTableScrollClearance) re-styles the
 * scroller alone. --tbl-head-block-size is registered `inherits: false`
 * (LeadTable.css): as an ordinary custom property, each write on `.tbl-wrap`
 * re-styled every row of the table even when the clearance it feeds did not
 * change. With the pin's write, that cost the cold Lead Queue ~340 ms of TBT
 * at 4x CPU (wave-4b integration, bisected to the a11y-v2 commit). Counted
 * elements, not time; the non-vacuity leg writes an ordinary (inherited)
 * custom property to the same element and must re-style the table.
 */
test('the measured thead size re-styles only the scroller, while the clearance it feeds holds', async ({ app, browser, mockApi, page }) => {
  registerVirtualQueue(mockApi);
  await app.gotoRoute('/lead-queue');
  await expect(page.locator('table.tbl tbody tr[data-borrower-row]').first()).toBeVisible();
  const wrap = page.getByRole('region', { name: 'Ranked borrowers table scroll region' });
  const settle = () => wrap.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await settle();
  const focusClear = () => wrap.evaluate((element) => getComputedStyle(element).getPropertyValue('--tbl-focus-clear').trim());
  const before = await focusClear();
  const rows = await page.locator('table.tbl tbody tr[data-borrower-row]').count();
  expect(rows, 'precondition: the virtual window rendered rows').toBeGreaterThan(5);

  // A thead a few px shorter: its clearance stays under the nav's, so
  // --tbl-focus-clear (the max of the two) does not change.
  const write = (property: string) => restyledElements(browser, page, async () => {
    await wrap.evaluate(async (element, name) => {
      element.style.setProperty(name, '40px');
      await new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
    }, property);
  });
  const registered = await write('--tbl-head-block-size');
  expect(await focusClear(), 'precondition: the clearance the rows read held').toBe(before);
  const inherited = await write('--tbl-probe-inherited');

  console.log(`[focus-clearance-cost] thead write re-styled ${registered}; an inherited property's write ${inherited} (${rows} rows)`);
  expect(inherited, 'non-vacuity: an inherited custom property re-styles the table').toBeGreaterThan(rows * 5);
  expect(registered, 'the measured thead size re-styles the scroller, not the table').toBeLessThanOrEqual(5);
});
