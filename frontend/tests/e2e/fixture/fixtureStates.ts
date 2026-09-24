/**
 * The page states the rendered-layer gates (axe.fixture.spec.ts,
 * visual.fixture.spec.ts) scan and capture, entered the same way in both so
 * an axe verdict and a pixel baseline describe the same DOM.
 *
 * Two kinds:
 *  - LOAD states (`degraded`, `empty`) change what the route's natural load
 *    receives, so `prepareState` registers their fixture BEFORE navigation;
 *  - OVERLAY states (`evidence-drawer`, `command-palette`, `genie`,
 *    `filter-menu`, `expanded-row`) are opened after the natural load by
 *    `enterState`, which never opens an audited read: the Borrower 360
 *    proof drawer is not among them, and visual.ts's audited-read guard
 *    checks every state after it is entered.
 */
import type { Page } from '@playwright/test';
import type { HealthPayload } from '../../../src/lib/apiTypes';
import type { LeadSummary } from '../../../src/types';
import type { AppDriver } from './app';
import { HEALTH_OK } from './data/shell';
import { json, type MockApi } from './mockApi';
import { expect } from './test';

export const OVERLAY_STATES = ['evidence-drawer', 'command-palette', 'genie'] as const;

export type FixtureState =
  | 'default'
  | (typeof OVERLAY_STATES)[number]
  | 'filter-menu'
  | 'expanded-row'
  | 'degraded'
  | 'empty';

/**
 * `/api/health` the way a cold warehouse answers it (its probe fails), as
 * map-encoding.fixture.spec.ts does: the DegradedBanner is the one explicit
 * degraded surface. The answer is constant, so the banner's copy does not
 * change between health polls (no countdown, no retry attempt counter).
 */
export const WAREHOUSE_DOWN_HEALTH: HealthPayload = {
  ...HEALTH_OK,
  status: 'degraded',
  dependencies: { warehouse: 'down', lakebase: 'up', genie: 'up' },
};

/** Register a load state's fixture. Call before `app.gotoRoute`; a no-op for overlay states. */
export function prepareState(mockApi: MockApi, state: FixtureState): void {
  if (state === 'degraded') {
    mockApi.register<HealthPayload>('GET', '/api/health', () => json(WAREHOUSE_DOWN_HEALTH));
  } else if (state === 'empty') {
    mockApi.register<LeadSummary[]>('GET', '/api/leads', () =>
      json<LeadSummary[]>([], { headers: { 'X-Total-Matching': '0', 'X-Returned-Rows': '0' } }),
    );
  }
}

/**
 * Bring the loaded route into `state` and wait until the reads that state
 * started have landed (the evidence drawer loads governed asset metadata
 * after it opens), so every run sees the same DOM.
 */
export async function enterState(app: AppDriver, page: Page, state: FixtureState): Promise<void> {
  switch (state) {
    case 'default':
      break;
    case 'evidence-drawer':
      await app.openEvidenceDrawer(page.locator('.evidence-chip:visible').first());
      break;
    case 'command-palette':
      await app.openCommandPalette();
      break;
    case 'genie':
      await app.openGenie();
      break;
    case 'filter-menu':
      await app.openFilterMenu('STATE');
      break;
    case 'expanded-row':
      await app.expandFirstLeadRow();
      break;
    case 'degraded':
      await expect(page.locator('.degraded-banner').first(), 'the warehouse-down banner is the degraded surface').toBeVisible();
      break;
    case 'empty':
      await expect(page.locator('table.tbl tbody tr[aria-expanded], table.tbl tbody [aria-expanded]')).toHaveCount(0);
      break;
  }
  await app.settle();
}
