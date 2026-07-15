import {
  expect,
  test,
  type Locator,
  type Page,
  type Request,
  type Route,
} from '@playwright/test';
import { mockBorrowers, mockPortfolio, mockSegments } from '../../src/mocks/fixtureData';

const LIVE = process.env.E2E_LIVE === '1';
const MOCK = process.env.E2E_LAYOUT_MOCK === '1';
const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER
  ? { Authorization: `Bearer ${BEARER}` }
  : {};

test.skip(
  !LIVE && !MOCK,
  'Set E2E_LIVE=1 for deployed real-data coverage or E2E_LAYOUT_MOCK=1 for deterministic local coverage.',
);
test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

type Box = {
  x: number;
  y: number;
  width: number;
  height: number;
};

type Anchor = {
  name: string;
  locator: Locator;
};

type BoxSnapshot = {
  viewport: { width: number; height: number };
  boxes: Record<string, Box>;
};

type LayoutShiftState = {
  supported: boolean;
  all: number;
  cls: number;
  entries: Array<{ value: number; hadRecentInput: boolean }>;
};

type MovementBudget = {
  positionPx: number;
  sizePx: number;
  shiftScore: number;
};

type RequestPredicate = (url: URL, request: Request) => boolean;

const EXECUTIVE_FIXTURE = {
  totals: {
    snapshot_date: '2026-07-14',
    addressable_borrowers: 1_000,
    in_the_money_borrowers: 420,
    high_opportunity_borrowers: 180,
    offer_recommended_borrowers: 610,
    approved_borrowers: 96,
    actioned_borrowers: 51,
  },
  stages: [
    { stage: 'addressable', stage_order: 1, borrower_count: 1_000 },
    { stage: 'in_the_money', stage_order: 2, borrower_count: 420 },
    { stage: 'high_opportunity', stage_order: 3, borrower_count: 180 },
    { stage: 'offer_recommended', stage_order: 4, borrower_count: 610 },
    { stage: 'approved', stage_order: 5, borrower_count: 96 },
    { stage: 'actioned', stage_order: 6, borrower_count: 51 },
  ],
  score_distribution: [
    { score_bucket: 50, borrower_count: 120 },
    { score_bucket: 70, borrower_count: 260 },
    { score_bucket: 90, borrower_count: 80 },
  ],
};

function normalizedApiPath(rawUrl: string): string {
  return new URL(rawUrl).pathname.replace(/^\/api\/v\d+(?=\/)/, '/api');
}

async function fulfillJson(
  route: Route,
  body: unknown,
  headers: Record<string, string> = {},
): Promise<void> {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    headers,
    body: JSON.stringify(body),
  });
}

function filteredLeadCount(url: URL): number {
  if (url.searchParams.has('state')) return 2;
  return 3;
}

async function installMockApi(page: Page): Promise<string[]> {
  const unhandled: string[] = [];

  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = normalizedApiPath(request.url());

    if (path === '/api/health') {
      await fulfillJson(route, {
        status: 'ok',
        mode: 'live',
        dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
        circuit_breakers: { warehouse: 'closed', lakebase: 'closed', genie: 'closed' },
      });
      return;
    }
    if (path === '/api/session') {
      await fulfillJson(route, { can_access_admin: false });
      return;
    }
    if (path === '/api/workspace') {
      await fulfillJson(route, { saved_leads: [], saved_drafts: [] });
      return;
    }
    if (path === '/api/config/options') {
      await fulfillJson(route, {
        lender_name: 'Summit Mortgage',
        rum_enabled: false,
        geographies: ['All states', 'Illinois', 'Texas', 'California'],
        geographies_status: 'live',
        occupancy: ['All', 'Owner-occupied', 'Non-owner-occupied'],
        lien_status: ['Any', 'Open 1st lien', 'Open HELOC', 'Free & clear'],
        lender_relationships: ['All', 'Current customer', 'Former customer', 'Competitor customer'],
        products: ['All products', 'Refi', 'HELOC', 'Cash-out', 'Purchase', 'Retention'],
        equity_thresholds: ['Any', '>= 15%', '>= 25%', '>= 40%'],
        target_lender_refs: ['All', 'Competitor A'],
        target_lender_refs_status: 'live',
      });
      return;
    }
    if (path === '/api/config/footprint') {
      await fulfillJson(route, {
        states: [
          { state_code: 'IL', state_name: 'Illinois', display_order: 1, is_default_state: true },
          { state_code: 'TX', state_name: 'Texas', display_order: 2, is_default_state: false },
          { state_code: 'CA', state_name: 'California', display_order: 3, is_default_state: false },
        ],
        using_fallback: false,
        geography_scope: {
          state_count: 3,
          county_count: 3,
          zip_count: 3,
          scope_label: 'Cotality data coverage: 3 counties across 3 states',
          counties: [
            { state: 'IL', fips_5: '17031', county_name: 'Cook County', addressable_borrowers: 20 },
            { state: 'TX', fips_5: '48201', county_name: 'Harris County', addressable_borrowers: 15 },
            { state: 'CA', fips_5: '06037', county_name: 'Los Angeles County', addressable_borrowers: 12 },
          ],
        },
      });
      return;
    }
    if (path === '/api/admin/rules') {
      await fulfillJson(route, {
        offer_rules_version: 'layout-fixture-v1',
        rules_edited_at: '2026-07-14T12:00:00Z',
        thresholds: [],
      });
      return;
    }
    if (path === '/api/segments') {
      const selected = (url.searchParams.get('segment_codes') ?? '').split(',').filter(Boolean);
      const allMode = url.searchParams.get('segment_mode') === 'all';
      await fulfillJson(
        route,
        mockSegments.map((segment) => ({
          ...segment,
          count: selected.length === 0
            ? segment.count
            : Math.max(1, Math.round(segment.count / (allMode ? 8 : 3))),
        })),
      );
      return;
    }
    if (path === '/api/leads') {
      const count = filteredLeadCount(url);
      await fulfillJson(route, mockBorrowers.slice(0, count), {
        'X-Total-Matching': String(count),
        'X-Returned-Rows': String(count),
      });
      return;
    }
    if (path === '/api/geo/state-rollups') {
      const selected = Boolean(url.searchParams.get('segment_codes'));
      const divisor = url.searchParams.get('segment_mode') === 'all' ? 5 : selected ? 2 : 1;
      await fulfillJson(route, {
        rollups: [
          { state: 'IL', addressable: Math.round(600 / divisor), in_the_money: 260, top_tier_opportunities: 120, avg_score: 81, top_segment_code: 'itm' },
          { state: 'TX', addressable: Math.round(400 / divisor), in_the_money: 160, top_tier_opportunities: 60, avg_score: 76, top_segment_code: 'equity' },
        ],
        snapshot_date: '2026-07-14',
      });
      return;
    }
    if (path === '/api/analytics/executive') {
      const filtered = url.searchParams.get('states') === 'IL';
      await fulfillJson(route, filtered
        ? {
            ...EXECUTIVE_FIXTURE,
            totals: {
              ...EXECUTIVE_FIXTURE.totals,
              addressable_borrowers: 600,
              in_the_money_borrowers: 260,
              high_opportunity_borrowers: 120,
              offer_recommended_borrowers: 370,
              approved_borrowers: 61,
              actioned_borrowers: 34,
            },
            stages: EXECUTIVE_FIXTURE.stages.map((stage) => ({
              ...stage,
              borrower_count: Math.max(1, Math.round(stage.borrower_count * 0.6)),
            })),
          }
        : EXECUTIVE_FIXTURE);
      return;
    }
    if (path === '/api/portfolio/preview') {
      const postData = request.postDataJSON() as { criteria?: Record<string, unknown> } | null;
      const filtered = postData?.criteria?.product === 'Refi';
      await fulfillJson(route, filtered
        ? {
            ...mockPortfolio,
            marketable_population: 71_553,
            high_intent_leads: 10_100,
            top_tier_opportunities: 3_600,
            offers_recommended: 5_200,
            avg_score: 79,
          }
        : mockPortfolio);
      return;
    }
    if (path === '/api/portfolio/campaign-recommendation') {
      await fulfillJson(route, {
        generation_mode: 'reviewed_fallback',
        generator_label: 'Reviewed campaign template',
        performance_status: 'insufficient_sample',
        audience_summary: 'Eligible borrower cohort',
        strategy: 'Review two governed variants before approval.',
        variants: [
          { variant_name: 'Benefit-led', subject: 'Review your mortgage options', body: 'A draft for human review.', hypothesis: 'Benefit framing', provenance_token: null },
          { variant_name: 'Guidance-led', subject: 'A mortgage review may help', body: 'A second draft for human review.', hypothesis: 'Guidance framing', provenance_token: null },
        ],
        holdout_pct: 10,
        evidence: [],
        warnings: [],
      });
      return;
    }
    if (path === '/api/sales/campaign-performance') {
      await fulfillJson(route, {
        from_date: '2026-04-16',
        to_date: '2026-07-14',
        unique_leads_attempted: 100,
        unique_contacts_reached: 40,
        unique_application_starts: 12,
        unique_applications_submitted: 8,
        unique_closed_funded: 3,
        methodology: 'same_borrower_nested_funnel',
      });
      return;
    }
    if (path === '/api/campaigns') {
      await fulfillJson(route, { campaigns: [] });
      return;
    }
    if (path === '/api/sales/team') {
      await fulfillJson(route, []);
      return;
    }

    unhandled.push(`${request.method()} ${path}`);
    await route.fulfill({
      status: 501,
      contentType: 'application/json',
      body: JSON.stringify({ detail: `Unhandled layout test API: ${path}` }),
    });
  });

  return unhandled;
}

async function createRequestGate(page: Page, predicate: RequestPredicate) {
  let armed = false;
  let count = 0;
  let seen: string[] = [];
  let release: (() => void) | null = null;
  let hold = Promise.resolve();

  await page.route('**/api/**', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (!armed || !predicate(url, request)) {
      await route.fallback();
      return;
    }
    count += 1;
    seen.push(`${request.method()} ${normalizedApiPath(request.url())}${url.search}`);
    await hold;
    await route.fallback();
  });

  return {
    arm() {
      armed = true;
      count = 0;
      seen = [];
      hold = new Promise<void>((resolve) => {
        release = resolve;
      });
    },
    async waitForCount(minimum = 1) {
      await expect.poll(() => count, {
        message: `expected at least ${minimum} gated API requests`,
        timeout: LIVE ? 30_000 : 10_000,
      }).toBeGreaterThanOrEqual(minimum);
    },
    release() {
      armed = false;
      release?.();
      release = null;
    },
    urls() {
      return [...seen];
    },
  };
}

async function installLayoutShiftObserver(page: Page): Promise<void> {
  await page.addInitScript(() => {
    type ShiftWindow = Window & { __mipLayoutShift?: LayoutShiftState };
    const state: LayoutShiftState = {
      supported: PerformanceObserver.supportedEntryTypes?.includes('layout-shift') ?? false,
      all: 0,
      cls: 0,
      entries: [],
    };
    (window as ShiftWindow).__mipLayoutShift = state;
    if (!state.supported) return;
    const observer = new PerformanceObserver((list) => {
      for (const rawEntry of list.getEntries()) {
        const entry = rawEntry as PerformanceEntry & { value: number; hadRecentInput: boolean };
        state.all += entry.value;
        if (!entry.hadRecentInput) state.cls += entry.value;
        state.entries.push({ value: entry.value, hadRecentInput: entry.hadRecentInput });
      }
    });
    observer.observe({ type: 'layout-shift', buffered: true });
  });
}

async function resetLayoutShift(page: Page): Promise<void> {
  await page.evaluate(() => {
    const state = (window as Window & { __mipLayoutShift?: LayoutShiftState }).__mipLayoutShift;
    if (!state) return;
    state.all = 0;
    state.cls = 0;
    state.entries = [];
  });
}

async function readLayoutShift(page: Page): Promise<LayoutShiftState> {
  return page.evaluate(() => {
    return (window as Window & { __mipLayoutShift?: LayoutShiftState }).__mipLayoutShift ?? {
      supported: false,
      all: 0,
      cls: 0,
      entries: [],
    };
  });
}

async function captureBoxes(page: Page, anchors: Anchor[]): Promise<BoxSnapshot> {
  const viewport = page.viewportSize();
  expect(viewport, 'layout stability tests require a fixed viewport').toBeTruthy();
  const boxes: Record<string, Box> = {};
  for (const anchor of anchors) {
    await expect(anchor.locator, `${anchor.name} should be visible before measuring`).toBeVisible();
    const box = await anchor.locator.boundingBox();
    expect(box, `${anchor.name} should have a browser bounding box`).toBeTruthy();
    boxes[anchor.name] = box!;
  }
  return { viewport: viewport!, boxes };
}

function assertMovement(
  before: BoxSnapshot,
  next: BoxSnapshot,
  label: string,
  budget: MovementBudget,
): void {
  for (const [name, start] of Object.entries(before.boxes)) {
    const end = next.boxes[name];
    expect(end, `${label}: missing ${name} in later snapshot`).toBeTruthy();
    const dx = Math.abs(end.x - start.x);
    const dy = Math.abs(end.y - start.y);
    const dw = Math.abs(end.width - start.width);
    const dh = Math.abs(end.height - start.height);
    const unionLeft = Math.min(start.x, end.x);
    const unionTop = Math.min(start.y, end.y);
    const unionRight = Math.max(start.x + start.width, end.x + end.width);
    const unionBottom = Math.max(start.y + start.height, end.y + end.height);
    const impactFraction = (
      (unionRight - unionLeft) * (unionBottom - unionTop)
    ) / (before.viewport.width * before.viewport.height);
    const distanceFraction = Math.max(
      dx / before.viewport.width,
      dy / before.viewport.height,
    );
    const shiftScore = impactFraction * distanceFraction;

    expect(Math.max(dx, dy), `${label}: ${name} moved ${dx.toFixed(1)}x${dy.toFixed(1)}px`).toBeLessThanOrEqual(budget.positionPx);
    expect(Math.max(dw, dh), `${label}: ${name} resized ${dw.toFixed(1)}x${dh.toFixed(1)}px`).toBeLessThanOrEqual(budget.sizePx);
    expect(shiftScore, `${label}: ${name} bounding-box shift score`).toBeLessThanOrEqual(budget.shiftScore);
  }
}

async function settleInitialLayout(page: Page): Promise<void> {
  await page.evaluate(async () => {
    await document.fonts.ready;
  });
  // KPI count-up and route entrance animations finish before the baseline.
  await page.waitForTimeout(1_400);
}

async function expectObservedClsBelow(page: Page, label: string, maxCls = 0.05): Promise<void> {
  const shift = await readLayoutShift(page);
  if (!shift.supported) return;
  expect(shift.cls, `${label}: unexpected browser CLS entries ${JSON.stringify(shift.entries)}`).toBeLessThanOrEqual(maxCls);
}

test.describe('async route layout stability', () => {
  test.describe.configure({ timeout: LIVE ? 120_000 : 60_000 });

  test.beforeEach(async ({ page }) => {
    await installLayoutShiftObserver(page);
  });

test('Segment Intelligence keeps cards, Any/All controls, ranked list, and map anchored across async refreshes @desktop', async ({ page }) => {
  const unhandled = MOCK ? await installMockApi(page) : [];
  const gate = await createRequestGate(page, (url) => [
    '/api/segments',
    '/api/leads',
    '/api/geo/state-rollups',
  ].includes(normalizedApiPath(url.toString())));

  await page.goto('/segment-intelligence', { waitUntil: 'domcontentloaded' });
  const cards = page.locator('.seg-grid');
  const workbench = page.locator('.layoutA-grid--segment-workbench');
  await expect(cards).toHaveAttribute('aria-busy', 'false', { timeout: 45_000 });
  await expect(workbench).toHaveAttribute('aria-busy', 'false', { timeout: 45_000 });
  await expect(page.locator('.lead-table__borrower-btn').first()).toBeVisible({ timeout: 45_000 });
  await settleInitialLayout(page);

  const anchors: Anchor[] = [
    { name: 'segment-grid', locator: cards },
    { name: 'first-segment-card', locator: page.locator('.seg-card').first() },
    { name: 'match-mode-control', locator: page.locator('.segment-mode-control') },
    { name: 'ranked-heading', locator: page.locator('.section-hdr', { hasText: 'Ranked borrowers' }).first() },
    { name: 'ranked-workbench', locator: workbench },
    { name: 'map', locator: page.locator('.map-wrap').first() },
  ];

  const exerciseTransition = async (
    label: string,
    action: () => Promise<void>,
  ) => {
    const before = await captureBoxes(page, anchors);
    await resetLayoutShift(page);
    gate.arm();
    await action();
    await gate.waitForCount(3);
    await expect(cards).toHaveAttribute('aria-busy', 'true');
    await expect(workbench).toHaveAttribute('aria-busy', 'true');
    await expect(page.locator('.lead-table__borrower-btn').first(), `${label}: retained rows stay visible`).toBeVisible();
    await page.waitForTimeout(650);
    const pending = await captureBoxes(page, anchors);
    assertMovement(before, pending, `${label} while requests are pending`, {
      positionPx: 28,
      sizePx: 28,
      shiftScore: 0.008,
    });

    gate.release();
    await expect(cards).toHaveAttribute('aria-busy', 'false', { timeout: 45_000 });
    await expect(workbench).toHaveAttribute('aria-busy', 'false', { timeout: 45_000 });
    await page.waitForTimeout(450);
    const resolved = await captureBoxes(page, anchors);
    assertMovement(pending, resolved, `${label} after responses resolve`, {
      positionPx: 16,
      sizePx: 16,
      shiftScore: 0.004,
    });
    await expectObservedClsBelow(page, label);
  };

  const refiCard = page.getByRole('button', { name: /Prime Refi Candidates/i });
  await exerciseTransition('select first segment card', async () => {
    await refiCard.press('Enter');
    await expect(refiCard).toHaveAttribute('aria-pressed', 'true');
  });
  expect(gate.urls().some((url) => url.includes('/api/leads') && url.includes('segment_codes=itm'))).toBe(true);

  const equityCard = page.getByRole('button', { name: /Home Equity Candidate/i });
  await exerciseTransition('compose Any-selected card cohort', async () => {
    await equityCard.press('Enter');
    await expect(equityCard).toHaveAttribute('aria-pressed', 'true');
  });
  await expect(page.getByRole('button', { name: /Any selected/i })).toHaveAttribute('aria-pressed', 'true');
  expect(gate.urls().some((url) => url.includes('segment_mode=any'))).toBe(true);

  await exerciseTransition('switch Any-selected cohort to All-selected', async () => {
    await page.getByRole('button', { name: /All selected/i }).click();
    await expect(page.getByRole('button', { name: /All selected/i })).toHaveAttribute('aria-pressed', 'true');
  });
  expect(gate.urls().some((url) => url.includes('segment_mode=all'))).toBe(true);
  await expect(page).toHaveURL(/segment_mode=all/);
  expect(unhandled).toEqual([]);
});

test('Analytics retains executive anchors while a multi-select filter refresh is pending and resolved @desktop', async ({ page }) => {
  const unhandled = MOCK ? await installMockApi(page) : [];
  const gate = await createRequestGate(page, (url) => (
    normalizedApiPath(url.toString()) === '/api/analytics/executive'
    && url.searchParams.get('states') === 'IL'
  ));

  await page.goto('/analytics', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: 'Analytics', exact: true })).toBeVisible();
  const region = page.locator('.stable-refresh-region', { hasText: 'Activation funnel' }).first();
  await expect(region).toBeVisible({ timeout: 30_000 });
  await settleInitialLayout(page);
  const anchors: Anchor[] = [
    { name: 'analytics-tabs', locator: page.locator('.analytics-tabs') },
    { name: 'analytics-filters', locator: page.locator('.analytics-filters') },
    { name: 'analytics-kpis', locator: region.locator('.kpi-row').first() },
    { name: 'activation-funnel', locator: region.locator('.analytics-section').first() },
    { name: 'analytics-grid', locator: region.locator('.analytics-grid').first() },
  ];
  const before = await captureBoxes(page, anchors);
  const beforeValue = await region.locator('.kpi__value').first().innerText();
  await resetLayoutShift(page);

  gate.arm();
  await page.getByRole('button', { name: /^State:/i }).click();
  await page.getByRole('option', { name: 'IL', exact: true }).click();
  await gate.waitForCount();
  await expect(region).toHaveAttribute('aria-busy', 'true');
  await expect(region.locator('.kpi__value').first()).toHaveText(beforeValue);
  await page.waitForTimeout(650);
  const pending = await captureBoxes(page, anchors);
  assertMovement(before, pending, 'Analytics while executive refresh is pending', {
    positionPx: 12,
    sizePx: 12,
    shiftScore: 0.004,
  });

  gate.release();
  await expect(region).not.toHaveAttribute('aria-busy', 'true', { timeout: 45_000 });
  await page.waitForTimeout(1_400);
  const resolved = await captureBoxes(page, anchors);
  assertMovement(pending, resolved, 'Analytics after executive refresh resolves', {
    positionPx: 12,
    sizePx: 12,
    shiftScore: 0.004,
  });
  await expect(page).toHaveURL(/states=IL/);
  if (MOCK) await expect(region.locator('.kpi__value').first()).toHaveText('600');
  await expectObservedClsBelow(page, 'Analytics state filter');
  expect(unhandled).toEqual([]);
});

test('Portfolio Builder retains KPI and campaign anchors during an explicit filtered build @desktop', async ({ page }) => {
  const unhandled = MOCK ? await installMockApi(page) : [];
  const gate = await createRequestGate(page, (url, request) => (
    normalizedApiPath(url.toString()) === '/api/portfolio/preview'
    && request.method() === 'POST'
    && request.postData()?.includes('Refi') === true
  ));

  await page.goto('/portfolio-builder', { waitUntil: 'domcontentloaded' });
  const kpiRow = page.locator('.kpi-row--spaced');
  await expect(kpiRow.locator('.kpi:not(.is-loading)').first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText('Campaign setup', { exact: true })).toBeVisible({ timeout: 30_000 });
  await settleInitialLayout(page);

  await page.getByRole('button', { name: /^PRODUCT:/i }).click();
  await page.getByRole('option', { name: 'Refi', exact: true }).click();
  await expect(page.getByRole('button', { name: /^PRODUCT: Refi$/i })).toBeVisible();
  const anchors: Anchor[] = [
    { name: 'portfolio-filter-surface', locator: page.locator('.surface', { hasText: 'Filters' }).first() },
    { name: 'portfolio-kpi-row', locator: kpiRow },
    { name: 'first-portfolio-kpi', locator: kpiRow.locator('.kpi').first() },
    { name: 'last-portfolio-kpi', locator: kpiRow.locator('.kpi').last() },
    { name: 'campaign-setup', locator: page.locator('.surface', { hasText: 'Campaign setup' }).first() },
  ];
  const before = await captureBoxes(page, anchors);
  const beforeValue = await kpiRow.locator('.kpi__value').first().innerText();
  await resetLayoutShift(page);

  gate.arm();
  const runBuild = page.getByRole('button', { name: 'Run build' });
  await runBuild.click();
  await gate.waitForCount();
  await expect(page.getByRole('button', { name: 'Running…' })).toHaveAttribute('aria-busy', 'true');
  await expect(kpiRow.locator('.kpi__value').first()).toHaveText(beforeValue);
  await page.waitForTimeout(650);
  const pending = await captureBoxes(page, anchors);
  assertMovement(before, pending, 'Portfolio while filtered build is pending', {
    positionPx: 12,
    sizePx: 12,
    shiftScore: 0.004,
  });

  gate.release();
  await expect(page.getByRole('button', { name: 'Run build' })).not.toHaveAttribute('aria-busy', 'true', { timeout: 45_000 });
  await page.waitForTimeout(1_400);
  const resolved = await captureBoxes(page, anchors);
  assertMovement(pending, resolved, 'Portfolio after filtered build resolves', {
    positionPx: 12,
    sizePx: 12,
    shiftScore: 0.004,
  });
  if (MOCK) await expect(kpiRow.locator('.kpi__value').first()).toHaveText('71,553');
  await expect(page).toHaveURL(/product=Refi/);
  await expectObservedClsBelow(page, 'Portfolio filtered build');
  expect(unhandled).toEqual([]);
});

test('Lead Queue keeps controls and retained rows anchored while a state transition resolves @desktop', async ({ page }) => {
  const unhandled = MOCK ? await installMockApi(page) : [];
  const gate = await createRequestGate(page, (url) => (
    normalizedApiPath(url.toString()) === '/api/leads'
    && url.searchParams.get('state') === 'IL'
  ));

  await page.goto('/lead-queue', { waitUntil: 'domcontentloaded' });
  const region = page.locator('.stable-refresh-region--table');
  const table = region.locator('table.tbl');
  await expect(table).toBeVisible({ timeout: 30_000 });
  await expect(region.locator('.surface__ft')).toContainText(/total matching filters/);
  await settleInitialLayout(page);

  const stableAnchors: Anchor[] = [
    { name: 'lead-filters', locator: page.locator('.filter-row--lead-queue') },
    { name: 'lead-table-region', locator: region },
    { name: 'lead-table', locator: table },
    { name: 'first-lead-row', locator: table.locator('tbody tr').first() },
  ];
  const footerAnchor: Anchor = { name: 'lead-table-footer', locator: region.locator('.surface__ft') };
  const before = await captureBoxes(page, [...stableAnchors, footerAnchor]);
  const retainedBorrower = await table.locator('.lead-table__borrower').first().innerText();
  await resetLayoutShift(page);

  gate.arm();
  await page.getByRole('button', { name: /^STATE:/i }).click();
  await page.getByRole('option', { name: 'IL', exact: true }).click();
  await gate.waitForCount();
  await expect(region).toHaveAttribute('aria-busy', 'true');
  await expect(table.locator('.lead-table__borrower').first()).toHaveText(retainedBorrower);
  await expect(page.getByLabel('Active analytics drilldown filters')).toContainText('State: IL');
  const pendingStart = await captureBoxes(page, [...stableAnchors, footerAnchor]);
  assertMovement(before, pendingStart, 'Lead Queue inserts the selected-state scope disclosure', {
    positionPx: 52,
    sizePx: 12,
    shiftScore: 0.024,
  });
  await page.waitForTimeout(650);
  const pending = await captureBoxes(page, [...stableAnchors, footerAnchor]);
  assertMovement(pendingStart, pending, 'Lead Queue remains anchored while the state refresh is pending', {
    positionPx: 4,
    sizePx: 4,
    shiftScore: 0.001,
  });

  gate.release();
  await expect(region).toHaveAttribute('aria-busy', 'false', { timeout: 45_000 });
  await page.waitForTimeout(450);
  const resolvedStable = await captureBoxes(page, stableAnchors);
  assertMovement(
    { viewport: pending.viewport, boxes: Object.fromEntries(stableAnchors.map(({ name }) => [name, pending.boxes[name]])) },
    resolvedStable,
    'Lead Queue stable anchors after state refresh resolves',
    { positionPx: 16, sizePx: 72, shiftScore: 0.006 },
  );
  const resolvedFooter = await captureBoxes(page, [footerAnchor]);
  assertMovement(
    { viewport: pending.viewport, boxes: { [footerAnchor.name]: pending.boxes[footerAnchor.name] } },
    resolvedFooter,
    'Lead Queue footer after one-row result contraction',
    { positionPx: 72, sizePx: 12, shiftScore: 0.01 },
  );
  if (MOCK) await expect(region.locator('.surface__ft')).toContainText('of 2 total matching filters');
  await expect(page).toHaveURL(/state=IL/);
  await expectObservedClsBelow(page, 'Lead Queue state filter');
  expect(unhandled).toEqual([]);
});

});
