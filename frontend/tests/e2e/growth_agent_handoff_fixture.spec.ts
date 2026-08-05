import { expect, test, type Page, type Route } from '@playwright/test';

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const LIVE = process.env.E2E_LIVE === '1';
test.skip(LIVE, 'This spec uses route-fulfilled identity headers; live coverage is in growth_agent_live.spec.ts.');
test.use({ baseURL: APP_URL });

const RUN_ID = '11111111-1111-4111-8111-111111111111';
const SNAPSHOT_ID = '2026-07-14 12:00:00';
const TOOL_RESULT_HASH = 'a'.repeat(64);
const COHORT_DIGEST = 'b'.repeat(64);
const COHORT_FINGERPRINT = '8c91a6378bcc3cd62df18369faed832c2016d8343fdd85b9d298978eea7eb40d';
const SIGNED_HANDOFF = 'v1.non-admin-growth-agent-handoff.signature';

function normalizedApiPath(url: string): string {
  return new URL(url).pathname.replace(/^\/api\/v1(?=\/|$)/, '/api');
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

async function mockLeadQueue(page: Page, responseRunId = RUN_ID): Promise<string[]> {
  const leadRequests: string[] = [];
  await page.route('**/api/**', async (route) => {
    const path = normalizedApiPath(route.request().url());
    if (path === '/api/session') return fulfillJson(route, { can_access_admin: false });
    if (path === '/api/workspace') {
      return fulfillJson(route, { saved_leads: [], saved_drafts: [] });
    }
    if (path === '/api/health') {
      return fulfillJson(route, {
        status: 'ok',
        mode: 'live',
        dependencies: { warehouse: 'up', lakebase: 'up', genie: 'up' },
        circuit_breakers: { warehouse: 'closed', lakebase: 'closed', genie: 'closed' },
      });
    }
    if (path === '/api/config/options') {
      return fulfillJson(route, {
        lender_name: 'Summit Mortgage',
        rum_enabled: false,
        geographies: ['All states'],
        geographies_status: 'live',
        occupancy: ['All'],
        lien_status: ['Any'],
        lender_relationships: ['All'],
        products: ['All products'],
        equity_thresholds: ['Any'],
        target_lender_refs: ['All'],
        target_lender_refs_status: 'live',
      });
    }
    if (path === '/api/config/footprint') {
      return fulfillJson(route, { states: [], using_fallback: false, geography_scope: null });
    }
    if (path === '/api/sales/team') return fulfillJson(route, []);
    if (path === '/api/portfolio/preview') {
      return fulfillJson(route, { data_refreshed_at: '2026-07-14T12:00:00Z' });
    }
    if (path === '/api/admin/rules') {
      return fulfillJson(route, { offer_rules_version: 'fixture-v1' });
    }
    if (path === '/api/audit/my-events') {
      return fulfillJson(route, { items: [], next_cursor: null });
    }
    if (path === '/api/leads') {
      leadRequests.push(route.request().url());
      return fulfillJson(route, [], {
        'X-Total-Matching': '12',
        'X-Returned-Rows': '0',
        'X-Cohort-Digest': COHORT_DIGEST,
        'X-Cohort-Fingerprint': COHORT_FINGERPRINT,
        'X-Cohort-Snapshot-ID': SNAPSHOT_ID,
        'X-Growth-Agent-Run-ID': responseRunId,
      });
    }
    return route.fulfill({
      status: 501,
      contentType: 'application/json',
      body: JSON.stringify({ detail: `Unhandled handoff fixture API: ${path}` }),
    });
  });
  return leadRequests;
}

function proofRoute(): string {
  const params = new URLSearchParams({
    segment: 'itm',
    growth_agent_run_id: RUN_ID,
    actionable_total: '12',
    actionable_cohort_fingerprint: COHORT_FINGERPRINT,
    actionable_snapshot_id: SNAPSHOT_ID,
    tool_result_hash: TOOL_RESULT_HASH,
    growth_handoff: SIGNED_HANDOFF,
  });
  return `/lead-queue?${params.toString()}`;
}

test('renders verified provenance only after all Lead Queue identity headers reconcile', async ({ page }) => {
  const leadRequests = await mockLeadQueue(page);
  await page.goto(proofRoute());

  const proof = page.getByTestId('growth-agent-cohort-proof');
  await expect(proof).toBeVisible({ timeout: 30_000 });
  await expect(proof).toContainText('12 borrowers');
  await expect(proof).toContainText(`proof ${COHORT_FINGERPRINT.slice(0, 12)}`);
  await expect(proof).toContainText(`snapshot ${SNAPSHOT_ID}`);
  await expect(proof).toContainText(`run ${RUN_ID.slice(0, 12)}`);
  expect(leadRequests.length).toBeGreaterThan(0);
  for (const leadRequest of leadRequests) {
    const request = new URL(leadRequest);
    expect(request.searchParams.get('include_identity_proof')).toBe('true');
    expect(request.searchParams.get('growth_handoff')).toBe(SIGNED_HANDOFF);
  }
});

test('does not trust a proof-shaped URL when the backend run-id header differs', async ({ page }) => {
  await mockLeadQueue(page, '22222222-2222-4222-8222-222222222222');
  await page.goto(proofRoute());

  await expect(page.getByTestId('growth-agent-cohort-proof')).toHaveCount(0);
  await expect(page.getByRole('alert')).toContainText('Growth Agent cohort is stale', { timeout: 30_000 });
});
