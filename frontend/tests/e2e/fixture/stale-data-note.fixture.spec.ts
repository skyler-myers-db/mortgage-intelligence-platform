/**
 * W5b w5-home-geo-lever (audit delivery-06 client half, D-platform-process-e1
 * items 7-9): the server marks a value it retained after a failed refresh
 * with `X-Data-Last-Good-At`, and the page says so once, inline.
 *
 *  - Segment Intelligence shows ONE note, the oldest last good read of the
 *    catalog (GET /segments) and the map read (GET /geo/state-rollups), in
 *    the ranked table's header beside FetchedAt, as the one-line compact
 *    form; axe-clean in both themes.
 *  - The note moves nothing: the segment cards and the map keep their boxes
 *    with and without the header (the 2-line full sentence grew the header
 *    by 12px and moved the map; the compact form keeps the row's height).
 *  - Home (no host-owned note) shows the map's note in the legend when only
 *    the map read is stale.
 */
import type { Page } from '@playwright/test';
import { expectAxeClean } from './axe';
import { STALE_NOTE_LAST_GOOD_AT } from './fixtureStates';
import type { MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const CATALOG_LAST_GOOD = '2026-07-14T10:00:00Z';
const MAP_LAST_GOOD = STALE_NOTE_LAST_GOOD_AT;

function serveStale(mockApi: MockApi, which: { catalog?: string; map?: string }): void {
  if (which.catalog) mockApi.withHeaders('GET', '/api/segments', { 'X-Data-Last-Good-At': which.catalog });
  if (which.map) mockApi.withHeaders('GET', '/api/geo/state-rollups', { 'X-Data-Last-Good-At': which.map });
}

/** Every document starts without a restored snapshot (lib/queryPersist), so each state is read fresh. */
async function noRestoredSnapshot(page: Page): Promise<void> {
  await page.addInitScript(() => window.sessionStorage.removeItem('mip.queryCache.v1'));
}

const notes = (page: Page) => page.locator('[data-testid="stale-data-note"]');

test.describe('the retained-value note (delivery-06 client half)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`Segment Intelligence shows one note, the oldest read, beside FetchedAt (${theme})`, async ({ app, page, mockApi }) => {
      await noRestoredSnapshot(page);
      serveStale(mockApi, { catalog: CATALOG_LAST_GOOD, map: MAP_LAST_GOOD });
      await app.setTheme(theme);
      await app.gotoRoute('/segment-intelligence');
      await expect(notes(page)).toHaveCount(1);
      const note = notes(page).first();
      await expect(note).toHaveAttribute('role', 'status');
      await expect(note.locator('time')).toHaveAttribute('datetime', '2026-07-14T08:00:00.000Z');
      // The header's one-line form: the failure and the age visible, the next refresh read to assistive technology.
      await expect(note.locator('.chip__label')).toHaveText(/^Refresh failed; counts from /);
      await expect(note).toContainText('The counts update on a later refresh once the warehouse responds.');
      await expect(note).not.toContainText(/refresh to try again/i);
      const box = await note.boundingBox();
      expect(box?.height ?? 0, 'one chip-high line').toBeLessThanOrEqual(32);
      // In the ranked table's header row, before FetchedAt; the legend shows none.
      await expect(page.locator('.surface__hdr [data-testid="stale-data-note"] + [data-testid="fetched-at"]')).toHaveCount(1);
      await expect(page.locator('.map-legend [data-testid="stale-data-note"]')).toHaveCount(0);
      await expectAxeClean(page, { key: { route: 'segment-intelligence', state: 'stale-data-note' }, theme, known: {} });
    });
  }

  test('the note moves nothing: segment cards and the map keep their boxes', async ({ app, page, mockApi }) => {
    await noRestoredSnapshot(page);
    await app.gotoRoute('/segment-intelligence');
    await expect(page.locator('.seg-card').first()).toBeVisible();
    await expect(page.locator('#main-content .map-wrap path.map-region.has-data').first()).toBeVisible();
    const boxes = async () => ({
      cards: await page.locator('.seg-grid').boundingBox(),
      map: await page.locator('#main-content .map-wrap').boundingBox(),
    });
    const plain = await boxes();
    await expect(notes(page)).toHaveCount(0);

    serveStale(mockApi, { catalog: CATALOG_LAST_GOOD, map: MAP_LAST_GOOD });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await app.settle();
    await expect(notes(page)).toHaveCount(1);
    const stale = await boxes();
    expect(stale.cards).toEqual(plain.cards);
    expect(stale.map?.x).toBeCloseTo(plain.map?.x ?? -1, 0);
    expect(stale.map?.y).toBeCloseTo(plain.map?.y ?? -1, 0);
    expect(stale.map?.width).toBeCloseTo(plain.map?.width ?? -1, 0);
  });

  test('Home shows the map read\'s note in the legend when only the map is stale', async ({ app, page, mockApi }) => {
    await noRestoredSnapshot(page);
    serveStale(mockApi, { map: MAP_LAST_GOOD });
    await app.gotoRoute('/');
    const legendNote = page.locator('#main-content .map-legend [data-testid="stale-data-note"]');
    await expect(legendNote).toBeVisible();
    await expect(legendNote.locator('time')).toHaveAttribute('datetime', '2026-07-14T08:00:00.000Z');
    // One note on the page: Home's hero reads are current.
    await expect(notes(page)).toHaveCount(1);
    await expectAxeClean(page, { key: { route: 'home', state: 'stale-data-note' }, theme: 'dark', known: {}, include: '.map-wrap' });
  });
});
