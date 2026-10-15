/**
 * A mouse-pressed period preset over a dirty custom draft (W5e C11 residual
 * 2), shared by the Chromium case in sales-loop.fixture.spec.ts and the
 * WebKit case in sales-period-preset.webkit.fixture.spec.ts.
 *
 * The defect had a different shape in each engine. WebKit never focuses a
 * pressed button, so the press blurred the date field with no relatedTarget,
 * SalesPeriodRange applied the draft, and the click then wrote the preset:
 * two history entries, and Back landed on a draft the user never applied.
 * preventDefault alone on the press fixed that but kept focus in the date
 * field, which unmounts when the preset leaves Custom, so focus fell to
 * <body> in both engines. The press now focuses the preset itself.
 *
 * Proof at the rendered layer: one history entry, the preset in the URL,
 * focus on the pressed preset (not <body>), and Back returns to the range the
 * URL held before (the draft never applied).
 */
import type { Page } from '@playwright/test';
import type { AppDriver } from './app';
import { expect } from './test';

const CUSTOM_RANGE = '/analytics?view=sales-ops&sales_period=custom&sales_from=2026-07-01&sales_to=2026-07-10';

export async function expectPresetPressOverDirtyDraft(app: AppDriver, page: Page): Promise<void> {
  const historyLength = () => page.evaluate(() => window.history.length);
  await app.gotoRoute(CUSTOM_RANGE);
  const period = page.locator('.sales-ops-period');
  const from = period.getByLabel('From');
  await expect(from).toHaveValue('2026-07-01');
  const start = page.url();
  const before = await historyLength();

  // A dirty draft: en-US segments are mm/dd/yyyy, ArrowRight reaches the
  // day and ArrowUp moves it. The draft is not applied yet.
  await from.focus();
  await page.keyboard.press('ArrowRight');
  await page.keyboard.press('ArrowUp');
  await expect(from).toHaveValue('2026-07-02');
  await expect(from).toBeFocused();
  expect(page.url(), 'the draft is not applied before the press').toBe(start);

  const preset = period.getByRole('button', { name: '7 days' });
  await preset.click();
  await expect(page).toHaveURL(/[?&]sales_period=7d(&|$)/);
  await expect(preset).toHaveAttribute('aria-pressed', 'true');
  await expect(period.locator('input[type="date"]')).toHaveCount(0);
  await expect(preset, 'focus stays on the pressed preset').toBeFocused();
  expect(await page.evaluate(() => document.activeElement === document.body), 'focus never drops to <body>').toBe(false);
  expect(await historyLength(), 'the press wrote one history entry').toBe(before + 1);

  // Back returns to the range the URL held: the draft never applied.
  await page.goBack();
  await expect(page).toHaveURL(/[?&]sales_period=custom(&|$)/);
  await expect(page).toHaveURL(/[?&]sales_from=2026-07-01(&|$)/);
  await expect(page).toHaveURL(/[?&]sales_to=2026-07-10(&|$)/);
  await expect(period.getByLabel('From')).toHaveValue('2026-07-01');
}
