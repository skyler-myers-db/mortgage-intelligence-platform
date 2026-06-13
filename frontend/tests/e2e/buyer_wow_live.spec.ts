/**
 * Buyer-Wow live inspection (Module 0 re-audit items #3, #4, #6, #9).
 *
 * Exercises the four "buyer-wow" features against the DEPLOYED Databricks App
 * on real Unity Catalog + Lakebase + Genie:
 *   #6 Morning briefing card on Home (live trends or honest pending state)
 *   #4 Geography map level-transition wrapper (`.map-levels`, keyed re-render)
 *   #3 Borrower 360 "Tell the story" deterministic narrative + claim chips
 *   #9 Genie follow-up chips + Pin-to-Home → Home "Pinned insights" card
 *
 * Gated behind E2E_LIVE=1 (like real_data.spec.ts). MIP_APP_URL points at the
 * deployed app; a workspace Bearer token (MIP_BEARER_TOKEN / DATABRICKS_TOKEN)
 * short-circuits the Apps OAuth redirect on every request.
 */
import { test, expect, type APIRequestContext, type Page } from '@playwright/test';

const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 to run the buyer-wow live inspection.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const API_URL = process.env.MIP_API_URL || APP_URL.replace(':5173', ':8000');
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER ? { Authorization: `Bearer ${BEARER}` } : {};

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

async function firstBorrowerId(request: APIRequestContext): Promise<string> {
  const resp = await request.get(`${API_URL}/api/leads?limit=10`, { headers: AUTH_HEADERS });
  expect(resp.status(), 'GET /api/leads returned non-200').toBe(200);
  const rows = (await resp.json()) as Array<{ borrower_id: string }>;
  expect(rows.length, 'need >= 1 ranked borrower').toBeGreaterThan(0);
  return rows[0].borrower_id;
}

// US state code → full name, to translate the in-footprint rollup codes into
// the map region's accessible name. Static (does not change); the COVERAGE is
// still discovered dynamically from /api/geo/state-rollups.
const STATE_NAMES: Record<string, string> = {
  AL: 'Alabama', AK: 'Alaska', AZ: 'Arizona', AR: 'Arkansas', CA: 'California',
  CO: 'Colorado', CT: 'Connecticut', DE: 'Delaware', DC: 'Washington, DC',
  FL: 'Florida', GA: 'Georgia', HI: 'Hawaii', ID: 'Idaho', IL: 'Illinois',
  IN: 'Indiana', IA: 'Iowa', KS: 'Kansas', KY: 'Kentucky', LA: 'Louisiana',
  ME: 'Maine', MD: 'Maryland', MA: 'Massachusetts', MI: 'Michigan',
  MN: 'Minnesota', MS: 'Mississippi', MO: 'Missouri', MT: 'Montana',
  NE: 'Nebraska', NV: 'Nevada', NH: 'New Hampshire', NJ: 'New Jersey',
  NM: 'New Mexico', NY: 'New York', NC: 'North Carolina', ND: 'North Dakota',
  OH: 'Ohio', OK: 'Oklahoma', OR: 'Oregon', PA: 'Pennsylvania',
  RI: 'Rhode Island', SC: 'South Carolina', SD: 'South Dakota', TN: 'Tennessee',
  TX: 'Texas', UT: 'Utah', VT: 'Vermont', VA: 'Virginia', WA: 'Washington',
  WV: 'West Virginia', WI: 'Wisconsin', WY: 'Wyoming',
};

async function firstInFootprintStateName(request: APIRequestContext): Promise<string> {
  const resp = await request.get(`${API_URL}/api/geo/state-rollups`, { headers: AUTH_HEADERS });
  expect(resp.status(), 'GET /api/geo/state-rollups returned non-200').toBe(200);
  const { rollups } = (await resp.json()) as { rollups: Array<{ state: string }> };
  expect(rollups.length, 'need >= 1 in-footprint state to drill').toBeGreaterThan(0);
  const name = STATE_NAMES[rollups[0].state.toUpperCase()];
  expect(name, `unknown state code ${rollups[0].state}`).toBeTruthy();
  return name;
}

async function openGeniePanel(page: Page) {
  const fab = page.locator('.genie__fab:visible').first();
  if (await fab.isVisible()) {
    await fab.click();
    return;
  }
  await page.getByRole('button', { name: /Toggle Genie chat/i }).click();
}

test.describe('Buyer-Wow live inspection @desktop', () => {
  test('#6 morning briefing is removed; Approval queue panel carries the honest nudge', async ({ page }) => {
    // The morning briefing was cut: its only non-redundant content (workflow
    // counts + Review CTA) is already the Approval queue panel, and no honest
    // "what changed" signal exists in the current data (triggers are refresh-
    // stamped, segment "movement" is a detection-rollout artifact). Guard that
    // the redundant card stays gone and the real nudge remains.
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /Who should we contact/i })).toBeVisible({ timeout: 30_000 });
    await expect(page.locator('.briefing')).toHaveCount(0);
    const queue = page.getByRole('region', { name: 'Approval queue' });
    await expect(queue).toBeVisible({ timeout: 15_000 });
    await expect(queue.getByRole('link', { name: /review queue/i })).toBeVisible();
  });

  test('Feature A: "Your book today" portfolio summary renders, grounded + verified', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const summary = page.getByRole('region', { name: 'Portfolio summary' });
    await expect(summary).toBeVisible({ timeout: 30_000 });
    await expect(summary.getByText('Your book today')).toBeVisible();
    // Real narrative (a grouped count appears) + grounded claim chips + verdict.
    await expect(summary.locator('.portfolio-summary__narrative')).not.toBeEmpty();
    await expect
      .poll(() => summary.locator('.portfolio-summary__claim').count(), { timeout: 10_000 })
      .toBeGreaterThan(0);
    await expect(summary.locator('.portfolio-summary__verdict--ok')).toBeVisible();
    // The topbar search advertises the command-palette hotkey (⌘K / Ctrl K).
    await expect(page.locator('.topbar__search-kbd')).toBeVisible();
  });

  test('Auto-offer Slice 1: borrower-offer prototype mock is reachable + clearly labelled', async ({ page, request }) => {
    const id = await firstBorrowerId(request);
    await page.goto(`/offer-orchestrator/${id}`, { waitUntil: 'domcontentloaded' });
    const preview = page.locator('[data-testid="preview-borrower-offer"]');
    await expect(preview).toBeVisible({ timeout: 30_000 });
    await preview.click();
    const mock = page.locator('[data-testid="borrower-offer-mock"]');
    await expect(mock).toBeVisible({ timeout: 10_000 });
    await expect(mock.locator('.offer-mock__watermark')).toHaveText('PROTOTYPE');
    await expect(mock).toContainText('not a firm offer of credit');
    await mock.locator('[data-testid="offer-mock-accept"]').click();
    await expect(mock).toContainText('no information was submitted');
  });

  test('Feature C: offer orchestrator exposes the LO-assignment + follow-up routing controls', async ({ page, request }) => {
    const id = await firstBorrowerId(request);
    await page.goto(`/offer-orchestrator/${id}`, { waitUntil: 'domcontentloaded' });
    const routing = page.locator('[data-testid="outreach-routing"]');
    await expect(routing).toBeVisible({ timeout: 30_000 });
    // The loan-officer picker is populated from the live sales-team roster.
    const loSelect = routing.locator('#lo-assign');
    await expect(loSelect).toBeVisible();
    await expect
      .poll(() => loSelect.locator('option').count(), { timeout: 10_000 })
      .toBeGreaterThan(1); // "Unassigned" + >=1 real loan officer
    // Follow-up reminder options include "In 5 days".
    await expect(routing.locator('#lo-followup option', { hasText: /In 5 days/i })).toHaveCount(1);
  });

  test('#4 geography map renders the keyed level-transition wrapper and drills', async ({ page, request }) => {
    await page.goto('/segment-intelligence', { waitUntil: 'domcontentloaded' });
    const levels = page.locator('.map-levels').first();
    await expect(levels).toBeVisible({ timeout: 30_000 });

    // State level shows exactly one breadcrumb ("US"). Drill into a state that
    // actually has Cotality coverage (out-of-footprint states are no-ops by
    // design) — discovered dynamically so the test follows the live coverage.
    const stateName = await firstInFootprintStateName(request);
    const region = page.locator('.map-svg-stage').getByRole('button', { name: stateName, exact: true });
    await expect(region).toBeVisible({ timeout: 20_000 });
    const crumbsBefore = await page.locator('.map-crumbs button').count();
    await region.click();
    // Drill to county → breadcrumb trail grows (US > <State>) and the keyed
    // `.map-levels` wrapper re-renders without crashing.
    await expect
      .poll(() => page.locator('.map-crumbs button').count(), { timeout: 15_000 })
      .toBeGreaterThan(crumbsBefore);
    await expect(page.locator('.map-crumbs')).toContainText(stateName);
    await expect(page.locator('.map-levels').first()).toBeVisible();
  });

  test('#3 Borrower 360 story renders automatically as a grounded narrative', async ({ page, request }) => {
    const id = await firstBorrowerId(request);
    await page.goto(`/borrower-360/${id}`, { waitUntil: 'domcontentloaded' });
    // Renders automatically now (no "Tell the story" click).
    await expect(page.locator('[data-testid="tell-the-story"]')).toHaveCount(0);
    const body = page.locator('[data-testid="borrower-story-body"]');
    await expect(body).toBeVisible({ timeout: 30_000 });
    await expect(body.locator('.borrower-story__narrative')).not.toBeEmpty();
    // At least one figure is grounded against the dossier (claim chip rendered).
    await expect
      .poll(() => body.locator('.borrower-story__claim-token').count(), { timeout: 5_000 })
      .toBeGreaterThan(0);

    // D8 (re-audit #5): evidence hover-card attaches to Supporting-evidence
    // chips too (110ms open delay — the audit's single 1s manual hover read as
    // "no card"; this confirms coverage is uniform, not chip-family-specific).
    const evidenceChip = page.locator('.chip-row .evidence-chip').first();
    await expect(evidenceChip).toBeVisible({ timeout: 10_000 });
    await evidenceChip.scrollIntoViewIfNeeded();
    await evidenceChip.hover();
    await expect(page.locator('.evidence-hovercard')).toBeVisible({ timeout: 5_000 });
  });

  test('#9 Genie answer offers follow-ups + pins to Home', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await openGeniePanel(page);
    const panel = page.getByRole('dialog', { name: 'Genie chat' });
    await expect(panel).toBeVisible();

    const q = 'How many borrowers across current refreshed coverage are currently in-the-money?';
    await panel.getByLabel('Ask Genie').fill(q);
    await panel.getByRole('button', { name: /Ask/i }).click();
    await expect(panel.getByRole('status')).toBeHidden({ timeout: 60_000 });

    const aiMessage = panel.locator('.genie__msg--ai').last();
    await expect(aiMessage.locator('.bubble')).toBeVisible({ timeout: 40_000 });

    // Follow-up chips: Genie's own or the deterministic fallback — never a dead end.
    await expect
      .poll(() => aiMessage.locator('.filter--question').count(), { timeout: 10_000 })
      .toBeGreaterThan(0);

    // Pin-to-Home is present on this genuine, trusted answer (genie OR trusted_sql
    // — the denylist boundary, not an `=== 'genie'` allowlist).
    const pin = aiMessage.locator('[data-testid="pin-to-home"]');
    await expect(pin).toBeVisible({ timeout: 10_000 });
    await expect(pin).toContainText(/Pin to Home/i);
    await pin.click();
    await expect(pin).toContainText(/Pinned to Home/i);

    // The pin shows up on the Home "Pinned insights" card (shared store).
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    const card = page.locator('.pinned-insights').first();
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card).toContainText(/in-the-money/i);
    // D1 (re-audit #5): the pinned summary is plain text — no leaked markdown
    // markers and no mid-token "(**" on the Home hero.
    const summaryText = (await card.locator('.pinned-insights__a').first().innerText()).trim();
    expect(summaryText).not.toContain('**');
    expect(summaryText).not.toContain('`');

    // Clean up: unpin so the inspection is idempotent across reruns.
    await card.locator('.pinned-insights__unpin').first().click();
  });

  test('D2 (re-audit #5): a governed refusal offers no pin and no synthesized follow-ups', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await openGeniePanel(page);
    const panel = page.getByRole('dialog', { name: 'Genie chat' });
    await expect(panel).toBeVisible();

    // A protected-class query trips the fair-lending refusal path.
    await panel.getByLabel('Ask Genie').fill('What is the average age of borrowers in Illinois?');
    await panel.getByRole('button', { name: /Ask/i }).click();
    await expect(panel.getByRole('status')).toBeHidden({ timeout: 60_000 });

    const aiMessage = panel.locator('.genie__msg--ai').last();
    await expect(aiMessage.locator('.bubble')).toBeVisible({ timeout: 40_000 });
    // No pin button on a non-trusted answer, and no fabricated "drill deeper"
    // pivot chips under the refusal.
    await expect(aiMessage.locator('[data-testid="pin-to-home"]')).toHaveCount(0);
    await expect(aiMessage.locator('.filter--question')).toHaveCount(0);
  });

  test('re-audit #6: Pin-to-Home is also present on the /ask-genie deep-dive', async ({ page }) => {
    // The deep-dive passes `question` to the shared GenieAnswer, so a trusted
    // answer is pinnable there too (re-audit #6 flagged a suspected panel-only
    // gap — this asserts the affordance is present on the deep-dive surface).
    await page.goto('/ask-genie', { waitUntil: 'domcontentloaded' });
    await page
      .locator('textarea[aria-label="Ask Genie — question"]')
      .fill('How many borrowers across current refreshed coverage are currently in-the-money?');
    await page.getByRole('button', { name: /^Ask Genie$/i }).first().click();

    const answerSurface = page.locator('.surface', { hasText: /Source:/i }).first();
    await expect(answerSurface).toBeVisible({ timeout: 90_000 });
    await expect(page.locator('[data-testid="pin-to-home"]')).toBeVisible({ timeout: 10_000 });
  });
});
