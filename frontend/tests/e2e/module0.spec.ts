import { test, expect, type ConsoleMessage } from '@playwright/test';
import { expectKpiValue } from './helpers';

/**
 * Module 0 — golden path end-to-end spec.
 *
 * Pins the product narrative: portfolio → segment → lead → borrower →
 * approve → audit, plus floating Genie and theme toggle. Resilient selectors
 * (getByRole / getByText / aria labels). Viewport is 1440x900 via
 * playwright.config.ts. Direct-fetch against /api/audit/events verifies the
 * human-approval round-trip actually wrote to the backend.
 */

test.describe('Module 0 — golden path', () => {
  const consoleErrors: string[] = [];

  test.beforeEach(async ({ page }) => {
    consoleErrors.length = 0;
    page.on('console', (msg: ConsoleMessage) => {
      if (msg.type() === 'error') {
        const text = msg.text();
        if (/favicon|React DevTools|Download the React/i.test(text)) return;
        consoleErrors.push(text);
      }
    });
  });

  test('home: hero + four KPIs + map + agent log', async ({ page }) => {
    await page.goto('/');

    await expect(page).toHaveTitle(/Mortgage Intelligence Platform/);
    await expect(
      page.getByRole('heading', { name: 'Who should we contact, why now, and with what offer?' }),
    ).toBeVisible();

    await expectKpiValue(page, 'Marketable population', '89,553');
    await expectKpiValue(page, 'High-intent leads', '12,840');
    await expectKpiValue(page, 'Top-tier opportunities', '4,120');
    await expectKpiValue(page, 'Offers recommended', '6,250');

    // Slice 9: assert Illinois since it's the anchor metro for the county
    // drill (Chicago/Cook County). State topology ships aria-labels for every
    // state; picking IL aligns the test with the product narrative.
    await expect(page.locator('[aria-label="Illinois"]').first()).toBeVisible({ timeout: 5_000 });
    await expect(page.locator('.surface', { hasText: /agent|activity/i }).first()).toBeVisible();

    expect(consoleErrors, `unexpected console errors: ${consoleErrors.join(' | ')}`).toEqual([]);
  });

  test('topbar: breadcrumbs, workspace pills, theme toggle round-trip', async ({ page }) => {
    await page.goto('/');
    const banner = page.getByRole('banner');

    await expect(banner.getByText('mip-app', { exact: true })).toBeVisible();
    await expect(banner.getByText('Module 0: Top of Funnel', { exact: true })).toBeVisible();
    await expect(banner.locator('.cur')).toHaveText('Home');

    await expect(banner.getByText('Summit Mortgage', { exact: true })).toBeVisible();
    await expect(banner.getByText('sandbox', { exact: true })).toBeVisible();
    await expect(banner.getByText('serverless-xl', { exact: true })).toBeVisible();

    const html = page.locator('html');
    const initialTheme = await html.getAttribute('data-theme');
    const themeToggle = banner.getByRole('button', { name: 'Toggle theme' });
    await themeToggle.click();
    await expect
      .poll(() => html.getAttribute('data-theme'), { timeout: 2_000 })
      .not.toBe(initialTheme);
    await themeToggle.click();
    await expect.poll(() => html.getAttribute('data-theme'), { timeout: 2_000 }).toBe(initialTheme);
  });

  test('portfolio builder: six filters, Texas select, forward nav', async ({ page }) => {
    await page.goto('/portfolio-builder');

    for (const label of ['GEO', 'OCCUPANCY', 'LIEN STATUS', 'RELATIONSHIP', 'PRODUCT', 'EQUITY']) {
      await expect(page.getByRole('button', { name: new RegExp(`^${label}:`) })).toBeVisible();
    }

    await page.getByRole('button', { name: /^GEO:/ }).click();
    await page.getByRole('option', { name: 'Texas' }).click();
    await expect(page.getByRole('button', { name: 'GEO: Texas' })).toBeVisible();

    await expect(
      page.getByRole('button', { name: 'Generate approval-required outreach' }),
    ).toBeVisible();

    await page.getByRole('link', { name: /Next: segment intelligence/ }).click();
    await expect(page).toHaveURL(/\/segment-intelligence$/);
  });

  test('segment intelligence: six cards, standalone counts, toggle + clear', async ({ page }) => {
    const segmentRequests: string[] = [];
    page.on('request', (request) => {
      const url = request.url();
      if (url.includes('/api/leads') || url.includes('/api/geo/state-rollups')) {
        segmentRequests.push(url);
      }
    });

    await page.goto('/segment-intelligence');

    for (const name of [
      'Prime Refi Candidates',
      'Listed for Sale',
      'HELOC Intent',
      'Investor / Multi-Property',
      'Home Equity Candidate',
      'Retention Risk',
    ]) {
      await expect(page.getByText(name, { exact: true }).first()).toBeVisible();
    }

    // `.h-2` scopes to the ranked-borrower section header (avoids the surface
    // footer + sample Genie questions that also say "N borrowers").
    const rankedHeader = page.locator('.h-2').filter({ hasText: /borrowers/ }).first();
    await expect(rankedHeader).toBeVisible();
    await expect(rankedHeader).not.toContainText(/segment filter:/);

    await page.getByText('Listed for Sale', { exact: true }).click();
    await expect(rankedHeader).toContainText(/segment filter: Listed for Sale/);

    await page.getByText('Home Equity Candidate', { exact: true }).click();
    await expect(rankedHeader).toContainText(/segment filter: Listed for Sale \+ Home Equity Candidate/);
    await expect(rankedHeader).toContainText(/matches any selected segment/);
    await expect
      .poll(() =>
        segmentRequests.some(
          (url) =>
            url.includes('/api/leads') &&
            url.includes('segment_codes=') &&
            url.includes('segment_mode=any'),
        ),
      )
      .toBe(true);
    await expect
      .poll(() =>
        segmentRequests.some(
          (url) =>
            url.includes('/api/geo/state-rollups') &&
            url.includes('segment_codes=') &&
            url.includes('segment_mode=any'),
        ),
      )
      .toBe(true);

    await page.getByRole('button', { name: /Clear filters/ }).click();
    await expect(rankedHeader).not.toContainText(/segment filter:/);
  });

  test('unknown routes render a 404 surface without silently redirecting home', async ({ page }) => {
    await page.goto('/this-route-does-not-exist');

    await expect(page).toHaveURL(/\/this-route-does-not-exist$/);
    await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible();
    await expect(page.getByText('/this-route-does-not-exist')).toBeVisible();
    await expect(page.getByRole('banner').locator('.cur')).toHaveText('Not Found');
    await expect(page.getByRole('link', { name: /Open lead queue/i })).toHaveAttribute('href', '/lead-queue');
  });

  test('lead queue: 23 rows, headers, B-48291 canonical row', async ({ page }) => {
    await page.goto('/lead-queue');
    const rows = page.locator('table.tbl tbody tr').filter({ hasNot: page.locator('.tbl__expand') });
    await expect.poll(async () => rows.count(), { timeout: 5_000 }).toBeGreaterThanOrEqual(20);

    const thead = page.locator('table.tbl thead');
    for (const header of [
      /Borrower/i, /Location/i, /Segments/i, /Equity/i, /Rate.*bps/i,
      /Next-best-offer/i, /Score/i, /Confidence/i, /Approval/i,
    ]) {
      await expect(thead.getByText(header).first()).toBeVisible();
    }

    const flagRow = page.locator('table.tbl tbody tr', { hasText: 'B-48291' }).first();
    await expect(flagRow).toBeVisible();
    await expect(flagRow).toContainText('+88');
    await expect(flagRow).toContainText('Refinance + HELOC');
  });

  test('borrower 360: rationale, evidence, CTA forward', async ({ page }) => {
    await page.goto('/borrower-360/B-48291');

    // Skeleton → real dossier title.
    await expect(page.getByRole('heading', { name: 'James & Maria Rodriguez' })).toBeVisible({
      timeout: 10_000,
    });

    await expect(page.getByText('clip_demo_48291')).toBeVisible();
    await expect(page.getByText('ol_demo_48291')).toBeVisible();
    await expect(page.getByText('$625,000')).toBeVisible();
    await expect(page.getByText('54%')).toBeVisible();

    await expect(page.getByText('In-the-money', { exact: true })).toBeVisible();
    await expect(page.getByText('+88 bps', { exact: true }).first()).toBeVisible();
    await expect(page.getByText('+88 bps spread (>= 75) AND 46% equity (>= 15%)')).toBeVisible();

    await expect(page.getByText('fn_rate_spread').first()).toBeVisible();
    await expect(page.getByText('fn_in_the_money').first()).toBeVisible();

    // Trigger timeline: >= 3 events (backend ships 3 evidence items).
    const timeline = page.locator('.trig, [class*="trigger"] li, [class*="trig"]');
    await expect.poll(async () => timeline.count(), { timeout: 2_000 }).toBeGreaterThanOrEqual(3);

    await page.getByRole('link', { name: /Build outreach draft/ }).click();
    await expect(page).toHaveURL(/\/offer-orchestrator\/B-48291$/);
  });

  test('offer orchestrator: approve writes audit event (UI + backend)', async ({ page, request }) => {
    const before = (await (await request.get('http://localhost:8000/api/audit/events')).json()) as Array<{
      action?: string;
      entity_id?: string;
    }>;

    await page.goto('/offer-orchestrator/B-48291');

    await expect(page.getByText('Refinance + HELOC').first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByText(/\+88 bps/).first()).toBeVisible();
    await expect(page.getByText(/46%/).first()).toBeVisible();

    for (const src of ['fn_next_best_offer', 'fn_rate_spread', 'fn_in_the_money', 'fn_lead_score']) {
      await expect(page.getByText(src).first()).toBeVisible();
    }

    await expect(page.getByText(/2 other products? ruled out/)).toBeVisible();

    for (const key of [
      /Min spread/i, /Min equity/i, /HELOC equity floor/i,
      /Cash-out equity floor/i, /Retention min spread/i,
    ]) {
      await expect(page.getByText(key).first()).toBeVisible();
    }

    // ApprovalBanner primary button. Regex tolerates "Approve" / "Approve outreach" wording drift.
    await page.getByRole('button', { name: /^Approve$|approve outreach/i }).first().click();

    await expect(page.getByText(/Approved and logged to audit/i)).toBeVisible({ timeout: 5_000 });
    await expect(page.getByText(/audit:\s*evt-/i)).toBeVisible();

    // Backend verification: new outreach.approve audit event for B-48291.
    const after = (await (await request.get('http://localhost:8000/api/audit/events')).json()) as Array<{
      action?: string;
      entity_id?: string;
      event_id?: string;
    }>;
    expect(after.length).toBeGreaterThan(before.length);
    const evt = after.find(
      (e) =>
        (e.action === 'outreach.approve' || e.action === 'outreach_approve') &&
        e.entity_id === 'B-48291',
    );
    expect(evt, 'expected outreach.approve audit event for B-48291').toBeTruthy();
  });

  test('floating Genie: FAB, metric answer, evidence chip, follow-up round-trip', async ({ page }) => {
    await page.goto('/');

    // The floating Genie FAB is the canonical entry from every page; the
    // Console right rail (visible by default) also exposes an "Open Genie"
    // button, so scope to the FAB explicitly to avoid strict-mode conflict.
    await page.locator('.genie__fab').click();

    const panel = page.getByRole('dialog', { name: 'Genie chat' });
    await expect(panel).toBeVisible();
    await expect(panel).not.toHaveAttribute('aria-modal', 'true');
    await expect(panel.getByText(/I'm Genie/i)).toBeVisible();

    await panel.getByLabel('Ask Genie').fill('How many HELOC candidates?');
    await panel.getByRole('button', { name: /Ask/i }).click();

    // Permit-specific HELOC counts are blocked until Cotality shares the
    // Building Permits feed; fallback and live paths must not fabricate
    // positive permit-derived borrower volume.
    await expect(panel.getByText(/Building Permits share lands|pending permit/i).first()).toBeVisible({ timeout: 5_000 });
    await expect(panel.locator('.evidence-chip').first()).toBeVisible();

    const followUp = panel.locator('.genie-answer__followups .filter').first();
    await expect(followUp).toBeVisible();

    const followUpText = (await followUp.textContent()) ?? '';
    await followUp.click();
    await expect(panel.locator('.genie__msg--user').last()).toContainText(
      followUpText.replace(/^Ask\s*/, '').trim().slice(0, 20),
    );
  });

  test('ask-genie action: borrower-list cohort opens exact filtered lead queue', async ({ page }) => {
    const borrowerIds = ['B-11111', 'B-22222'];
    const leadUrls: string[] = [];
    await page.route('**/api/genie/start', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ conversation_id: null, trusted_assets: ['mip.gold.borrower_360'] }),
      });
    });
    await page.route('**/api/genie/message', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          conversation_id: 'conv-exact',
          message_id: 'msg-exact',
          question_hash: 'hash-exact',
          question: 'Show the top borrowers by score.',
          answer: 'The top borrower rows are returned from borrower_360.',
          source: 'genie',
          trusted_assets: ['mip.gold.borrower_360'],
          row_count: 2,
          proof: {
            source_assets: ['mip.gold.borrower_360'],
            row_count: 2,
            trusted: true,
            filters: [],
            known_data_gaps: [],
          },
          visualization: {
            kind: 'borrower_list',
            title: 'Borrower drill-down',
            x: 'borrower_id',
            y: 'opportunity_score',
          },
          table_rows: [
            { borrower_id: borrowerIds[0], city: 'Seattle', state: 'WA', zip: '98118', opportunity_score: 92 },
            { borrower_id: borrowerIds[1], city: 'Chicago', state: 'IL', zip: '60617', opportunity_score: 91 },
          ],
          actions: [
            {
              id: 'open-cohort',
              label: 'Open this cohort in Lead Queue',
              action_type: 'open_cohort',
              description: 'Navigate into the lead queue with this Genie result audited.',
              route: `/lead-queue?borrower_ids=${encodeURIComponent(borrowerIds.join(','))}`,
              borrower_ids: borrowerIds,
              criteria: {
                source: 'genie',
                source_assets: ['mip.gold.borrower_360'],
                visualization_kind: 'borrower_list',
                row_count: 2,
                result_filters: {
                  borrower_ids: borrowerIds,
                },
                sql_hash: 'hash-sql',
              },
              request_id: 'req-exact',
              confirmation_token: 'token-exact',
            },
          ],
        }),
      });
    });
    await page.route('**/api/genie/actions', async (route) => {
      const posted = route.request().postDataJSON() as { route?: string | null; borrower_ids?: string[] };
      expect(posted.borrower_ids).toEqual(borrowerIds);
      expect(posted.route).toContain('borrower_ids=');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: true,
          action_type: 'open_cohort',
          audit_event_id: 'audit-exact',
          route: posted.route,
          saved_count: 0,
          message: 'Genie action recorded to the governed audit ledger.',
        }),
      });
    });
    await page.route('**/api/leads**', async (route) => {
      leadUrls.push(route.request().url());
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(
          borrowerIds.map((borrowerId, i) => ({
            borrower_id: borrowerId,
            display_name: 'Owner synthetic',
            city: i === 0 ? 'Seattle' : 'Chicago',
            state: i === 0 ? 'WA' : 'IL',
            zip: i === 0 ? '98118' : '60617',
            segment_codes: ['itm'],
            equity_estimate: 500000,
            rate_spread_bps: 150,
            opportunity_score: 92 - i,
            confidence: 86,
            recommended_offer: 'Refinance + HELOC',
            why_now: 'test',
            evidence_ids: [],
            approval_status: 'pending',
          })),
        ),
      });
    });

    await page.goto('/ask-genie');
    await page.locator('textarea[aria-label="Ask Genie — question"]').fill('Show the top borrowers by score.');
    await page.getByRole('button', { name: /^Ask Genie$/i }).first().click();
    const cohortAction = page.locator('.genie-action', { hasText: /Open this cohort in Lead Queue/i }).first();
    await expect(cohortAction).toBeVisible();
    await cohortAction.getByRole('button', { name: /Run/i }).click();
    await cohortAction.getByRole('button', { name: /Confirm/i }).click();

    await expect(page).toHaveURL(/\/lead-queue\?.*borrower_ids=/);
    await expect.poll(() => leadUrls.some((url) =>
      new URL(url, 'http://localhost').searchParams.get('borrower_ids') === borrowerIds.join(','),
    )).toBe(true);
    await expect(page.locator('table.tbl tbody')).toContainText(borrowerIds[0]);
    await expect(page.locator('table.tbl tbody')).toContainText(borrowerIds[1]);
  });

  test('ask-genie action: failed confirmation is visible and does not navigate', async ({ page }) => {
    await page.route('**/api/genie/start', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ conversation_id: null, trusted_assets: ['mip.gold.borrower_360'] }),
      });
    });
    await page.route('**/api/genie/message', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          conversation_id: 'conv-fail',
          message_id: 'msg-fail',
          question_hash: 'hash-fail',
          question: 'Show borrowers.',
          answer: 'Borrower rows.',
          source: 'genie',
          trusted_assets: ['mip.gold.borrower_360'],
          row_count: 1,
          proof: { source_assets: ['mip.gold.borrower_360'], row_count: 1, trusted: true },
          table_rows: [{ borrower_id: 'B-11111', opportunity_score: 92 }],
          actions: [
            {
              id: 'open-cohort',
              label: 'Open this cohort in Lead Queue',
              action_type: 'open_cohort',
              description: 'Navigate into the lead queue with this Genie result audited.',
              route: '/lead-queue?borrower_ids=B-11111',
              borrower_ids: ['B-11111'],
              criteria: { source: 'genie', source_assets: ['mip.gold.borrower_360'], row_count: 1 },
              request_id: 'req-fail',
              confirmation_token: 'token-fail',
            },
          ],
        }),
      });
    });
    await page.route('**/api/genie/actions', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          ok: false,
          action_type: 'open_cohort',
          route: '/lead-queue?borrower_ids=B-11111',
          saved_count: 0,
          message: 'Confirmation token rejected.',
        }),
      });
    });

    await page.goto('/ask-genie');
    await page.locator('textarea[aria-label="Ask Genie — question"]').fill('Show borrowers.');
    await page.getByRole('button', { name: /^Ask Genie$/i }).first().click();
    const cohortAction = page.locator('.genie-action', { hasText: /Open this cohort in Lead Queue/i }).first();
    await expect(cohortAction).toBeVisible();
    await cohortAction.getByRole('button', { name: /Run/i }).click();
    await cohortAction.getByRole('button', { name: /Confirm/i }).click();

    await expect(page.getByText(/Action failed: Confirmation token rejected/i)).toBeVisible();
    await expect(page).toHaveURL(/\/ask-genie$/);
  });
});
