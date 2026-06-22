import { test, expect, type Locator, type Page } from '@playwright/test';

const APP_URL = process.env.MIP_APP_URL || 'http://127.0.0.1:5173';
const BEARER = process.env.MIP_BEARER_TOKEN || process.env.DATABRICKS_TOKEN || '';
const AUTH_HEADERS: Record<string, string> = BEARER
  ? { Authorization: `Bearer ${BEARER}` }
  : {};

const LIVE = process.env.E2E_LIVE === '1';
const MOCKED_LAYOUT_CANARY = process.env.GENIE_PROOF_LAYOUT_MOCKED === '1';
test.skip(
  LIVE && !MOCKED_LAYOUT_CANARY,
  'genie_proof_layout.spec.ts route-fulfills Genie; run with GENIE_PROOF_LAYOUT_MOCKED=1 when counted as mocked layout coverage',
);

test.use({ baseURL: APP_URL, extraHTTPHeaders: AUTH_HEADERS });

type Box = NonNullable<Awaited<ReturnType<Locator['boundingBox']>>>;

async function box(locator: Locator, label: string): Promise<Box> {
  const rect = await locator.boundingBox();
  expect(rect, `${label} should have a layout box`).toBeTruthy();
  return rect as Box;
}

function intersects(a: Box, b: Box): boolean {
  return !(
    a.x + a.width <= b.x ||
    b.x + b.width <= a.x ||
    a.y + a.height <= b.y ||
    b.y + b.height <= a.y
  );
}

async function mockGenieProofAnswer(page: Page) {
  await page.route(/\/api\/(?:v1\/)?genie\/start$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        conversation_id: null,
        trusted_assets: ['mip.gold.borrower_360', 'mip.gold.evidence_events'],
      }),
    });
  });
  await page.route(/\/api\/(?:v1\/)?genie\/message$/, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        conversation_id: 'conv-proof-layout',
        message_id: 'msg-proof-layout',
        question_hash: 'hash-proof-layout',
        question: 'Show the retention proof layout.',
        answer: 'There are 304 retention-list borrowers with recent competitor-lien evidence.',
        source: 'trusted_sql',
        trusted_assets: [
          'mip.gold.borrower_360',
          'mip.gold.evidence_events',
          'mip.semantics.borrower_opportunity_metric_view',
        ],
        row_count: 304,
        proof: {
          source_assets: [
            'mip.gold.borrower_360',
            'mip.gold.evidence_events',
            'mip.semantics.borrower_opportunity_metric_view',
          ],
          row_count: 304,
          trusted: true,
          elapsed_ms: 1842,
          data_freshness: [
            {
              asset: 'mip.gold.borrower_360',
              refreshed_at: '2026-05-11T16:52:00Z',
              status: 'live',
            },
            {
              asset: 'mip.gold.evidence_events',
              refreshed_at: '2026-05-11T16:50:00Z',
              status: 'live',
            },
          ],
          filters: [
            "array_contains(b.segment_codes, 'retention') AND e.signal_type = 'competitor_lien'",
          ],
          known_data_gaps: [],
        },
        table_rows: [
          {
            borrower_id: 'B-102FL7THC6Q3L',
            city: 'Calumet City',
            state: 'IL',
            opportunity_score: 88,
          },
        ],
        actions: [],
      }),
    });
  });
}

test.describe('Genie proof drawer layout', () => {
  test('trust, row count, source assets, and freshness do not overlap', async ({ page }) => {
    await mockGenieProofAnswer(page);

    await page.goto('/ask-genie');
    await page.locator('textarea[aria-label="Ask Genie — question"]').fill('Show the retention proof layout.');
    await page.getByRole('button', { name: /^Ask Genie$/i }).first().click();
    await page.getByRole('button', { name: /Show proof/i }).click();

    const drawer = page.getByRole('dialog', { name: /Genie answer proof/i });
    await expect(drawer).toBeVisible();
    await page.evaluate(() => document.fonts?.ready);
    await page.waitForTimeout(350);
    const proof = drawer.locator('.genie-proof').first();
    const trustChip = proof.locator('.chip--success', { hasText: /Trusted SELECT on curated assets/i }).first();
    const rowsValue = proof.locator('.genie-proof__value').filter({ hasText: /^304$/ }).first();
    const sourceSection = proof.locator('.genie-proof__section', { hasText: /Source UC assets/i }).first();
    const freshnessSection = proof.locator('.genie-proof__section', { hasText: /Data freshness/i }).first();

    await expect(trustChip).toBeVisible();
    await expect(rowsValue).toBeVisible();
    await expect(sourceSection).toBeVisible();
    await expect(freshnessSection).toBeVisible();

    const drawerBox = await box(drawer, 'proof drawer');
    const trustBox = await box(trustChip, 'trust chip');
    const rowsBox = await box(rowsValue, 'rows value');
    const sourceBox = await box(sourceSection, 'source assets section');
    const freshnessBox = await box(freshnessSection, 'data freshness section');

    expect(intersects(trustBox, rowsBox), 'Trust chip must not overlap Rows value').toBe(false);
    expect(intersects(sourceBox, freshnessBox), 'Source UC assets must not visually crowd Data freshness').toBe(false);

    for (const [label, rect] of [
      ['trust chip', trustBox],
      ['rows value', rowsBox],
      ['source assets', sourceBox],
      ['data freshness', freshnessBox],
    ] as const) {
      expect(rect.x, `${label} should stay inside drawer left edge`).toBeGreaterThanOrEqual(drawerBox.x);
      expect(rect.x + rect.width, `${label} should stay inside drawer right edge`).toBeLessThanOrEqual(
        drawerBox.x + drawerBox.width + 1,
      );
    }
  });
});
