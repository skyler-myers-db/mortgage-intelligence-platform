import { test, expect, type APIRequestContext, type Locator, type Page } from '@playwright/test';

const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 to run demo visual regression checks.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const API_URL = process.env.MIP_API_URL || APP_URL.replace(':5173', ':8000');
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER
  ? { Authorization: `Bearer ${BEARER}` }
  : {};

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

type MapDrillTarget = {
  stateName: string;
  countyName: string;
};

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

async function discoverMapDrillTarget(
  request: APIRequestContext,
  segmentCodes: string[] = [],
): Promise<MapDrillTarget> {
  const params = new URLSearchParams();
  if (segmentCodes.length > 0) {
    params.set('segment_codes', segmentCodes.join(','));
    params.set('segment_mode', 'all');
  }
  params.set('marketing_eligibility', 'Eligible only');
  const suffix = params.toString() ? `?${params.toString()}` : '';
  const statesResp = await request.get(`${API_URL}/api/geo/state-rollups${suffix}`, {
    headers: AUTH_HEADERS,
  });
  expect(statesResp.status(), 'state rollups target discovery').toBe(200);
  const statesPayload = await statesResp.json();
  const stateRollup = statesPayload.rollups.find((row: { state?: string; addressable?: number }) =>
    row.state && Number(row.addressable ?? 0) > 0,
  );
  expect(stateRollup, 'state rollups should expose a populated state').toBeTruthy();

  const footprintResp = await request.get(`${API_URL}/api/config/footprint`, {
    headers: AUTH_HEADERS,
  });
  expect(footprintResp.status(), 'footprint target discovery').toBe(200);
  const footprint = await footprintResp.json();
  const stateName =
    footprint.states?.find((row: { state_code?: string }) => row.state_code === stateRollup.state)
      ?.state_name ?? stateRollup.state;

  const countyResp = await request.get(
    `${API_URL}/api/geo/county-rollups?state=${stateRollup.state}${params.toString() ? `&${params.toString()}` : ''}`,
    { headers: AUTH_HEADERS },
  );
  expect(countyResp.status(), 'county rollups target discovery').toBe(200);
  const countyPayload = await countyResp.json();
  let county: { fips_5?: string; county_name?: string; addressable_borrowers?: number } | undefined;
  for (const row of countyPayload.rollups as Array<{ fips_5?: string; county_name?: string; addressable_borrowers?: number }>) {
    if (!row.fips_5 || Number(row.addressable_borrowers ?? 0) <= 0) continue;
    const zipResp = await request.get(
      `${API_URL}/api/geo/zip-rollups?county_fips=${row.fips_5}${params.toString() ? `&${params.toString()}` : ''}`,
      { headers: AUTH_HEADERS },
    );
    if (zipResp.status() !== 200) continue;
    const zipPayload = await zipResp.json();
    if (Array.isArray(zipPayload.rollups) && zipPayload.rollups.length > 0) {
      county = row;
      break;
    }
  }
  expect(county, 'county rollups should expose a populated county').toBeTruthy();
  const rawCountyName = String(county.county_name || county.fips_5);
  return {
    stateName,
    countyName: rawCountyName.toLowerCase().endsWith('county')
      ? rawCountyName
      : `${rawCountyName} County`,
  };
}

async function clickSegmentCard(page: Page, label: string) {
  await page.locator('.seg-card', { hasText: label }).click();
}

async function clickSvgRegion(page: Page, target: Locator, label: string) {
  await expect(target, `${label} SVG region should be visible`).toBeVisible({ timeout: 10_000 });
  await target.scrollIntoViewIfNeeded();
  const point = await target.evaluate((node) => {
    const rect = node.getBoundingClientRect();
    if (!(node instanceof SVGGeometryElement) || !node.ownerSVGElement) {
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    }

    const bbox = node.getBBox();
    const ctm = node.getScreenCTM();
    const svgPoint = node.ownerSVGElement.createSVGPoint();
    if (!ctm || bbox.width === 0 || bbox.height === 0) {
      return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
    }

    for (const xStep of [0.5, 0.35, 0.65, 0.2, 0.8]) {
      for (const yStep of [0.5, 0.35, 0.65, 0.2, 0.8]) {
        svgPoint.x = bbox.x + bbox.width * xStep;
        svgPoint.y = bbox.y + bbox.height * yStep;
        if (node.isPointInFill(svgPoint) || node.isPointInStroke(svgPoint)) {
          const clientPoint = svgPoint.matrixTransform(ctm);
          return { x: clientPoint.x, y: clientPoint.y };
        }
      }
    }

    const pathPoint = node.getPointAtLength(node.getTotalLength() / 2).matrixTransform(ctm);
    return { x: pathPoint.x, y: pathPoint.y };
  });
  await page.mouse.click(point.x, point.y);
}

async function drillToZipLayer(page: Page, target: MapDrillTarget) {
  const map = page.locator('.map-wrap').first();
  await bringMapIntoViewport(page);
  const state = map.locator(
    `svg.map-svg-stage [role="button"][aria-label="${target.stateName}"]`,
  ).first();
  await clickSvgRegion(page, state, target.stateName);

  await bringMapIntoViewport(page);
  const county = map.locator(
    `svg.map-svg-stage [role="button"][aria-label="${target.countyName}"]`,
  ).first();
  await expect(county).toBeVisible({ timeout: 10_000 });
  await county.click({ force: true });
}

async function bringMapIntoViewport(page: Page) {
  await page.evaluate(() => {
    const scroller = document.querySelector('.main') as HTMLElement | null;
    const map = document.querySelector('.map-wrap') as HTMLElement | null;
    if (scroller && map) scroller.scrollTop = Math.max(0, map.offsetTop - 120);
  });
}

async function expectMapCornerIconsCompact(page: Page, label: string) {
  const boxes = await page.locator('.map-corner-chips .chip svg').evaluateAll((nodes) =>
    nodes.map((node) => {
      const rect = node.getBoundingClientRect();
      return { width: rect.width, height: rect.height };
    }),
  );
  expect(boxes.length, `${label}: map header chips should include icons`).toBeGreaterThan(0);
  for (const box of boxes) {
    expect(box.width, `${label}: map chip icon width should stay compact`).toBeLessThanOrEqual(16);
    expect(box.height, `${label}: map chip icon height should stay compact`).toBeLessThanOrEqual(16);
  }
}

test.describe('Module 0 demo visual baselines', () => {
  test('Ask Genie empty state is polished at desktop and mobile widths', async ({ page }) => {
    await page.goto('/ask-genie');
    const main = page.locator('#main-content');
    await expect(main.getByText('Ready for governed analysis')).toBeVisible();
    const desktopGrid = page.locator('.layoutA-grid');
    await expect(desktopGrid).toBeVisible();
    const desktopChildren = await desktopGrid.locator(':scope > *').evaluateAll((nodes) =>
      nodes.map((node) => {
        const rect = node.getBoundingClientRect();
        return { x: rect.x, y: rect.y, right: rect.right, bottom: rect.bottom };
      }),
    );
    expect(desktopChildren.length, 'Ask Genie desktop should render both grid columns').toBeGreaterThanOrEqual(2);
    for (let i = 0; i < desktopChildren.length; i += 1) {
      for (let j = i + 1; j < desktopChildren.length; j += 1) {
        const a = desktopChildren[i];
        const b = desktopChildren[j];
        const separated = a.right <= b.x || b.right <= a.x || a.bottom <= b.y || b.bottom <= a.y;
        expect(separated, 'Ask Genie desktop grid children should not overlap').toBe(true);
      }
    }

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(main.getByText('Ready for governed analysis')).toBeVisible();
    const mobileGrid = page.locator('.layoutA-grid');
    const gridBox = await mobileGrid.boundingBox();
    const directChildren = mobileGrid.locator(':scope > *');
    const firstSurface = await directChildren.first().boundingBox();
    const secondSurface = await directChildren.nth(1).boundingBox();
    expect(gridBox, 'Ask Genie mobile grid should have a layout box').toBeTruthy();
    expect(firstSurface, 'Ask Genie mobile question surface should have a layout box').toBeTruthy();
    expect(secondSurface, 'Ask Genie mobile asset surface should have a layout box').toBeTruthy();
    if (firstSurface && secondSurface) {
      expect(firstSurface.y + firstSurface.height, 'Ask Genie mobile surfaces should stack without overlap').toBeLessThanOrEqual(
        secondSurface.y + 1,
      );
    }
  });

  test('Segment filter row keeps aligned controls and honest pending-source copy', async ({ page }) => {
    await page.goto('/segment-intelligence');
    const filterRow = page.locator('.filter-row[aria-label="Secondary borrower filters"]');
    await expect(filterRow).toBeVisible({ timeout: 20_000 });
    const hint = filterRow.getByText(/Delta shares pending/);
    await expect(hint).toBeVisible();

    const controlBoxes = await filterRow.locator('.filter').evaluateAll((nodes) =>
      nodes.map((node) => {
        const rect = node.getBoundingClientRect();
        return { x: rect.x, y: rect.y, right: rect.right, bottom: rect.bottom };
      }),
    );
    expect(controlBoxes.length).toBeGreaterThanOrEqual(6);
    for (let i = 0; i < controlBoxes.length; i += 1) {
      for (let j = i + 1; j < controlBoxes.length; j += 1) {
        const a = controlBoxes[i];
        const b = controlBoxes[j];
        const separated =
          a.right + 1 <= b.x ||
          b.right + 1 <= a.x ||
          a.bottom + 1 <= b.y ||
          b.bottom + 1 <= a.y;
        expect(separated, 'filter controls should not overlap').toBe(true);
      }
    }

    const hintBox = await hint.boundingBox();
    expect(hintBox, 'pending-source hint should have a layout box').toBeTruthy();
    expect(hintBox!.y, 'pending-source hint should sit below the controls').toBeGreaterThan(
      Math.max(...controlBoxes.map((box) => box.bottom)),
    );
  });

  test('Segment card grid preserves the prototype contract without dynamic-count drift', async ({ page }) => {
    await page.goto('/segment-intelligence');
    const grid = page.locator('.seg-grid');
    await expect(grid).toBeVisible({ timeout: 20_000 });
    await expect(grid.locator('.seg-card')).toHaveCount(6);
    await page.evaluate(() => document.fonts.ready);
    const cardBoxes = await grid.locator('.seg-card').evaluateAll((nodes) =>
      nodes.map((node) => {
        const rect = node.getBoundingClientRect();
        const scrollOverflow = node.scrollHeight > node.clientHeight + 1 || node.scrollWidth > node.clientWidth + 1;
        return {
          x: rect.x,
          y: rect.y,
          right: rect.right,
          bottom: rect.bottom,
          scrollOverflow,
        };
      }),
    );
    for (const box of cardBoxes) {
      expect(box.scrollOverflow, 'segment cards should not clip dynamic copy').toBe(false);
    }
    for (let i = 0; i < cardBoxes.length; i += 1) {
      for (let j = i + 1; j < cardBoxes.length; j += 1) {
        const a = cardBoxes[i];
        const b = cardBoxes[j];
        const separated =
          a.right <= b.x ||
          b.right <= a.x ||
          a.bottom <= b.y ||
          b.bottom <= a.y;
        expect(separated, 'segment cards should not overlap').toBe(true);
      }
    }
  });

	  test('Segment geography drill header keeps breadcrumbs clickable at ZIP layer', async ({ page, request }) => {
	    await page.goto('/segment-intelligence');
	    for (const label of ['Investor / Multi-Property', 'Home Equity Candidate']) {
	      await clickSegmentCard(page, label);
	    }
      const target = await discoverMapDrillTarget(request, ['itm', 'investor', 'equity']);
	    await drillToZipLayer(page, target);
	    await expect(page.locator('.zip-tiles')).toBeVisible({ timeout: 10_000 });

	    for (const width of [1440, 1280, 1150, 1024]) {
	      await page.setViewportSize({ width, height: 900 });
	      const header = await page.locator('.map-hdr').boundingBox();
	      const zipGrid = await page.locator('.zip-tiles').boundingBox();
	      const crumbs = await page.locator('.map-crumbs').boundingBox();
	      const chips = await page.locator('.map-corner-chips').boundingBox();
	      expect(header, `map header should render at ${width}px`).toBeTruthy();
	      expect(zipGrid, `ZIP grid should render at ${width}px`).toBeTruthy();
	      if (header && zipGrid) {
	        expect(header.y + header.height, `map header should not overlap ZIP grid at ${width}px`).toBeLessThanOrEqual(
	          zipGrid.y + 1,
	        );
	      }
	      expect(crumbs, `map crumbs should render at ${width}px`).toBeTruthy();
      expect(chips, `map chips should render at ${width}px`).toBeTruthy();
      await expectMapCornerIconsCompact(page, `segment ZIP map ${width}px`);
      if (crumbs && chips) {
        const separated =
          crumbs.x + crumbs.width <= chips.x ||
          chips.x + chips.width <= crumbs.x ||
          crumbs.y + crumbs.height <= chips.y ||
          chips.y + chips.height <= crumbs.y;
        expect(separated, `map header overlays should not collide at ${width}px`).toBe(true);
	      }
	      await page.getByRole('button', { name: /^US$/ }).click();
	      await expect(page.locator(`[aria-label="${target.stateName}"]`).first()).toBeVisible();
	      await drillToZipLayer(page, target);
	      await expect(page.locator('.zip-tiles')).toBeVisible({ timeout: 10_000 });
	    }
	  });
});
