/**
 * The four W5e w5-analytics states the visual and axe matrices capture, in
 * both themes (each entered after the route's natural load, never opening an
 * audited read):
 *
 *  - `executive-evidence`: an Analytics Executive KPI drawer open on its
 *    "How we got" (the KPI proof and freshness reads are audit-free);
 *  - `executive-stale`: a manual Refresh that failed over the Executive data
 *    on screen, so the stale note shows and the data stays;
 *  - `economics-scatter-tip`: a keyboard-focused density cell with its tip;
 *  - `sales-ops-workload`: the Sales ops per-officer workload table.
 *
 * Each entry names the route path to load and returns the region to scan or
 * capture.
 */
import type { Locator, Page } from '@playwright/test';
import type { AppDriver } from './app';
import type { MockApi } from './mockApi';
import { expect } from './test';

export type AnalyticsW5eState = 'executive-evidence' | 'executive-stale' | 'economics-scatter-tip' | 'sales-ops-workload';

export const ANALYTICS_W5E_STATES: readonly AnalyticsW5eState[] = [
  'executive-evidence',
  'executive-stale',
  'economics-scatter-tip',
  'sales-ops-workload',
];

/** The fixture route (routes.ts name) and path each state loads. */
export const ANALYTICS_W5E_ROUTE: Readonly<Record<AnalyticsW5eState, { route: string; path: string }>> = {
  'executive-evidence': { route: 'analytics-executive', path: '/analytics' },
  'executive-stale': { route: 'analytics-executive', path: '/analytics' },
  'economics-scatter-tip': { route: 'analytics-economics', path: '/analytics?view=economics' },
  'sales-ops-workload': { route: 'analytics-sales-ops', path: '/analytics?view=sales-ops' },
};

/** The first Executive KPI card's evidence chip (Addressable Borrowers). */
export function executiveKpiChip(page: Page): Locator {
  return page.locator('#main-content .kpi-row').first().locator('.kpi .kpi__source .evidence-chip').first();
}

/** Enter `state` on its loaded route; returns the region the state is about. */
export async function enterAnalyticsW5eState(app: AppDriver, page: Page, mockApi: MockApi, state: AnalyticsW5eState): Promise<Locator> {
  switch (state) {
    case 'executive-evidence': {
      const drawer = await app.openEvidenceDrawer(executiveKpiChip(page));
      const how = drawer.locator('[data-testid="evidence-how-we-got"]');
      await expect(how.getByRole('heading', { level: 3 })).toHaveText(/^How we got [\d,]+$/);
      await expect(drawer.locator('[data-testid="evidence-freshness-foot"]')).toBeVisible();
      await app.settle();
      return drawer;
    }
    case 'executive-stale': {
      await expect(page.locator('#main-content .funnel-sankey')).toBeVisible();
      mockApi.degrade('/api/analytics/executive', { status: 500, method: 'GET', body: { detail: 'fixture: executive refresh failed' } });
      await page.locator('.proto-hero__actions [data-testid="fetched-at"]').getByRole('button', { name: 'Refresh executive analytics' }).click();
      const note = page.locator('#main-content [data-testid="stale-data-note"]');
      await expect(note).toHaveCount(1);
      await expect(page.locator('#main-content .funnel-sankey')).toBeVisible();
      await app.settle();
      return page.locator('#main-content');
    }
    case 'economics-scatter-tip': {
      const bin = page.locator('#main-content .analytics-scatter__bin').first();
      await expect(bin).toBeVisible();
      await bin.focus();
      const tip = page.getByTestId('scatter-tip');
      await expect(tip).toBeVisible();
      await app.settle();
      return page.locator('#main-content [data-testid="equity-spread-scatter"]');
    }
    case 'sales-ops-workload': {
      const table = page.locator('#main-content [data-testid="sales-workload"]');
      await expect(table.locator('tbody a').first()).toBeVisible();
      await table.scrollIntoViewIfNeeded();
      await app.settle();
      return table;
    }
  }
}
