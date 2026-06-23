import { randomUUID } from 'node:crypto';
import { expect, test, type APIRequestContext } from '@playwright/test';

const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 to run closed-loop regressions against the deployed app.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const API_URL = process.env.MIP_API_URL || APP_URL.replace(':5173', ':8000');
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER
  ? { Authorization: `Bearer ${BEARER}` }
  : {};

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

type LeadRow = {
  borrower_id?: string;
  assigned_to_email?: string | null;
};

async function fetchLeadForSalesState(request: APIRequestContext): Promise<string> {
  const resp = await request.get(`${API_URL}/api/leads?limit=100`, { headers: AUTH_HEADERS });
  expect(resp.status(), 'GET /api/leads for sales-state regression target').toBe(200);
  const rows = (await resp.json()) as LeadRow[];
  const candidate = rows.find((row) => !row.assigned_to_email || row.assigned_to_email === 'lo01@summit.example')
    ?? rows[0];
  expect(candidate?.borrower_id, 'need a live borrower for closed-loop regression probes').toBeTruthy();
  return candidate.borrower_id!;
}

async function outcomeSummary(request: APIRequestContext) {
  const today = new Date();
  const from = new Date(today.getTime() - 7 * 24 * 60 * 60 * 1000);
  const resp = await request.get(
    `${API_URL}/api/sales/outcomes/summary?from=${from.toISOString().slice(0, 10)}&to=${today.toISOString().slice(0, 10)}`,
    { headers: AUTH_HEADERS },
  );
  expect(resp.status(), 'GET /api/sales/outcomes/summary').toBe(200);
  return await resp.json() as {
    top_competitors?: Array<{ competitor_lender_label?: string }>;
  };
}

test('closed-loop outcome labels reject PII/raw names and summarize only governed aliases', async ({ request }) => {
  const borrowerId = await fetchLeadForSalesState(request);

  for (const competitor_lender_label of ['John Smith', 'Maria Garcia', 'Rocket Mortgage']) {
    const blocked = await request.post(`${API_URL}/api/leads/${borrowerId}/outcome`, {
      headers: AUTH_HEADERS,
      data: {
        outcome_type: 'lost_to_competitor',
        source_system: 'manual_import',
        source_record_ref: `manual_${randomUUID()}`,
        competitor_lender_label,
        request_id: randomUUID(),
      },
    });
    expect(blocked.status(), `${competitor_lender_label} must not be accepted as public competitor label`).toBe(422);
  }

  const requestId = randomUUID();
  const accepted = await request.post(`${API_URL}/api/leads/${borrowerId}/outcome`, {
    headers: AUTH_HEADERS,
    data: {
      outcome_type: 'lost_to_competitor',
      source_system: 'manual_import',
      source_record_ref: `manual_${requestId}`,
      competitor_lender_label: 'Competitor D',
      request_id: requestId,
    },
  });
  expect(accepted.status(), 'governed competitor alias should be accepted').toBe(200);

  const summary = await outcomeSummary(request);
  for (const row of summary.top_competitors ?? []) {
    expect(
      row.competitor_lender_label,
      'top competitor summaries must expose governed aliases only',
    ).toMatch(/^Competitor ([A-Z]|Other)$/);
  }
});

test('duplicate disposition request ids replay without opening the Lakebase breaker', async ({ request }) => {
  const borrowerId = await fetchLeadForSalesState(request);
  const requestId = randomUUID();
  const payload = {
    lo_email: 'lo01@summit.example',
    outcome: 'connected',
    notes: 'Reviewed scenario and next steps.',
    request_id: requestId,
  };

  const first = await request.post(`${API_URL}/api/leads/${borrowerId}/disposition`, {
    headers: AUTH_HEADERS,
    data: payload,
  });
  expect(first.status(), 'first disposition write').toBe(200);
  const firstBody = await first.json();

  const replay = await request.post(`${API_URL}/api/leads/${borrowerId}/disposition`, {
    headers: AUTH_HEADERS,
    data: payload,
  });
  expect(replay.status(), 'duplicate disposition replay').toBe(200);
  const replayBody = await replay.json();
  expect(replayBody.disposition.disposition_id).toBe(firstBody.disposition.disposition_id);

  const mismatch = await request.post(`${API_URL}/api/leads/${borrowerId}/disposition`, {
    headers: AUTH_HEADERS,
    data: { ...payload, outcome: 'not_now' },
  });
  expect(mismatch.status(), 'same request id with different disposition must be a client conflict').toBe(409);

  const health = await request.get(`${API_URL}/api/health`, { headers: AUTH_HEADERS });
  expect(health.status(), 'duplicate disposition must not trip health to 503').toBe(200);
  const leads = await request.get(`${API_URL}/api/leads?limit=1`, { headers: AUTH_HEADERS });
  expect(leads.status(), 'duplicate disposition must not trip Lakebase-backed lead reads').toBe(200);
});

test('simultaneous outcome idempotency race produces one durable outcome without a breaker cascade', async ({ request }) => {
  const borrowerId = await fetchLeadForSalesState(request);
  const requestId = randomUUID();
  const data = {
    outcome_type: 'application_submitted',
    source_system: 'manual_import',
    source_record_ref: `manual_${requestId}`,
    request_id: requestId,
  };

  const [first, second] = await Promise.all([
    request.post(`${API_URL}/api/leads/${borrowerId}/outcome`, { headers: AUTH_HEADERS, data }),
    request.post(`${API_URL}/api/leads/${borrowerId}/outcome`, { headers: AUTH_HEADERS, data }),
  ]);
  expect([first.status(), second.status()].sort()).toEqual([200, 200]);
  const firstBody = await first.json();
  const secondBody = await second.json();
  expect(firstBody.outcome.outcome_id).toBe(secondBody.outcome.outcome_id);

  const mismatch = await request.post(`${API_URL}/api/leads/${borrowerId}/outcome`, {
    headers: AUTH_HEADERS,
    data: { ...data, outcome_type: 'withdrawn' },
  });
  expect(mismatch.status(), 'same outcome idempotency key with changed payload').toBe(409);

  const health = await request.get(`${API_URL}/api/health`, { headers: AUTH_HEADERS });
  expect(health.status(), 'outcome race must not trip health to 503').toBe(200);
});

test('Movement filters survive Portfolio to Segment to Lead Queue browser flow', async ({ page }) => {
  await page.goto('/portfolio-builder?owner_link=Portfolio+investor+%285%2B%29&purchase_intent=HELOC+intent');
  await page.getByRole('link', { name: /Next: (?:segment intelligence|segments)/i }).click();
  await expect(page.getByRole('button', { name: /OWNER LINK: Portfolio investor \(5\+\)/i })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: /PURCHASE INTENT: HELOC intent/i })).toBeVisible();

  const leadsResponse = page.waitForResponse((response) => {
    if (response.status() !== 200) return false;
    const parsed = new URL(response.url());
    if (!/\/api\/(?:v1\/)?leads$/.test(parsed.pathname)) return false;
    return parsed.searchParams.get('owner_link') === 'Portfolio investor (5+)'
      && parsed.searchParams.get('purchase_intent') === 'HELOC intent';
  });
  await page.getByRole('link', { name: /Deep-dive lead queue/i }).click();
  await leadsResponse;
  await expect(page).toHaveURL(/\/lead-queue\?/);
  await expect(page.getByRole('button', { name: /OWNER LINK: Portfolio investor \(5\+\)/i })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole('button', { name: /PURCHASE INTENT: HELOC intent/i })).toBeVisible();
  await expect(page.getByText('withdrawn', { exact: true })).toBeVisible({ timeout: 45_000 });
  await expect(page.getByText('not qualified', { exact: true })).toBeVisible();
});
