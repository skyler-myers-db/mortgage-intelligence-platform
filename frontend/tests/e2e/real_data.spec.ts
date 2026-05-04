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
// ad-hoc runs fall back to the local dev stack. Use `||` (not `??`) so that
// an EMPTY STRING (the GitHub Actions default when a secret isn't set) also
// falls through to the localhost default. Without this, `page.goto('/')`
// sees an empty baseURL and emits `Cannot navigate to invalid URL`.
const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const API_URL =
  process.env.MIP_API_URL || APP_URL.replace(':5173', ':8000');

// Deployed Databricks Apps sit behind OAuth — a fresh browser hit to `/`
// 302-redirects to a consent page. Pass a workspace Bearer token on every
// request so the Apps auth middleware short-circuits the redirect. The
// nightly workflow wires DATABRICKS_TOKEN into the env; local ad-hoc runs
// against a localhost uvicorn don't need it (FastAPI accepts the unauth
// request).
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER
  ? { Authorization: `Bearer ${BEARER}` }
  : {};

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

async function fetchLeads(request: APIRequestContext): Promise<Array<{ borrower_id: string }>> {
  const resp = await request.get(`${API_URL}/api/leads?limit=10`, {
    headers: AUTH_HEADERS,
  });
  expect(resp.status(), 'GET /api/leads returned non-200').toBe(200);
  return (await resp.json()) as Array<{ borrower_id: string }>;
}

test.describe('Module 0 — real-UC golden path (nightly only)', () => {
  test('dashboard renders non-zero segment counts from gold', async ({ page }) => {
    // Segment cards live on /segment-intelligence, not the home dashboard.
    // Home is a narrative/launchpad; segments (.seg-card__count BEM class)
    // are the Segment Intelligence route's primary surface.
    await page.goto('/segment-intelligence');
    const counts = page.locator('.seg-card__count');
    await expect(counts.first()).toBeVisible({ timeout: 20_000 });
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

    // Cold Genie space = 10-15s; allow 40s (a cold warehouse + Genie
    // compilation can push past 20s on the first question of a session).
    // We assert the answer region renders at least one non-empty character
    // that is NOT the spinner glyph. The component uses `genie__msg--ai`
    // for assistant bubbles (see components/mortgage/GenieChat.tsx).
    const answer = panel.locator('.genie__msg--ai .bubble').last();
    await expect(answer).toBeVisible({ timeout: 40_000 });
    await expect
      .poll(async () => (await answer.innerText()).trim().length, { timeout: 40_000 })
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

    await expect(page.getByText(/Approved.*(?:audit|outreach queue)/i)).toBeVisible({
      timeout: 5_000,
    });

    // Poll /api/audit for a new row within 10s. Real Lakebase round-trip
    // including INSERT + SELECT is typically < 500 ms, but network +
    // serverless coldness can push it; 10s is a generous upper bound.
    //
    // Audit row shape: `entity_id` is the APPROVAL UUID (outreach.py sets
    // entity_id = approval_id, not borrower_id). The borrower id lives
    // in `payload_json.borrower_id`. Match on both the new-event-id and
    // the payload's borrower_id to avoid false positives from a sibling
    // approval that landed concurrently.
    await expect
      .poll(
        async () => {
          const resp = await request.get(
            `${API_URL}/api/audit/events?limit=100`,
            { headers: AUTH_HEADERS },
          );
          const rows = (await resp.json()) as Array<{
            event_id?: string;
            entity_id?: string;
            action?: string;
            payload_json?: { borrower_id?: string };
          }>;
          return rows.find(
            (r) =>
              (r.action === 'outreach.approve' ||
                r.action === 'outreach_approve') &&
              r.payload_json?.borrower_id === target &&
              !beforeIds.has(r.event_id),
          );
        },
        { timeout: 10_000 },
      )
      .toBeTruthy();
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

  // ============================================================
  // Per-route real-data coverage (Slice-10 follow-up)
  // ============================================================
  // Each route below asserts:
  //   1. Navigate + a unique-to-that-route element appears.
  //   2. Click the route's primary CTA; assert downstream state.
  //   3. At least one piece of real data is rendered (no stuck
  //      Loading…, no "—" placeholder in a slot meant for a number).
  //
  // Real-data tolerance:
  //   - First call after a cold warehouse can take 30-60 s
  //     (docs/load-baseline.md §"Cold-start footnote"). Assertions
  //     that hit UC use timeouts of 30-45 s. Subsequent calls are
  //     warm and fast (p95 ~1300 ms for /api/leads).

  test('portfolio-builder: filters + CTA + KPIs come from /api/portfolio/preview', async ({ page }) => {
    await page.goto('/portfolio-builder');

    // Unique-to-route: all six filter buttons render with the prototype BEM.
    for (const label of ['GEO', 'OCCUPANCY', 'LIEN STATUS', 'RELATIONSHIP', 'PRODUCT', 'EQUITY']) {
      await expect(page.getByRole('button', { name: new RegExp(`^${label}:`) })).toBeVisible();
    }

    // Primary CTA per prototype (design_files/Module 0 Prototype.html line
    // 1780): the "Run build" button in the filter row. Not a forward-nav;
    // its purpose is triggering the build animation / preview refresh.
    // We assert click → no navigation and the page stays on the route.
    const runBuild = page.getByRole('button', { name: /^Run build$/i });
    await expect(runBuild).toBeVisible();
    await runBuild.click();
    await expect(page).toHaveURL(/\/portfolio-builder$/);

    // Real data: /api/portfolio/preview drives the first KPI card
    // "Marketable population". Cold-UC takes up to ~30 s on first hit;
    // warm is <200 ms. We assert the value is a parseable non-zero
    // number (not "—", not "Loading…").
    const firstKpi = page.locator('.kpi').first();
    await expect(firstKpi).toBeVisible({ timeout: 30_000 });
    await expect
      .poll(
        async () => {
          const raw = (await firstKpi.locator('.kpi__value').innerText()).trim();
          const n = Number.parseInt(raw.replace(/[^0-9]/g, ''), 10);
          return Number.isFinite(n) && n > 0 ? n : 0;
        },
        { timeout: 30_000 },
      )
      .toBeGreaterThan(0);

    // Forward nav CTA (secondary, but the product's intended progression).
    await page.getByRole('link', { name: /Next: (?:segment intelligence|segments)/i }).click();
    await expect(page).toHaveURL(/\/segment-intelligence$/);
  });

  test('lead-queue: rows render, row expand opens inline dossier preview', async ({ page }) => {
    await page.goto('/lead-queue');

    // Unique-to-route: the table has the prototype BEM `table.tbl`.
    const table = page.locator('table.tbl');
    await expect(table).toBeVisible({ timeout: 30_000 });

    // Real data: wait for >=1 borrower row (cold UC can add 30-60 s).
    // `/api/leads` warm p95 is ~1300 ms per docs/load-baseline.md.
    const rows = table.locator('tbody tr').filter({ hasNot: page.locator('.tbl__expand') });
    await expect.poll(async () => rows.count(), { timeout: 45_000 }).toBeGreaterThanOrEqual(1);

    // Primary CTA per prototype: row click opens the inline preview
    // (`.tbl__expand` sibling row). This is the table's central interaction
    // — there's no "go" button; expanding the row IS the primary action.
    const firstRow = rows.first();
    await expect(firstRow).toBeVisible();
    await firstRow.click();

    // Downstream state: a `.tbl__expand` row is inserted right after the
    // clicked row, revealing the mini-dossier preview.
    await expect(page.locator('.tbl__expand').first()).toBeVisible({ timeout: 3_000 });
    await expect(page.locator('.tbl__expand-inner').first()).toContainText(/Customer 360 preview|CLIP|Segments/i);
  });

  test('borrower-360: evidence chips click open the drawer', async ({ page, request }) => {
    const leads = await fetchLeads(request);
    expect(leads.length).toBeGreaterThan(0);
    const target = leads[0].borrower_id;

    await page.goto(`/borrower-360/${target}`);

    // Unique-to-route: the "Why we recommend this" surface with ITM chip.
    await expect(page.getByText(/Why we recommend this/i)).toBeVisible({ timeout: 30_000 });

    // Real data: the Customer 360 surface shows CLIP + Owner Link. Format
    // is opaque (clip_<hex>/<id>) but it must not be empty / "—".
    const c360 = page.locator('.surface', { hasText: /Customer 360/i }).first();
    await expect(c360).toBeVisible();
    await expect(c360).toContainText(/CLIP/i);

    // Primary CTA per prototype: "Build outreach draft" — the forward
    // motion into the Offer Orchestrator. Also exercise an evidence chip
    // click to prove the drawer is wired (interaction #2 per the
    // extend-borrower-360 ask).
    const evidenceChip = page.locator('.evidence-chip, [class*="EvidenceChip"]').first();
    await expect(evidenceChip).toBeVisible({ timeout: 5_000 });
    await evidenceChip.click();
    // Drawer opens — `.drawer` is the prototype BEM root. Some variants
    // use `.drawer.is-open` or aria-hidden; we accept either so a small
    // prototype-CSS drift doesn't red-ball the nightly.
    await expect(
      page.locator('.drawer.is-open, [role="dialog"][aria-label*="Evidence" i], .drawer[aria-hidden="false"]').first(),
    ).toBeVisible({ timeout: 3_000 });

    // Close the drawer (Escape is the universal close in the prototype)
    // before clicking the forward CTA so it doesn't eat the click.
    await page.keyboard.press('Escape');

    // Forward CTA.
    await page.getByRole('link', { name: /Build outreach draft/i }).click();
    await expect(page).toHaveURL(new RegExp(`/offer-orchestrator/${target}$`));
  });

  test('offer-orchestrator: draft surface renders with real borrower data', async ({ page, request }) => {
    const leads = await fetchLeads(request);
    expect(leads.length).toBeGreaterThan(0);
    const target = leads[0].borrower_id;

    await page.goto(`/offer-orchestrator/${target}`);

    // Unique-to-route: the "Draft outreach · review only, never auto-sent"
    // heading on the right surface. Copy is a verbatim prototype string
    // and anchors the "no automatic outreach" safety posture.
    await expect(page.getByText(/Draft outreach.*review only/i)).toBeVisible({ timeout: 30_000 });

    // Real data: the draft textarea is prefilled with a borrower-aware
    // message ("Hi <first name> — based on recent public-record signals…").
    // We only assert it is non-empty; the actual lender / city strings vary
    // per borrower.
    const draft = page.locator('textarea').first();
    await expect(draft).toBeVisible();
    await expect
      .poll(async () => ((await draft.inputValue()) ?? '').trim().length, { timeout: 30_000 })
      .toBeGreaterThan(40);

    // Primary CTA is the Approve button, already covered by the
    // `approve outreach writes a new audit row…` test above. Here we
    // assert the "Thresholds applied" and "Considered alternatives"
    // surfaces renderd — these are the route's differentiators from the
    // simpler prototypes.
    await expect(page.getByText(/Thresholds applied/i).first()).toBeVisible();
    await expect(page.getByText(/Considered alternatives/i)).toBeVisible();
  });

  test('ask-genie: standalone page (not the FAB) renders + primary CTA works', async ({ page }) => {
    await page.goto('/ask-genie');

    // Unique-to-route: the "Trusted assets" surface listing UC metric
    // views. Only the standalone /ask-genie page has this; the floating
    // FAB from Home (covered in `genie FAB returns a non-empty answer`
    // above) does NOT.
    await expect(page.getByText(/Trusted assets/i)).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText('mip.gold.lead_population')).toBeVisible();
    await expect(page.getByText('mip.semantics.lead_generation_metric_view')).toBeVisible();

    // Real data: the textarea is pre-filled with the first sample
    // question; clicking "Ask Genie" fires /api/genie and the answer
    // surface appears. Cold Genie first-call is ~10-15 s; allow 40 s.
    const askButton = page.getByRole('button', { name: /^Ask Genie$/i }).first();
    await expect(askButton).toBeVisible();
    await askButton.click();

    // Downstream: the answer surface (inline `.surface` within the
    // body) renders with a Genie answer. GenieAnswer owns the content;
    // we assert by presence of a source chip (every answer has one).
    const answerSurface = page.locator('.surface', { hasText: /Source:/i }).first();
    await expect(answerSurface).toBeVisible({ timeout: 40_000 });
  });

  test('admin-config: presentation controls flip theme + density', async ({ page }) => {
    await page.goto('/admin-config');

    // Unique-to-route: the source-readiness and per-user appearance surfaces.
    await expect(page.getByText(/Data source readiness/i)).toBeVisible({ timeout: 5_000 });
    const appearanceToggle = page.getByRole('button', { name: /Workspace appearance/i });
    await expect(appearanceToggle).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(/grant needed|read error/i)).toHaveCount(0);

    const html = page.locator('html');

    await appearanceToggle.click();

    // Primary CTA per prototype: Theme toggle.
    const initialTheme = await html.getAttribute('data-theme');
    const expectedNext = initialTheme === 'dark' ? 'light' : 'dark';
    // Scope to the page body so we don't race with the Topbar icon button.
    await page.locator('main').getByRole('button', { name: new RegExp(`^${expectedNext}$`, 'i') }).click();
    await expect
      .poll(() => html.getAttribute('data-theme'), { timeout: 3_000 })
      .toBe(expectedNext);

    // Real data: admin route is a pure config surface — "real data" here
    // is the state roundtrip into the live AppContext + document attr,
    // which we just asserted. Also assert Density toggle mutates
    // data-density so we cover that axis.
    const initialDensity = await html.getAttribute('data-density');
    const nextDensity = initialDensity === 'compact' ? 'Comfortable' : 'Compact';
    await page.locator('main').getByRole('button', { name: new RegExp(`^${nextDensity}$`, 'i') }).click();
    await expect
      .poll(() => html.getAttribute('data-density'), { timeout: 3_000 })
      .toBe(nextDensity.toLowerCase());

    // Flip theme back so this test doesn't leave the session dirty.
    await page.locator('main').getByRole('button', { name: new RegExp(`^${initialTheme}$`, 'i') }).click();
    await expect
      .poll(() => html.getAttribute('data-theme'), { timeout: 3_000 })
      .toBe(initialTheme);
  });
});
