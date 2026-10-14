/**
 * The evidence drawer at the rendered layer (W5c w5-evidence-drawer), at
 * 1440x900 in both themes, against the production build and the fixture API:
 *
 *  (a) a non-admin opens Home's refi KPI: "How we got {value}", the server's
 *      filter chips, the as-of, the freshness chip and its one refresh line,
 *      zero /api/admin/* calls, axe clean (critic-03, flow-06);
 *  (b) an administrator's chip comes from the same every-user read;
 *  (c) a 503 on that read says "Freshness not loaded", never "Stale";
 *  (d) a source no readiness row covers reads "Freshness not tracked";
 *  (e) Under the hood shows the server-emitted reproduce SQL and Copy SQL,
 *      axe clean (flow-06 phase 2, flow-10);
 *  (f) a since-last-login number keeps its Delta Explainer; the competitor
 *      lien one says it is not snapshotted (wow-ai-3);
 *  (g) hovering a chip past the hover-intent delay asks for no freshness and
 *      no proof: drawer data is fetched only while it is open;
 *  (h) the dialog carries data-rum-target="drawer" (D-platform-process-d2).
 * No state here opens an audited read.
 */
import type { Locator, Page } from '@playwright/test';
import type { SessionResponse } from '../../../src/types';
import { expectAxeClean } from './axe';
import { SESSION } from './data/shell';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const NON_ADMIN: SessionResponse = { ...SESSION, can_access_admin: false };
/** components/ui/anchorPlacement.ts SHOW_DELAY_MS (350) plus slack. */
const PAST_SHOW_DELAY_MS = 600;
const FRESHNESS_READ = /^\/api\/assets\/[^/]+\/freshness$/;
const KPI_PROOF_READ = /^\/api\/kpi-proof$/;

function itmChip(page: Page): Locator {
  return page.locator('.kpi .kpi__source .evidence-chip', { hasText: 'Rate + equity screen' });
}

function calls(mockApi: { calls: ReadonlyArray<{ path: string }> }, pattern: RegExp): number {
  return mockApi.calls.filter((call) => pattern.test(call.path)).length;
}

for (const theme of FIXTURE_THEMES) {
  test.describe(`${theme}`, () => {
    test.beforeEach(async ({ app, page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await app.setTheme(theme);
    });

    test('(a) a non-admin KPI drawer leads with How we got and every-user freshness', async ({ app, mockApi, page }) => {
      mockApi.register<SessionResponse>('GET', '/api/session', () => ({ body: NON_ADMIN }));
      await app.gotoRoute('/');
      const naturalLoad = markNaturalLoad(mockApi);
      const drawer = await app.openEvidenceDrawer(itmChip(page));

      const how = drawer.locator('[data-testid="evidence-how-we-got"]');
      await expect(how.getByRole('heading', { level: 3 })).toHaveText(/^How we got [\d,]+$/);
      await expect(how).toContainText('Borrowers who clear the refinance economics screen');
      await expect(how.locator('.chip')).toHaveText(['in_the_money = TRUE']);
      await expect(how.locator('time')).toBeVisible();
      await expect(how).toContainText('Data as of');
      await expect(drawer.locator('.source-freshness')).toHaveText('Fresh');
      await expect(drawer.getByTestId('evidence-freshness-foot')).toHaveText(/^Last refresh .+ · via UC Gold Borrower 360$/);
      await expect(drawer).not.toContainText('Admin-only freshness');
      await expect(drawer.getByRole('link', { name: 'View asset details' })).toHaveCount(0);
      await app.settle();

      expect(mockApi.calls.filter((call) => /^\/api\/admin\//.test(call.path))).toEqual([]);
      expect(calls(mockApi, FRESHNESS_READ)).toBe(1);
      expect(calls(mockApi, KPI_PROOF_READ)).toBe(1);
      await expectAxeClean(page, { key: { route: 'home', state: 'evidence-how-we-got-non-admin' }, theme, known: {}, include: 'dialog.drawer' });
      expectNoAuditedReadSince(mockApi, naturalLoad, 'home · non-admin KPI drawer');
    });

    test('(b) an administrator\'s chip comes from the same freshness read', async ({ app, mockApi, page }) => {
      await app.gotoRoute('/');
      const drawer = await app.openEvidenceDrawer(itmChip(page));
      await expect(drawer.locator('.source-freshness')).toHaveText('Fresh');
      await expect(drawer.getByTestId('evidence-freshness-foot')).toContainText('via UC Gold Borrower 360');
      await app.settle();
      expect(calls(mockApi, FRESHNESS_READ)).toBe(1);
      expect(mockApi.calls.filter((call) => /^\/api\/admin\/assets\//.test(call.path))).toHaveLength(1);
    });

    test('(c) a failed freshness read says so, never "Stale"', async ({ app, page }) => {
      app.degrade('/api/assets/:assetKey/freshness', {
        status: 503,
        method: 'GET',
        body: { detail: 'warehouse unavailable (fixture)' },
      });
      await app.gotoRoute('/');
      const drawer = await app.openEvidenceDrawer(itmChip(page));
      await expect(drawer.locator('.source-freshness')).toHaveText('Freshness not loaded');
      await expect(drawer).toContainText('This does not mean the source is stale.');
      await expect(drawer.locator('.source-summary').getByRole('button', { name: 'Retry' })).toBeVisible();
      await expect(drawer.locator('.source-freshness')).not.toHaveText(/Stale|unavailable/);
      await expect(drawer.getByTestId('evidence-freshness-foot')).toHaveCount(0);
    });

    test('(d) a source no readiness row covers reads "Freshness not tracked"', async ({ app, page }) => {
      await app.gotoRoute('/admin-config');
      // The data estate's lane proof chip opens the source-readiness ledger,
      // which is not itself a readiness row (asset_registry: not tracked).
      const proof = page.locator('.data-estate__lane-proof').first();
      await proof.scrollIntoViewIfNeeded();
      const drawer = await app.openEvidenceDrawer(proof);
      await expect(drawer.locator('.source-freshness')).toHaveText('Freshness not tracked');
      await expect(drawer).toContainText('This asset has no source-readiness row; its freshness is not tracked.');
      await expect(drawer.getByTestId('evidence-freshness-foot')).toHaveCount(0);
    });

    test('(e) Under the hood shows the reproduce SQL and Copy SQL', async ({ app, mockApi, page }) => {
      await app.gotoRoute('/');
      const drawer = await app.openEvidenceDrawer(itmChip(page));
      await expect(drawer.locator('[data-testid="evidence-how-we-got"] .chip').first()).toBeVisible();
      await drawer.getByRole('tab', { name: 'Under the hood' }).click();
      const proof = drawer.getByTestId('evidence-kpi-proof');
      await expect(proof.locator('.proof-sql-card__sql')).toContainText('AS high_intent_leads');
      await expect(proof).toContainText('Column high_intent_leads of this statement is the number on the card.');
      await expect(proof.getByRole('button', { name: 'Copy SQL' })).toBeVisible();
      await expect(drawer).toContainText('How these assets are listed');
      await expect(drawer).not.toContainText('Compact semantics');
      await app.settle();
      // One proof read, shared by both tabs.
      expect(calls(mockApi, KPI_PROOF_READ)).toBe(1);
      await expectAxeClean(page, { key: { route: 'home', state: 'evidence-under-the-hood-spec' }, theme, known: {}, include: 'dialog.drawer' });
    });

    test('(f) since-last-login numbers keep their Delta Explainer; competitor liens are not snapshotted', async ({ app, page }) => {
      await app.gotoRoute('/');
      const why = page.locator('.home-answer .login-summary');
      const refi = why.locator('.home-answer__trigger', { hasText: 'refi screen' }).locator('.evidence-chip');
      let drawer = await app.openEvidenceDrawer(refi);
      await expect(drawer.locator('[data-testid="delta-explainer"] table tbody tr').first()).toBeVisible();
      await expect(drawer.locator('[data-testid="evidence-how-we-got"] h3')).toHaveText(/^How we got \+/);
      await drawer.getByRole('button', { name: 'Close drawer' }).click();
      await expect(drawer).not.toHaveClass(/is-open/);

      const lien = why.locator('.home-answer__trigger', { hasText: 'competitor lien' }).locator('.evidence-chip');
      drawer = await app.openEvidenceDrawer(lien);
      await expect(drawer.getByTestId('delta-explainer-not-snapshotted')).toHaveText(
        'Per-state attribution is not snapshotted for this measure yet.',
      );
      await expect(drawer).not.toContainText('No daily funnel snapshot covers this period yet');
    });

    test('(g) hovering a chip past the hover-intent delay asks for no drawer data', async ({ app, mockApi, page }) => {
      await app.gotoRoute('/');
      await app.settle();
      const before = { freshness: calls(mockApi, FRESHNESS_READ), proof: calls(mockApi, KPI_PROOF_READ) };
      await itmChip(page).hover();
      await page.waitForTimeout(PAST_SHOW_DELAY_MS);
      await expect(page.locator('.evidence-hovercard')).toBeVisible();
      await app.settle();
      expect({ freshness: calls(mockApi, FRESHNESS_READ), proof: calls(mockApi, KPI_PROOF_READ) }).toEqual(before);
      expect(before).toEqual({ freshness: 0, proof: 0 });
    });

    test('(h) the evidence dialog is a field-attribution target', async ({ app, page }) => {
      await app.gotoRoute('/');
      const drawer = await app.openEvidenceDrawer(itmChip(page));
      await expect(drawer).toHaveAttribute('data-rum-target', 'drawer');
    });
  });
}
