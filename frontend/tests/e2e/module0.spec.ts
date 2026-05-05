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
    await expectKpiValue(page, 'Cost per contact (est.)', '$2.18');
    await expectKpiValue(page, 'Projected contact → app', /9\.7/);

    // Slice 9: assert Illinois since it's the anchor metro for the county
    // drill (Chicago/Cook County). @svg-maps/usa ships aria-labels for every
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

  test('segment intelligence: six cards, ITM preselected, toggle + clear', async ({ page }) => {
    const segmentRequests: string[] = [];
    page.on('request', (request) => {
      const url = request.url();
      if (url.includes('/api/leads') || url.includes('/api/geo/state-rollups')) {
        segmentRequests.push(url);
      }
    });

    await page.goto('/segment-intelligence');

    for (const name of [
      'In the Money',
      'Listed for Sale',
      'Permit Activity',
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
    await expect(rankedHeader).toContainText(/filtered by itm/);

    await page.getByText('Listed for Sale', { exact: true }).click();
    await expect(rankedHeader).toContainText(/filtered by .*listed/);

    await page.getByText('Home Equity Candidate', { exact: true }).click();
    await expect(rankedHeader).toContainText(/must match all selected segments/);
    await expect
      .poll(() =>
        segmentRequests.some(
          (url) =>
            url.includes('/api/leads') &&
            url.includes('segment_codes=') &&
            url.includes('segment_mode=all'),
        ),
      )
      .toBe(true);
    await expect
      .poll(() =>
        segmentRequests.some(
          (url) =>
            url.includes('/api/geo/state-rollups') &&
            url.includes('segment_codes=') &&
            url.includes('segment_mode=all'),
        ),
      )
      .toBe(true);

    await page.getByRole('button', { name: /Clear filters/ }).click();
    await expect(rankedHeader).not.toContainText(/filtered by/);
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
    await expect(panel.getByText(/I'm Genie/i)).toBeVisible();

    await panel.getByLabel('Ask Genie').fill('How many HELOC candidates?');
    await panel.getByRole('button', { name: /Ask/i }).click();

    // Deterministic fallback surfaces metric_value = "4,108".
    await expect(panel.getByText('4,108').first()).toBeVisible({ timeout: 5_000 });
    await expect(panel.locator('.evidence-chip').first()).toBeVisible();

    const followUp = panel.locator('.genie-answer__followups .filter').first();
    await expect(followUp).toBeVisible();

    const followUpText = (await followUp.textContent()) ?? '';
    await followUp.click();
    await expect(panel.locator('.genie__msg--user').last()).toContainText(
      followUpText.replace(/^Ask\s*/, '').trim().slice(0, 20),
    );
  });
});
