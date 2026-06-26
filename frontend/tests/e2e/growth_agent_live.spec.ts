import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

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
  specialist_agent: string;
  trace_id: string;
  tool_result_hash: string;
  broad_label: string;
  actionable_label: string;
  broad_total: number;
  actionable_total: number;
  route: string;
  criteria: {
    lead_queue_filters?: {
      segment_codes?: string[];
      segment_mode?: string;
      funnel_stage?: string;
      portfolio_criteria?: Record<string, unknown>;
    };
  };
  source_assets: string[];
  tool_steps: Array<{
    label: string;
    status: string;
    detail: string;
    tool_name?: string | null;
    result_hash?: string | null;
  }>;
  policy_checks: Array<{ label: string; status: string; detail: string }>;
  governance_chips: Array<{ label: string; status: string; detail: string }>;
  interpreted_intent?: string | null;
  audit_event_id?: string | null;
};

type GrowthAgentMonitorResponse = {
  name: string;
  workflow_id: string;
  criteria: Record<string, unknown>;
  route: string;
  actionable_total: number;
  source_assets: string[];
  last_run_id?: string | null;
};

test.beforeEach(({ page }) => {
  page.on('console', (message) => {
    if (message.type() === 'error') {
      throw new Error(`Browser console error: ${message.text()}`);
    }
  });
  page.on('pageerror', (error) => {
    throw error;
  });
});

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

async function expectLeadQueueHandoffMatchesActionableTotal(
  request: APIRequestContext,
  run: GrowthAgentRunResponse,
): Promise<void> {
  const apiPath = apiPathFromLeadQueueRoute(run.route);
  const leadResp = await request.get(`${API_URL}${apiPath}`, { headers: AUTH_HEADERS });
  expect(leadResp.status(), `GET ${apiPath} returned non-200`).toBe(200);
  expect(Number(leadResp.headers()['x-total-matching'] ?? -1)).toBe(run.actionable_total);
}

test('Growth Agent run, saved monitor, and Lead Queue handoff are live and reconciled', async ({
  page,
  request,
}) => {
  await gotoApp(page, '/ask-genie');

  await expect(page.getByText('Mortgage Growth Agent').first()).toBeVisible();
  await expect(page.getByText('Borrower Dossier Review')).toBeVisible();
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
  expect(run.specialist_agent).toBe('structured_data_agent');
  expect(run.trace_id).toMatch(/^agent-trace-/);
  expect(run.tool_result_hash).toMatch(/^[a-f0-9]{64}$/);
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
  expect(run.tool_steps.map((step) => step.tool_name)).toEqual(
    expect.arrayContaining(['fn_build_cohort', 'fn_segment_counts', 'fn_lead_queue_url']),
  );
  expect(run.tool_steps.every((step) => step.result_hash === run.tool_result_hash)).toBe(true);
  expect(run.governance_chips.map((chip) => chip.label)).toEqual(
    expect.arrayContaining(['PII-safe output', 'Human approval required', 'Policy checks']),
  );

  await expect(page.getByLabel('Latest Growth Agent run')).toBeVisible();
  await expect(page.getByText('Broad opportunity', { exact: true })).toBeVisible();
  await expect(page.getByText('Eligible subset', { exact: true })).toBeVisible();
  await expect(page.getByText('Tool timeline', { exact: true })).toBeVisible();
  await expect(page.getByText('Policy checks', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Growth Agent governance proof')).toContainText('PII-safe output');
  await expect(page.getByText(new RegExp(`Trace ${run.trace_id.slice(-12)}`, 'i'))).toBeVisible();
  await expect(page.getByText(new RegExp(`Hash ${run.tool_result_hash.slice(0, 12)}`))).toBeVisible();
  await expect(page.getByLabel('Saved Growth Agent monitors')).toContainText(
    'Daily Refi Opportunity Brief',
  );

  await expectLeadQueueHandoffMatchesActionableTotal(request, run);

  await page.getByRole('button', { name: 'Open eligible refi subset' }).click();
  await expect(page).toHaveURL(/\/lead-queue\?/);
  await expect(page.getByRole('heading', { name: /Ranked borrowers/i })).toBeVisible();
  const landed = new URL(page.url());
  expect(landed.searchParams.get('marketing_eligibility')).toBe('Eligible only');
  expect(landed.searchParams.get('segment')).toBe('itm');
  expect(landed.searchParams.get('states')).toBe('IL,TX');
});

test('custom Growth Agent Any and All workflows reconcile to live Lead Queue totals', async ({
  page,
  request,
}) => {
  await gotoApp(page, '/ask-genie');
  await expect(page.getByLabel('Build a custom Growth Agent workflow')).toBeVisible();

  await page.getByLabel('Growth Agent state scope').fill('IL TX');
  const customAnyResponsePromise = page.waitForResponse((response) =>
    response.request().method() === 'POST' &&
    /\/api(?:\/v1)?\/growth-agent\/custom\/run/.test(response.url()),
  );
  await page.getByRole('button', { name: 'Run custom' }).click();
  const customAnyResponse = await customAnyResponsePromise;
  expect(customAnyResponse.status(), 'custom Any workflow run returned non-200').toBe(200);
  const customAny = (await customAnyResponse.json()) as GrowthAgentRunResponse;

  expect(customAny.workflow.id).toBe('custom_segment_watch');
  expect(customAny.criteria.lead_queue_filters?.segment_codes).toEqual(['itm', 'listed']);
  expect(customAny.criteria.lead_queue_filters?.segment_mode).toBe('any');
  expect(customAny.route).toContain('segment_mode=any');
  expect(customAny.route).toContain('marketing_eligibility=Eligible+only');
  expect(customAny.broad_total).toBeGreaterThanOrEqual(customAny.actionable_total);
  expect(customAny.policy_checks.map((check) => check.label)).toEqual(
    expect.arrayContaining(['No outbound activation', 'Reviewed custom workflow']),
  );
  await expectLeadQueueHandoffMatchesActionableTotal(request, customAny);

  await page.getByRole('button', { name: 'Open eligible custom subset' }).click();
  await expect(page).toHaveURL(/\/lead-queue\?/);
  const anyUrl = new URL(page.url());
  expect(anyUrl.searchParams.get('segment_codes')).toBe('itm,listed');
  expect(anyUrl.searchParams.get('segment_mode')).toBe('any');
  expect(anyUrl.searchParams.get('marketing_eligibility')).toBe('Eligible only');

  const allResponse = await request.post(`${API_URL}/api/growth-agent/custom/run`, {
    headers: AUTH_HEADERS,
    data: {
      states: ['IL', 'TX'],
      segment_codes: ['itm', 'listed'],
      segment_mode: 'all',
      save_monitor: false,
    },
  });
  expect(allResponse.status(), 'custom All workflow run returned non-200').toBe(200);
  const customAll = (await allResponse.json()) as GrowthAgentRunResponse;

  expect(customAll.workflow.id).toBe('custom_segment_watch');
  expect(customAll.criteria.lead_queue_filters?.segment_codes).toEqual(['itm', 'listed']);
  expect(customAll.criteria.lead_queue_filters?.segment_mode).toBe('all');
  expect(customAll.route).toContain('segment_mode=all');
  expect(customAll.broad_total).toBeGreaterThanOrEqual(customAll.actionable_total);
  await expectLeadQueueHandoffMatchesActionableTotal(request, customAll);
});

test('natural-language Mortgage Growth Agent routes to reviewed tools and reconciled handoff', async ({
  page,
  request,
}) => {
  await gotoApp(page, '/ask-genie');

  await page.getByLabel('Growth Agent state scope').fill('IL');
  await page
    .getByLabel('Mortgage Growth Agent prompt')
    .fill('Find prime refinance opportunities for a branch manager review.');

  const promptRunPromise = page.waitForResponse((response) =>
    response.request().method() === 'POST' &&
    /\/api(?:\/v1)?\/growth-agent\/agent\/run/.test(response.url()),
  );
  await page.getByRole('button', { name: 'Plan and run' }).click();
  const promptRunResponse = await promptRunPromise;
  expect(promptRunResponse.status(), 'prompt-routed Growth Agent run returned non-200').toBe(200);
  const run = (await promptRunResponse.json()) as GrowthAgentRunResponse;

  expect(run.workflow.id).toBe('branch_capacity_review');
  expect(run.specialist_agent).toBe('campaign_agent');
  expect(run.interpreted_intent).toContain('branch-manager capacity review');
  expect(run.trace_id).toMatch(/^agent-trace-/);
  expect(run.tool_result_hash).toMatch(/^[a-f0-9]{64}$/);
  expect(run.broad_total).toBeGreaterThanOrEqual(run.actionable_total);
  expect(run.route).toContain('/lead-queue?');
  expect(run.route).toContain('approval_status=approved');
  expect(run.route).toContain('aged_days=7');
  expect(run.policy_checks.map((check) => check.label)).toEqual(
    expect.arrayContaining(['No outbound activation', 'Manager review only']),
  );
  expect(run.governance_chips.map((chip) => chip.label)).toEqual(
    expect.arrayContaining(['PII-safe output', 'Human approval required']),
  );
  await expect(page.getByText('Campaign Agent', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Growth Agent governance proof')).toContainText('Human approval required');
  await expectLeadQueueHandoffMatchesActionableTotal(request, run);

  await page
    .getByLabel('Mortgage Growth Agent prompt')
    .fill('Find prime refinance opportunities for a branch manager monitor.');
  const promptSavePromise = page.waitForResponse((response) =>
    response.request().method() === 'POST' &&
    /\/api(?:\/v1)?\/growth-agent\/agent\/run/.test(response.url()),
  );
  await page.getByRole('button', { name: 'Plan and save monitor' }).click();
  const promptSaveResponse = await promptSavePromise;
  expect(promptSaveResponse.status(), 'prompt-routed Growth Agent save returned non-200').toBe(200);
  const savedRun = (await promptSaveResponse.json()) as GrowthAgentRunResponse;
  expect(savedRun.monitor?.name).toContain('Mortgage Growth Agent');
  expect(savedRun.monitor?.actionable_total).toBe(savedRun.actionable_total);
  expect(savedRun.monitor?.name.toLowerCase()).not.toContain('find prime refinance');
  await expect(page.getByLabel('Saved Growth Agent monitors')).toContainText(savedRun.monitor?.name ?? '');
  await expectLeadQueueHandoffMatchesActionableTotal(request, savedRun);

  const monitorsResponse = await request.get(`${API_URL}/api/growth-agent/monitors`, {
    headers: AUTH_HEADERS,
  });
  expect(monitorsResponse.status(), 'Growth Agent monitors route returned non-200').toBe(200);
  const monitors = (await monitorsResponse.json()) as GrowthAgentMonitorResponse[];
  const persistedMonitor = monitors.find((monitor) =>
    monitor.last_run_id === savedRun.run_id || monitor.name === savedRun.monitor?.name,
  );
  expect(persistedMonitor, 'saved prompt monitor was not returned by monitor list').toBeTruthy();
  const persistedText = JSON.stringify(persistedMonitor).toLowerCase();
  expect(persistedText).not.toContain('find prime refinance');
  expect(persistedMonitor?.criteria).toMatchObject({
    lead_queue_filters: expect.objectContaining({
      states: ['IL'],
      approval_status: 'approved',
      aged_days: '7',
    }),
  });
});

test('natural-language dossier and data-ops specialists use governed traces and safe handoffs', async ({
  page,
  request,
}) => {
  await gotoApp(page, '/ask-genie');

  await page
    .getByLabel('Mortgage Growth Agent prompt')
    .fill('Prepare borrower story dossiers for the top opportunities.');
  const dossierRunPromise = page.waitForResponse((response) =>
    response.request().method() === 'POST' &&
    /\/api(?:\/v1)?\/growth-agent\/agent\/run/.test(response.url()),
  );
  await page.getByRole('button', { name: 'Plan and run' }).click();
  const dossierRunResponse = await dossierRunPromise;
  expect(dossierRunResponse.status(), 'dossier prompt run returned non-200').toBe(200);
  const dossierRun = (await dossierRunResponse.json()) as GrowthAgentRunResponse;

  expect(dossierRun.workflow.id).toBe('borrower_dossier_review');
  expect(dossierRun.specialist_agent).toBe('borrower_dossier_agent');
  expect(dossierRun.criteria.lead_queue_filters?.funnel_stage).toBe('high_opportunity');
  expect(dossierRun.route).toContain('funnel_stage=high_opportunity');
  expect(dossierRun.source_assets).toEqual(
    expect.arrayContaining(['mip.gold.borrower_dossier', 'mip.gold.evidence_events']),
  );
  expect(dossierRun.tool_steps.map((step) => step.tool_name)).toEqual(
    expect.arrayContaining(['fn_borrower_dossier_evidence', 'fn_lead_queue_url']),
  );
  expect(dossierRun.policy_checks.map((check) => check.label)).toEqual(
    expect.arrayContaining(['Dossier privacy', 'Broad vs actionable reconciliation']),
  );
  await expect(page.getByText('Borrower Dossier Agent', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Growth Agent governance proof')).toContainText('PII-safe output');
  await expectLeadQueueHandoffMatchesActionableTotal(request, dossierRun);

  await page
    .getByLabel('Mortgage Growth Agent prompt')
    .fill('Check source readiness and data freshness before a lender walkthrough.');
  const sourceRunPromise = page.waitForResponse((response) =>
    response.request().method() === 'POST' &&
    /\/api(?:\/v1)?\/growth-agent\/agent\/run/.test(response.url()),
  );
  await page.getByRole('button', { name: 'Plan and run' }).click();
  const sourceRunResponse = await sourceRunPromise;
  expect(sourceRunResponse.status(), 'source sentinel prompt run returned non-200').toBe(200);
  const sourceRun = (await sourceRunResponse.json()) as GrowthAgentRunResponse;

  expect(sourceRun.workflow.id).toBe('source_freshness_sentinel');
  expect(sourceRun.specialist_agent).toBe('data_ops_agent');
  expect(sourceRun.route).toContain('/admin-config?panel=data-operations');
  expect(sourceRun.source_assets).toEqual(['mip.gold.source_readiness']);
  expect(sourceRun.tool_steps.map((step) => step.tool_name)).toEqual([
    'fn_source_readiness',
    'source_readiness_status_rollup',
    'open_admin_data_operations',
  ]);
  expect(sourceRun.governance_chips.map((chip) => chip.label)).toEqual(
    expect.arrayContaining(['PII-safe output', 'Freshness signal']),
  );
  await expect(page.getByText('Data Ops Agent', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Open data operations' }).click();
  await expect(page).toHaveURL(/\/admin-config\?panel=data-operations/);
  await expect(page.getByText(/Data operations/i).first()).toBeVisible();
});
