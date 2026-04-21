/**
 * Module 0 — Live Unity Catalog end-to-end spec (Slice 9).
 *
 * This spec exercises the deployed app against REAL Unity Catalog + Lakebase
 * + Genie. It is gated on ``E2E_LIVE=1`` so local dev + PR CI never runs it
 * (those run ``module0.spec.ts`` against in-process repositories). Only the
 * nightly ``playwright-e2e-live`` workflow job sets ``E2E_LIVE=1`` and the
 * ``MIP_APP_URL`` pointing at the deployed Databricks App.
 *
 * What it proves end-to-end:
 *   1. Dashboard renders a non-zero segment count from live gold tables.
 *   2. A ranked borrower row (from /api/leads) opens the Borrower 360,
 *      Evidence Drawer shows ≥ 2 real evidence rows from
 *      mip.gold.evidence_events.
 *   3. The Genie floating FAB returns a non-empty natural-language answer
 *      within 20s (first cold call can be 10-15s; we allow headroom).
 *   4. Approving an outreach produces a new row in /api/audit within 5s,
 *      proving the Lakebase audit write path.
 *   5. Mid-run, toggling the backend feature flag that forces a 503 causes
 *      the DegradedBanner to appear within 5s — the resilience story works
 *      on real infra, not just unit fixtures.
 *
 * Non-negotiables per CLAUDE.md:
 *   * No mock fallback. If the app can't reach UC, the spec fails — that is
 *     correct behaviour (the nightly workflow gets paged).
 *   * No real PII asserted; we assert on synthetic borrower IDs (B-*).
 *   * Resilient selectors (getByRole, getByText, aria-label) matching the
 *     prototype's BEM class names; no brittle xpath.
 */
import { test, expect, type APIRequestContext } from '@playwright/test';

// Gate: skip everything unless E2E_LIVE=1 is set by the nightly workflow.
const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 in the nightly workflow to run real-UC e2e.');

// Target URL — nightly workflow sets MIP_APP_URL to the deployed app; local
// ad-hoc runs fall back to the local dev stack.
const APP_URL = process.env.MIP_APP_URL ?? 'http://127.0.0.1:5173';
const API_URL = process.env.MIP_API_URL ?? APP_URL.replace(':5173', ':8000');

test.use({ baseURL: APP_URL });

async function fetchLeads(request: APIRequestContext): Promise<Array<{ borrower_id: string }>> {
  const resp = await request.get(`${API_URL}/api/leads?limit=10`);
  expect(resp.status(), 'GET /api/leads returned non-200').toBe(200);
  return (await resp.json()) as Array<{ borrower_id: string }>;
}

test.describe('Module 0 — real-UC golden path (nightly only)', () => {
  test('dashboard renders non-zero segment counts from gold', async ({ page }) => {
    await page.goto('/');
    // Segment cards surface as `.seg-card__count` — the prototype BEM class.
    // At least one card should display a count that is not "0" / "—".
    const counts = page.locator('.seg-card__count');
    await expect(counts.first()).toBeVisible({ timeout: 15_000 });
    const rendered = await counts.allTextContents();
    const hasPositive = rendered.some((raw) => {
      const n = Number.parseInt(raw.replace(/[^0-9]/g, ''), 10);
      return Number.isFinite(n) && n > 0;
    });
    expect(hasPositive, `no segment card had a positive count: ${rendered.join(' | ')}`).toBe(true);
  });

  test('ranked borrower -> evidence drawer shows >= 2 rows', async ({ page, request }) => {
    const leads = await fetchLeads(request);
    expect(leads.length, 'need >= 1 ranked borrower from /api/leads').toBeGreaterThan(0);

    // Pick the first real borrower id; navigate directly to the dossier so we
    // don't depend on row-click target rewriting.
    const target = leads[0].borrower_id;
    await page.goto(`/borrower-360/${target}`);

    // Evidence drawer lives as `.drawer` per the prototype BEM. The evidence
    // list renders rows with a `.trig` / trigger-timeline class; we accept
    // either entry point as "evidence is visible".
    const evidenceRows = page.locator('.trig, .drawer .evidence-row, .evidence-chip');
    await expect
      .poll(() => evidenceRows.count(), { timeout: 15_000 })
      .toBeGreaterThanOrEqual(2);
  });

  test('genie FAB returns a non-empty answer within 20s', async ({ page }) => {
    await page.goto('/');

    // Open the floating FAB (consistent selector with module0.spec.ts).
    await page.locator('.genie__fab').click();
    const panel = page.getByRole('dialog', { name: 'Genie chat' });
    await expect(panel).toBeVisible();

    const canonicalQ =
      'How many borrowers across the 6-state footprint are currently in-the-money?';
    await panel.getByLabel('Ask Genie').fill(canonicalQ);
    await panel.getByRole('button', { name: /Ask/i }).click();

    // Cold Genie space = 10-15s; allow 20s. We assert the answer region
    // renders at least one non-empty character that is NOT the spinner glyph.
    const answer = panel.locator('.genie-answer, .genie__msg--assistant').last();
    await expect(answer).toBeVisible({ timeout: 20_000 });
    await expect
      .poll(async () => (await answer.innerText()).trim().length, { timeout: 20_000 })
      .toBeGreaterThan(20);
  });

  test('approve outreach writes a new audit row visible in /api/audit within 5s', async ({ page, request }) => {
    const leads = await fetchLeads(request);
    expect(leads.length).toBeGreaterThan(0);
    const target = leads[0].borrower_id;

    const before = (await (await request.get(`${API_URL}/api/audit/events?limit=100`)).json()) as Array<{
      event_id?: string;
      entity_id?: string;
      action?: string;
    }>;
    const beforeIds = new Set(before.map((e) => e.event_id).filter(Boolean));

    await page.goto(`/offer-orchestrator/${target}`);
    // The ApprovalBanner primary button — same selector as module0.spec.ts.
    await page
      .getByRole('button', { name: /^Approve$|approve outreach/i })
      .first()
      .click();

    await expect(page.getByText(/Approved and logged to audit/i)).toBeVisible({
      timeout: 5_000,
    });

    // Poll /api/audit for a new row within 5s. Real Lakebase round-trip
    // including INSERT + SELECT is typically < 500 ms, but network + serverless
    // coldness can push it; 5s is the upper bound.
    const newRow = await expect
      .poll(
        async () => {
          const resp = await request.get(`${API_URL}/api/audit/events?limit=100`);
          const rows = (await resp.json()) as Array<{ event_id?: string; entity_id?: string; action?: string }>;
          return rows.find(
            (r) =>
              r.entity_id === target &&
              (r.action === 'outreach.approve' || r.action === 'outreach_approve') &&
              !beforeIds.has(r.event_id),
          );
        },
        { timeout: 5_000 },
      )
      .toBeTruthy();
    expect(newRow).toBeTruthy();
  });

  test('forcing a 503 surfaces the DegradedBanner within 5s', async ({ page, request }) => {
    // Preconditions: the nightly workflow sets MIP_FORCE_DEGRADED_TOKEN so
    // the dev-only admin endpoint can flip the feature flag. We leave the app
    // in forced-degraded state briefly, assert the banner, then un-flip.
    const forceToken = process.env.MIP_FORCE_DEGRADED_TOKEN;
    test.skip(
      !forceToken,
      'Requires MIP_FORCE_DEGRADED_TOKEN to invoke the admin force-degraded endpoint.',
    );

    const flip = async (state: 'on' | 'off') => {
      const resp = await request.post(`${API_URL}/api/admin/force-degraded`, {
        headers: { 'X-Admin-Token': forceToken! },
        data: { state },
      });
      // The admin endpoint returns 200/204 on success; accept either.
      expect([200, 204]).toContain(resp.status());
    };

    try {
      await flip('on');
      await page.goto('/');
      // DegradedBanner convention: top-of-page strip with role="alert" and
      // class `degraded-banner` (or data-testid `degraded-banner`).
      const banner = page
        .locator('[data-testid="degraded-banner"], .degraded-banner, [role="alert"]')
        .first();
      await expect(banner).toBeVisible({ timeout: 5_000 });
      await expect(banner).toContainText(/degraded|warming|unavailable/i);
    } finally {
      await flip('off');
    }
  });
});
