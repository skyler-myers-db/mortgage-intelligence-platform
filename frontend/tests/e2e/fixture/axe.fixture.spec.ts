/**
 * axe gate on pull requests (audit a11y-05, quality-05): WCAG 2.0/2.1/2.2 A
 * and AA rules at every impact, through the shared helper in axe.ts, over a
 * matrix derived from FIXTURE_ROUTES:
 *
 *  - the default state of every route (both index routes, every Analytics
 *    tab, glossary, asset detail, not-found included), both themes;
 *  - the overlays the nightly live scan never opens (evidence drawer,
 *    command palette, Genie panel) on Home, Lead Queue, Borrower 360, the
 *    Offer Orchestrator and Ask Genie; the filter menu and an expanded row on
 *    Lead Queue; the warehouse-down degraded state on Home;
 *  - the teal, navy and red accents on Home, Lead Queue and Borrower 360,
 *    default and evidence drawer, both themes (bright is everything above).
 *
 * The ratchet (axe.ts KNOWN_VIOLATIONS) is validated against this matrix at
 * load: every entry names a scan that runs here, in each theme and accent it
 * claims. Best-practice rules are reported as advisory attachments. No scan
 * opens an audited read beyond the route's natural load (visual.ts guard).
 */
import type { FixtureTheme } from './app';
import { KNOWN_VIOLATIONS, expectAxeClean, validateKnownViolations, type AxeAccent, type AxeScanTarget } from './axe';
import { OVERLAY_STATES, enterState, prepareState, type FixtureState } from './fixtureStates';
import { FIXTURE_ROUTES, FIXTURE_THEMES, type FixtureRoute } from './routes';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const OVERLAY_ROUTES = new Set(['home', 'lead-queue', 'borrower-360-detail', 'offer-orchestrator-detail', 'ask-genie']);
const EXTRA_STATES: Readonly<Record<string, readonly FixtureState[]>> = {
  'lead-queue': ['filter-menu', 'expanded-row'],
  home: ['degraded'],
};
const ACCENT_ROUTES = new Set(['home', 'lead-queue', 'borrower-360-detail']);
const SWEPT_ACCENTS: readonly AxeAccent[] = ['teal', 'navy', 'red'];
const ACCENT_STATES: readonly FixtureState[] = ['default', 'evidence-drawer'];

interface AxeScan {
  route: FixtureRoute;
  state: FixtureState;
  theme: FixtureTheme;
  accent: AxeAccent;
}

function statesFor(route: FixtureRoute): FixtureState[] {
  return [
    'default',
    ...(OVERLAY_ROUTES.has(route.name) ? OVERLAY_STATES : []),
    ...(EXTRA_STATES[route.name] ?? []),
  ];
}

const SCANS: readonly AxeScan[] = FIXTURE_THEMES.flatMap((theme) =>
  FIXTURE_ROUTES.flatMap((route) => [
    ...statesFor(route).map((state): AxeScan => ({ route, state, theme, accent: 'bright' })),
    ...(ACCENT_ROUTES.has(route.name)
      ? SWEPT_ACCENTS.flatMap((accent) => ACCENT_STATES.map((state): AxeScan => ({ route, state, theme, accent })))
      : []),
  ]),
);

// A mistyped or unscanned key would sit in the map, never match and never go
// stale: fail at load instead.
const ratchetProblems = validateKnownViolations(
  KNOWN_VIOLATIONS,
  SCANS.map((scan): AxeScanTarget => ({ route: scan.route.name, state: scan.state, theme: scan.theme, accent: scan.accent })),
);
if (ratchetProblems.length > 0) throw new Error(ratchetProblems.join('\n'));

for (const { route, state, theme, accent } of SCANS) {
  const accentLabel = accent === 'bright' ? '' : ` · accent ${accent}`;
  test(`${route.name} · ${state} · ${theme}${accentLabel} has no WCAG A/AA violation beyond the recorded ratchet`, async ({ app, mockApi, page }) => {
    await app.setTheme(theme);
    if (accent !== 'bright') await app.setAccent(accent);
    prepareState(mockApi, state);
    await app.gotoRoute(route.path);
    const naturalLoad = markNaturalLoad(mockApi);
    await expect(page.locator('html')).toHaveAttribute('data-accent', accent);
    // Scan the state once the reads it started have landed, so every run
    // scans the same DOM rather than whichever half-loaded state won the race.
    await enterState(app, page, state);

    await expectAxeClean(page, { key: { route: route.name, state }, theme, accent, known: KNOWN_VIOLATIONS });
    expectNoAuditedReadSince(mockApi, naturalLoad, `${route.name} · ${state}`);
  });
}

test.describe('named groups in the evidence drawer', () => {
  // ARIA 1.2 prohibits aria-label on an element with no role (a plain div).
  // axe files it under aria-prohibited-attr: "needs review" while the element
  // has text, a serious violation while it has none, so a scan that catches
  // the drawer mid-render can fail on it while a settled scan passes it.
  // Checking the needs-review results too makes the proof independent of timing.
  test('every aria-label in the open drawer sits on a role that permits it', async ({ app, page }) => {
    await app.setTheme('dark');
    await app.gotoRoute('/lead-queue');
    const drawer = await app.openEvidenceDrawer(page.locator('.evidence-chip:visible').first());
    await app.settle();
    // Both labelled groups are rendered: the governed-asset metadata stats
    // (admin session) and the governed-assets list of the lineage family.
    await expect(drawer.locator('.source-stat-grid')).toBeVisible();
    await expect(drawer.locator('.governed-assets__list')).toBeVisible();

    // The drawer is a native <dialog> (stack-05): scope the gate to it. The
    // helper fails on any violation; the needs-review results are read here.
    const r = await expectAxeClean(page, {
      key: { route: 'lead-queue', state: 'evidence-drawer-groups' },
      theme: 'dark',
      known: {},
      include: 'dialog.drawer:not(.proof-drawer)',
    });
    const flagged = r.incomplete
      .filter((result) => result.id === 'aria-prohibited-attr')
      .flatMap((result) => result.nodes.map((node) => `${node.target.join(' ')} (${result.impact ?? 'n/a'})`));
    expect(flagged, 'aria-label on a role that prohibits it, inside the drawer').toEqual([]);
  });
});
