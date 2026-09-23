/**
 * S2 loan-officer assignment lifecycle — live regression.
 *
 * Same opt-in contract as the other nightly real-data specs: set
 * E2E_LIVE=1 (plus MIP_APP_URL / MIP_API_URL / bearer) to run against a
 * deployed app on real Unity Catalog + Lakebase data.
 *
 * Flow under test: assign a lead to a seeded loan officer via the S2
 * endpoint -> the Lead Queue names the lifecycle stage -> advancing the
 * lifecycle updates it -> an illegal transition is rejected with 409.
 *
 * Where the stage lives (audit tables-04, one-line rows): the Default
 * view's merged Status cell may fold the assignment into `+n` behind a
 * newer outreach or call state, so the spec reads the Sales ops view,
 * whose Assigned-to cell shows the assignee chip with the stage as its
 * `+n` ("1 more assignment detail: Stage: Assigned"), and then the
 * expanded row's workflow strip, which carries the lifecycle stage chip
 * and its advance control. The same locators are proven against the
 * rendered fixture DOM in fixture/queue-layout.fixture.spec.ts.
 */

import { randomUUID } from 'node:crypto';
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 to run loan-officer assignment regressions against the deployed app.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const API_URL = process.env.MIP_API_URL || APP_URL.replace(':5173', ':8000');
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER
  ? { Authorization: `Bearer ${BEARER}` }
  : {};

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

type LoanOfficer = {
  loan_officer_id: string;
  email: string;
  coverage_states: string[];
  coverage_counties: string[];
};

async function fetchSeededOfficers(request: APIRequestContext): Promise<LoanOfficer[]> {
  const resp = await request.get(`${API_URL}/api/loan-officers`, { headers: AUTH_HEADERS });
  expect(resp.status(), 'GET /api/loan-officers').toBe(200);
  return (await resp.json()) as LoanOfficer[];
}

async function fetchLeadForAssignment(request: APIRequestContext): Promise<string> {
  const resp = await request.get(`${API_URL}/api/leads?limit=25`, { headers: AUTH_HEADERS });
  expect(resp.status(), 'GET /api/leads for assignment target').toBe(200);
  const rows = (await resp.json()) as Array<{ borrower_id?: string }>;
  expect(rows.length, 'need live leads to assign').toBeGreaterThan(0);
  return rows[0].borrower_id!;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Open the lead in the Sales ops view and assert its lifecycle stage in-row and in the workflow strip. */
async function expectLifecycleStage(page: Page, borrowerId: string, stage: string): Promise<void> {
  await page.goto(`/lead-queue?view=sales-ops&borrower_ids=${encodeURIComponent(borrowerId)}`, {
    waitUntil: 'domcontentloaded',
  });
  const row = page.locator('tr', { has: page.getByTestId(`lead-select-${borrowerId}`) });
  await expect(row).toBeVisible({ timeout: 30_000 });
  const assignment = row.getByTestId(`lead-assignment-${borrowerId}`);
  await expect(
    assignment.getByRole('button', { name: new RegExp(`Stage: ${escapeRegExp(stage)}$`) }),
    `the Assigned-to cell names the ${stage} stage`,
  ).toBeVisible();

  await page.getByRole('button', { name: `Toggle preview for lead ${borrowerId}` }).click();
  const workflow = page.getByTestId(`lead-workflow-${borrowerId}`);
  await expect(workflow).toBeVisible();
  await expect(
    workflow.locator('.chip__label', { hasText: new RegExp(`^${escapeRegExp(stage)}$`) }),
    `the expanded row's workflow strip shows the ${stage} chip`,
  ).toBeVisible();
}

test('seeded loan-officer roster exposes array coverage', async ({ request }) => {
  const officers = await fetchSeededOfficers(request);
  expect(officers.length, 'deploy seed provisions six loan officers').toBeGreaterThanOrEqual(6);
  for (const officer of officers) {
    expect(officer.coverage_states.length).toBeGreaterThan(0);
    expect(officer.coverage_counties.length).toBeGreaterThan(0);
    for (const county of officer.coverage_counties) {
      expect(county).toMatch(/^\d{5}$/);
    }
  }
});

test('assign a lead -> lifecycle chip appears in the Lead Queue and advances', async ({ page, request }) => {
  const officers = await fetchSeededOfficers(request);
  const officer = officers[0];
  const borrowerId = await fetchLeadForAssignment(request);

  const assigned = await request.post(`${API_URL}/api/loan-officers/assignments`, {
    headers: AUTH_HEADERS,
    data: {
      borrower_id: borrowerId,
      loan_officer_id: officer.loan_officer_id,
      request_id: randomUUID(),
    },
  });
  expect(assigned.status(), 'POST /api/loan-officers/assignments').toBe(200);
  const body = (await assigned.json()) as {
    assignment: { assignment_id: string; status: string };
    audit_event_id?: string | null;
  };
  expect(body.assignment.status).toBe('assigned');
  expect(body.audit_event_id, 'assign must write an audit row').toBeTruthy();

  // The stage appears for the assigned lead in the Lead Queue.
  await expectLifecycleStage(page, borrowerId, 'Assigned');

  // Illegal jump is rejected; the lifecycle is enforced server-side.
  const illegal = await request.patch(
    `${API_URL}/api/loan-officers/assignments/${body.assignment.assignment_id}/status`,
    { headers: AUTH_HEADERS, data: { status: 'actioned', request_id: randomUUID() } },
  );
  expect(illegal.status(), 'assigned -> actioned must be rejected').toBe(409);

  // One legal step forward, then the chip reflects the new stage.
  const advanced = await request.patch(
    `${API_URL}/api/loan-officers/assignments/${body.assignment.assignment_id}/status`,
    { headers: AUTH_HEADERS, data: { status: 'contact_drafted', request_id: randomUUID() } },
  );
  expect(advanced.status(), 'assigned -> contact_drafted').toBe(200);
  const advancedBody = (await advanced.json()) as {
    assignment: { status: string };
    audit_event_id?: string | null;
  };
  expect(advancedBody.assignment.status).toBe('contact_drafted');
  expect(advancedBody.audit_event_id, 'status transition must write an audit row').toBeTruthy();

  await expectLifecycleStage(page, borrowerId, 'Contact drafted');
});
