import { type Locator, type Page, expect } from '@playwright/test';

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

/**
 * The evidence drawer: a native modal `dialog.drawer` since wave 4a overlays
 * (it replaced the `<aside class="drawer">` and its separate scrim element).
 * Borrower 360's proof drawer (`.drawer.proof-drawer`) and a Genie answer's
 * proof drawer (`.drawer.genie-proof-drawer`) share the block class, so both
 * are excluded. The dialog stays mounted while closed; `.is-open` marks it
 * open.
 * Mirrors tests/e2e/fixture/app.ts `evidenceDrawer()`.
 */
export const EVIDENCE_DRAWER = 'dialog.drawer:not(.proof-drawer):not(.genie-proof-drawer)';

export function evidenceDrawer(page: Page): Locator {
  return page.locator(EVIDENCE_DRAWER);
}

/** The evidence drawer only while it is open (none matches once it closes). */
export function openEvidenceDrawer(page: Page): Locator {
  return page.locator(`${EVIDENCE_DRAWER}.is-open`);
}

/**
 * Light-dismiss a native modal dialog the way a pointer user does: a press on
 * its `::backdrop`, outside the dialog's own box (useModalDialog's
 * `backdrop: 'outside'`). Entry motion settles first: a point measured beside
 * a drawer that is still sliding in can land inside its settled box
 * (overlays.fixture.spec.ts `settled`).
 */
export async function clickDialogBackdrop(page: Page, dialog: Locator): Promise<void> {
  await dialog.evaluate((el) =>
    Promise.all(el.getAnimations({ subtree: true }).map((animation) => animation.finished.catch(() => undefined))).then(
      () => undefined,
    ),
  );
  const point = await dialog.evaluate((el) => {
    const box = el.getBoundingClientRect();
    return box.left > 120 ? { x: box.left - 60, y: box.top + 60 } : { x: 24, y: 24 };
  });
  await page.mouse.click(point.x, point.y);
}
