/**
 * axe gate on pull requests (audit a11y-05, quality-05): WCAG 2.0/2.1/2.2 A
 * and AA rules over the core routes, both themes, and the overlay states
 * the nightly live scan never opened (evidence drawer, command palette,
 * Genie panel, a filter menu).
 *
 * Any violation fails, with one escape hatch: KNOWN_VIOLATIONS below, keyed
 * by route + state + rule id, each entry naming the audit finding that owns
 * the fix and the day it was recorded. The map is a RATCHET: an entry that
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
}

/**
 * `${route}|${state}|${ruleId}` → owner. Populated only with what reproduced
 * on the integrated wave-0 base on the recorded date.
 */
const KNOWN_VIOLATIONS: Readonly<Record<string, KnownViolation>> = {
  // a11y-01: the selected evidence tab (`.drawer__tab.is-active`) uses the
  // light accent as text on a light surface (1.75:1). Light theme only.
  'home|evidence-drawer|color-contrast': { finding: 'a11y-01', recorded: '2026-09-22', themes: ['light'] },
  'lead-queue|evidence-drawer|color-contrast': { finding: 'a11y-01', recorded: '2026-09-22', themes: ['light'] },
  'borrower-360|evidence-drawer|color-contrast': { finding: 'a11y-01', recorded: '2026-09-22', themes: ['light'] },
  'offer-orchestrator|evidence-drawer|color-contrast': { finding: 'a11y-01', recorded: '2026-09-22', themes: ['light'] },
  'ask-genie|evidence-drawer|color-contrast': { finding: 'a11y-01', recorded: '2026-09-22', themes: ['light'] },
  // a11y-02: FilterSelect's open `ul.filter-menu` listbox scrolls but takes
  // no keyboard focus (no option ids / activedescendant). Both themes.
  'lead-queue|filter-menu|scrollable-region-focusable': { finding: 'a11y-02', recorded: '2026-09-22', themes: ['dark', 'light'] },
};

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

function describeViolation(violation: AxeResults['violations'][number]): string {
  const targets = violation.nodes.slice(0, 3).map((node) => node.target.join(' ')).join(' ; ');
  return `${violation.id} [${violation.impact ?? 'n/a'}] ${violation.help} (${violation.nodes.length} node(s): ${targets})`;
}

for (const theme of FIXTURE_THEMES) {
  for (const route of AXE_ROUTES) {
    for (const state of route.states) {
      test(`${route.name} · ${state} · ${theme} has no WCAG A/AA violation beyond the recorded ratchet`, async ({ app, page }) => {
        await app.setTheme(theme);
        await app.gotoRoute(route.path);
        await enterState(app, page, state);
        await page.evaluate(() => document.fonts.ready.then(() => undefined));

        const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
        const found = new Map(results.violations.map((violation) => [violation.id, violation]));

        const unknown: string[] = [];
        for (const violation of results.violations) {
          const known = KNOWN_VIOLATIONS[`${route.name}|${state}|${violation.id}`];
          if (!known || !known.themes.includes(theme)) unknown.push(describeViolation(violation));
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
