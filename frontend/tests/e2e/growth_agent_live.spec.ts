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
  execution_mode: string;
  trace_kind: string;
  planner_label: string;
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
  agent_reasoning?: string | null;
  genie_conversation_id?: string | null;
  genie_row_count?: number | null;
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

type AuditRow = {
  event_id: string;
  event_type?: string | null;
  payload_json?: Record<string, unknown>;
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

async function expectAuditRowMatchesGrowthRun(
  request: APIRequestContext,
  run: GrowthAgentRunResponse,
): Promise<void> {
  const resp = await request.get(`${API_URL}/api/audit/events?limit=100&event_type=GROWTH_AGENT_RUN`, {
    headers: AUTH_HEADERS,
  });
  expect(resp.status(), 'GET /api/audit/events for Growth Agent returned non-200').toBe(200);
  const rows = (await resp.json()) as AuditRow[];
  const row = rows.find((item) => item.event_id === run.audit_event_id);
  expect(row, `audit event ${run.audit_event_id} not found`).toBeTruthy();
  const payload = row?.payload_json ?? {};
  expect(payload.workflow_id).toBe(run.workflow.id);
  expect(payload.trace_id).toBe(run.trace_id);
  expect(payload.tool_result_hash).toBe(run.tool_result_hash);
  expect(payload.route).toBe(run.route);
  expect(payload.source_assets).toEqual(expect.arrayContaining(run.source_assets));
  expect(payload.result_filters).toMatchObject(run.criteria.lead_queue_filters ?? {});
  const payloadText = JSON.stringify(payload).toLowerCase();
  for (const forbidden of ['borrower_id', 'subject_clip', 'owner_name', 'raw_clip', 'street']) {
    expect(payloadText).not.toContain(forbidden);
  }
}

test('Growth Agent run, saved watchlist, and Lead Queue handoff are live and reconciled', async ({
  page,
  request,
}) => {
  await gotoApp(page, '/ask-genie');

  await expect(page.getByText('Mortgage Growth Agent').first()).toBeVisible();
  await expect(page.getByText('Borrower Dossier Review')).toBeVisible();
  await expect(page.getByText('No auto-send · no scheduled automation')).toBeVisible();
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
  for (const button of await page.getByRole('button', { name: /Save (watchlist|custom watchlist)/ }).all()) {
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
  await dailyCard.getByRole('button', { name: 'Save watchlist' }).click();
  const runResponse = await runResponsePromise;
  expect(runResponse.status(), 'Growth Agent workflow run returned non-200').toBe(200);
  const run = (await runResponse.json()) as GrowthAgentRunResponse;

  expect(run.run_id).toBeTruthy();
  expect(run.audit_event_id).toBeTruthy();
  expect(run.workflow.id).toBe('daily_refi_brief');
  expect(run.specialist_agent).toBe('structured_data_agent');
  expect(run.execution_mode).toBe('deterministic');
  expect(run.trace_kind).toBe('local_hash');
  expect(run.planner_label).toBe('Reviewed workflow runner');
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
      'Watchlist saved to Lakebase',
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
  await expect(
    page.locator('.growth-agent-run__section').filter({ hasText: 'Policy checks' }).first(),
  ).toBeVisible();
  await expect(page.getByLabel('Growth Agent governance proof')).toContainText('PII-safe output');
  await expect(page.getByText(new RegExp(`Run correlation ${run.trace_id.slice(-12)}`, 'i'))).toBeVisible();
  await expect(page.getByText(new RegExp(`Hash ${run.tool_result_hash.slice(0, 12)}`))).toBeVisible();
  await expect(page.getByLabel('Saved Growth Agent watchlists')).toContainText(
    'Daily Refi Opportunity Brief',
  );

  await expectLeadQueueHandoffMatchesActionableTotal(request, run);
  await expectAuditRowMatchesGrowthRun(request, run);

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
  await expectAuditRowMatchesGrowthRun(request, customAny);

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
  await expectAuditRowMatchesGrowthRun(request, customAll);
});

test('remaining reviewed workflow cards reconcile to live Lead Queue totals', async ({ request }) => {
  for (const workflowId of [
    'listing_watch',
    'competitor_recapture_monitor',
    'high_equity_heloc_watch',
  ]) {
    const response = await request.post(`${API_URL}/api/growth-agent/workflows/${workflowId}/run`, {
      headers: AUTH_HEADERS,
      data: {
        states: ['IL', 'TX'],
        save_monitor: false,
      },
    });
    expect(response.status(), `${workflowId} returned non-200`).toBe(200);
    const run = (await response.json()) as GrowthAgentRunResponse;
    expect(run.workflow.id).toBe(workflowId);
    expect(run.actionable_total).toBeGreaterThanOrEqual(0);
    expect(run.route).toContain('/lead-queue?');
    expect(run.policy_checks.map((check) => check.label)).toEqual(
      expect.arrayContaining(['No outbound activation', 'Broad vs actionable reconciliation']),
    );
    expect(run.governance_chips.map((chip) => chip.label)).toEqual(
      expect.arrayContaining(['PII-safe output', 'Human approval required', 'Policy checks']),
    );
    await expectLeadQueueHandoffMatchesActionableTotal(request, run);
    await expectAuditRowMatchesGrowthRun(request, run);
  }
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
  await page.getByRole('button', { name: 'Plan reviewed workflow' }).click();
  const promptRunResponse = await promptRunPromise;
  expect(promptRunResponse.status(), 'prompt-routed Growth Agent run returned non-200').toBe(200);
  const run = (await promptRunResponse.json()) as GrowthAgentRunResponse;

  expect(run.workflow.id).toBe('branch_capacity_review');
  expect(run.specialist_agent).toBe('campaign_agent');
  expect(run.execution_mode).toBe('deterministic');
  expect(run.trace_kind).toBe('local_hash');
  expect(run.agent_reasoning ?? '').toContain('No model-generated SQL');
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
  await expect(page.getByText(/Owner:\s*Campaign lens/)).toBeVisible();
  await expect(page.getByLabel('Growth Agent governance proof')).toContainText('Human approval required');
  await expectLeadQueueHandoffMatchesActionableTotal(request, run);
  await expectAuditRowMatchesGrowthRun(request, run);

  await page
    .getByLabel('Mortgage Growth Agent prompt')
    .fill('Find prime refinance opportunities for a branch manager monitor.');
  const promptSavePromise = page.waitForResponse((response) =>
    response.request().method() === 'POST' &&
    /\/api(?:\/v1)?\/growth-agent\/agent\/run/.test(response.url()),
  );
  await page.getByRole('button', { name: 'Save reviewed watchlist' }).click();
  const promptSaveResponse = await promptSavePromise;
  expect(promptSaveResponse.status(), 'prompt-routed Growth Agent save returned non-200').toBe(200);
  const savedRun = (await promptSaveResponse.json()) as GrowthAgentRunResponse;
  expect(savedRun.monitor?.name).toContain('Mortgage Growth Agent');
  expect(savedRun.monitor?.actionable_total).toBe(savedRun.actionable_total);
  expect(savedRun.monitor?.name.toLowerCase()).not.toContain('find prime refinance');
  await expect(page.getByLabel('Saved Growth Agent watchlists')).toContainText(savedRun.monitor?.name ?? '');
  await expectLeadQueueHandoffMatchesActionableTotal(request, savedRun);
  await expectAuditRowMatchesGrowthRun(request, savedRun);

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
  await page.getByRole('button', { name: 'Plan reviewed workflow' }).click();
  const dossierRunResponse = await dossierRunPromise;
  expect(dossierRunResponse.status(), 'dossier prompt run returned non-200').toBe(200);
  const dossierRun = (await dossierRunResponse.json()) as GrowthAgentRunResponse;

  expect(dossierRun.workflow.id).toBe('borrower_dossier_review');
  expect(dossierRun.specialist_agent).toBe('borrower_dossier_agent');
  expect(dossierRun.execution_mode).toBe('deterministic');
  expect(dossierRun.trace_kind).toBe('local_hash');
  expect(dossierRun.agent_reasoning ?? '').toContain('No model-generated SQL');
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
  await expect(page.getByText(/Owner:\s*Borrower dossier lens/)).toBeVisible();
  await expect(page.getByLabel('Growth Agent governance proof')).toContainText('PII-safe output');
  await expectLeadQueueHandoffMatchesActionableTotal(request, dossierRun);
  await expectAuditRowMatchesGrowthRun(request, dossierRun);

  await page
    .getByLabel('Mortgage Growth Agent prompt')
    .fill('Check source readiness and data freshness before a lender walkthrough.');
  const sourceRunPromise = page.waitForResponse((response) =>
    response.request().method() === 'POST' &&
    /\/api(?:\/v1)?\/growth-agent\/agent\/run/.test(response.url()),
  );
  await page.getByRole('button', { name: 'Plan reviewed workflow' }).click();
  const sourceRunResponse = await sourceRunPromise;
  expect(sourceRunResponse.status(), 'source sentinel prompt run returned non-200').toBe(200);
  const sourceRun = (await sourceRunResponse.json()) as GrowthAgentRunResponse;

  expect(sourceRun.workflow.id).toBe('source_freshness_sentinel');
  expect(sourceRun.specialist_agent).toBe('data_ops_agent');
  expect(sourceRun.execution_mode).toBe('deterministic');
  expect(sourceRun.trace_kind).toBe('local_hash');
  expect(sourceRun.agent_reasoning ?? '').toContain('No model-generated SQL');
  expect(sourceRun.route).toContain('/admin-config?panel=data-operations');
  expect(sourceRun.source_assets).toEqual(['mip.gold.source_readiness']);
  expect(sourceRun.tool_steps.map((step) => step.tool_name)).toEqual(
    expect.arrayContaining([
      'fn_source_readiness',
      'source_readiness_status_rollup',
      'open_admin_data_operations',
    ]),
  );
  expect(sourceRun.governance_chips.map((chip) => chip.label)).toEqual(
    expect.arrayContaining(['PII-safe output', 'Freshness signal']),
  );
  expect(sourceRun.route).toBe('/admin-config?panel=data-operations');
  expect(sourceRun.criteria.lead_queue_filters?.source).toBe('trusted_sql');
  expect(sourceRun.criteria.lead_queue_filters?.portfolio_criteria).toMatchObject({
    marketing_eligibility: 'Eligible only',
  });
  expect(sourceRun.criteria.lead_queue_filters?.states).toBeUndefined();
  await expectAuditRowMatchesGrowthRun(request, sourceRun);
  await expect(page.getByText(/Owner:\s*Data operations lens/)).toBeVisible();
  await page.getByRole('button', { name: 'Open data operations' }).click();
  await expect(page).toHaveURL(/\/admin-config\?panel=data-operations/);
  await expect(page.getByText(/Data operations/i).first()).toBeVisible();
});
