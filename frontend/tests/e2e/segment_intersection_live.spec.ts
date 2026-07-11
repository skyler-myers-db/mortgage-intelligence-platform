/**
 * S8 — Segment intersection live e2e (refi ∩ investor).
 *
 * Exercises the full intersection flow against real Unity Catalog data:
 *   1. Segment Intelligence: select the Refi Propensity + Investor cards,
 *      switch to "All selected" — the Selected-cohort counter shows the live
 *      intersected count computed server-side in UC.
 *   2. Deep-dive to the Lead Queue: the URL carries segment_codes +
 *      segment_mode=all, the queue renders one removable `.chip` per
 *      segment plus the honest intersection label, the footer total equals
 *      the count the segments page previewed, and the visible rows are
 *      ranked by the S1 canonical opportunity score (non-increasing).
 *   3. Remove the Investor chip: the composed predicate recomputes
 *      server-side — one chip remains, the URL collapses to the single
 *      `segment` param, and the total widens to the refi-only cohort
 *      (a superset of the intersection).
 *
 * Gated behind E2E_LIVE=1 (like real_data.spec.ts). MIP_APP_URL points at
 * the deployed app; a workspace Bearer token short-circuits the Apps OAuth
 * redirect.
 */
import { test, expect, type Page } from '@playwright/test';

const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 to run the segment-intersection live e2e.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER ? { Authorization: `Bearer ${BEARER}` } : {};

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

const REFI_CARD = /Refi Propensity/;
const INVESTOR_CARD = /Investor \/ Multi-Property/;

function parseCount(text: string | null): number {
  const digits = (text ?? '').replace(/[^0-9]/g, '');
  expect(digits.length, `expected a numeric count, got ${JSON.stringify(text)}`).toBeGreaterThan(0);
  return Number(digits);
}

async function cohortTotal(page: Page): Promise<number> {
  const totalNum = page.locator('.segment-mode-control__total .num');
  await expect(totalNum).toBeVisible({ timeout: 30_000 });
  return parseCount(await totalNum.textContent());
}

async function queueTotalMatching(page: Page): Promise<number> {
  // LeadTable footer: "Showing N ranked borrowers of M total matching filters".
  const footer = page.getByText(/of [\d,]+ total matching filters/).first();
  await expect(footer).toBeVisible({ timeout: 30_000 });
  const match = (await footer.textContent())?.match(/of ([\d,]+) total matching filters/);
  expect(match, 'LeadTable footer must report the matching total').toBeTruthy();
  return parseCount(match![1]);
}

test('refi ∩ investor: live intersected count flows into a chip-filtered, score-ranked Lead Queue', async ({ page }) => {
  await page.goto('/segment-intelligence', { waitUntil: 'domcontentloaded', timeout: 60_000 });

  // Segment cards come from live gold.segment_population.
  const refiCard = page.getByRole('button', { name: REFI_CARD });
  const investorCard = page.getByRole('button', { name: INVESTOR_CARD });
  await expect(refiCard).toBeVisible({ timeout: 45_000 });
  await refiCard.click();
  await expect(refiCard).toHaveAttribute('aria-pressed', 'true');
  await investorCard.click();
  await expect(investorCard).toHaveAttribute('aria-pressed', 'true');

  // Compose the intersection server-side.
  await page.getByRole('button', { name: /All selected/ }).click();
  await expect(page).toHaveURL(/segment_codes=refi_propensity%2Cinvestor|segment_codes=refi_propensity,investor/);
  await expect(page).toHaveURL(/segment_mode=all/);

  // Live intersected count (X-Total-Matching from UC for the AND cohort).
  await expect(page.locator('.segment-mode-control__total')).toContainText('Selected cohort', { timeout: 30_000 });
  const intersectionCount = await cohortTotal(page);
  expect(intersectionCount, 'live refi ∩ investor eligible cohort should be non-empty').toBeGreaterThan(0);

  // Continue to the Lead Queue with the intersection applied.
  await page.getByRole('link', { name: /Deep-dive lead queue/ }).click();
  await expect(page).toHaveURL(/\/lead-queue\?.*segment_mode=all/, { timeout: 30_000 });

  const chipRow = page.locator('[aria-label="Active segment filters"]');
  await expect(chipRow).toBeVisible({ timeout: 30_000 });
  await expect(chipRow.locator('.chip')).toHaveCount(2);
  await expect(chipRow).toContainText('Refi Propensity');
  await expect(chipRow).toContainText('Investor / Multi-Property');
  await expect(chipRow).toContainText('Intersection');

  // The queue total is the same server-side intersected count the segments
  // page previewed (same composed predicate, same eligibility defaults).
  const queueTotal = await queueTotalMatching(page);
  expect(queueTotal).toBe(intersectionCount);

  // Ranked rows render, ordered by the S1 canonical opportunity score.
  const scores = page.locator('tbody .score');
  await expect(scores.first()).toBeVisible({ timeout: 30_000 });
  const visibleScores = (await scores.allTextContents())
    .map((text) => parseCount(text))
    .slice(0, 10);
  expect(visibleScores.length).toBeGreaterThan(0);
  for (let i = 1; i < visibleScores.length; i += 1) {
    expect(visibleScores[i], 'rows must be ranked by canonical opportunity score').toBeLessThanOrEqual(visibleScores[i - 1]);
  }

  // Remove the Investor chip → the composed predicate recomputes in UC.
  await page.getByRole('button', { name: 'Remove Investor / Multi-Property segment filter' }).click();
  await expect(chipRow.locator('.chip')).toHaveCount(1);
  await expect(chipRow).not.toContainText('Intersection');
  await expect(page).toHaveURL(/segment=refi_propensity/);
  await expect(page).not.toHaveURL(/segment_codes=/);

  const refiOnlyTotal = await queueTotalMatching(page);
  expect(refiOnlyTotal, 'refi-only cohort must contain the refi ∩ investor intersection').toBeGreaterThanOrEqual(intersectionCount);
  expect(refiOnlyTotal, 'removing a chip must recompute the cohort server-side').not.toBe(0);
});
