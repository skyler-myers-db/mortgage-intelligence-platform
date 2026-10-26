/**
 * Rendered-layer proofs for W5e w5-compiler-lint (proof P2) at 1440x900 in
 * both themes unless a case sets its own viewport:
 *
 *  (a) critic-08, the Rail: keyboard focus on M0 opens #mip-tooltip with the
 *      module description; the admin link is named 'Admin / settings'; no
 *      element in .rail carries a native title.
 *  (b) a [title] sweep over ten routes finds no native title: the
 *      integrator's residual commit emptied EXPECTED_PENDING_TITLES and
 *      TITLE_BAN_ALLOWLIST. SVG <title> elements and iframes are exempt.
 *  (c) a11y-05, the Console's recent activity on offer-orchestrator-detail:
 *      with 25 events it is a region named 'My recent activity', reachable
 *      by Tab and scrolled by the arrow keys; with 2 it takes no tab stop.
 *  (d) a shortcut tooltip's popup text equals its hidden description.
 *  (e) the Escape decision: a hover-opened tooltip over the evidence drawer
 *      closes with the drawer on one Escape; a keyboard-opened one in the
 *      Genie panel takes the first Escape alone. (The evidence drawer is a
 *      native modal <dialog>, so nothing behind it, the Topbar included, can
 *      be hovered while it is open: the hover case uses the <Timestamp> in
 *      the drawer's freshness foot.)
 *  (f) at 1440x640 the ? sheet's list is a named, focusable region.
 *  (g) the Triage deck's draft copy is a focusable region.
 */
import type { Locator, Page } from '@playwright/test';
import type { ActorAuditEventPage } from '../../../src/lib/apiTypes';
import { LEADS, PRIMARY_BORROWER } from './data/borrowers';
import { json, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { TOOLTIP_POPUP } from './tooltip';

test.use({ viewport: { width: 1440, height: 900 } });

const OFFER_DETAIL = `/offer-orchestrator/${PRIMARY_BORROWER.borrower_id}`;

function popup(page: Page): Locator {
  return page.locator(`${TOOLTIP_POPUP}[role="tooltip"]:not([hidden])`);
}

/** The tooltip controller loads after the first paint; its install creates the hidden popup. */
async function controllerReady(page: Page): Promise<void> {
  await expect(page.locator(TOOLTIP_POPUP)).toBeAttached();
}

/** The actor's own audit-free activity, `count` rows, newest first. */
function registerMyEvents(mockApi: MockApi, count: number): void {
  mockApi.register<ActorAuditEventPage>('GET', '/api/audit/my-events', () =>
    json<ActorAuditEventPage>({
      items: Array.from({ length: count }, (_, index) => ({
        event_type: index % 2 === 0 ? 'VIEW_LEADS' : 'RUN_GENIE',
        entity_type: 'borrower',
        subject_id: LEADS[index % LEADS.length].borrower_id,
        created_at: `2026-07-14T${String(14 - Math.floor(index / 10)).padStart(2, '0')}:${String(50 - (index % 10) * 5).padStart(2, '0')}:00Z`,
      })),
      next_cursor: null,
    }),
  );
}

/**
 * Native titles still allowed, by route and the selector of the element that
 * carries them. Emptied by the integrator's residual commit (W5e): every
 * owner lane migrated its sites and eslint TITLE_BAN_ALLOWLIST is empty, so
 * any native title on a swept route fails. SHRINK-ONLY; never add an entry.
 */
const EXPECTED_PENDING_TITLES: Readonly<Record<string, readonly string[]>> = {};

const SWEPT_ROUTES = [
  '/',
  '/lead-queue',
  `/borrower-360/${PRIMARY_BORROWER.borrower_id}`,
  OFFER_DETAIL,
  '/ask-genie',
  '/admin-config',
  '/audit-ledger',
  '/analytics',
  '/segment-intelligence',
  '/glossary',
];

for (const theme of FIXTURE_THEMES) {
  test.describe(`compiler-lint (${theme})`, () => {
    test.beforeEach(async ({ app }) => {
      await app.setTheme(theme);
    });

    test('(a) the Rail: M0 describes its module on keyboard focus, the admin link has a name, no native title', async ({ app, page }) => {
      await app.gotoRoute('/');
      await controllerReady(page);
      const rail = page.locator('.rail');
      await expect(rail.locator('[title]')).toHaveCount(0);
      await expect(rail.getByRole('link', { name: 'Admin / settings' })).toBeVisible();

      // Keyboard focus: from the brand link, Tab lands on M0.
      await rail.locator('.rail__brand').focus();
      await page.keyboard.press('Tab');
      const m0 = rail.locator('.rail__item.is-active');
      await expect(m0).toBeFocused();
      await expect(m0).toHaveAccessibleName('M0');
      await expect(popup(page)).toHaveText('Module 0: Top-of-Funnel — Lead generation + borrower segmentation (ships today).');
    });

    test('(b) the [title] sweep: every native title left belongs to a TITLE_BAN_ALLOWLIST owner', async ({ app, page }) => {
      const strays: Record<string, string[]> = {};
      const matched = new Set<string>();
      for (const path of SWEPT_ROUTES) {
        await app.gotoRoute(path);
        await app.settle();
        const allowed = EXPECTED_PENDING_TITLES[path] ?? [];
        const found = await page.evaluate((selectors) => {
          const elements = Array.from(document.querySelectorAll<HTMLElement>('[title]'))
            // An SVG <title> is an element, not this attribute; an iframe keeps its required title.
            .filter((element) => element.tagName !== 'IFRAME' && !(element instanceof SVGElement));
          return {
            strays: [...new Set(elements
              .filter((element) => !selectors.some((selector) => element.matches(selector)))
              .map((element) => `${element.tagName.toLowerCase()}.${[...element.classList].join('.')}`))],
            matched: selectors.filter((selector) => elements.some((element) => element.matches(selector))),
          };
        }, [...allowed]);
        if (found.strays.length > 0) strays[path] = found.strays;
        for (const selector of found.matched) matched.add(`${path} ${selector}`);
      }
      expect(strays, 'native titles outside EXPECTED_PENDING_TITLES (migrate them)').toEqual({});
      // Shrink-only: an expected entry that no longer matches is stale.
      const stale = Object.entries(EXPECTED_PENDING_TITLES)
        .flatMap(([path, selectors]) => selectors.map((selector) => `${path} ${selector}`))
        .filter((entry) => !matched.has(entry));
      expect(stale, 'EXPECTED_PENDING_TITLES entries with no title left: delete them').toEqual([]);
    });

    test('(c) the Console activity is a named region that takes a tab stop only while it scrolls', async ({ app, page, mockApi }) => {
      registerMyEvents(mockApi, 25);
      await app.gotoRoute(OFFER_DETAIL);
      let consolePanel = await app.openConsole();
      let region = consolePanel.getByRole('region', { name: 'My recent activity' });
      await expect(region.locator('.audit')).toHaveCount(25);
      await expect(region).toHaveAttribute('tabindex', '0');
      await consolePanel.getByRole('button', { name: 'Refresh' }).focus();
      await page.keyboard.press('Tab');
      await expect(region).toBeFocused();
      const before = await region.evaluate((element) => element.scrollTop);
      await page.keyboard.press('ArrowDown');
      await page.keyboard.press('ArrowDown');
      await expect.poll(() => region.evaluate((element) => element.scrollTop), 'the arrow keys scroll the list').toBeGreaterThan(before);

      registerMyEvents(mockApi, 2);
      await app.gotoRoute(OFFER_DETAIL);
      consolePanel = await app.openConsole();
      region = consolePanel.getByRole('region', { name: 'My recent activity' });
      await expect(region.locator('.audit')).toHaveCount(2);
      await expect(region, 'no needless tab stop on a list that fits').not.toHaveAttribute('tabindex', /.*/);
    });

    test('(d) a shortcut tooltip reads exactly like its description', async ({ app, page }) => {
      await app.gotoRoute('/');
      await controllerReady(page);
      await page.getByRole('combobox', { name: 'Search borrowers' }).click();
      await page.keyboard.press('Tab');
      const palette = page.locator('.topbar__search-kbd');
      await expect(palette).toBeFocused();
      await expect(popup(page).locator('kbd.tooltip__kbd')).toBeVisible();
      const described = await palette.evaluate((node) => {
        const id = node.getAttribute('data-tooltip');
        return id ? document.getElementById(id)?.textContent ?? '' : '';
      });
      await expect(popup(page)).toHaveText(described);
      expect(described).toMatch(/^Command palette \((⌘K|Ctrl K)\)$/);
    });

    test('(e) Escape: a hover tooltip shares the key with the layer below; a keyboard one takes it alone', async ({ app, page }) => {
      await app.gotoRoute('/');
      await controllerReady(page);
      const drawer = await app.openEvidenceDrawer(page.locator('.kpi .kpi__source .evidence-chip', { hasText: 'Rate + equity screen' }));
      const stamp = drawer.locator('[data-testid="evidence-freshness-foot"] time');
      await expect(stamp).toBeVisible();
      await stamp.hover();
      await expect(popup(page)).toHaveText(/UTC$/);
      await page.keyboard.press('Escape');
      await expect(popup(page)).toHaveCount(0);
      await expect(drawer, 'the same Escape closed the drawer').not.toHaveClass(/is-open/);

      const genie = await app.openGenie();
      const newThread = genie.getByRole('button', { name: 'Start a new Genie thread' });
      await genie.getByRole('textbox', { name: 'Ask Genie' }).focus();
      await page.keyboard.press('Shift+Tab');
      await page.keyboard.press('Shift+Tab');
      // Walk back to the header's New thread control by keyboard.
      for (let step = 0; step < 12 && !(await newThread.evaluate((node) => node === document.activeElement)); step += 1) {
        await page.keyboard.press('Shift+Tab');
      }
      await expect(newThread).toBeFocused();
      await expect(popup(page)).toHaveText('New thread');
      await page.keyboard.press('Escape');
      await expect(popup(page)).toHaveCount(0);
      await expect(newThread, 'focus stays on the trigger').toBeFocused();
      await expect(genie, 'the first Escape closed only the tooltip').toBeVisible();
      await page.keyboard.press('Escape');
      await expect(genie).toBeHidden();
    });

    test('(f) at 1440x640 the ? sheet list is a named region that takes a tab stop', async ({ app, page }) => {
      await page.setViewportSize({ width: 1440, height: 640 });
      await app.gotoRoute('/lead-queue');
      await page.locator('main#main-content h1').filter({ visible: true }).focus();
      await page.keyboard.press('?');
      const sheet = page.getByTestId('shortcut-sheet');
      await expect(sheet).toBeVisible();
      const list = sheet.getByRole('region', { name: 'Shortcut list' });
      await expect(list).toHaveAttribute('tabindex', '0');
      await sheet.getByRole('button', { name: 'Close keyboard shortcuts' }).focus();
      await page.keyboard.press('Tab');
      await expect(list).toBeFocused();
      // Shorter still, the list scrolls inside the sheet (the reason it is a
      // focusable region; the W5d two-column layout fits it from ~640 px).
      await page.setViewportSize({ width: 1440, height: 480 });
      await expect.poll(() => list.evaluate((element) => element.scrollHeight - element.clientHeight)).toBeGreaterThan(0);
      await expect(list).toHaveAttribute('tabindex', '0');
    });

    test('(g) the Triage deck draft copy is a focusable region', async ({ app, page }) => {
      await app.gotoRoute('/lead-queue');
      await page.getByTestId('lead-triage-enter').click();
      await expect(page.getByTestId('triage-deck')).toBeVisible();
      await expect(page.locator('.triage__card .triage__name')).toBeFocused();
      await page.keyboard.press('a');
      await expect(page.locator('[data-testid="lead-approve-review"][data-review-phase="ready"]')).toBeVisible();
      const copy = page.getByRole('region', { name: /^Draft outreach for / });
      await expect(copy).toBeVisible();
      await expect(copy).toHaveAttribute('tabindex', '0');
    });
  });
}

// The R5 popup for a disabled control (proof P3) is compiler-lint.cross-engine.fixture.spec.ts.
