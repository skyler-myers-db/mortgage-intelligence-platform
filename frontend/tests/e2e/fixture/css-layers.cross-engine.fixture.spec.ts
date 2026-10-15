/**
 * Cascade layers in the served CSSOM, in Chromium and WebKit (2026-09-21
 * audit css-05 / stack-v3 / bundle-06; docs/testing.md "Cascade layers
 * (css-05)"). The source twin is src/design-system/cssLayers.test.ts.
 *
 *  - the first stylesheet names every layer, in the manifest's order, before
 *    any style rule (the minifier rewrites layers.css's statement but keeps
 *    its order; see the test);
 *  - on a natural load of every fixture route, every style rule of every
 *    same-origin sheet sits in a @layer block and every @font-face stays top
 *    level (rules a sheet still in PENDING_INTEGRATOR_WRAP declares are
 *    exempt, with an annotation, until the integrator wraps it);
 *  - the !important winners layering could invert still win: the
 *    reduced-motion reset, the forced-colors active-chip ink, print's
 *    reveal opacity;
 *  - a registered @property inside a layer is still registered;
 *  - each block css-05 moved out of the initial sheet still styles its
 *    surface when its lazy chunk loads.
 */
import fs from 'node:fs';
import type { Locator, Page } from '@playwright/test';
import type { GenieAnswer } from '../../../src/types';
import { PRIMARY_BORROWER } from './data/borrowers';
import { GENIE_QUESTION, genieAnswerFixture, registerGenieTurn } from './data/genieTurn';
import { FIXTURE_ROUTES, FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

/**
 * src/test/cssLayerMap.json, the map cssLayers.test.ts reads too. Read with
 * node:fs: fixture files may only `import type` from frontend/src.
 */
interface CssLayerMap {
  manifest: string[];
  pendingIntegratorWrap: string[];
}
const LAYER_MAP = JSON.parse(fs.readFileSync(`${process.cwd()}/src/test/cssLayerMap.json`, 'utf8')) as CssLayerMap;
const LAYER_MANIFEST = LAYER_MAP.manifest;
const PENDING_INTEGRATOR_WRAP = LAYER_MAP.pendingIntegratorWrap;

/** A selector in a comparable shape: no whitespace, quotes or pseudo-element double colon. */
const normalizeSelector = (selector: string): string =>
  selector.replace(/\s+/g, '').replace(/["']/g, '').replace(/::/g, ':').toLowerCase();

/** Split a selector list at its top-level commas. */
function selectorParts(list: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let current = '';
  for (const ch of list) {
    if (ch === '(' || ch === '[') depth += 1;
    else if (ch === ')' || ch === ']') depth -= 1;
    if (ch === ',' && depth === 0) {
      parts.push(current);
      current = '';
    } else current += ch;
  }
  return [...parts, current].map(normalizeSelector).filter(Boolean);
}

/**
 * Every selector a sheet still listed in PENDING_INTEGRATOR_WRAP declares.
 * Those sheets are unwrapped on purpose until the integrator wraps them, and
 * the app preloads other routes' chunks (and their CSS) on idle, so their
 * rules reach every route; the unlayered check exempts exactly these
 * selectors, and nothing once the set is empty.
 */
function pendingSelectors(): Set<string> {
  const selectors = new Set<string>();
  for (const path of PENDING_INTEGRATOR_WRAP) {
    const text = fs.readFileSync(`${process.cwd()}/${path}`, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    for (const match of text.matchAll(/([^{};]+)\{/g)) {
      const prelude = match[1].trim();
      if (prelude.startsWith('@') || /^(?:from|to|\d+%)/.test(prelude)) continue;
      for (const part of selectorParts(prelude)) selectors.add(part);
    }
  }
  return selectors;
}

/** Every same-origin style rule outside a layer block, and every font face inside one. */
async function unlayeredRules(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const problems: string[] = [];
    const inLayer = (rule: CSSRule): boolean => {
      for (let parent = rule.parentRule; parent; parent = parent.parentRule) {
        if (parent instanceof CSSLayerBlockRule) return true;
      }
      return false;
    };
    const visit = (rules: CSSRuleList) => {
      for (const rule of rules) {
        if (rule instanceof CSSFontFaceRule) {
          if (rule.parentRule) problems.push(`@font-face ${rule.style.getPropertyValue('font-family')} is not top level`);
        } else if (rule instanceof CSSStyleRule) {
          if (!inLayer(rule)) problems.push(rule.selectorText);
        } else if (rule instanceof CSSGroupingRule) {
          visit(rule.cssRules);
        }
      }
    };
    for (const sheet of document.styleSheets) {
      if (sheet.href && new URL(sheet.href).origin !== location.origin) continue;
      visit(sheet.cssRules);
    }
    return problems;
  });
}

function main(page: Page): Locator {
  return page.locator('#main-content');
}

function lineAnswer(): GenieAnswer {
  const rows = Array.from({ length: 12 }, (_, i) => ({ week: `2026-W${String(i + 1).padStart(2, '0')}`, total_balance: 100_000 + i * 12_500 }));
  return genieAnswerFixture({
    table_rows: rows,
    row_count: rows.length,
    visualization: { kind: 'line', title: 'Balance by week', x: 'week', y: 'total_balance', reason: 'A weekly series.' },
  } as Partial<GenieAnswer>);
}

for (const theme of FIXTURE_THEMES) {
  test.describe(`${theme}`, () => {
    // The minifier (Lightning CSS) keeps layer semantics but rewrites the
    // statement: it merges same-named blocks in place of the first mention of
    // each name and trims the statement to the names no block declares
    // (`@layer reset;` ... `@layer routes;`). So the served proof is the
    // ORDER of first mention in the first sheet, before any style rule.
    test('the first stylesheet declares the layer order before any style rule', async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/');
      const first = await page.evaluate(() => {
        const sheet = document.styleSheets[0];
        const names: string[] = [];
        let styleBeforeLayer = false;
        for (const rule of sheet?.cssRules ?? []) {
          if (rule instanceof CSSLayerStatementRule) names.push(...rule.nameList);
          else if (rule instanceof CSSLayerBlockRule) names.push(rule.name);
          else if (rule instanceof CSSStyleRule && names.length === 0) styleBeforeLayer = true;
        }
        return {
          href: sheet?.href ?? null,
          firstIsLayer: sheet?.cssRules[0] instanceof CSSLayerStatementRule || sheet?.cssRules[0] instanceof CSSLayerBlockRule,
          styleBeforeLayer,
          order: names.filter((name, index) => names.indexOf(name) === index),
        };
      });
      expect(first.href).toMatch(/\/assets\/index-[\w-]+\.css$/);
      expect(first.firstIsLayer).toBe(true);
      expect(first.styleBeforeLayer).toBe(false);
      expect(first.order).toEqual([...LAYER_MANIFEST]);
    });

    test('every route loads only layered style rules; font faces stay top level', async ({ app, browserName, hygiene, mockApi, page }) => {
      // Twenty-two natural loads in one test: generous for a loaded runner.
      test.setTimeout(480_000);
      if (browserName === 'webkit') {
        // Understood noise, not a CSS finding (W5e integration): settle() waits
        // for the API, not for a route's speculative chunk preloads, and
        // WebKit cancels the ones still in flight when the walk's page.goto
        // leaves the route; Vite then dispatches vite:preloadError, which
        // staleChunkRecovery reports as a 'preload' client error. Only the
        // cancelled .js requests and that one report are allowed.
        hygiene.allow('request-failed', /\/assets\/[\w.-]+\.js failed: cancelled$/);
        hygiene.allow('console.error', /^\[mip\] client error \{source: preload, kind: chunk, errorName: TypeError, .*\} TypeError: Importing a module script failed\./);
      }
      await app.setTheme(theme);
      const pending = pendingSelectors();
      let exempt = 0;
      for (const route of FIXTURE_ROUTES) {
        await app.gotoRoute(route.path);
        const natural = markNaturalLoad(mockApi);
        const problems: string[] = [];
        for (const selector of await unlayeredRules(page)) {
          if (selector.startsWith('@font-face') || !selectorParts(selector).every((part) => pending.has(part))) {
            problems.push(selector);
          } else exempt += 1;
        }
        // Soft, per route: a later route's load failure cannot hide one found here.
        expect.soft(problems, `${route.name}: unlayered rules`).toEqual([]);
        expectNoAuditedReadSince(mockApi, natural, route.name);
      }
      if (PENDING_INTEGRATOR_WRAP.length > 0) {
        test.info().annotations.push({
          type: 'integrator-follow-up',
          description: `${exempt} unlayered rules exempted as declared by PENDING_INTEGRATOR_WRAP sheets (src/test/cssLayerMap.json); none once the integrator wraps them`,
        });
      } else expect(exempt).toBe(0);
    });
  });
}

test.describe('!important winners keep winning across layers', () => {
  test('the reduced-motion reset still zeroes a button transition', async ({ app, page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await app.gotoRoute('/');
    const duration = await page.locator('.btn').first().evaluate((el) => getComputedStyle(el).transitionDuration);
    // 0.01ms !important (01-app-shell.css), whatever the button's own transition says.
    for (const part of duration.split(',')) expect(parseFloat(part), duration).toBeLessThanOrEqual(0.00001);
  });

  test('forced colors: an active filter chip reads HighlightText', async ({ app, page }) => {
    await page.emulateMedia({ forcedColors: 'active' });
    await app.gotoRoute('/');
    const chip = main(page).locator('.filter.is-active').first();
    await expect(chip).toBeVisible();
    const [ink, system] = await chip.evaluate((el) => {
      const probe = document.createElement('span');
      probe.style.color = 'HighlightText';
      document.body.append(probe);
      const expected = getComputedStyle(probe).color;
      probe.remove();
      return [getComputedStyle(el).color, expected];
    });
    expect(ink).toBe(system);
  });

  test('print: a reveal-on-scroll block prints at full opacity', async ({ app, page }) => {
    await app.gotoRoute('/');
    const reveal = main(page).locator('.reveal-on-scroll').first();
    await expect(reveal).toBeAttached();
    await page.emulateMedia({ media: 'print' });
    expect(await reveal.evaluate((el) => getComputedStyle(el).opacity)).toBe('1');
  });
});

test.describe('registered custom properties survive layering', () => {
  test('a layered @property (01-app-shell.css --nav-clear) keeps its registered initial length', async ({ app, page }) => {
    await app.gotoRoute('/');
    // Registered with initial-value 0px; an unregistered property reads "".
    expect(await page.evaluate(() => getComputedStyle(document.body).getPropertyValue('--nav-clear').trim())).toBe('0px');
  });

  test("LeadTable's --tbl-head-block-size computes as a length", async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    const wrap = page.locator('.tbl-wrap:has(> .lead-table__table)').first();
    await expect(wrap).toBeVisible();
    expect(await wrap.evaluate((el) => getComputedStyle(el).getPropertyValue('--tbl-head-block-size').trim())).toMatch(/^\d+(?:\.\d+)?px$/);
  });
});

test.describe('blocks that left the initial sheet still style their surfaces', () => {
  test('Genie markdown: the floating panel answer and the /ask-genie answer', async ({ app, mockApi, page }) => {
    registerGenieTurn(mockApi, { holdProgress: false });
    await app.gotoRoute('/');
    const genie = await app.openGenie();
    await genie.getByRole('textbox', { name: 'Ask Genie' }).fill(GENIE_QUESTION);
    await genie.getByRole('button', { name: 'Ask', exact: true }).click();
    // `.genie-md-p { margin: var(--sp-2) 0 0 }` zeroes the UA paragraph's 1em
    // block-end margin; `.genie-proof-toggle` is a flex row (a block div unstyled).
    const styled = async (answer: Locator) => {
      const paragraph = answer.locator('p.genie-md-p').first();
      await expect(paragraph).toBeVisible({ timeout: 20_000 });
      expect(await paragraph.evaluate((el) => getComputedStyle(el).marginBottom)).toBe('0px');
      expect(await answer.locator('.genie-proof-toggle').first().evaluate((el) => getComputedStyle(el).display)).toBe('flex');
    };
    await styled(genie.locator('.genie-answer').first());

    await app.gotoRoute('/ask-genie');
    await main(page).getByRole('textbox', { name: 'Ask Genie — question' }).fill(GENIE_QUESTION);
    await main(page).getByRole('button', { name: 'Ask Genie', exact: true }).click();
    await styled(main(page).locator('.genie-thread .genie-answer').first());
  });

  test("the Borrower 360 proof drawer's tabs", async ({ app, page }) => {
    await app.gotoRoute(`/borrower-360/${PRIMARY_BORROWER.borrower_id}`);
    await main(page).getByRole('button', { name: `Show scoring math for borrower ${PRIMARY_BORROWER.borrower_id}` }).click();
    const tab = page.locator('dialog.proof-drawer .proof-tab').first();
    await expect(tab).toBeVisible();
    expect(await tab.evaluate((el) => getComputedStyle(el).minHeight)).toBe('32px');
  });

  test('the glossary layout grid', async ({ app, page }) => {
    await app.gotoRoute('/glossary');
    const layout = main(page).locator('.glossary-layout').first();
    await expect(layout).toBeVisible();
    expect(await layout.evaluate((el) => getComputedStyle(el).display)).toBe('grid');
  });

  test('an asset page layout', async ({ app, page }) => {
    const asset = FIXTURE_ROUTES.find((route) => route.name === 'asset-detail');
    await app.gotoRoute(asset?.path ?? '/');
    const layout = main(page).locator('.asset-layout').first();
    await expect(layout).toBeVisible();
    expect(await layout.evaluate((el) => getComputedStyle(el).display)).toBe('flex');
  });

  test("an Analytics chart's y-tick column is the plot's height", async ({ app, page }) => {
    await app.gotoRoute('/analytics?view=signals');
    const ticks = main(page).locator('.analytics-chart__y-ticks').first();
    await expect(ticks).toBeVisible();
    const [column, plot] = await ticks.evaluate((el) => [getComputedStyle(el).alignSelf, getComputedStyle(el).blockSize]);
    expect(column).toBe('start');
    // block-size: var(--analytics-chart-h), 3 x --sp-16 + --sp-12 (10-native-analytics.css / charts.css).
    expect(plot).toBe('240px');
  });

  test("a Genie line chart's plot grid", async ({ app, mockApi, page }) => {
    registerGenieTurn(mockApi, { holdProgress: false, answer: lineAnswer() });
    await app.gotoRoute('/ask-genie');
    await main(page).getByRole('textbox', { name: 'Ask Genie — question' }).fill(GENIE_QUESTION);
    await main(page).getByRole('button', { name: 'Ask Genie', exact: true }).click();
    const plot = main(page).locator('.genie-chart .analytics-chart__plot').first();
    await expect(plot).toBeVisible({ timeout: 20_000 });
    expect(await plot.evaluate((el) => getComputedStyle(el).display)).toBe('grid');
  });
});
