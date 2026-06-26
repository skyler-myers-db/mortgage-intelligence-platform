import { expect, test, type Page } from '@playwright/test';

const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 to run live Growth Agent workflow coverage.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const API_URL = process.env.MIP_API_URL || APP_URL.replace(':5173', ':8000');
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER ? { Authorization: `Bearer ${BEARER}` } : {};

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

type GrowthAgentRunResponse = {
  run_id: string;
  workflow: { id: string; title: string };
  monitor?: { name: string; actionable_total: number } | null;
  broad_total: number;
  actionable_total: number;
  route: string;
  criteria: {
    lead_queue_filters?: {
      segment_codes?: string[];
      segment_mode?: string;
      portfolio_criteria?: Record<string, unknown>;
    };
  };
  source_assets: string[];
  tool_steps: Array<{ label: string; status: string; detail: string }>;
  policy_checks: Array<{ label: string; status: string; detail: string }>;
  audit_event_id?: string | null;
};

async function gotoApp(page: Page, path: string): Promise<void> {
  await page.goto(path, { waitUntil: 'domcontentloaded', timeout: 60_000 });
  await expect(page.locator('main')).toBeVisible({ timeout: 30_000 });
}

function apiPathFromLeadQueueRoute(route: string): string {
  const url = new URL(route, APP_URL);
  const params = new URLSearchParams(url.searchParams);
  params.set('limit', '1');
  return `/api/leads?${params.toString()}`;
}

test('Growth Agent run, saved monitor, and Lead Queue handoff are live and reconciled', async ({
  page,
  request,
}) => {
  await gotoApp(page, '/ask-genie');

  await expect(page.getByText('Mortgage Growth Agent').first()).toBeVisible();
  await expect(page.getByText('No auto-send')).toBeVisible();
  await expect(page.getByText('Audited Lakebase run')).toBeVisible();

  const stateScope = page.getByLabel('Growth Agent state scope');
  let workflowPostCount = 0;
  page.on('request', (req) => {
    if (
      req.method() === 'POST' &&
      /\/api(?:\/v1)?\/growth-agent\/workflows\/.+\/run/.test(req.url())
    ) {
      workflowPostCount += 1;
    }
  });

  await stateScope.fill('IL illinois');
  await expect(page.getByText('Invalid: illinois')).toBeVisible();
  for (const button of await page.getByRole('button', { name: /^Run$/ }).all()) {
    await expect(button).toBeDisabled();
  }
  for (const button of await page.getByRole('button', { name: 'Save monitor' }).all()) {
    await expect(button).toBeDisabled();
  }
  await page.waitForTimeout(500);
  expect(workflowPostCount).toBe(0);

  await stateScope.fill('IL TX');
  await expect(page.getByText('Scoped to IL, TX')).toBeVisible();
  const dailyCard = page.locator('.growth-agent-card').filter({
    hasText: 'Daily Refi Opportunity Brief',
  });
  await expect(dailyCard).toBeVisible();

  const runResponsePromise = page.waitForResponse((response) =>
    response.request().method() === 'POST' &&
    /\/api(?:\/v1)?\/growth-agent\/workflows\/daily_refi_brief\/run/.test(response.url()),
  );
  await dailyCard.getByRole('button', { name: 'Save monitor' }).click();
  const runResponse = await runResponsePromise;
  expect(runResponse.status(), 'Growth Agent workflow run returned non-200').toBe(200);
  const run = (await runResponse.json()) as GrowthAgentRunResponse;

  expect(run.run_id).toBeTruthy();
  expect(run.audit_event_id).toBeTruthy();
  expect(run.workflow.id).toBe('daily_refi_brief');
  expect(run.broad_total).toBeGreaterThan(0);
  expect(run.actionable_total).toBeGreaterThan(0);
  expect(run.broad_total).toBeGreaterThanOrEqual(run.actionable_total);
  expect(run.route).toContain('/lead-queue?');
  expect(run.criteria.lead_queue_filters?.segment_codes).toEqual(['itm']);
  expect(run.criteria.lead_queue_filters?.segment_mode).toBe('any');
  expect(run.criteria.lead_queue_filters?.portfolio_criteria?.marketing_eligibility).toBe(
    'Eligible only',
  );
  expect(run.source_assets).toEqual(
    expect.arrayContaining(['mip.gold.borrower_360', 'mip.gold.lead_population']),
  );
  expect(run.policy_checks.map((check) => check.label)).toEqual(
    expect.arrayContaining([
      'No outbound activation',
      'Broad vs actionable reconciliation',
      'Monitor saved to Lakebase',
    ]),
  );

  await expect(page.getByLabel('Latest Growth Agent run')).toBeVisible();
  await expect(page.getByText('Broad opportunity', { exact: true })).toBeVisible();
  await expect(page.getByText('Eligible subset', { exact: true })).toBeVisible();
  await expect(page.getByText('Tool timeline', { exact: true })).toBeVisible();
  await expect(page.getByText('Policy checks', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Saved Growth Agent monitors')).toContainText(
    'Daily Refi Opportunity Brief',
  );

  const apiPath = apiPathFromLeadQueueRoute(run.route);
  const leadResp = await request.get(`${API_URL}${apiPath}`, { headers: AUTH_HEADERS });
  expect(leadResp.status(), `GET ${apiPath} returned non-200`).toBe(200);
  expect(Number(leadResp.headers()['x-total-matching'] ?? -1)).toBe(run.actionable_total);

  await page.getByRole('button', { name: 'Open eligible Lead Queue subset' }).click();
  await expect(page).toHaveURL(/\/lead-queue\?/);
  await expect(page.getByRole('heading', { name: /Ranked borrowers/i })).toBeVisible();
  const landed = new URL(page.url());
  expect(landed.searchParams.get('marketing_eligibility')).toBe('Eligible only');
  expect(landed.searchParams.get('segment')).toBe('itm');
  expect(landed.searchParams.get('states')).toBe('IL,TX');
});
