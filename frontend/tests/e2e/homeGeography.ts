/**
 * Home's geography contract since the 2026-09-21 audit (visual-06): the map is
 * no longer a full-width row. It pairs with a side panel (the approval queue,
 * pinned insights, the secondary actions) in the prototype's `.layoutA-grid`,
 * as Module 0 Prototype.html pairs MapPanel with RightRail, and it is the
 * PAIR that spans the content width.
 *
 * Shared by the live responsive.spec.ts (credential-gated) and the
 * credential-free home-answer.fixture.spec.ts, so the assertions the live
 * matrix makes also run in the fixture job on every change.
 */
import { expect, type Page } from '@playwright/test';

/** Share of `.main__inner`'s width the geography row must span. */
export const HOME_GEO_MIN_SHARE = 0.95;

function trackCount(columns: string): number {
  return columns.split(' ').filter((track) => track.trim().length > 0).length;
}

/**
 * Asserts Home's geography row at the current viewport: `.layoutA-grid.home-geo`
 * lays out `expectedCols` tracks, spans at least 95% of `.main__inner`, holds
 * the map, and, when paired (2 tracks), sets the side panel at or right of the
 * map's right edge.
 */
export async function expectHomeGeographyPaired(
  page: Page,
  expectedCols: number,
  label: string,
): Promise<void> {
  const geo = page.locator('.main__inner .layoutA-grid.home-geo');
  await expect(geo, `${label}: Home renders one geography row`).toHaveCount(1);
  await expect
    .poll(
      async () => trackCount(await geo.evaluate((el) => getComputedStyle(el).gridTemplateColumns)),
      { message: `${label}: .layoutA-grid.home-geo track count`, timeout: 5_000 },
    )
    .toBe(expectedCols);

  // The map lives inside the geography row: Home has no other map.
  const map = geo.locator('.map-wrap');
  await expect(map, `${label}: the map sits inside .home-geo`).toHaveCount(1);
  await expect(page.locator('.main__inner .map-wrap'), `${label}: Home renders one map`).toHaveCount(1);
  await expect(map).toBeVisible({ timeout: 30_000 });

  const innerWidth = await page
    .locator('.main__inner')
    .first()
    .evaluate((el) => el.getBoundingClientRect().width);
  const geoWidth = await geo.evaluate((el) => el.getBoundingClientRect().width);
  expect(geoWidth, `${label}: .home-geo width vs .main__inner ${innerWidth.toFixed(1)}px`).toBeGreaterThanOrEqual(
    innerWidth * HOME_GEO_MIN_SHARE,
  );

  if (expectedCols === 2) {
    const side = geo.locator(':scope > .home-side');
    await expect(side, `${label}: the side panel sits in the geography row`).toHaveCount(1);
    const mapRight = await map.evaluate((el) => el.getBoundingClientRect().right);
    const sideLeft = await side.evaluate((el) => el.getBoundingClientRect().left);
    expect(sideLeft, `${label}: side panel left edge vs map right edge ${mapRight.toFixed(1)}px`).toBeGreaterThanOrEqual(
      mapRight,
    );
  }
}
