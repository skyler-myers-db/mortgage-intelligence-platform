import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 to run live hardening regression checks.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const API_URL = process.env.MIP_API_URL || APP_URL.replace(':5173', ':8000');
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const ADMIN_BEARER = process.env.MIP_ADMIN_BEARER_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER ? { Authorization: `Bearer ${BEARER}` } : {};
const ADMIN_AUTH_HEADERS: Record<string, string> = ADMIN_BEARER
  ? { Authorization: `Bearer ${ADMIN_BEARER}` }
  : {};

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

async function gotoApp(page: Page, path: string): Promise<void> {
  await page.goto(path, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await expect(page.locator('main')).toBeVisible({ timeout: 30_000 });
}

type StateRollup = {
  state: string;
  addressable?: number;
  addressable_borrowers?: number;
  marketable_borrowers?: number;
};

type CountyRollup = {
  fips_5: string;
  state: string;
  county_name: string;
  addressable_borrowers?: number;
  marketable_borrowers?: number;
};

type GenieAction = {
  id: string;
  label: string;
  action_type: string;
  description?: string;
  borrower_ids?: string[];
  criteria?: Record<string, unknown>;
  route?: string | null;
  request_id?: string;
  confirmation_token?: string | null;
};

type GenieAnswer = {
  answer?: string;
  source?: string;
  trusted_assets?: string[];
  actions?: GenieAction[];
  conversation_id?: string | null;
  message_id?: string | null;
  question_hash?: string | null;
};

type GenieActionResult = {
  ok: boolean;
  action_type: string;
  route?: string | null;
  message?: string;
};

type SessionResponse = {
  can_access_admin: boolean;
};

type ActorActivityPage = {
  items: Array<{
    event_type: string;
    entity_type: string;
    subject_id?: string | null;
    created_at: string;
    [key: string]: unknown;
  }>;
  next_cursor?: string | null;
};

const STATE_NAMES: Record<string, string> = {
  AL: 'Alabama',
  AK: 'Alaska',
  AZ: 'Arizona',
  AR: 'Arkansas',
  CA: 'California',
  CO: 'Colorado',
  CT: 'Connecticut',
  DE: 'Delaware',
  FL: 'Florida',
  GA: 'Georgia',
  HI: 'Hawaii',
  ID: 'Idaho',
  IL: 'Illinois',
  IN: 'Indiana',
  IA: 'Iowa',
  KS: 'Kansas',
  KY: 'Kentucky',
  LA: 'Louisiana',
  ME: 'Maine',
  MD: 'Maryland',
  MA: 'Massachusetts',
  MI: 'Michigan',
  MN: 'Minnesota',
  MS: 'Mississippi',
  MO: 'Missouri',
  MT: 'Montana',
  NE: 'Nebraska',
  NV: 'Nevada',
  NH: 'New Hampshire',
  NJ: 'New Jersey',
  NM: 'New Mexico',
  NY: 'New York',
  NC: 'North Carolina',
  ND: 'North Dakota',
  OH: 'Ohio',
  OK: 'Oklahoma',
  OR: 'Oregon',
  PA: 'Pennsylvania',
  RI: 'Rhode Island',
  SC: 'South Carolina',
  SD: 'South Dakota',
  TN: 'Tennessee',
  TX: 'Texas',
  UT: 'Utah',
  VT: 'Vermont',
  VA: 'Virginia',
  WA: 'Washington',
  WV: 'West Virginia',
  WI: 'Wisconsin',
  WY: 'Wyoming',
};

function countValue(row: StateRollup | CountyRollup): number {
  return Number(row.addressable_borrowers ?? row.marketable_borrowers ?? ('addressable' in row ? row.addressable : 0) ?? 0);
}

function urlIncludesApiPath(url: string, path: string): boolean {
  const normalized = path.startsWith('/') ? path : `/${path}`;
  return url.includes(normalized) || url.includes(normalized.replace('/api/', '/api/v1/'));
}

async function getJson<T>(request: APIRequestContext, path: string): Promise<T> {
  const resp = await request.get(`${API_URL}${path}`, { headers: AUTH_HEADERS });
  expect(resp.status(), `GET ${path}`).toBe(200);
  return await resp.json() as T;
}

async function askGenie(request: APIRequestContext, question: string): Promise<GenieAnswer> {
  const resp = await request.post(`${API_URL}/api/genie/message`, {
    headers: AUTH_HEADERS,
    data: { question, conversation_id: null },
    timeout: 90_000,
  });
  expect(resp.status(), `Genie question failed: ${question}`).toBe(200);
  const payload = await resp.json() as GenieAnswer;
  expect(payload.source, `Genie should not block valid question: ${question}`).not.toMatch(/policy|refused|degraded|reconnecting/i);
  expect(payload.answer ?? '', `Genie returned an empty answer: ${question}`).not.toHaveLength(0);
  return payload;
}

async function leadTotal(request: APIRequestContext, query: string): Promise<number> {
  const resp = await request.get(`${API_URL}/api/leads?limit=1&${query}`, { headers: AUTH_HEADERS });
  expect(resp.status(), `GET /api/leads?${query}`).toBe(200);
  const header = resp.headers()['x-total-matching'];
  expect(header, `GET /api/leads?${query} should include x-total-matching`).toBeTruthy();
  return Number(header);
}

async function executeOpenCohortAction(
  request: APIRequestContext,
  answer: GenieAnswer,
  action: GenieAction,
): Promise<GenieActionResult> {
  const resp = await request.post(`${API_URL}/api/genie/actions`, {
    headers: AUTH_HEADERS,
    data: {
      action_type: action.action_type,
      conversation_id: answer.conversation_id ?? null,
      message_id: answer.message_id ?? null,
      question_hash: answer.question_hash ?? null,
      borrower_ids: action.borrower_ids ?? [],
      criteria: action.criteria ?? {},
      route: action.route ?? null,
      request_id: action.request_id,
      confirmed: true,
      confirmation_token: action.confirmation_token ?? null,
    },
    timeout: 90_000,
  });
  expect(resp.status(), 'open-cohort action should succeed').toBe(200);
  const result = await resp.json() as GenieActionResult;
  expect(result.ok).toBe(true);
  expect(result.route, 'open-cohort result should include a lead queue route').toMatch(/^\/lead-queue\?/);
  return result;
}

function leadQueueApiPathFromRoute(route: string): string {
  const routeUrl = new URL(route, APP_URL);
  return `/api/leads?${routeUrl.searchParams.toString()}`;
}

async function clickSegment(page: Page, label: string): Promise<void> {
  const card = page.locator('.seg-card', { hasText: label }).first();
  await expect(card, `segment card ${label}`).toBeVisible({ timeout: 45_000 });
  await card.click();
}

async function expectClearFiltersState(page: Page, disabled: boolean): Promise<void> {
  const clear = page.getByRole('button', { name: /^Clear filters$/ }).first();
  await expect(clear).toBeVisible({ timeout: 30_000 });
  if (disabled) {
    await expect(clear).toBeDisabled();
  } else {
    await expect(clear).toBeEnabled();
  }
}

test('admin and non-admin identities enforce navigation and activity boundaries', async ({
  browser,
  page,
  request,
}) => {
  test.setTimeout(180_000);
  expect(BEARER, 'live role proof requires the non-admin M2M bearer').not.toBe('');
  expect(ADMIN_BEARER, 'live role proof requires a distinct admin bearer').not.toBe('');
  expect(ADMIN_BEARER, 'admin and non-admin proofs must not reuse one identity').not.toBe(BEARER);

  const nonAdminSessionResponse = await request.get(`${API_URL}/api/session`, {
    headers: AUTH_HEADERS,
  });
  expect(nonAdminSessionResponse.status(), 'non-admin session lookup').toBe(200);
  const nonAdminSession = await nonAdminSessionResponse.json() as SessionResponse;
  expect(nonAdminSession.can_access_admin).toBe(false);

  const ownActivityResponse = await request.get(`${API_URL}/api/audit/my-events?limit=8`, {
    headers: AUTH_HEADERS,
  });
  expect(ownActivityResponse.status(), 'non-admin actor-scoped activity lookup').toBe(200);
  expect(ownActivityResponse.headers()['cache-control']).toContain('private');
  expect(ownActivityResponse.headers()['cache-control']).toContain('no-store');
  const ownActivity = await ownActivityResponse.json() as ActorActivityPage;
  expect(Array.isArray(ownActivity.items)).toBe(true);
  for (const item of ownActivity.items) {
    expect(item.event_type.trim()).not.toBe('');
    expect(item.entity_type.trim()).not.toBe('');
    expect(item.created_at.trim()).not.toBe('');
    expect(item).not.toHaveProperty('actor');
    expect(item).not.toHaveProperty('payload_json');
    expect(item).not.toHaveProperty('evidence_ids');
  }

  const actorOverrideResponse = await request.get(
    `${API_URL}/api/audit/my-events?limit=8&actor=another-user%40example.com`,
    { headers: AUTH_HEADERS },
  );
  expect(actorOverrideResponse.status(), 'actor-scoped feed must reject actor overrides').toBe(422);

  const deniedGlobalActivity = await request.get(`${API_URL}/api/audit/events?limit=1`, {
    headers: AUTH_HEADERS,
  });
  expect(deniedGlobalActivity.status(), 'non-admin global activity lookup must fail closed').toBe(403);

  await gotoApp(page, '/');
  const nonAdminNavigation = page.getByRole('navigation', { name: 'Main navigation' });
  await expect(nonAdminNavigation.getByRole('link', { name: 'Admin', exact: true })).toHaveCount(0);
  await page.getByRole('banner').getByRole('button', { name: 'Toggle console' }).click();
  const consolePanel = page.getByRole('complementary', { name: 'Workspace console' });
  await expect(consolePanel.getByText('My recent activity', { exact: true })).toBeVisible();
  await expect(consolePanel.getByText('Loading recent activity…')).toBeHidden({ timeout: 30_000 });

  await page.goto('/admin-config', { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await expect.poll(() => new URL(page.url()).pathname, { timeout: 30_000 }).toBe('/');

  const adminSessionResponse = await request.get(`${API_URL}/api/session`, {
    headers: ADMIN_AUTH_HEADERS,
  });
  expect(adminSessionResponse.status(), 'admin session lookup').toBe(200);
  const adminSession = await adminSessionResponse.json() as SessionResponse;
  expect(adminSession.can_access_admin).toBe(true);

  const adminGlobalActivity = await request.get(`${API_URL}/api/audit/events?limit=1`, {
    headers: ADMIN_AUTH_HEADERS,
  });
  expect(adminGlobalActivity.status(), 'admin global activity lookup').toBe(200);
  expect(Array.isArray(await adminGlobalActivity.json())).toBe(true);

  const adminContext = await browser.newContext({
    baseURL: APP_URL,
    extraHTTPHeaders: ADMIN_AUTH_HEADERS,
  });
  const adminPage = await adminContext.newPage();
  try {
    await gotoApp(adminPage, '/admin-config');
    await expect(adminPage).toHaveURL(/\/admin-config$/);
    await expect(
      adminPage
        .getByRole('navigation', { name: 'Main navigation' })
        .getByRole('link', { name: 'Admin', exact: true }),
    ).toBeVisible();
    await expect(
      adminPage.getByRole('heading', { name: 'Rules, data sources, and audit' }),
    ).toBeVisible();
    const auditTrail = adminPage.locator('.surface', { hasText: 'Audit trail' }).first();
    await expect(auditTrail).toBeVisible();
    await expect(auditTrail.getByText('Probing Lakebase…')).toBeHidden({ timeout: 30_000 });
    await expect(auditTrail).toContainText(/Last event|No events yet/);
  } finally {
    await adminContext.close();
  }
});

test('Clear filters is visible, disabled when clean, and clears active filters on core routes', async ({ page }) => {
  await gotoApp(page, '/lead-queue');
  await expectClearFiltersState(page, true);
  await gotoApp(page, '/lead-queue?states=IL&segment_codes=itm,equity&segment_mode=any');
  await expectClearFiltersState(page, false);
  await page.getByRole('button', { name: /^Clear filters$/ }).first().click();
  await expect(page).toHaveURL(/\/lead-queue$/);
  await expectClearFiltersState(page, true);

  await gotoApp(page, '/analytics');
  await expectClearFiltersState(page, true);
  await gotoApp(page, '/analytics?states=IL&segment_codes=itm&signal_types=listing');
  await expectClearFiltersState(page, false);
  await page.getByRole('button', { name: /^Clear filters$/ }).first().click();
  await expect(page).toHaveURL(/\/analytics$/);
  await expectClearFiltersState(page, true);

  await gotoApp(page, '/segment-intelligence');
  await expectClearFiltersState(page, true);
  await clickSegment(page, 'Prime Refi Candidates');
  await expectClearFiltersState(page, false);
  await page.getByRole('button', { name: /^Clear filters$/ }).first().click();
  await expectClearFiltersState(page, true);
  await expect(page.getByText(/any selected segment, de-duplicated/i)).toHaveCount(0);
});

test('Segment Intelligence stacks selected segments into an any-match cohort and replays controls', async ({ page }) => {
  await gotoApp(page, '/segment-intelligence?segment_codes=itm,listed&segment_mode=all');
  await expect(page.getByRole('button', { name: /All selected/i })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('button', { name: /Prime Refi Candidates/i })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByRole('button', { name: /Listed for Sale/i })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText(/all selected segments/i)).toBeVisible({ timeout: 20_000 });

  await gotoApp(page, '/segment-intelligence');
  await expect(page.getByRole('button', { name: /Any selected/i })).toHaveAttribute('aria-pressed', 'true');
  await clickSegment(page, 'Prime Refi Candidates');
  await clickSegment(page, 'Listed for Sale');
  const anyUrl = new URL(page.url());
  expect((anyUrl.searchParams.get('segment_codes') ?? '').split(',').sort()).toEqual(['itm', 'listed']);
  expect(anyUrl.searchParams.get('segment_mode')).toBe('any');

  await expect(page.getByText(/any selected segment, de-duplicated/i)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/all selected segments/i)).toHaveCount(0);

  const allModeResponse = page.waitForResponse((response) => {
    if (response.status() !== 200 || !urlIncludesApiPath(response.url(), '/api/leads')) return false;
    const parsed = new URL(response.url());
    const codes = parsed.searchParams.get('segment_codes') ?? '';
    return parsed.searchParams.get('segment_mode') === 'all' && codes.includes('itm') && codes.includes('listed');
  });
  const allModeStateRollupResponse = page.waitForResponse((response) => {
    if (response.status() !== 200 || !urlIncludesApiPath(response.url(), '/api/geo/state-rollups')) return false;
    const parsed = new URL(response.url());
    const codes = parsed.searchParams.get('segment_codes') ?? '';
    return parsed.searchParams.get('segment_mode') === 'all' && codes.includes('itm') && codes.includes('listed');
  });
  await page.getByRole('button', { name: /All selected/i }).click();
  const [, stateRollupResponse] = await Promise.all([allModeResponse, allModeStateRollupResponse]);
  const stateRollups = await stateRollupResponse.json();
  expect(Array.isArray(stateRollups.rollups)).toBe(true);
  const allUrl = new URL(page.url());
  expect((allUrl.searchParams.get('segment_codes') ?? '').split(',').sort()).toEqual(['itm', 'listed']);
  expect(allUrl.searchParams.get('segment_mode')).toBe('all');
  await expect(page.getByRole('button', { name: /All selected/i })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText(/all selected segments/i)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/any selected segment, de-duplicated/i)).toHaveCount(0);

  await page.getByRole('link', { name: /Deep-dive lead queue/i }).click();
  await expect(page).toHaveURL(/\/lead-queue\?/, { timeout: 20_000 });

  const url = new URL(page.url());
  expect(url.searchParams.get('segment_mode')).toBe('all');
  expect(url.searchParams.get('segment_codes') ?? url.searchParams.get('segments')).toMatch(/itm/);
  expect(url.searchParams.get('segment_codes') ?? url.searchParams.get('segments')).toMatch(/listed/);
  await expect(page.getByRole('button', { name: /SEGMENT:\s*2 segments selected \(all selected\)/i })).toBeVisible({ timeout: 20_000 });
});

test('segment any/all API counts are de-duplicated and intersection-safe', async ({ request }) => {
  const [itm, equity, anyMode, allMode] = await Promise.all([
    leadTotal(request, 'segment=itm'),
    leadTotal(request, 'segment=equity'),
    leadTotal(request, 'segment_codes=itm,equity&segment_mode=any'),
    leadTotal(request, 'segment_codes=itm,equity&segment_mode=all'),
  ]);

  expect(anyMode, 'OR count must equal A + B - overlap; summed duplicate memberships would fail this').toBe(itm + equity - allMode);
  expect(anyMode, 'OR count should include at least the larger individual segment').toBeGreaterThanOrEqual(Math.max(itm, equity));
  expect(allMode, 'AND count must be no larger than either individual segment').toBeLessThanOrEqual(Math.min(itm, equity));
  expect(allMode, 'live proof requires a nonempty ITM/equity intersection').toBeGreaterThan(0);

  const anyPageResponse = await request.get(
    `${API_URL}/api/leads?limit=100&segment_codes=itm,equity&segment_mode=any`,
    { headers: AUTH_HEADERS },
  );
  expect(anyPageResponse.status(), 'de-duplicated OR page').toBe(200);
  const anyRows = await anyPageResponse.json() as Array<{ borrower_id?: string }>;
  const borrowerIds = anyRows.map((row) => row.borrower_id ?? '');
  expect(borrowerIds.length, 'OR page should contain live borrowers').toBeGreaterThan(0);
  expect(borrowerIds.every(Boolean), 'every OR row must carry a public borrower id').toBe(true);
  expect(
    new Set(borrowerIds).size,
    'a borrower matching both segments must appear only once in the any-mode page',
  ).toBe(borrowerIds.length);
});

test('county drilldown distinguishes loading counties from loaded positive and empty counties', async ({ page, request }) => {
  const query = 'segment_codes=itm,listed&segment_mode=any';
  const states = await getJson<{ rollups: StateRollup[] }>(request, `/api/geo/state-rollups?${query}`);
  const candidates = states.rollups
    .filter((row) => countValue(row) > 0 && STATE_NAMES[row.state])
    .sort((a, b) => countValue(b) - countValue(a));

  expect(candidates.length, 'expected at least one live state with borrowers for the selected segments').toBeGreaterThan(0);

  let selected: { state: string; stateName: string; countyName: string } | null = null;
  for (const state of candidates) {
    const counties = await getJson<{ rollups: CountyRollup[] }>(
      request,
      `/api/geo/county-rollups?state=${state.state}&${query}`,
    );
    const county = counties.rollups.find((row) => countValue(row) > 0);
    if (county) {
      selected = {
        state: state.state,
        stateName: STATE_NAMES[state.state],
        countyName: county.county_name,
      };
      break;
    }
  }

  expect(selected, 'expected a live county with borrowers for the selected segments').not.toBeNull();

  await gotoApp(page, '/segment-intelligence');
  await clickSegment(page, 'Prime Refi Candidates');
  await clickSegment(page, 'Listed for Sale');
  await page.locator('path.map-region', { hasText: '' }).first().waitFor({ state: 'visible', timeout: 45_000 });
  await page.locator(`path[aria-label="${selected!.stateName}"]`).click();

  const countyPath = page.locator(`path[aria-label="${selected!.countyName} County"]`).first();
  await expect(countyPath, `county path ${selected!.countyName}`).toBeVisible({ timeout: 45_000 });
  await expect(countyPath).toHaveClass(/(?:is-loading|has-data)/);
  await expect(countyPath).toHaveClass(/has-data/, { timeout: 45_000 });
  await expect(countyPath).not.toHaveClass(/is-empty/);
  await expect(countyPath).toHaveClass(/lvl-[1-4]/);
});

test('Genie answers valid recommended and free-form questions without policy-blocking', async ({ page, request }) => {
  const questions = [
    'What are the overall best borrowers for any offer?',
    'What is the addressable market size — how many eligible borrowers across the current Cotality data coverage?',
    'How many borrowers have at least 35% modeled equity across the current Cotality data coverage?',
    'Show the top cohorts.',
  ];

  for (const question of questions) {
    const answer = await askGenie(request, question);
    expect(answer.trusted_assets?.length ?? 0, `Genie answer should cite trusted assets: ${question}`).toBeGreaterThan(0);
  }

  await gotoApp(page, '/ask-genie');
  await page.locator('textarea[aria-label="Ask Genie — question"]').fill(questions[0]);
  await page.getByRole('button', { name: /^Ask Genie$/i }).first().click();
  await expect(page.getByText(/Policy blocked|Genie reconnecting|Genie is warming up/i)).toHaveCount(0, { timeout: 60_000 });
  await expect(page.getByText(/mip\.gold\.lead_population/i).first()).toBeVisible({ timeout: 60_000 });
});

test('Genie state-breakdown action reconciles broad answer with eligible Lead Queue subset', async ({ request }) => {
  const answer = await askGenie(
    request,
    'Break down in-the-money borrowers by current coverage state; which state leads?',
  );
  expect(answer.source).toBe('trusted_sql');
  expect(answer.answer ?? '').toMatch(/broad economic screen/i);
  expect(answer.answer ?? '').toMatch(/Lead Queue action opens/i);

  const action = answer.actions?.find((candidate) => candidate.action_type === 'open_cohort');
  expect(action, 'Genie should return an open-cohort action for the state breakdown').toBeTruthy();
  expect(action!.label).toMatch(/^Open eligible Lead Queue subset \([\d,]+\)$/);
  expect(action!.description ?? '').toMatch(/marketing-eligible Lead Queue subset/i);
  const actionableTotal = Number(action!.criteria?.actionable_total);
  expect(Number.isFinite(actionableTotal), 'action should carry actionable_total').toBeTruthy();
  expect(actionableTotal).toBeGreaterThan(0);

  const result = await executeOpenCohortAction(request, answer, action!);
  const leadsResp = await request.get(`${API_URL}${leadQueueApiPathFromRoute(result.route!)}`, {
    headers: AUTH_HEADERS,
    timeout: 90_000,
  });
  expect(leadsResp.status(), 'Lead Queue API should accept the governed cohort route').toBe(200);
  const totalMatching = Number(leadsResp.headers()['x-total-matching']);
  expect(totalMatching, 'Lead Queue total must equal Genie actionable_total').toBe(actionableTotal);
});

test('Genie open-cohort action, Lead Queue URL, dropdowns, and rows agree', async ({ page, request }) => {
  const answer = await askGenie(request, 'Which ZIPs have the most in-the-money refinance candidates?');
  const action = answer.actions?.find((candidate) => candidate.action_type === 'open_cohort');
  expect(action, 'Genie should return an open-cohort action for a ZIP-ranked answer').toBeTruthy();

  const result = await executeOpenCohortAction(request, answer, action!);

  await gotoApp(page, result.route!);
  await expect(page.getByText(/Genie cohort/i)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText(/Loading leads/i)).toBeHidden({ timeout: 60_000 });

  const url = new URL(page.url());
  const zips = (url.searchParams.get('zips') ?? url.searchParams.get('zip') ?? '').split(',').filter(Boolean);
  const segments = (url.searchParams.get('segment_codes') ?? url.searchParams.get('segments') ?? url.searchParams.get('segment') ?? '')
    .split(',')
    .filter(Boolean);

  if (zips.length > 1) {
    await expect(page.getByText(new RegExp(`zips = ${zips.length} selected`, 'i'))).toBeVisible();
  } else if (zips.length === 1) {
    await expect(page.getByText(new RegExp(`zip = ${zips[0]}`, 'i'))).toBeVisible();
  }
  if (segments.length > 1) {
    await expect(page.getByRole('button', { name: /SEGMENT:\s*\d+ segments selected/i })).toBeVisible();
  } else {
    await expect(page.getByRole('button', { name: /SEGMENT:\s*Prime Refi Candidates/i })).toBeVisible();
  }

  if (zips.length > 0) {
    await expect(page.locator('.lead-table__zip').first()).toBeVisible({ timeout: 60_000 });
    const rowZips = await page.locator('.lead-table__zip').evaluateAll((nodes) =>
      nodes.slice(0, 25).map((node) => node.textContent?.trim() ?? '').filter(Boolean),
    );
    expect(rowZips.length, 'Lead Queue should render rows for the Genie cohort').toBeGreaterThan(0);
    expect(rowZips.every((zip) => zips.includes(zip))).toBeTruthy();
  }
});
