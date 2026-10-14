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
 *  - the non-bannered failed read (`read-failed`, quality-06) on Lead Queue
 *    and Segment Intelligence;
 *  - W5b w5-home-geo-lever: the Delta Explainer drawer on Home
 *    (`delta-explainer`) and the retained-value note on Segment
 *    Intelligence (`stale-note`);
 *  - W5c w5-evidence-drawer: a Home KPI drawer's How we got and its Under
 *    the hood tab (`evidence-how-we-got`, `evidence-under-the-hood`);
 *  - the teal, navy and red accents on Home, Lead Queue and Borrower 360,
 *    default and evidence drawer, both themes (bright is everything above);
 *  - W5d w5-sales-loop: the Sales ops manager view (30-day period) and the
 *    Distribution Board's dry-run plan, both themes.
 *
 * The ratchet (axe.ts KNOWN_VIOLATIONS) is validated against this matrix at
 * load: every entry names a scan that runs here, in each theme and accent it
 * claims. Best-practice rules are reported as advisory attachments. No scan
 * opens an audited read beyond the route's natural load (visual.ts guard).
 */
import type { FixtureTheme } from './app';
import { KNOWN_VIOLATIONS, expectAxeClean, validateKnownViolations, type AxeAccent, type AxeScanTarget } from './axe';
import { registerBoard } from './data/salesLoop';
import { OVERLAY_STATES, enterState, prepareState, type FixtureState } from './fixtureStates';
import { FIXTURE_ROUTES, FIXTURE_THEMES, type FixtureRoute } from './routes';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const OVERLAY_ROUTES = new Set(['home', 'lead-queue', 'borrower-360-detail', 'offer-orchestrator-detail', 'ask-genie']);
const EXTRA_STATES: Readonly<Record<string, readonly FixtureState[]>> = {
  'lead-queue': ['filter-menu', 'expanded-row', 'read-failed', 'triage', 'approval-requests', 'my-approval-requests', 'request-approval-form'],
  home: ['degraded', 'delta-explainer', 'evidence-how-we-got', 'evidence-under-the-hood', 'followups-tray'],
  'segment-intelligence': ['read-failed', 'stale-note'],
  'admin-config': ['run-dialog-open'],
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
    prepareState(mockApi, state, route.name);
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

// W5d w5-sales-loop (critic-10 / wow-power-5): the Sales ops manager view on
// a 30-day period and the Distribution Board's dry-run plan, both themes, with
// no ratchet entry (known: {}).
test.describe('the sales manager view and the Distribution Board', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`analytics · sales-ops manager view · ${theme} has no WCAG A/AA violation`, async ({ app, mockApi, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/analytics?view=sales-ops&sales_period=30d');
      const naturalLoad = markNaturalLoad(mockApi);
      await expect(page.locator('.sales-ops-period').getByRole('button', { name: '30 days' })).toHaveAttribute('aria-pressed', 'true');
      await expectAxeClean(page, { key: { route: 'analytics-sales-ops', state: 'manager-view' }, theme, known: {} });
      expectNoAuditedReadSince(mockApi, naturalLoad, 'analytics · sales-ops manager view');
    });

    test(`analytics · distribution board · ${theme} has no WCAG A/AA violation`, async ({ app, mockApi, page }) => {
      await app.setTheme(theme);
      registerBoard(mockApi);
      await app.gotoRoute('/analytics?view=sales-ops');
      const naturalLoad = markNaturalLoad(mockApi);
      await page.getByRole('button', { name: 'Distribute approved leads…' }).click();
      await expect(page.locator('dialog.distribution-board .distribution-board__bar')).toHaveCount(2);
      await app.settle();
      await expectAxeClean(page, { key: { route: 'analytics-sales-ops', state: 'distribution-board' }, theme, known: {} });
      expectNoAuditedReadSince(mockApi, naturalLoad, 'analytics · distribution board');
    });
  }
});

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
    // Both labelled groups are rendered: the governed-assets list of the
    // lineage family on Overview, and the governed-asset metadata stats
    // (admin session) under the hood (W5c w5-evidence-drawer, flow-10).
    await expect(drawer.locator('.governed-assets__list')).toBeVisible();
    const flaggedIn = async (state: string): Promise<string[]> => {
      // The drawer is a native <dialog> (stack-05): scope the gate to it. The
      // helper fails on any violation; the needs-review results are read here.
      const r = await expectAxeClean(page, {
        key: { route: 'lead-queue', state },
        theme: 'dark',
        known: {},
        include: 'dialog.drawer:not(.proof-drawer)',
      });
      return r.incomplete
        .filter((result) => result.id === 'aria-prohibited-attr')
        .flatMap((result) => result.nodes.map((node) => `${node.target.join(' ')} (${result.impact ?? 'n/a'})`));
    };
    expect(await flaggedIn('evidence-drawer-groups'), 'aria-label on a role that prohibits it, inside the drawer').toEqual([]);
    await drawer.getByRole('tab', { name: 'Under the hood' }).click();
    await expect(drawer.locator('.source-stat-grid')).toBeVisible();
    await app.settle();
    expect(await flaggedIn('evidence-drawer-groups-under-the-hood'), 'aria-label on a role that prohibits it, under the hood').toEqual([]);
  });
});

// W5d w5-palette-offer-sales-loop: the palette's provider groups, the ? sheet
// in two columns at 1440x720 with the Triage deck open, and the Offer's
// reject-note field error.
test.describe('palette groups, the short-viewport sheet and the Offer note error', () => {
  const offerDetail = FIXTURE_ROUTES.find((route) => route.name === 'offer-orchestrator-detail');
  for (const theme of FIXTURE_THEMES) {
    test(`home · command-palette-groups · ${theme}`, async ({ app, mockApi, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/');
      const naturalLoad = markNaturalLoad(mockApi);
      const palette = await app.openCommandPalette();
      await palette.getByRole('combobox').fill('refi');
      await expect(palette.getByRole('group', { name: 'Glossary' })).toBeVisible();
      await app.settle();
      await expectAxeClean(page, {
        key: { route: 'home', state: 'command-palette-groups' },
        theme,
        known: KNOWN_VIOLATIONS,
        include: 'dialog.cmdk[open]',
      });
      expectNoAuditedReadSince(mockApi, naturalLoad, `palette groups · ${theme}`);
    });

    test(`lead-queue · shortcut sheet at 1440x720 with the Triage deck · ${theme}`, async ({ app, page }) => {
      await page.setViewportSize({ width: 1440, height: 720 });
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      await page.getByTestId('lead-triage-enter').click();
      await expect(page.getByTestId('triage-deck')).toBeVisible();
      await page.keyboard.press('?');
      await expect(page.getByTestId('shortcut-sheet')).toBeVisible();
      await expectAxeClean(page, {
        key: { route: 'lead-queue', state: 'shortcut-sheet-1440x720-triage' },
        theme,
        known: KNOWN_VIOLATIONS,
        include: '[data-testid="shortcut-sheet"]',
      });
    });

    test(`offer-orchestrator-detail · reject note error · ${theme}`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute(offerDetail?.path ?? '/offer-orchestrator');
      const bar = page.getByTestId('offer-action-bar');
      await bar.getByRole('button', { name: 'Reject', exact: true }).click();
      await bar.getByLabel('Reason').selectOption('other_with_text');
      // aria-disabled until a note is given: a keyboard press still submits.
      await bar.getByRole('button', { name: 'Confirm reject' }).focus();
      await page.keyboard.press('Enter');
      await expect(bar.getByLabel('Rationale note')).toHaveAttribute('aria-invalid', 'true');
      await expectAxeClean(page, {
        key: { route: 'offer-orchestrator-detail', state: 'reject-note-error' },
        theme,
        known: KNOWN_VIOLATIONS,
        include: '[data-testid="offer-action-bar"]',
      });
    });
  }
});
