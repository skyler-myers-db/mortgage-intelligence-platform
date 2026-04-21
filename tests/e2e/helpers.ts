import { type Page, expect } from '@playwright/test';

/**
 * Waits for a KpiCard to finish its count-up animation and display the
 * expected final value. KpiCard animates numbers over ~1200ms, so we assert on
 * the final text rather than the in-flight intermediate values.
 */
export async function expectKpiValue(page: Page, label: string, finalText: string | RegExp): Promise<void> {
  // KpiCard root is `.kpi`; label sits in `.kpi__label`, value in `.kpi__value`.
  const card = page.locator('.kpi', { has: page.getByText(label, { exact: true }) }).first();
  await expect(card).toBeVisible();
  const value = card.locator('.kpi__value').first();
  if (finalText instanceof RegExp) {
    await expect(value).toHaveText(finalText, { timeout: 3_000 });
  } else {
    await expect(value).toContainText(finalText, { timeout: 3_000 });
  }
}

/**
 * Clears approval state for the target borrower by opening an incognito-like
 * fresh state. We rely on AppContext being in-memory, so a page.reload()
 * wipes any prior approval from test order.
 */
export async function resetApprovalState(page: Page): Promise<void> {
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.evaluate(() => window.localStorage.clear());
}
