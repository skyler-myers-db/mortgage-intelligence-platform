/**
 * W5a filters and saved views (audit tables-06, tables-09 phase 2, flow-08
 * slice 1, wow-stage-1), proven in the production build at 1440x900 in both
 * themes:
 *
 *  - the score / rate-spread inputs commit on blur or Enter only: exactly one
 *    GET /api/leads per commit carrying the bound, none per keystroke; their
 *    removable chips and the More filters count; the clamp notice, the
 *    inverted-bounds error and the cohort-disabled state;
 *  - opening the STATE menu reads exactly one audit-free /api/leads/facets
 *    (without `state`), shows counts, and option names stay exact;
 *  - saved views: save, apply (one GET /api/leads), aria-current, delete, the
 *    409 and 422 copy (never echoing the name), the 503 degraded state,
 *    Escape and focus return;
 *  - no menu, panel or hover writes a VIEW_LEADS row; the presets row stays
 *    one line and 8 ranked rows stay above the fold, Console open or closed;
 *  - forced colours keep a visible outline on the new controls.
 */
import type { Locator, Page } from '@playwright/test';
import { KNOWN_VIOLATIONS, expectAxeClean } from './axe';
import { SEEDED_VIEW, SavedViewsStore, boundedLeadsPage } from './data/filtersSavedViews';
import type { MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { auditedReadsAfter, markNaturalLoad } from './visual';

const ROWS = 'table.lead-table__table tbody tr[aria-rowindex]:not(.tbl__expand)';
const COHORT = '11111111-1111-1111-1111-111111111111';
/**
 * Off Linux, widen the presets row's text to Linux Chromium's wider Geist
 * metrics (the LINUX_TEXT_EMULATION pattern of charts.fixture.spec.ts), so a
 * one-line check that passes here also passes on CI.
 */
const LINUX_TEXT_EMULATION = '.lead-queue-views .filter, .lead-queue-views .btn { letter-spacing: 0.5px; }';

function leadReads(mockApi: MockApi): string[] {
  return mockApi.calls.filter((call) => call.method === 'GET' && call.path === '/api/leads').map((call) => call.search);
}

function facetReads(mockApi: MockApi): string[] {
  return mockApi.calls.filter((call) => call.method === 'GET' && call.path === '/api/leads/facets').map((call) => call.search);
}

function savedViewCalls(mockApi: MockApi): string[] {
  return mockApi.calls
    .filter((call) => call.path.startsWith('/api/workspace/saved-views'))
    .map((call) => `${call.method} ${call.path}`);
}

async function openMoreFilters(page: Page): Promise<Locator> {
  const toggle = page.getByTestId('lead-queue-more-filters');
  if ((await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
  const panel = page.getByRole('group', { name: 'More queue filters' });
  await expect(panel).toBeVisible();
  return panel;
}

async function openSavedViews(page: Page): Promise<Locator> {
  await page.getByTestId('lead-queue-saved-views').click();
  const panel = page.getByTestId('lead-queue-saved-views-panel');
  await expect(panel).toBeVisible();
  return panel;
}

async function rowsAboveTheFold(page: Page): Promise<number> {
  const fold = page.viewportSize()?.height ?? 900;
  return page.locator(ROWS).evaluateAll(
    (rows, bottom) => rows.filter((row) => {
      const rect = row.getBoundingClientRect();
      return rect.top >= 0 && rect.bottom <= bottom;
    }).length,
    fold,
  );
}

/**
 * Lines the presets row wraps to: its children are centred, so a pill and the
 * shorter Copy link button have different tops on one line. Children share a
 * line when their vertical extents overlap.
 */
async function presetsRowLines(page: Page): Promise<number> {
  return page.locator('.lead-queue-views').evaluate((row) => {
    const lines: Array<{ top: number; bottom: number }> = [];
    for (const child of row.children) {
      const rect = child.getBoundingClientRect();
      const line = lines.find((candidate) => rect.top < candidate.bottom && rect.bottom > candidate.top);
      if (line) {
        line.top = Math.min(line.top, rect.top);
        line.bottom = Math.max(line.bottom, rect.bottom);
      } else {
        lines.push({ top: rect.top, bottom: rect.bottom });
      }
    }
    return lines.length;
  });
}

async function hasVisibleOutline(target: Locator): Promise<boolean> {
  return target.evaluate((el) => {
    const style = getComputedStyle(el);
    return style.outlineStyle !== 'none' && Number.parseFloat(style.outlineWidth) > 0;
  });
}

for (const theme of FIXTURE_THEMES) {
  test.describe(`${theme}`, () => {
    test('score and spread bounds commit once per blur or Enter, with chips, clamp and inverted states', async ({ app, page, mockApi }) => {
      mockApi.register('GET', '/api/leads', boundedLeadsPage);
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue?state=IL');
      const t0 = markNaturalLoad(mockApi);
      const baseline = leadReads(mockApi).length;
      const panel = await openMoreFilters(page);

      const scoreMin = panel.getByLabel('Score at least');
      await scoreMin.click();
      await page.keyboard.type('70');
      await page.waitForTimeout(250);
      expect(leadReads(mockApi), 'no read per keystroke').toHaveLength(baseline);
      await page.keyboard.press('Tab');
      await expect.poll(() => leadReads(mockApi).length).toBe(baseline + 1);
      await app.settle();
      expect(new URLSearchParams(leadReads(mockApi)[baseline]).get('min_opportunity_score')).toBe('70');
      await expect(page).toHaveURL(/min_opportunity_score=70/);

      // Enter commits too; an unchanged value writes nothing.
      const spreadMax = panel.getByLabel('Rate spread at most (bps)');
      await spreadMax.fill('150');
      await spreadMax.press('Enter');
      await expect.poll(() => leadReads(mockApi).length).toBe(baseline + 2);
      await app.settle();
      expect(new URLSearchParams(leadReads(mockApi)[baseline + 1]).get('max_rate_spread_bps')).toBe('150');
      await spreadMax.fill('150');
      await spreadMax.press('Enter');
      await page.waitForTimeout(250);
      expect(leadReads(mockApi)).toHaveLength(baseline + 2);

      // Chips and the More filters count.
      const hero = page.getByTestId('lead-queue-active-filters');
      await expect(hero.getByRole('button', { name: 'Remove SCORE: ≥ 70 filter' })).toBeVisible();
      await expect(hero.getByRole('button', { name: 'Remove RATE SPREAD: ≤ +150 bps filter' })).toBeVisible();
      await expect(page.getByTestId('lead-queue-more-filters')).toHaveAttribute('aria-label', 'More filters (2 active)');

      // Clamp: an out-of-range at-most becomes 100 and the field says so.
      const scoreMax = panel.getByLabel('Score at most');
      await scoreMax.fill('150');
      await scoreMax.press('Tab');
      await expect(page).toHaveURL(/max_opportunity_score=100/);
      await expect(panel.getByText('Allowed range is 0 to 100; set to 100.')).toBeVisible();
      await app.settle();

      // Inverted: an at-least above the at-most is an error and writes nothing.
      const before = leadReads(mockApi).length;
      await scoreMin.fill('120');
      await scoreMin.press('Enter');
      // 120 clamps to 100 = the at-most, which is allowed; 101 is out of range.
      await expect(page).toHaveURL(/min_opportunity_score=100/);
      await app.settle();
      await scoreMax.fill('90');
      await scoreMax.press('Enter');
      await expect(scoreMax).toHaveAttribute('aria-invalid', 'true');
      await expect(panel.getByText('The at-least value must not be above the at-most value.')).toBeVisible();
      await page.waitForTimeout(250);
      expect(leadReads(mockApi)).toHaveLength(before + 1);
      await expectAxeClean(page, { key: { route: 'lead-queue', state: 'range-filters' }, theme, known: KNOWN_VIOLATIONS });

      // Removing a chip drops both keys of its dimension in one read.
      const beforeRemove = leadReads(mockApi).length;
      await hero.getByRole('button', { name: /^Remove SCORE:/ }).click();
      await expect.poll(() => leadReads(mockApi).length).toBe(beforeRemove + 1);
      const last = new URLSearchParams(leadReads(mockApi)[beforeRemove]);
      expect(last.has('min_opportunity_score') || last.has('max_opportunity_score')).toBe(false);
      expect(last.get('max_rate_spread_bps')).toBe('150');
      await app.settle();

      expect(auditedReadsAfter(mockApi.calls, t0).every((read) => read.startsWith('GET /api/leads?')), 'only the commits read the queue').toBe(true);
    });

    test('a Genie cohort disables the bounds with a hint', async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute(`/lead-queue?cohort_id=${COHORT}&min_opportunity_score=70`);
      const panel = await openMoreFilters(page);
      const inputs = panel.locator('input[type="number"]');
      await expect(inputs).toHaveCount(4);
      for (const input of await inputs.all()) await expect(input).toBeDisabled();
      await expect(panel.getByText('A Genie cohort or a Growth Agent handoff sets its own score and spread thresholds.')).toBeVisible();
      await expect(page.getByTestId('lead-queue-active-filters').getByRole('button', { name: /^Remove SCORE:/ })).toHaveCount(0);
      await expectAxeClean(page, { key: { route: 'lead-queue', state: 'range-filters-cohort' }, theme, known: KNOWN_VIOLATIONS });
    });

    test('the STATE menu reads its facet counts once on open, never on hover, and keeps exact option names', async ({ app, page, mockApi }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue?state=IL&segment=itm');
      const t0 = markNaturalLoad(mockApi);
      const trigger = page.locator('button[aria-haspopup="listbox"][aria-label^="STATE:"]').first();
      await trigger.hover();
      await page.waitForTimeout(300);
      expect(facetReads(mockApi), 'hovering reads nothing').toEqual([]);

      const menu = await app.openFilterMenu('STATE');
      await expect.poll(() => facetReads(mockApi).length).toBe(1);
      const query = new URLSearchParams(facetReads(mockApi)[0]);
      expect(query.get('dimension')).toBe('state');
      expect(query.has('state'), 'the menu counts every state').toBe(false);
      expect(query.get('segment')).toBe('itm');
      await expect(menu.locator('.filter-menu__count').first()).toBeVisible();
      await expect(menu.getByRole('option', { name: 'TX', exact: true })).toHaveCount(1);
      const tx = menu.getByRole('option', { name: 'TX', exact: true });
      await expect(tx).toHaveAccessibleName('TX');
      await expect(tx).toHaveAccessibleDescription(/^\d[\d,]*$/);
      await expectAxeClean(page, { key: { route: 'lead-queue', state: 'facet-counts' }, theme, known: KNOWN_VIOLATIONS });

      await page.keyboard.press('Escape');
      await app.openFilterMenu('STATE');
      await page.waitForTimeout(250);
      expect(facetReads(mockApi), 'a reopen within 60 s is served from cache').toHaveLength(1);
      await page.keyboard.press('Escape');
      expect(auditedReadsAfter(mockApi.calls, t0), 'no menu or hover writes VIEW_LEADS').toEqual([]);
    });

    test('saved views: save, aria-current, apply, delete, fixed error copy, degraded state and Escape', async ({ app, page, mockApi, hygiene }) => {
      // The scripted 409 and 422 answers log as failed loads, by design.
      hygiene.allow('console.error', /status of (?:409|422) .*\/workspace\/saved-views/);
      const store = new SavedViewsStore();
      store.register(mockApi);
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue?state=IL&approval_status=pending');
      expect(savedViewCalls(mockApi), 'mounting the queue reads no saved view').toEqual([]);
      const t0 = markNaturalLoad(mockApi);

      const panel = await openSavedViews(page);
      await expect.poll(() => savedViewCalls(mockApi)).toEqual(['GET /api/workspace/saved-views']);
      const seeded = panel.getByRole('link', { name: SEEDED_VIEW.name });
      await expect(seeded).toHaveAttribute('aria-current', 'true');
      await expectAxeClean(page, { key: { route: 'lead-queue', state: 'saved-views' }, theme, known: KNOWN_VIOLATIONS });

      // Save the current queue under a new name.
      await panel.getByLabel('View name').fill('Pending approval – IL');
      await panel.getByTestId('lead-queue-saved-views-save').click();
      await expect(panel.getByRole('link', { name: 'Pending approval – IL' })).toBeVisible();
      expect(store.posted).toEqual([{ name: 'Pending approval – IL', params: 'state=IL&approval_status=pending' }]);

      // 409 and 422: fixed copy, never the typed name.
      store.failure = 'duplicate';
      await panel.getByLabel('View name').fill('IL pending');
      await panel.getByTestId('lead-queue-saved-views-save').click();
      await expect(panel.locator('.field__error')).toHaveText('A saved view with this name already exists');
      store.failure = 'name';
      await panel.getByLabel('View name').fill('John Smith leads');
      await panel.getByTestId('lead-queue-saved-views-save').click();
      await expect(panel.locator('.field__error')).toHaveText('Use a name without personal details, emails, phone numbers or IDs.');
      await expect(panel.getByLabel('View name')).toHaveAttribute('aria-invalid', 'true');
      await expectAxeClean(page, { key: { route: 'lead-queue', state: 'saved-views-error' }, theme, known: KNOWN_VIOLATIONS });
      store.failure = 'none';
      expect(auditedReadsAfter(mockApi.calls, t0), 'the panel writes no VIEW_LEADS').toEqual([]);

      // Delete: pessimistic, then the list refetches.
      const remove = panel.getByRole('button', { name: `Delete saved view: ${SEEDED_VIEW.name}` });
      await remove.click();
      await expect(panel.getByRole('link', { name: SEEDED_VIEW.name })).toHaveCount(0);
      expect(store.deleted).toEqual([SEEDED_VIEW.view_id]);

      // Escape closes and focus returns to the trigger.
      await page.keyboard.press('Escape');
      await expect(panel).toHaveCount(0);
      await expect(page.getByTestId('lead-queue-saved-views')).toBeFocused();

      // Apply a view from a different queue: one read of that queue.
      await app.gotoRoute('/lead-queue?state=TX');
      const reopened = await openSavedViews(page);
      const reads = leadReads(mockApi).length;
      await reopened.getByRole('link', { name: 'Pending approval – IL' }).click();
      await expect(page).toHaveURL(/state=IL&approval_status=pending/);
      await expect.poll(() => leadReads(mockApi).length).toBe(reads + 1);
      await app.settle();
    });

    test('a Lakebase outage shows Saved views unavailable with Retry and no alert', async ({ app, page, mockApi, hygiene }) => {
      hygiene.allow('console.error', /status of 503 .*\/workspace\/saved-views/);
      const store = new SavedViewsStore();
      store.failure = 'unavailable';
      store.register(mockApi);
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      const panel = await openSavedViews(page);
      await expect(panel.getByText('Saved views unavailable')).toBeVisible();
      await expect(panel.getByRole('alert')).toHaveCount(0);
      await expectAxeClean(page, { key: { route: 'lead-queue', state: 'saved-views-unavailable' }, theme, known: KNOWN_VIOLATIONS });
      store.failure = 'none';
      await panel.getByRole('button', { name: 'Retry' }).click();
      await expect(panel.getByRole('link', { name: SEEDED_VIEW.name })).toBeVisible();
    });
  });
}

/**
 * Ranked rows fully above the fold at 1440x900. Console closed: the queue's
 * contract (queue-layout.fixture.spec.ts), 8. Console open: the hero already
 * stacks its presets under the title on the base branch (measured at 0a30fca2:
 * 6 rows), so the Saved views pill must not cost a row there either.
 */
const ROWS_ABOVE_THE_FOLD = { closed: 8, open: 6 } as const;

test.describe('layout at 1440x900', () => {
  for (const consoleState of ['closed', 'open'] as const) {
    test(`the presets row stays one line and the ranked rows keep their fold (Console ${consoleState})`, async ({ app, page }) => {
      await app.gotoRoute('/lead-queue');
      if (process.platform !== 'linux') await page.addStyleTag({ content: LINUX_TEXT_EMULATION });
      if (consoleState === 'open') await app.openConsole();
      await expect(page.locator(ROWS).nth(5)).toBeVisible();
      expect(await presetsRowLines(page), '.lead-queue-views is one line').toBe(1);
      await expect(page.locator('.lead-queue-views__saved-long'), 'the full label fits').toBeVisible();
      await expect(page.locator('.lead-queue-views__saved-short')).toBeHidden();
      expect(await rowsAboveTheFold(page), 'ranked rows fully above the fold').toBeGreaterThanOrEqual(ROWS_ABOVE_THE_FOLD[consoleState]);
    });
  }
});

test.describe('forced colours', () => {
  test('the range input, a saved-view link and its delete button keep a visible focus outline', async ({ app, page, mockApi }) => {
    new SavedViewsStore().register(mockApi);
    await page.emulateMedia({ forcedColors: 'active' });
    await app.gotoRoute('/lead-queue');
    const more = await openMoreFilters(page);
    const input = more.getByLabel('Score at least');
    // Keyboard modality, so programmatic focus shows :focus-visible.
    await page.keyboard.press('Shift');
    await input.focus();
    expect(await hasVisibleOutline(input), 'range input').toBe(true);
    const panel = await openSavedViews(page);
    const link = panel.getByRole('link', { name: SEEDED_VIEW.name });
    await page.keyboard.press('Shift');
    await link.focus();
    expect(await hasVisibleOutline(link), 'saved-view link').toBe(true);
    const remove = panel.getByRole('button', { name: `Delete saved view: ${SEEDED_VIEW.name}` });
    await page.keyboard.press('Shift');
    await remove.focus();
    expect(await hasVisibleOutline(remove), 'delete button').toBe(true);
  });
});
