/**
 * axe gate on pull requests (audit a11y-05, quality-05): WCAG 2.0/2.1/2.2 A
 * and AA rules over the core routes, both themes, and the overlay states
 * the nightly live scan never opened (evidence drawer, command palette,
 * Genie panel, a filter menu).
 *
 * Any violation fails, with one escape hatch: KNOWN_VIOLATIONS below, keyed
 * by route + state + rule id and pinned to a selector the violating nodes
 * must match, each entry naming the audit finding that owns the fix and the
 * day it was recorded. The map is a RATCHET: an entry that
 * no longer reproduces fails the test too, so a fix retires its entry in the
 * same change. Best-practice rules stay advisory and are not scanned here.
 */
import AxeBuilder from '@axe-core/playwright';
import type { AxeResults } from 'axe-core';
import type { Page } from '@playwright/test';
import type { AppDriver, FixtureTheme } from './app';
import { PRIMARY_BORROWER } from './data/borrowers';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

type AxeState = 'default' | 'evidence-drawer' | 'command-palette' | 'genie' | 'filter-menu';

interface AxeRoute {
  name: string;
  path: string;
  states: readonly AxeState[];
}

const OVERLAYS: readonly AxeState[] = ['default', 'evidence-drawer', 'command-palette', 'genie'];

const AXE_ROUTES: readonly AxeRoute[] = [
  { name: 'home', path: '/', states: OVERLAYS },
  { name: 'lead-queue', path: '/lead-queue', states: [...OVERLAYS, 'filter-menu'] },
  { name: 'borrower-360', path: `/borrower-360/${PRIMARY_BORROWER.borrower_id}`, states: OVERLAYS },
  { name: 'offer-orchestrator', path: `/offer-orchestrator/${PRIMARY_BORROWER.borrower_id}`, states: OVERLAYS },
  { name: 'ask-genie', path: '/ask-genie', states: OVERLAYS },
];

interface KnownViolation {
  /** Audit finding id that owns the fix (docs/audits/...-findings.md). */
  finding: string;
  /** ISO date the entry was recorded against the base branch. */
  recorded: string;
  /** Themes the violation reproduces in. It must reproduce in each, and in no other. */
  themes: readonly FixtureTheme[];
  /**
   * CSS selector every violating node must match. A node of the same rule
   * elsewhere on the page is not covered by the entry and fails the test.
   */
  nodes: string;
}

/**
 * `${route}|${state}|${ruleId}` → owner. Populated only with what reproduced
 * on the integrated wave-0 base on the recorded date.
 */
const KNOWN_VIOLATIONS: Readonly<Record<string, KnownViolation>> = {
  // a11y-01 (the selected evidence tab painted the light accent at 1.75:1)
  // was retired 2026-09-23: the theme x accent token lane moved
  // `.drawer__tab.is-active` onto --accent-ink and the five
  // `*|evidence-drawer|color-contrast` entries stopped reproducing.
  // a11y-02 (FilterSelect's open `ul.filter-menu` listbox scrolled but took
  // no keyboard focus) was retired 2026-09-23: the filter-listbox lane made
  // FilterSelect an APG select-only combobox whose trigger owns focus,
  // aria-controls the listbox and names the active option through
  // aria-activedescendant, and the
  // `lead-queue|filter-menu|scrollable-region-focusable` entry stopped
  // reproducing in both themes.
};

// A mistyped key would sit in the map, never match, and hide nothing: fail
// at load instead, so every entry names a route and state this spec scans.
for (const key of Object.keys(KNOWN_VIOLATIONS)) {
  const [routeName, state, ruleId] = key.split('|');
  const route = AXE_ROUTES.find((candidate) => candidate.name === routeName);
  if (!route || !route.states.includes(state as AxeState) || !ruleId) {
    throw new Error(`KNOWN_VIOLATIONS key "${key}" does not name a scanned route|state|rule`);
  }
}

async function enterState(app: AppDriver, page: Page, state: AxeState): Promise<void> {
  switch (state) {
    case 'default':
      return;
    case 'evidence-drawer':
      await app.openEvidenceDrawer(page.locator('.evidence-chip:visible').first());
      return;
    case 'command-palette':
      await app.openCommandPalette();
      return;
    case 'genie':
      await app.openGenie();
      return;
    case 'filter-menu':
      await app.openFilterMenu('STATE');
      return;
  }
}

type AxeViolation = AxeResults['violations'][number];

function describeViolation(violation: AxeViolation, nodes: AxeViolation['nodes'] = violation.nodes): string {
  const targets = nodes.slice(0, 3).map((node) => node.target.join(' ')).join(' ; ');
  return `${violation.id} [${violation.impact ?? 'n/a'}] ${violation.help} (${nodes.length} node(s): ${targets})`;
}

/** The violating nodes that do NOT match `selector` (resolved in the page, not by axe's selector text). */
async function nodesOutside(page: Page, violation: AxeViolation, selector: string): Promise<AxeViolation['nodes']> {
  // axe's `target` holds one selector per frame or shadow-root hop, so joining
  // with a space is only right for a light-DOM node in the top document. A
  // frame or shadow target resolves to null below and counts as uncovered:
  // the check fails closed. Walk the hops if frames or shadow roots appear.
  const targets = violation.nodes.map((node) => node.target.join(' '));
  const inside = await page.evaluate(
    ([paths, expected]) =>
      paths.map((path) => {
        const element = document.querySelector(path);
        return element !== null && element.matches(expected);
      }),
    [targets, selector] as const,
  );
  return violation.nodes.filter((_node, index) => !inside[index]);
}

for (const theme of FIXTURE_THEMES) {
  for (const route of AXE_ROUTES) {
    for (const state of route.states) {
      test(`${route.name} · ${state} · ${theme} has no WCAG A/AA violation beyond the recorded ratchet`, async ({ app, page }) => {
        await app.setTheme(theme);
        await app.gotoRoute(route.path);
        await enterState(app, page, state);
        // Scan the overlay once the reads it started have landed (the drawer
        // loads governed asset metadata after it opens), so every run scans
        // the same DOM rather than whichever half-loaded state won the race.
        await app.settle();

        const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
        const found = new Map(results.violations.map((violation) => [violation.id, violation]));

        const unknown: string[] = [];
        for (const violation of results.violations) {
          const known = KNOWN_VIOLATIONS[`${route.name}|${state}|${violation.id}`];
          if (!known || !known.themes.includes(theme)) {
            unknown.push(describeViolation(violation));
            continue;
          }
          const uncovered = await nodesOutside(page, violation, known.nodes);
          if (uncovered.length > 0) unknown.push(`${describeViolation(violation, uncovered)} outside ${known.nodes}`);
        }
        const stale: string[] = [];
        for (const [key, known] of Object.entries(KNOWN_VIOLATIONS)) {
          const [knownRoute, knownState, ruleId] = key.split('|');
          if (knownRoute !== route.name || knownState !== state || !known.themes.includes(theme)) continue;
          if (!found.has(ruleId)) stale.push(`${key} (${known.finding}, recorded ${known.recorded}) no longer reproduces in ${theme}: retire it`);
        }

        expect(unknown, `unrecorded axe violations on ${route.path} (${state}, ${theme})`).toEqual([]);
        expect(stale, 'ratchet: recorded violations that no longer reproduce').toEqual([]);
        expect(results.passes.length, 'axe evaluated the page').toBeGreaterThan(0);
      });
    }
  }
}

test.describe('named groups in the evidence drawer', () => {
  // ARIA 1.2 prohibits aria-label on an element with no role (a plain div).
  // axe files it under aria-prohibited-attr: "needs review" while the element
  // has text, a serious violation while it has none, so a scan that catches
  // the drawer mid-render can fail on it while a settled scan passes it.
  // Checking the needs-review results too makes the proof independent of timing.
  test('every aria-label in the open drawer sits on a role that permits it', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    const drawer = await app.openEvidenceDrawer(page.locator('.evidence-chip:visible').first());
    await app.settle();
    // Both labelled groups are rendered: the governed-asset metadata stats
    // (admin session) and the governed-assets list of the lineage family.
    await expect(drawer.locator('.source-stat-grid')).toBeVisible();
    await expect(drawer.locator('.governed-assets__list')).toBeVisible();

    const results = await new AxeBuilder({ page }).include('aside.drawer').withRules(['aria-prohibited-attr']).analyze();
    const flagged = [...results.violations, ...results.incomplete].flatMap((result) =>
      result.nodes.map((node) => `${node.target.join(' ')} (${result.impact ?? 'n/a'})`),
    );
    expect(flagged, 'aria-label on a role that prohibits it, inside the drawer').toEqual([]);
  });
});
