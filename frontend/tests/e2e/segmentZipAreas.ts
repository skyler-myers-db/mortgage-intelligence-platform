/**
 * Segment Intelligence's ZIP stage since the approved ZCTA geometry (W5c
 * dataviz-01, deviation:zcta-level; committed 2026-10-26): a state drill
 * draws the drilled state's Census 2020 ZCTAs as `svg.map-zcta` polygons, not
 * the densest-ZIP tiles. Home keeps the tiles. On Segments the tiles are only
 * the degraded fallback (a geometry read that failed, under a status line),
 * so a Segments drill that lands on tiles is a failure, never a pass.
 *
 * Shared by the live real_data, live_hardening_regressions and demo_visual
 * specs (credential-gated, nightly.yml) and the credential-free
 * zcta-drill.fixture.spec.ts, so the assertions the live suite makes on the
 * polygon stage also run in the fixture job on every change.
 */
import { expect, type Locator, type Page } from '@playwright/test';

/** Either ZIP stage: Home's tiles, or Segments' ZCTA polygons (or their tile fallback). */
export const ZIP_STAGE = '.zip-tiles, svg.map-zcta';

/** The drilled state's ZIP areas on Segment Intelligence (the polygon stage). */
export function segmentZipAreas(page: Page, stateName: string): Locator {
  return page.locator(`.map-wrap svg.map-zcta[aria-label="ZIP areas in ${stateName}"]`);
}

/** Waits out the drill tween: two equal viewBox reads a poll apart. */
export async function settleZipAreas(zipAreas: Locator): Promise<void> {
  let previous = '';
  await expect
    .poll(
      async () => {
        const current = (await zipAreas.getAttribute('viewBox')) ?? '';
        const same = current !== '' && current === previous;
        previous = current;
        return same;
      },
      { message: 'the ZIP areas viewBox should settle after the drill tween', intervals: [120], timeout: 10_000 },
    )
    .toBe(true);
}

/**
 * The stage's live, segment-filtered fills (the tiles' "real ZIP with a real
 * count" pin, for every ZIP area with borrowers in the selection): at least
 * one such area, and each is a 5-digit ZIP painted on the ramp
 * (`has-data lvl-1..4`) and named with a count, never "borrowers: unknown".
 * The map's unattended overlay must be off (its fills follow another count).
 */
export async function expectZipAreasPainted(zipAreas: Locator, label: string): Promise<void> {
  const populated = zipAreas.locator('path[data-populated]');
  await expect
    .poll(() => populated.count(), { message: `${label}: ZIP areas with borrowers in the selection` })
    .toBeGreaterThan(0);
  const areas = await populated.evaluateAll((paths) =>
    paths.map((path) => ({
      zip: path.getAttribute('data-map-unit') ?? '',
      fill: path.getAttribute('class') ?? '',
      name: path.getAttribute('aria-label') ?? '',
    })),
  );
  const onRamp = /(^|\s)has-data lvl-[1-4](\s|$)/;
  const counted = /^ZIP (\d{5}): \d[\d,]* borrowers(,|$)/;
  const unpainted = areas.filter(
    (area) => !/^\d{5}$/.test(area.zip) || !onRamp.test(area.fill) || counted.exec(area.name)?.[1] !== area.zip,
  );
  expect(unpainted, `${label}: every ZIP area with borrowers is a 5-digit ZIP on the ramp with a real count`).toEqual([]);
}

/**
 * Clicks a painted point of a ZIP area with borrowers once the drill tween
 * has settled, and returns its ZIP. The area's box centre can be a
 * neighbour's, and muted context areas take no pointer, so the point is the
 * first one `elementFromPoint` resolves to the area itself. The click is the
 * area's single-ZIP Lead Queue action, as a tile click was.
 */
export async function clickPopulatedZipArea(page: Page, zipAreas: Locator): Promise<string> {
  await zipAreas.scrollIntoViewIfNeeded();
  await settleZipAreas(zipAreas);
  const point = await zipAreas.evaluate((svg) => {
    for (const path of svg.querySelectorAll<SVGPathElement>('path[data-populated]')) {
      const box = path.getBoundingClientRect();
      for (let i = 1; i < 8; i += 1) {
        for (let j = 1; j < 8; j += 1) {
          const x = box.left + (box.width * i) / 8;
          const y = box.top + (box.height * j) / 8;
          if (document.elementFromPoint(x, y) === path) return { x, y, zip: path.getAttribute('data-map-unit') ?? '' };
        }
      }
    }
    return null;
  });
  if (!point) throw new Error('no ZIP area with borrowers is hittable on screen');
  await page.mouse.click(point.x, point.y);
  return point.zip;
}

/**
 * The map header clears the drilled ZIP stage: the header ends above the
 * stage, and the breadcrumbs and the corner tray (the chips, plus the zoom
 * row while ZIP areas show) do not overlap.
 */
export async function expectMapHeaderClear(page: Page, stage: Locator, label: string): Promise<void> {
  const header = await page.locator('.map-hdr').boundingBox();
  const stageBox = await stage.boundingBox();
  const crumbs = await page.locator('.map-crumbs').boundingBox();
  const chips = await page.locator('.map-corner-chips').boundingBox();
  expect(header, `${label}: map header should render`).toBeTruthy();
  expect(stageBox, `${label}: ZIP stage should render`).toBeTruthy();
  expect(crumbs, `${label}: map crumbs should render`).toBeTruthy();
  expect(chips, `${label}: map corner chips should render`).toBeTruthy();
  if (header && stageBox) {
    expect(header.y + header.height, `${label}: map header must not overlap the ZIP stage`).toBeLessThanOrEqual(
      stageBox.y + 1,
    );
  }
  if (crumbs && chips) {
    const separated =
      crumbs.x + crumbs.width <= chips.x ||
      chips.x + chips.width <= crumbs.x ||
      crumbs.y + crumbs.height <= chips.y ||
      chips.y + chips.height <= crumbs.y;
    expect(separated, `${label}: map breadcrumbs and corner chips must not overlap`).toBe(true);
  }
}
