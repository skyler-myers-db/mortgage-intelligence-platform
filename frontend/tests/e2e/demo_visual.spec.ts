import { test, expect } from '@playwright/test';

const LIVE = process.env.E2E_LIVE === '1';
test.skip(!LIVE, 'Set E2E_LIVE=1 to run demo visual regression checks.');

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER
  ? { Authorization: `Bearer ${BEARER}` }
  : {};

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

test.describe('Module 0 demo visual baselines', () => {
  test('Ask Genie empty state is polished at desktop and mobile widths', async ({ page }) => {
    await page.goto('/ask-genie');
    await expect(page.getByText('Ready for governed analysis')).toBeVisible();
    await expect(page.locator('.layoutA-grid')).toHaveScreenshot('ask-genie-empty-desktop.png', {
      animations: 'disabled',
      maxDiffPixelRatio: 0.03,
    });

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.locator('.layoutA-grid')).toHaveScreenshot('ask-genie-empty-mobile.png', {
      animations: 'disabled',
      maxDiffPixelRatio: 0.04,
    });
  });

  test('Segment filter row keeps aligned controls and honest pending-source copy', async ({ page }) => {
    await page.goto('/segment-intelligence');
    const filterRow = page.locator('.filter-row[aria-label="Secondary borrower filters"]');
    await expect(filterRow).toBeVisible({ timeout: 20_000 });
    await expect(filterRow.getByText(/Delta shares pending/)).toBeVisible();
    await expect(filterRow).toHaveScreenshot('segment-filter-row.png', {
      animations: 'disabled',
      maxDiffPixelRatio: 0.03,
    });
  });

  test('Segment card grid preserves the prototype contract without dynamic-count drift', async ({ page }) => {
    await page.goto('/segment-intelligence');
    const grid = page.locator('.seg-grid');
    await expect(grid).toBeVisible({ timeout: 20_000 });
    await expect(grid).toHaveScreenshot('segment-card-grid.png', {
      animations: 'disabled',
      mask: [grid.locator('.seg-card__count'), grid.locator('.seg-card__meta')],
      maxDiffPixelRatio: 0.04,
    });
  });
});
