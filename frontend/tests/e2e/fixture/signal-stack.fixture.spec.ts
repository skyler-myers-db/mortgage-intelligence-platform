/**
 * Signal stack on Segment Intelligence (audit wow-stage-5;
 * deviation:signal-stack), proven in the rendered page at 1440x900.
 *
 *  - Copy: the three-plus headline with its contactable subset, the largest
 *    overlap, the whole-book footnote; every link opens the Lead Queue's
 *    segment_mode=all intersection, and following one is the ONLY audited
 *    read it causes (one VIEW_LEADS, on the Lead Queue's mount).
 *  - Truth: the table's "at least these" counts for a single signal equal
 *    that segment's card, and the exact rows sum once per borrower.
 *  - States: not built and warming say so; a held response keeps its place,
 *    so the filter row below never moves; a settled one-line state keeps no
 *    reserved dead space (the reservation is scoped to aria-busy, W5c).
 *  - Evidence: the chip opens the drawer on mip.gold.segment_combination_rollup.
 *  - Layout and a11y: no sideways scroll, axe clean in both themes.
 */
import type { Page } from '@playwright/test';
import type { FixtureTheme } from './app';
import { expectAxeClean } from './axe';
import { SEGMENT_COMBINATIONS, SEGMENT_COMBINATIONS_NOT_BUILT, SEGMENTS } from './data/segments';
import { WAREHOUSE_WARMING_UP, json } from './mockApi';
import { expect, test } from './test';
import { auditedReadsAfter, expectNoAuditedReadSince, expectNoSurfaceOverflow, markNaturalLoad } from './visual';

const ROUTE = '/segment-intelligence';
const PATH = '/api/segments/combinations';
const THEMES: readonly FixtureTheme[] = ['dark', 'light'];
const COUNT = new Intl.NumberFormat('en-US');

const stack = (page: Page) => page.locator('section.signal-stack');
const button = (page: Page, name: string) => stack(page).getByRole('button', { name, exact: true });

async function openTable(page: Page): Promise<void> {
  await button(page, 'Show combinations').click();
  await button(page, 'View as table').click();
  await expect(stack(page).locator('table.tbl')).toBeVisible();
}

test('leads with the three-plus headline and the largest overlap, linked into the Lead Queue intersection', async ({ app, mockApi, page }) => {
  await app.gotoRoute(ROUTE);
  await expect(stack(page).getByRole('heading', { level: 2, name: 'Signal stack' })).toBeVisible();
  await expect(stack(page)).toContainText('795 borrowers fire three or more signals at once. 92 of them are contactable.');
  await expect(stack(page)).toContainText(
    'Largest overlap: Prime Refi Candidates, HELOC Intent and Home Equity Candidate: 520 borrowers carry all three (61 contactable).',
  );
  await expect(stack(page)).toContainText(
    'Whole book, six core segments; not narrowed by the filters below. The Lead Queue lists only contact-eligible borrowers scoring 50 or more.',
  );
  const link = stack(page).getByRole('link', { name: 'Open in Lead Queue', exact: true });
  await expect(link).toHaveAttribute('href', '/lead-queue?segment_codes=itm%2Cpermit%2Cequity&segment_mode=all');

  // Nothing the stack does on Segments reads an audited endpoint.
  const naturalLoad = markNaturalLoad(mockApi);
  await button(page, 'Show combinations').click();
  const columns = stack(page).locator('.signal-stack__column-link');
  await expect(columns).toHaveCount(SEGMENT_COMBINATIONS.combinations.filter((row) => row.signal_count >= 2).length);
  await expect(columns.first()).toHaveAttribute('href', '/lead-queue?segment_codes=itm%2Cequity&segment_mode=all');
  await expect(columns.first()).toHaveAccessibleName(
    'Prime Refi Candidates and Home Equity Candidate: 2,100 borrowers carry exactly these signals, 2,715 carry at least these, 313 contactable. Open in Lead Queue',
  );
  await columns.first().hover();
  await button(page, 'View as table').click();
  expectNoAuditedReadSince(mockApi, naturalLoad, 'signal stack');

  // Following the overlap link: one VIEW_LEADS, on the Lead Queue's own mount.
  const beforeClick = markNaturalLoad(mockApi);
  await link.click();
  await expect(page).toHaveURL(/\/lead-queue\?segment_codes=itm%2Cpermit%2Cequity&segment_mode=all$/);
  await app.settle();
  const audited = auditedReadsAfter(mockApi.calls, beforeClick);
  expect(audited).toHaveLength(1);
  expect(audited[0]).toMatch(/^GET \/api\/leads\?.*segment_codes=itm%2Cpermit%2Cequity.*\(VIEW_LEADS\)$/);
  expect(audited[0]).toContain('segment_mode=all');
});

test('the table counts every borrower once and each single signal equals its card', async ({ app, page }) => {
  await app.gotoRoute(ROUTE);
  await openTable(page);
  const table = stack(page).locator('table.tbl');
  await expect(table.locator('tbody tr')).toHaveCount(SEGMENT_COMBINATIONS.combinations.length);
  const exactTotal = SEGMENT_COMBINATIONS.combinations.reduce((sum, row) => sum + row.addressable, 0);
  await expect(table.getByTestId('signal-stack-total')).toHaveText(COUNT.format(exactTotal));
  await expect(table.locator('tfoot th')).toHaveText(`All combinations (${SEGMENT_COMBINATIONS.combinations.length})`);
  for (const segment of SEGMENTS) {
    const row = table.locator('tbody tr').filter({ has: page.getByRole('rowheader', { name: segment.name, exact: true }) });
    await expect(row.locator('td').nth(1), segment.code).toHaveText(COUNT.format(segment.count));
    await expect(row.locator('td').nth(2), segment.code).toHaveText(COUNT.format(segment.contactable ?? 0));
  }
});

test('the evidence chip opens the drawer on the gold table', async ({ app, page }) => {
  await app.gotoRoute(ROUTE);
  await stack(page).locator('.surface__hdr').getByRole('button', { name: /Signal combinations/ }).click();
  const drawer = page.getByRole('dialog').filter({ hasText: 'Signal stack: borrowers per exact set of core signals' });
  await expect(drawer).toBeVisible();
  await expect(drawer).toContainText('mip.gold.segment_combination_rollup');
});

test('says the stack is not built yet before the refresh builds it', async ({ app, mockApi, page }) => {
  mockApi.register('GET', PATH, () => json(SEGMENT_COMBINATIONS_NOT_BUILT));
  await app.gotoRoute(ROUTE);
  await expect(stack(page)).toContainText(
    'The signal stack is not built yet: the gold refresh job builds it (deploy or Admin > Data operations).',
  );
  await expect(stack(page)).not.toContainText('borrowers fire');
});

test('a warming warehouse shows the warming block, never a number', async ({ app, page }) => {
  app.degrade(PATH, WAREHOUSE_WARMING_UP);
  await page.goto(ROUTE, { waitUntil: 'domcontentloaded' });
  await expect(stack(page).getByTestId('warming-up-block')).toBeVisible({ timeout: 20_000 });
  await expect(stack(page)).not.toContainText('borrowers fire');
});

test('a held response keeps its place: the filter row below never moves', async ({ mockApi, page }) => {
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  mockApi.register('GET', PATH, async () => {
    await held;
    return json(SEGMENT_COMBINATIONS);
  });
  await page.goto(ROUTE, { waitUntil: 'domcontentloaded' });
  const filterRow = page.locator('.filter-row').first();
  await expect(stack(page)).toContainText('Loading the signal stack');
  await expect(page.locator('.seg-grid')).toHaveAttribute('aria-busy', 'false');
  const before = await filterRow.boundingBox();
  release();
  await expect(stack(page)).toContainText('borrowers fire three or more signals at once');
  const after = await filterRow.boundingBox();
  expect(after?.y).toBe(before?.y);
});

for (const theme of THEMES) {
  test(`a settled not-built body keeps no loading reservation (${theme})`, async ({ app, mockApi, page }) => {
    await app.setTheme(theme);
    mockApi.register('GET', PATH, () => json(SEGMENT_COMBINATIONS_NOT_BUILT));
    await app.gotoRoute(ROUTE);
    await expect(stack(page)).toContainText('The signal stack is not built yet');
    await expect(stack(page)).toHaveAttribute('aria-busy', 'false');
    const body = stack(page).locator('.signal-stack__body');
    // The loading reservation in px, resolved from the same tokens the rule uses.
    const reserved = await body.evaluate((element) => {
      const probe = element.ownerDocument.createElement('div');
      probe.style.blockSize = 'calc(var(--sp-16) * 2 + var(--sp-5))';
      element.appendChild(probe);
      const px = probe.getBoundingClientRect().height;
      probe.remove();
      return px;
    });
    expect(reserved).toBeGreaterThan(100);
    const box = await body.boundingBox();
    expect(box?.height ?? Infinity).toBeLessThan(reserved);
  });

  test(`fits without sideways scroll and is axe clean (${theme})`, async ({ app, page }) => {
    await app.setTheme(theme);
    await app.gotoRoute(ROUTE);
    await button(page, 'Show combinations').click();
    await expect(stack(page).locator('.signal-stack__columns')).toBeVisible();
    await expectNoSurfaceOverflow(page, { route: 'segment-intelligence', state: 'signal-stack', theme });
    await expectAxeClean(page, { key: { route: 'segment-intelligence', state: 'signal-stack-upset' }, theme, known: {}, include: '.signal-stack' });
    await button(page, 'View as table').click();
    await expectNoSurfaceOverflow(page, { route: 'segment-intelligence', state: 'signal-stack-table', theme });
    await expectAxeClean(page, { key: { route: 'segment-intelligence', state: 'signal-stack-table' }, theme, known: {}, include: '.signal-stack' });
  });
}
