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
  const county = countyPayload.rollups.find((row: { fips_5?: string; addressable_borrowers?: number }) =>
    row.fips_5 && Number(row.addressable_borrowers ?? 0) > 0,
  );
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
  const state = map.getByRole('button', { name: new RegExp(`^${escapeRegExp(target.stateName)}$`) }).first();
  await clickSvgRegion(page, state, target.stateName);

  await bringMapIntoViewport(page);
  const county = map.getByRole('button', { name: new RegExp(escapeRegExp(target.countyName), 'i') }).first();
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
    await expect(page.getByText('Ready for governed analysis')).toBeVisible();
    await expect(page.locator('.layoutA-grid')).toHaveScreenshot('ask-genie-empty-desktop.png', {
      animations: 'disabled',
      maxDiffPixelRatio: 0.03,
    });

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator('.layoutA-grid')).toHaveScreenshot('ask-genie-empty-mobile.png', {
      animations: 'disabled',
      maxDiffPixelRatio: 0.04,
    });
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
        return { x: rect.x, y: rect.y, bottom: rect.bottom };
      }),
    );
    expect(controlBoxes.length).toBeGreaterThanOrEqual(6);
    const top = controlBoxes[0].y;
    for (const box of controlBoxes) {
      expect(Math.abs(box.y - top), 'filter controls should share a top edge').toBeLessThanOrEqual(1);
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
    await expect(grid).toHaveScreenshot('segment-card-grid.png', {
      animations: 'disabled',
      mask: [grid.locator('.seg-card__count'), grid.locator('.seg-card__meta')],
      maxDiffPixelRatio: 0.04,
    });
  });

	  test('Segment geography drill header keeps breadcrumbs clickable at ZIP layer', async ({ page, request }) => {
	    await page.goto('/segment-intelligence');
	    for (const label of ['Investor / Multi-Property', 'Home Equity Candidate', 'Retention Risk']) {
	      await clickSegmentCard(page, label);
	    }
      const target = await discoverMapDrillTarget(request, ['itm', 'investor', 'equity', 'retention']);
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
