/**
 * The proof drawer's Math tab after W5c (lane w5-gold-slot-cache, audits
 * wow-ai-1 and wow-stage-4) at the rendered layer, 1440 x 900.
 *
 * THE TRAPS: every GET /api/borrowers/{id} writes VIEW_BORROWER and every
 * GET /api/borrowers/{id}/proof writes VIEW_BORROWER_PROOF. The margins and
 * "Crossed the line" must add neither: the chart reads the dossier from the
 * cache only and the audit-free GET /analytics/rate-window only while the
 * drawer is open for an eligible (FIX) borrower.
 *
 *  - Borrower 360 (dark and light): the natural load reads no proof; "Show
 *    scoring math" reads it once and no second dossier; the Math tab shows
 *    the margins with their note and the exact crossing caption; at most one
 *    rate-window read; the table toggle works; axe-clean with the chart and
 *    with the table open; no surface overflow; the chart inks resolve to the
 *    theme tokens.
 *  - Reduced motion: no animation on the chart; the draw-in runs otherwise.
 *  - An ARM borrower: the FIX-only state with zero rate-window reads.
 *  - Lead Queue: the preview's anatomy drawer shows the margins and the
 *    "open the dossier" note, with zero dossier and zero rate-window reads.
 */
import type { Locator, Page } from '@playwright/test';
import { expectAxeClean, type AxeTheme } from './axe';
import { registerAnatomyProof } from './data/scoreAnatomy';
import {
  SPREAD_ARM_BORROWER,
  SPREAD_CAPTION,
  SPREAD_FIX_BORROWER,
  registerSpreadHistoryBorrowers,
} from './data/spreadHistory';
import type { MockApi } from './mockApi';
import { expectNoAuditedReadSince, expectNoSurfaceOverflow, markNaturalLoad } from './visual';
import { expect, test } from './test';

const MARGINS_NOTE = 'Marketing prioritization, not a credit decision.';
const HISTORY_NOTE = "Today's rule and today's equity applied to past rates. History, not a forecast.";

function count(mockApi: MockApi, pattern: RegExp): number {
  return mockApi.calls.filter((call) => call.method === 'GET' && pattern.test(call.path)).length;
}

const proofCalls = (mockApi: MockApi) => count(mockApi, /^\/api(?:\/v1)?\/borrowers\/[^/]+\/proof$/);
const dossierCalls = (mockApi: MockApi) => count(mockApi, /^\/api(?:\/v1)?\/borrowers\/B-[0-9A-Z]{13}$/);
const rateWindowCalls = (mockApi: MockApi) => count(mockApi, /^\/api(?:\/v1)?\/analytics\/rate-window$/);

async function openMath(page: Page): Promise<Locator> {
  const card = page.locator('.surface', { has: page.locator('[data-testid="score-anatomy-spine"]') });
  await card.getByRole('button', { name: /Show scoring math/ }).click();
  const drawer = page.locator('.proof-drawer.is-open');
  await expect(drawer).toBeVisible();
  await expect(drawer.locator('.proof-tab.is-active')).toHaveText('Math');
  return drawer;
}

/** The resolved color of a CSS color expression, evaluated inside `.spread-history`. */
async function resolvedInk(chart: Locator, expression: string): Promise<string> {
  return chart.evaluate((root, value) => {
    const probe = document.createElement('span');
    probe.style.color = value;
    root.appendChild(probe);
    const color = getComputedStyle(probe).color;
    probe.remove();
    return color;
  }, expression);
}

const SCREEN_INK: Record<AxeTheme, string> = {
  dark: 'var(--status-warning-ink)',
  light: 'color-mix(in oklab, var(--signal-warning) 45%, var(--text-1))',
};

/**
 * The spread fill (and the crossing row's tint) is the rate window's band
 * fill (analytics.rate-window.css): --accent-soft measures 1.09:1 on the
 * light theme's white panel, so the light theme uses the 16% accent-ink mix.
 */
const SPREAD_FILL: Record<AxeTheme, string> = {
  dark: 'var(--accent-soft)',
  light: 'color-mix(in oklab, var(--accent-ink) 16%, transparent)',
};

test.describe('gold slot: margins and Crossed the line in the proof drawer', () => {
  for (const theme of ['dark', 'light'] as const) {
    test(`Borrower 360: one proof read, no second dossier, the margins and the crossing caption (${theme})`, async ({ app, page, mockApi }) => {
      registerAnatomyProof(mockApi, 'trusted');
      registerSpreadHistoryBorrowers(mockApi);
      await app.setTheme(theme);
      await app.gotoRoute(`/borrower-360/${SPREAD_FIX_BORROWER.borrower_id}`);
      const naturalLoad = markNaturalLoad(mockApi);
      expect(proofCalls(mockApi), 'the natural load reads no proof').toBe(0);
      const dossiersAtLoad = dossierCalls(mockApi);
      expect(dossiersAtLoad, 'the route read its dossier once').toBe(1);
      expect(rateWindowCalls(mockApi), 'the natural load reads no series').toBe(0);
      expectNoAuditedReadSince(mockApi, naturalLoad, 'natural load');

      const drawer = await openMath(page);
      await expect.poll(() => proofCalls(mockApi), 'opening the drawer read the proof once').toBe(1);

      const margins = drawer.getByTestId('score-margins');
      await expect(margins.locator('.score-margins__row')).toHaveCount(5);
      await expect(margins.getByTestId('score-margins-note')).toHaveText(MARGINS_NOTE);

      const chart = drawer.getByTestId('spread-history');
      await expect(chart.locator('.eyebrow').first()).toHaveText('Crossed the line');
      await expect(chart.getByTestId('spread-history-caption')).toHaveText(SPREAD_CAPTION);
      await expect(chart.getByTestId('spread-history-note')).toHaveText(HISTORY_NOTE);
      await expect(chart).not.toContainText(/forecast(?!\.)|will be|expected to/i);
      await expect(chart.getByTestId('spread-history-svg')).toHaveAttribute('aria-hidden', 'true');
      expect(await chart.locator('svg title, [title]').count()).toBe(0);
      expect(dossierCalls(mockApi), 'the chart read the dossier from the cache').toBe(dossiersAtLoad);
      expect(rateWindowCalls(mockApi), 'at most one series read').toBeLessThanOrEqual(1);
      await expect.poll(() => rateWindowCalls(mockApi)).toBe(1);

      // The inks resolve to the theme's tokens.
      const market = chart.locator('.spread-history__market');
      const screen = chart.locator('.spread-history__screen');
      expect(await market.evaluate((node) => getComputedStyle(node).stroke)).toBe(await resolvedInk(chart, 'var(--accent-data)'));
      expect(await screen.evaluate((node) => getComputedStyle(node).stroke)).toBe(await resolvedInk(chart, SCREEN_INK[theme]));
      const spreadFill = await resolvedInk(chart, SPREAD_FILL[theme]);
      expect(await chart.locator('.spread-history__spread').evaluate((node) => getComputedStyle(node).fill)).toBe(spreadFill);

      await expectAxeClean(page, { key: { route: 'gold-slot-cache', state: 'proof-drawer-math' }, theme, known: {}, include: '.proof-drawer.is-open' });
      await expectNoSurfaceOverflow(page, { route: 'borrower-360', state: 'proof-drawer-math', theme });

      const toggle = chart.getByRole('button', { name: 'Show as table' });
      await expect(toggle).toHaveAttribute('aria-expanded', 'false');
      await toggle.click();
      await expect(chart.getByRole('button', { name: 'Hide table' })).toHaveAttribute('aria-expanded', 'true');
      const table = chart.getByTestId('spread-history-table');
      await expect(table).toBeVisible();
      await expect(table.locator('thead th')).toHaveText(['Week', '30-year rate']);
      const crossingRow = table.locator('tr[data-crossing]');
      await expect(crossingRow).toHaveCount(1);
      await expect(crossingRow.locator('.chip')).toHaveText('Crossed');
      await expect(crossingRow).toContainText('in the money from here');
      expect(await crossingRow.locator('td').first().evaluate((node) => getComputedStyle(node).backgroundColor), 'the crossing row carries the spread tint').toBe(
        spreadFill,
      );
      await expectAxeClean(page, { key: { route: 'gold-slot-cache', state: 'proof-drawer-math-table' }, theme, known: {}, include: '.proof-drawer.is-open' });

      expect(proofCalls(mockApi)).toBe(1);
      expect(dossierCalls(mockApi)).toBe(dossiersAtLoad);
      expect(rateWindowCalls(mockApi)).toBe(1);
    });
  }

  test('reduced motion: the chart does not animate; without it the line draws in', async ({ app, page, mockApi }) => {
    registerAnatomyProof(mockApi, 'trusted');
    registerSpreadHistoryBorrowers(mockApi);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await app.gotoRoute(`/borrower-360/${SPREAD_FIX_BORROWER.borrower_id}`);
    let drawer = await openMath(page);
    let market = drawer.locator('.spread-history__market');
    await expect(market).toBeAttached();
    expect(await drawer.getByTestId('spread-history').evaluate((root) => root.getAnimations({ subtree: true }).length)).toBe(0);
    expect(await market.evaluate((node) => getComputedStyle(node).strokeDashoffset)).toMatch(/^0(px)?$/);
    await page.keyboard.press('Escape');

    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.reload();
    await app.settle();
    drawer = await openMath(page);
    market = drawer.locator('.spread-history__market');
    await expect(market).toBeAttached();
    expect(await market.evaluate((node) => node.getAnimations().map((animation) => (animation as CSSAnimation).animationName))).toContain(
      'spread-history-draw',
    );
  });

  test('an ARM borrower shows the FIX-only state and reads no series', async ({ app, page, mockApi }) => {
    registerAnatomyProof(mockApi, 'trusted');
    registerSpreadHistoryBorrowers(mockApi);
    await app.gotoRoute(`/borrower-360/${SPREAD_ARM_BORROWER.borrower_id}`);
    const drawer = await openMath(page);
    await expect(drawer.getByTestId('spread-history-empty')).toHaveText('Rate history is drawn for fixed-rate first liens only.');
    await expect(drawer.getByTestId('score-margins-note')).toHaveText(MARGINS_NOTE);
    expect(rateWindowCalls(mockApi)).toBe(0);
  });

  test("Lead Queue: the preview's drawer shows the margins and the open-the-dossier note, reading no dossier and no series", async ({ app, page, mockApi }) => {
    registerAnatomyProof(mockApi, 'trusted');
    await app.gotoRoute('/lead-queue');
    const expanded = await app.expandFirstLeadRow();
    const gate = expanded.locator('[data-testid="score-anatomy-spine"]');
    await gate.getByRole('button', { name: 'Score anatomy' }).click();
    await expect(gate.getByTestId('score-spine')).toBeVisible();
    await gate.locator('button.score-spine__seg').first().click();
    const drawer = page.locator('.proof-drawer.is-open');
    await expect(drawer).toBeVisible();
    await expect(drawer.getByTestId('score-margins-note')).toHaveText(MARGINS_NOTE);
    await expect(drawer.getByTestId('spread-history-empty')).toHaveText('Open the borrower dossier to see the rate history.');
    expect(dossierCalls(mockApi), 'no dossier read from the queue').toBe(0);
    expect(rateWindowCalls(mockApi), 'no series read without a cached dossier').toBe(0);
    expect(proofCalls(mockApi)).toBe(1);
  });
});
