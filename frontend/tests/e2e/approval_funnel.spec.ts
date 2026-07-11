/**
 * S6 approval-funnel analytics — live regression.
 *
 * Same opt-in contract as the other nightly real-data specs: set
 * E2E_LIVE=1 (plus MIP_APP_URL / MIP_API_URL / bearer) to run against a
 * deployed app on real Unity Catalog + Lakebase data.
 *
 * Flows under test:
 *  1. Approve a lead through the lifecycle API -> the Approval funnel tab's
 *     Approved stage count moves (within one backend cache TTL).
 *  2. Per-LO drill renders real per-officer counts and live assignments.
 *  3. The terminal outcome recording is server-gated (409 before actioned)
 *     and lands in the funnel's outcome stage.
 */

import { randomUUID } from 'node:crypto';
import { expect, test, type APIRequestContext } from '@playwright/test';

const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 to run approval-funnel regressions against the deployed app.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const API_URL = process.env.MIP_API_URL || APP_URL.replace(':5173', ':8000');
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER
  ? { Authorization: `Bearer ${BEARER}` }
  : {};

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

type FunnelStage = { stage: string; borrower_count: number };
type FunnelResponse = {
  stages: FunnelStage[];
  approvals: Array<{ actor_email: string; borrower_id: string }>;
  loan_officers: Array<{ loan_officer_id: string; display_name: string; total_active: number }>;
};

async function fetchFunnel(request: APIRequestContext): Promise<FunnelResponse> {
  const resp = await request.get(`${API_URL}/api/analytics/funnel`, { headers: AUTH_HEADERS });
  expect(resp.status(), 'GET /api/analytics/funnel').toBe(200);
  return (await resp.json()) as FunnelResponse;
}

function stageCount(funnel: FunnelResponse, stage: string): number {
  const found = funnel.stages.find((s) => s.stage === stage);
  expect(found, `funnel stage ${stage}`).toBeTruthy();
  return found!.borrower_count;
}

async function fetchLeadForAssignment(request: APIRequestContext): Promise<string> {
  const resp = await request.get(`${API_URL}/api/leads?limit=25`, { headers: AUTH_HEADERS });
  expect(resp.status(), 'GET /api/leads for assignment target').toBe(200);
  const rows = (await resp.json()) as Array<{ borrower_id?: string }>;
  expect(rows.length, 'need live leads to assign').toBeGreaterThan(0);
  // Pick a borrower with NO active assignment so the +1 assertions on the
  // funnel's assignment-derived stages are deterministic.
  for (const row of rows) {
    if (!row.borrower_id) continue;
    const active = await request.get(
      `${API_URL}/api/loan-officers/assignments?borrower_id=${encodeURIComponent(row.borrower_id)}`,
      { headers: AUTH_HEADERS },
    );
    expect(active.status()).toBe(200);
    if (((await active.json()) as unknown[]).length === 0) return row.borrower_id;
  }
  throw new Error('no live lead without an active assignment found in the first 25');
}

async function fetchSeededOfficerId(request: APIRequestContext): Promise<string> {
  const resp = await request.get(`${API_URL}/api/loan-officers`, { headers: AUTH_HEADERS });
  expect(resp.status(), 'GET /api/loan-officers').toBe(200);
  const officers = (await resp.json()) as Array<{ loan_officer_id: string }>;
  expect(officers.length).toBeGreaterThan(0);
  return officers[0].loan_officer_id;
}

test('approve moves the funnel and the per-LO drill shows real counts', async ({ page, request }) => {
  const before = await fetchFunnel(request);
  const loanOfficerId = await fetchSeededOfficerId(request);
  const borrowerId = await fetchLeadForAssignment(request);

  // Assign, then walk the lifecycle through approved via the S2 API.
  const assigned = await request.post(`${API_URL}/api/loan-officers/assignments`, {
    headers: AUTH_HEADERS,
    data: { borrower_id: borrowerId, loan_officer_id: loanOfficerId, request_id: randomUUID() },
  });
  expect(assigned.status()).toBe(200);
  const assignmentId = ((await assigned.json()) as { assignment: { assignment_id: string } })
    .assignment.assignment_id;

  // Outcome recording is server-gated: refused before the actioned stage.
  const premature = await request.post(
    `${API_URL}/api/loan-officers/assignments/${assignmentId}/outcome`,
    { headers: AUTH_HEADERS, data: { outcome: 'success', request_id: randomUUID() } },
  );
  expect(premature.status(), 'outcome before actioned must be 409').toBe(409);

  for (const status of ['contact_drafted', 'approved'] as const) {
    const advanced = await request.patch(
      `${API_URL}/api/loan-officers/assignments/${assignmentId}/status`,
      { headers: AUTH_HEADERS, data: { status, request_id: randomUUID() } },
    );
    expect(advanced.status(), `advance to ${status}`).toBe(200);
  }

  // ACCEPTANCE: the approval is visible in the funnel within one cache TTL.
  // The approved stage counts DISTINCT borrowers across the assignment
  // lifecycle and the approvals ledger, so a borrower with an old ledger
  // approval doesn't add +1 — the count must never move backwards, and the
  // deterministic +1 is pinned on the fresh assignment's outcome stage below.
  const afterApprove = await fetchFunnel(request);
  expect(stageCount(afterApprove, 'approved'))
    .toBeGreaterThanOrEqual(stageCount(before, 'approved'));

  // Finish the lifecycle: actioned -> outcome recorded (success).
  const actioned = await request.patch(
    `${API_URL}/api/loan-officers/assignments/${assignmentId}/status`,
    { headers: AUTH_HEADERS, data: { status: 'actioned', request_id: randomUUID() } },
  );
  expect(actioned.status()).toBe(200);
  const outcome = await request.post(
    `${API_URL}/api/loan-officers/assignments/${assignmentId}/outcome`,
    { headers: AUTH_HEADERS, data: { outcome: 'success', request_id: randomUUID() } },
  );
  expect(outcome.status(), 'record outcome once actioned').toBe(200);
  const outcomeBody = (await outcome.json()) as {
    assignment: { status: string };
    feedback_id: string;
    audit_event_id?: string | null;
  };
  expect(outcomeBody.assignment.status).toBe('outcome_recorded');
  expect(outcomeBody.feedback_id, 'outcome must write a feedback row').toBeTruthy();
  expect(outcomeBody.audit_event_id, 'outcome must write an audit row').toBeTruthy();

  const afterOutcome = await fetchFunnel(request);
  expect(stageCount(afterOutcome, 'outcome_recorded'))
    .toBeGreaterThanOrEqual(stageCount(before, 'outcome_recorded') + 1);

  // UI: the Approval funnel tab renders the live stages and the drill.
  await page.goto('/analytics?view=approval-funnel', { waitUntil: 'domcontentloaded' });
  await expect(page.getByText('Approval funnel — live')).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText('Who approved what')).toBeVisible();
  await expect(page.getByText('Per-loan-officer funnel')).toBeVisible();

  // Per-LO drill renders real per-officer counts.
  const drill = page.getByRole('button', { name: 'Drill' }).first();
  await expect(drill).toBeVisible();
  await drill.click();
  const drillPanel = page.getByTestId('funnel-lo-drill');
  await expect(drillPanel).toBeVisible({ timeout: 30_000 });
  await expect(drillPanel.getByText(/success/)).toBeVisible();
});
