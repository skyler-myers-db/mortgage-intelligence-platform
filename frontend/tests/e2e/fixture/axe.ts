/**
 * The shared axe gate (audit a11y-05, quality-05): one helper for the PR
 * fixture matrix (axe.fixture.spec.ts) and the on-demand live scan
 * (tests/e2e/accessibility.spec.ts), so both apply the same tags, the same
 * impact policy and the same ratchet.
 *
 *   await expectAxeClean(page, { key: { route: 'home', state: 'default' }, theme, accent, known });
 *
 * - One analyze over WCAG_TAGS plus 'best-practice'. A rule carrying any
 *   WCAG A/AA tag GATES at every impact, minor and moderate included. A
 *   best-practice-only rule is ADVISORY: it is attached as
 *   axe-best-practice.json with one annotation and never fails.
 * - A gating violation passes only when a ratchet entry covers it: keyed
 *   `${route}|${state}|${ruleId}`, dated, pinned to the finding that owns the
 *   fix, to the themes and accents it reproduces in, and to a selector every
 *   violating node must match. An entry that no longer reproduces FAILS
 *   (stale), so a fix retires its entry in the same change.
 * - Nothing is excluded and no rule is disabled.
 *
 * Imports only @axe-core/playwright, axe-core types and @playwright/test,
 * never the fixture harness, so the live spec can use it.
 */
import AxeBuilder from '@axe-core/playwright';
import type { AxeResults } from 'axe-core';
import { expect, test, type Page } from '@playwright/test';

export const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'] as const;
export const ADVISORY_TAG = 'best-practice';

export type AxeTheme = 'dark' | 'light';
export type AxeAccent = 'bright' | 'teal' | 'navy' | 'red';

export interface AxeScanKey {
  /** Route slug (FIXTURE_ROUTES name in the fixture matrix, the path in the live scan). */
  route: string;
  state: string;
}

export interface KnownViolation {
  /** Audit finding id that owns the fix (docs/audits/...-findings.md), or a new slug for wave 3/4. */
  finding: string;
  /** ISO date the entry was recorded against the base branch. */
  recorded: string;
  /** Themes the violation reproduces in. It must reproduce in each, and in no other. */
  themes: readonly AxeTheme[];
  /** Accents it reproduces in; default ['bright']. Same rule as themes. */
  accents?: readonly AxeAccent[];
  /**
   * CSS selector every violating node must match. A node of the same rule
   * elsewhere on the page is not covered by the entry and fails the test.
   */
  nodes: string;
}

export type KnownViolations = Readonly<Record<string, KnownViolation>>;

/**
 * `${route}|${state}|${ruleId}` → owner, for the PR fixture matrix. Populated
 * only with what reproduced on the base branch on the recorded date; every
 * key must name a scan axe.fixture.spec.ts runs (it validates at load).
 */
export const KNOWN_VIOLATIONS: KnownViolations = {
  // a11y-01's "red primary CTA 3.62:1": in dark + red, `.btn--primary` paints
  // #FFFFFF on the red accent #FF3621 at 3.61:1 (12-13px text needs 4.5:1).
  // Surfaced by the first accent sweep (wave 2); the token fix is not in any
  // wave-2 lane, so it waits for the a11y-01 remainder.
  'home|default|color-contrast': { finding: 'a11y-01', recorded: '2026-09-24', themes: ['dark'], accents: ['red'], nodes: '.btn--primary' },
  'home|evidence-drawer|color-contrast': { finding: 'a11y-01', recorded: '2026-09-24', themes: ['dark'], accents: ['red'], nodes: '.btn--primary' },
  'lead-queue|default|color-contrast': { finding: 'a11y-01', recorded: '2026-09-24', themes: ['dark'], accents: ['red'], nodes: '.btn--primary' },
  'lead-queue|evidence-drawer|color-contrast': { finding: 'a11y-01', recorded: '2026-09-24', themes: ['dark'], accents: ['red'], nodes: '.btn--primary' },
  'borrower-360-detail|default|color-contrast': { finding: 'a11y-01', recorded: '2026-09-24', themes: ['dark'], accents: ['red'], nodes: '.btn--primary' },
  'borrower-360-detail|evidence-drawer|color-contrast': { finding: 'a11y-01', recorded: '2026-09-24', themes: ['dark'], accents: ['red'], nodes: '.btn--primary' },
  // New slug (no register id fits; wave 3/4): Portfolio Builder's message
  // hypotheses wrap each <dt>/<dd> pair in `<div role="group" aria-label>`
  // (portfolio-builder.campaign-setup.tsx). A role on the wrapper takes the
  // pair out of the <dl>, so axe reports the list and its items. Surfaced by
  // the first default-state scan of this route (wave 2).
  'portfolio-builder|default|definition-list': {
    finding: 'a11y-w2-hypothesis-dl',
    recorded: '2026-09-24',
    themes: ['dark', 'light'],
    nodes: 'dl.campaign-recommendation__hypothesis-list',
  },
  'portfolio-builder|default|dlitem': {
    finding: 'a11y-w2-hypothesis-dl',
    recorded: '2026-09-24',
    themes: ['dark', 'light'],
    nodes: '.campaign-recommendation__hypothesis > dt, .campaign-recommendation__hypothesis > dd',
  },
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

export type AxeViolation = AxeResults['violations'][number];
type AxeNodes = AxeViolation['nodes'];

export interface AxeScanContext {
  key: AxeScanKey;
  theme: AxeTheme;
  accent: AxeAccent;
  known: KnownViolations;
}

export interface AxeVerdict {
  /** Gating violations (or nodes) no ratchet entry covers. */
  unrecorded: string[];
  /** Ratchet entries for this scan whose rule no longer reproduces. */
  stale: string[];
}

export function knownKey(key: AxeScanKey, ruleId: string): string {
  return `${key.route}|${key.state}|${ruleId}`;
}

/** Whether an entry claims this theme and accent. */
export function entryApplies(entry: KnownViolation, theme: AxeTheme, accent: AxeAccent): boolean {
  return entry.themes.includes(theme) && (entry.accents ?? ['bright']).includes(accent);
}

/** Split violations into gating (any WCAG A/AA tag) and advisory (best-practice only). Pure. */
export function partitionByTags(violations: readonly AxeViolation[]): { gating: AxeViolation[]; advisory: AxeViolation[] } {
  const wcag = new Set<string>(WCAG_TAGS);
  const gating: AxeViolation[] = [];
  const advisory: AxeViolation[] = [];
  for (const violation of violations) {
    (violation.tags.some((tag) => wcag.has(tag)) ? gating : advisory).push(violation);
  }
  return { gating, advisory };
}

export function describeViolation(violation: AxeViolation, nodes: AxeNodes = violation.nodes): string {
  const targets = nodes.slice(0, 3).map((node) => node.target.join(' ')).join(' ; ');
  return `${violation.id} [${violation.impact ?? 'n/a'}] ${violation.help} (${nodes.length} node(s): ${targets})`;
}

/**
 * Compare the gating violations of one scan with the ratchet. Pure: the
 * page-side part (which violating nodes fall outside an entry's selector) is
 * resolved first and passed in as `uncovered`, keyed by rule id; a rule with
 * a matching entry and no `uncovered` value is treated as fully uncovered.
 */
export function evaluateAxeRatchet(
  gating: readonly AxeViolation[],
  context: AxeScanContext,
  uncovered: ReadonlyMap<string, AxeNodes>,
): AxeVerdict {
  const { key, theme, accent, known } = context;
  const unrecorded: string[] = [];
  for (const violation of gating) {
    const entry = known[knownKey(key, violation.id)];
    if (!entry || !entryApplies(entry, theme, accent)) {
      unrecorded.push(describeViolation(violation));
      continue;
    }
    const outside = uncovered.get(violation.id) ?? violation.nodes;
    if (outside.length > 0) unrecorded.push(`${describeViolation(violation, outside)} outside ${entry.nodes}`);
  }
  const found = new Set(gating.map((violation) => violation.id));
  const stale: string[] = [];
  for (const [entryKey, entry] of Object.entries(known)) {
    const [route, state, ruleId] = entryKey.split('|');
    if (route !== key.route || state !== key.state || !entryApplies(entry, theme, accent)) continue;
    if (!found.has(ruleId)) {
      stale.push(`${entryKey} (${entry.finding}, recorded ${entry.recorded}) no longer reproduces in ${theme}/${accent}: retire it`);
    }
  }
  return { unrecorded, stale };
}

export interface AxeScanTarget extends AxeScanKey {
  theme: AxeTheme;
  accent: AxeAccent;
}

/**
 * Every entry must name a route|state|rule the caller scans, in every theme
 * and accent it claims; otherwise it could never fail as stale and would
 * hide nothing. Returns the problems (empty when valid). Pure.
 */
export function validateKnownViolations(known: KnownViolations, scans: readonly AxeScanTarget[]): string[] {
  const scanned = new Set(scans.map((scan) => `${scan.route}|${scan.state}|${scan.theme}|${scan.accent}`));
  const problems: string[] = [];
  for (const [entryKey, entry] of Object.entries(known)) {
    const [route, state, ruleId, extra] = entryKey.split('|');
    if (!route || !state || !ruleId || extra !== undefined) {
      problems.push(`KNOWN_VIOLATIONS key "${entryKey}" is not route|state|rule`);
      continue;
    }
    if (!/^\d{4}-\d{2}-\d{2}$/.test(entry.recorded) || !entry.finding || !entry.nodes || entry.themes.length === 0) {
      problems.push(`KNOWN_VIOLATIONS "${entryKey}" needs a finding, an ISO recorded date, themes and a nodes selector`);
    }
    for (const theme of entry.themes) {
      for (const accent of entry.accents ?? ['bright']) {
        if (!scanned.has(`${route}|${state}|${theme}|${accent}`)) {
          problems.push(`KNOWN_VIOLATIONS "${entryKey}" claims ${theme}/${accent}, which no scan covers`);
        }
      }
    }
  }
  return problems;
}

/** The violating nodes that do NOT match `selector` (resolved in the page, not by axe's selector text). */
export async function nodesOutside(page: Page, violation: AxeViolation, selector: string): Promise<AxeNodes> {
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

export interface ExpectAxeCleanOptions {
  key: AxeScanKey;
  theme: AxeTheme;
  /** Default 'bright'. */
  accent?: AxeAccent;
  known: KnownViolations;
  /** Scope the scan to one region (a CSS selector); default: the whole page. */
  include?: string;
}

/** Scan the page and fail on any gating violation the ratchet does not cover, or on a stale entry. */
export async function expectAxeClean(page: Page, options: ExpectAxeCleanOptions): Promise<AxeResults> {
  const accent = options.accent ?? 'bright';
  const context: AxeScanContext = { key: options.key, theme: options.theme, accent, known: options.known };
  let builder = new AxeBuilder({ page }).withTags([...WCAG_TAGS, ADVISORY_TAG]);
  if (options.include) builder = builder.include(options.include);
  const results = await builder.analyze();

  const { gating, advisory } = partitionByTags(results.violations);
  const uncovered = new Map<string, AxeNodes>();
  for (const violation of gating) {
    const entry = options.known[knownKey(options.key, violation.id)];
    if (entry && entryApplies(entry, options.theme, accent)) {
      uncovered.set(violation.id, await nodesOutside(page, violation, entry.nodes));
    }
  }
  const verdict = evaluateAxeRatchet(gating, context, uncovered);
  await reportAdvisory(context, advisory);

  const label = `${knownKey(options.key, '*')} (${options.theme}/${accent})`;
  expect(verdict.unrecorded, `unrecorded axe violations on ${label}`).toEqual([]);
  expect(verdict.stale, 'ratchet: recorded violations that no longer reproduce').toEqual([]);
  expect(results.passes.length, 'axe evaluated the page').toBeGreaterThan(0);
  return results;
}

async function reportAdvisory(context: AxeScanContext, advisory: readonly AxeViolation[]): Promise<void> {
  if (advisory.length === 0) return;
  const info = test.info();
  const scan = `${knownKey(context.key, '*')} ${context.theme}/${context.accent}`;
  const body = advisory.map((violation) => ({
    rule: violation.id,
    impact: violation.impact ?? null,
    help: violation.help,
    helpUrl: violation.helpUrl,
    nodes: violation.nodes.map((node) => node.target.join(' ')),
  }));
  await info.attach('axe-best-practice.json', {
    body: JSON.stringify({ scan, advisory: body }, null, 2),
    contentType: 'application/json',
  });
  info.annotations.push({
    type: 'axe-best-practice',
    description: `${scan}: ${advisory.length} advisory rule(s): ${advisory.map((violation) => violation.id).join(', ')}`,
  });
}
