/**
 * The page states the rendered-layer gates (axe.fixture.spec.ts,
 * visual.fixture.spec.ts) scan and capture, entered the same way in both so
 * an axe verdict and a pixel baseline describe the same DOM.
 *
 * Two kinds:
 *  - LOAD states (`degraded`, `empty`, `read-failed`, `stale-note`) change what the
 *    route's natural load receives, so `prepareState` registers their
 *    fixture BEFORE navigation;
 *  - OVERLAY states (`evidence-drawer`, `command-palette`, `genie`,
 *    `filter-menu`, `expanded-row`, `triage`, `delta-explainer`,
 *    `evidence-how-we-got`, `evidence-under-the-hood`) are opened
 *    after the natural load by
 *    `enterState`, which never opens an audited read: the Borrower 360
 *    proof drawer is not among them, and visual.ts's audited-read guard
 *    checks every state after it is entered.
 *
 * `read-failed` (audit quality-06) is the non-bannered failed read: the
 * route's reads answer the backend's 503 `retries_exhausted` body with no
 * Retry-After (lib/retryPlan.ts plans it terminal, so nothing re-sends and no
 * countdown ticks) while /api/health stays OK, so no DegradedBanner names the
 * outage. The bannered outage is error-surfaces.fixture.spec.ts's.
 */
import type { Page } from '@playwright/test';
import type { HealthPayload } from '../../../src/lib/apiTypes';
import type { LeadSummary } from '../../../src/types';
import type { AppDriver } from './app';
import { WAREHOUSE_OUTAGE_503 } from './data/errorSurfaces';
import { HEALTH_OK } from './data/shell';
import { json, type MockApi } from './mockApi';
import { expect } from './test';

export const OVERLAY_STATES = ['evidence-drawer', 'command-palette', 'genie'] as const;

export type FixtureState =
  | 'default'
  | (typeof OVERLAY_STATES)[number]
  | 'filter-menu'
  | 'expanded-row'
  | 'triage'
  | 'degraded'
  | 'empty'
  | 'read-failed'
  | 'run-dialog-open'
  | 'stale-note'
  | 'delta-explainer'
  | 'evidence-how-we-got'
  | 'evidence-under-the-hood';

/** The reads `read-failed` fails, per route name (routes.ts). */
export const READ_FAILED_ENDPOINTS: Readonly<Record<string, readonly string[]>> = {
  'lead-queue': ['/api/leads'],
  // Both Segment reads, so the map is not half-loaded beside failed cards.
  'segment-intelligence': ['/api/segments', '/api/geo/state-rollups'],
};

/**
 * The reads `stale-note` (W5b delivery-06 client half) serves with
 * `X-Data-Last-Good-At`, per route name: the server retained their values
 * after a failed refresh, so the page shows one StaleDataNote.
 */
export const STALE_NOTE_ENDPOINTS: Readonly<Record<string, readonly string[]>> = {
  'segment-intelligence': ['/api/segments', '/api/geo/state-rollups'],
};
export const STALE_NOTE_LAST_GOOD_AT = '2026-07-14T08:00:00Z';

/** A failed-read surface that is neither the bannered calm line nor a pending one. */
export const FAILED_READ_SURFACE =
  '#main-content [data-async-status]:not([data-async-status="bannered"]):not([data-async-status="pending"])';

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

/**
 * Register a load state's fixture. Call before `app.gotoRoute`; a no-op for
 * overlay states. `routeName` (routes.ts) picks the reads `read-failed` fails.
 */
export function prepareState(mockApi: MockApi, state: FixtureState, routeName?: string): void {
  if (state === 'read-failed') {
    const endpoints = READ_FAILED_ENDPOINTS[routeName ?? ''];
    if (!endpoints) throw new Error(`read-failed names no reads for route ${routeName ?? '(none)'}`);
    for (const endpoint of endpoints) mockApi.degrade(endpoint, WAREHOUSE_OUTAGE_503);
  } else if (state === 'degraded') {
    mockApi.register<HealthPayload>('GET', '/api/health', () => json(WAREHOUSE_DOWN_HEALTH));
  } else if (state === 'stale-note') {
    const endpoints = STALE_NOTE_ENDPOINTS[routeName ?? ''];
    if (!endpoints) throw new Error(`stale-note names no reads for route ${routeName ?? '(none)'}`);
    for (const endpoint of endpoints) mockApi.withHeaders('GET', endpoint, { 'X-Data-Last-Good-At': STALE_NOTE_LAST_GOOD_AT });
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
    case 'triage':
      // D-approval-flow-a2: the deck opens on its first card, drafting nothing.
      await page.getByTestId('lead-triage-enter').click();
      await expect(page.getByTestId('triage-deck')).toBeVisible();
      await expect(page.locator('.triage__card .triage__name')).toBeFocused();
      break;
    case 'degraded':
      await expect(page.locator('.degraded-banner').first(), 'the warehouse-down banner is the degraded surface').toBeVisible();
      break;
    case 'empty':
      await expect(page.locator('table.tbl tbody tr[aria-expanded], table.tbl tbody [aria-expanded]')).toHaveCount(0);
      break;
    case 'read-failed':
      await expect(page.locator(FAILED_READ_SURFACE).first(), 'the failed read renders its own surface').toBeVisible();
      await expect(page.locator('.degraded-banner'), 'health is OK: no banner names the outage').toHaveCount(0);
      break;
    case 'run-dialog-open':
      // Administration's Data operations confirm (critic-09): opening it posts nothing.
      await page.locator('#data-operations').getByRole('button', { name: 'Run', exact: true }).first().click();
      await expect(page.locator('dialog.admin-run-dialog')).toHaveAttribute('open', '');
      break;
    case 'stale-note': {
      const note = page.locator('#main-content [data-testid="stale-data-note"]');
      await expect(note, 'one retained-value note').toHaveCount(1);
      await note.scrollIntoViewIfNeeded();
      break;
    }
    case 'delta-explainer': {
      // Home's refi trigger (WHY NOW): its chip's drawer explains the change (audit-free read).
      const chip = page.locator('.home-answer .login-summary .home-answer__trigger', { hasText: 'refi screen' }).locator('.evidence-chip');
      const drawer = await app.openEvidenceDrawer(chip);
      await expect(drawer.locator('[data-testid="delta-explainer"] table tbody tr').first()).toBeVisible();
      break;
    }
    case 'evidence-how-we-got':
    case 'evidence-under-the-hood': {
      // W5c w5-evidence-drawer (flow-06): Home's refi KPI card (the plain
      // `evidence-drawer` state already opens the first one), its number,
      // definition and server filter chips; then its reproduce SQL. Both
      // reads (freshness, the KPI proof) are audit-free and open-only.
      const drawer = await app.openEvidenceDrawer(
        page.locator('.kpi .kpi__source .evidence-chip', { hasText: 'Rate + equity screen' }),
      );
      const how = drawer.locator('[data-testid="evidence-how-we-got"]');
      await expect(how.locator('.chip').first()).toBeVisible();
      await expect(drawer.locator('[data-testid="evidence-freshness-foot"]')).toBeVisible();
      if (state === 'evidence-under-the-hood') {
        await drawer.getByRole('tab', { name: 'Under the hood' }).click();
        await expect(drawer.locator('[data-testid="evidence-kpi-proof"] .proof-sql-card__sql')).toBeVisible();
      }
      break;
    }
  }
  await app.settle();
}
