/**
 * Heading outline of the eight product routes (audit 2026-09-21 a11y-03,
 * WCAG 1.3.1 / 2.4.6). Every product page used to expose one heading, its
 * `<h1>`: surface titles were `div`s carrying `.h-4`. They are now
 * `<SurfaceTitle>` h2 / h3 (frontend/src/components/ui/SurfaceTitle.tsx, whose
 * vitest source gate keeps a `div` title from coming back); this pins what a
 * screen-reader user actually gets from heading navigation on the built app:
 *
 *  - exactly one `<h1>` (the page hero),
 *  - at least one `<h2>` (the page's surfaces are reachable),
 *  - no skipped level in document order (h1 -> h3 without an h2 between),
 *  - no empty heading.
 *
 * Read through the accessibility tree (`getByRole('heading')`), so a closed
 * Console or Genie panel (aria-hidden / inert) does not count; the second
 * state opens the Console, an h2 whose group titles are h3s, after the page's
 * own outline.
 */
import type { Page } from '@playwright/test';
import { FIXTURE_ROUTES } from './routes';
import { expect, test } from './test';

/** The eight contracted product-flow routes, detail pages on the fixture borrower. */
const PRODUCT_ROUTE_NAMES = [
  'home',
  'portfolio-builder',
  'segment-intelligence',
  'lead-queue',
  'borrower-360-detail',
  'offer-orchestrator-detail',
  'ask-genie',
  'analytics-executive',
] as const;

const PRODUCT_ROUTES = PRODUCT_ROUTE_NAMES.map((name) => {
  const route = FIXTURE_ROUTES.find((candidate) => candidate.name === name);
  if (!route) throw new Error(`headings spec: no fixture route named ${name}`);
  return route;
});

interface OutlineEntry {
  level: number;
  text: string;
}

/** Every heading exposed to assistive technology, in document order. */
async function readOutline(page: Page): Promise<OutlineEntry[]> {
  return page.getByRole('heading').evaluateAll((elements) =>
    elements.map((element) => {
      const tag = /^H([1-6])$/.exec(element.tagName);
      const ariaLevel = Number(element.getAttribute('aria-level'));
      return {
        // aria-level wins over the tag; a bare role="heading" defaults to 2.
        level: ariaLevel > 0 ? ariaLevel : tag ? Number(tag[1]) : 2,
        text: ((element as HTMLElement).innerText ?? '').trim().replace(/\s+/g, ' '),
      };
    }),
  );
}

/** Pure: what is wrong with an outline, empty when it is sound. */
function outlineProblems(outline: readonly OutlineEntry[]): string[] {
  const problems: string[] = [];
  const h1s = outline.filter((entry) => entry.level === 1).length;
  if (h1s !== 1) problems.push(`expected exactly one h1, found ${h1s}`);
  if (!outline.some((entry) => entry.level === 2)) problems.push('expected at least one h2');
  let previous = 0;
  for (const entry of outline) {
    if (entry.level > previous + 1) {
      problems.push(`h${entry.level} "${entry.text}" skips a level after h${previous || '(none)'}`);
    }
    if (!entry.text) problems.push(`an h${entry.level} has no text`);
    previous = entry.level;
  }
  return problems;
}

const describeOutline = (outline: readonly OutlineEntry[]) =>
  outline.map((entry) => `${'  '.repeat(entry.level - 1)}h${entry.level} ${entry.text}`).join('\n');

test('the outline check rejects a second h1, a missing h2, a skipped level and an empty heading', () => {
  const sound = [
    { level: 1, text: 'Page' },
    { level: 2, text: 'Surface' },
    { level: 3, text: 'Sub-panel' },
    { level: 2, text: 'Next surface' },
  ];
  expect(outlineProblems(sound)).toEqual([]);
  expect(outlineProblems([...sound, { level: 1, text: 'Second page title' }])).toEqual(['expected exactly one h1, found 2']);
  expect(outlineProblems([{ level: 1, text: 'Page' }])).toEqual(['expected at least one h2']);
  expect(outlineProblems([{ level: 1, text: 'Page' }, { level: 3, text: 'Orphan' }, { level: 2, text: 'Surface' }]))
    .toEqual(['h3 "Orphan" skips a level after h1']);
  expect(outlineProblems([{ level: 2, text: 'Before the h1' }, { level: 1, text: 'Page' }]))
    .toEqual(['h2 "Before the h1" skips a level after h(none)']);
  expect(outlineProblems([...sound, { level: 3, text: '' }])).toEqual(['an h3 has no text']);
});

for (const route of PRODUCT_ROUTES) {
  for (const state of ['default', 'console'] as const) {
    test(`${route.name} (${state}) exposes one h1, at least one h2 and no skipped heading level`, async ({ app, page }) => {
      await app.gotoRoute(route.path);
      const main = page.locator('#main-content');
      if (route.populated) {
        await expect(main, 'the route rendered fixture data, not just its chrome').toContainText(route.populated);
      }
      if (state === 'console') {
        const consolePanel = await app.openConsole();
        await expect(consolePanel.getByRole('heading', { name: 'Console', level: 2 }), 'the Console titles itself').toBeVisible();
        await expect(
          consolePanel.getByRole('heading', { name: 'My recent activity', level: 3 }),
          'the Console group titles are h3s under it',
        ).toBeVisible();
      }
      await app.settle();

      const outline = await readOutline(page);
      expect(outlineProblems(outline), `heading outline of ${route.path} (${state}):\n${describeOutline(outline)}`).toEqual([]);
      await expect(main.getByRole('heading', { level: 1 }), 'the one h1 is the page hero in <main>').toHaveCount(1);
    });
  }
}
