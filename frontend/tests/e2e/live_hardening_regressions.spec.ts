import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 to run live hardening regression checks.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const API_URL = process.env.MIP_API_URL || APP_URL.replace(':5173', ':8000');
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER ? { Authorization: `Bearer ${BEARER}` } : {};

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

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

test('Clear filters is visible, disabled when clean, and clears active filters on core routes', async ({ page }) => {
  await page.goto('/lead-queue');
  await expectClearFiltersState(page, true);
  await page.goto('/lead-queue?states=IL&segment_codes=itm,equity&segment_mode=any');
  await expectClearFiltersState(page, false);
  await page.getByRole('button', { name: /^Clear filters$/ }).first().click();
  await expect(page).toHaveURL(/\/lead-queue$/);
  await expectClearFiltersState(page, true);

  await page.goto('/analytics');
  await expectClearFiltersState(page, true);
  await page.goto('/analytics?states=IL&segment_codes=itm&signal_types=listing');
  await expectClearFiltersState(page, false);
  await page.getByRole('button', { name: /^Clear filters$/ }).first().click();
  await expect(page).toHaveURL(/\/analytics$/);
  await expectClearFiltersState(page, true);

  await page.goto('/segment-intelligence');
  await expectClearFiltersState(page, true);
  await clickSegment(page, 'Prime Refi Candidates');
  await expectClearFiltersState(page, false);
  await page.getByRole('button', { name: /^Clear filters$/ }).first().click();
  await expectClearFiltersState(page, true);
  await expect(page.getByText(/matches any selected segment/i)).toHaveCount(0);
});

test('Segment Intelligence stacks selected segments into an any-match cohort and replays controls', async ({ page }) => {
  await page.goto('/segment-intelligence');
  await clickSegment(page, 'Prime Refi Candidates');
  await clickSegment(page, 'Listed for Sale');

  await expect(page.getByText(/matches any selected segment/i)).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText(/must match every selected segment/i)).toHaveCount(0);

  await page.getByRole('link', { name: /Deep-dive lead queue/i }).click();
  await expect(page).toHaveURL(/\/lead-queue\?/, { timeout: 20_000 });

  const url = new URL(page.url());
  expect(url.searchParams.get('segment_mode')).toBe('any');
  expect(url.searchParams.get('segment_codes') ?? url.searchParams.get('segments')).toMatch(/itm/);
  expect(url.searchParams.get('segment_codes') ?? url.searchParams.get('segments')).toMatch(/listed/);
  await expect(page.getByRole('button', { name: /SEGMENT:\s*2 segments selected/i })).toBeVisible({ timeout: 20_000 });
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

  await page.goto('/segment-intelligence');
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

  await page.goto('/ask-genie');
  await page.locator('textarea[aria-label="Ask Genie — question"]').fill(questions[0]);
  await page.getByRole('button', { name: /^Ask Genie$/i }).first().click();
  await expect(page.getByText(/Policy blocked|Genie reconnecting|Genie is warming up/i)).toHaveCount(0, { timeout: 60_000 });
  await expect(page.getByText(/mip\.gold\.lead_population/i).first()).toBeVisible({ timeout: 60_000 });
});

test('Genie open-cohort action, Lead Queue URL, dropdowns, and rows agree', async ({ page, request }) => {
  const answer = await askGenie(request, 'Which ZIPs have the most in-the-money refinance candidates?');
  const action = answer.actions?.find((candidate) => candidate.action_type === 'open_cohort');
  expect(action, 'Genie should return an open-cohort action for a ZIP-ranked answer').toBeTruthy();

  const resp = await request.post(`${API_URL}/api/genie/actions`, {
    headers: AUTH_HEADERS,
    data: {
      action_type: action!.action_type,
      conversation_id: answer.conversation_id ?? null,
      message_id: answer.message_id ?? null,
      question_hash: answer.question_hash ?? null,
      borrower_ids: action!.borrower_ids ?? [],
      criteria: action!.criteria ?? {},
      route: action!.route ?? null,
      request_id: action!.request_id,
      confirmed: true,
      confirmation_token: action!.confirmation_token ?? null,
    },
    timeout: 90_000,
  });
  expect(resp.status(), 'open-cohort action should succeed').toBe(200);
  const result = await resp.json() as GenieActionResult;
  expect(result.ok).toBe(true);
  expect(result.route, 'open-cohort result should include a lead queue route').toMatch(/^\/lead-queue\?/);

  await page.goto(result.route!);
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
