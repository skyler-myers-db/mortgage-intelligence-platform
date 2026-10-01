/**
 * Rendered-layer proofs for W5b w5-genie-verified-reveal (1440x900,
 * production build, dark and light):
 *
 *  (1) genie-01 phase 1b on /ask-genie: a count line below the floor, then
 *      three verified sections as "Partial research" (no Show all, no CSV,
 *      no cell or chart links), kept across a same-revision poll whose body
 *      carries sections_rev, replaced by the recorded answer on success;
 *  (2) a job that expires after revealing withdraws the sections;
 *  (3) the floating panel on Home renders the reveal dense, without
 *      horizontal overflow;
 *  (4) genie-10 phase 1 in the proof drawer, and nothing for claims null;
 *  (5) dataviz-05 / stack-06: the bar chart's money values and cohort drill
 *      links (equal to the table cells' own links) with the Lead Queue
 *      disclosure and forced-colors ink; the line chart's nice "$" ticks,
 *      more than two x labels and a keyboard readout, with tick text the
 *      same size in the panel and on the route.
 *
 * Every case opens no audited read after the natural load, and the reveal
 * never calls /api/genie/actions or the export receipt. Axe runs on the
 * reveal, proof and chart states in both themes. (The run-history case of
 * the plan moved to W5c with genie-09 part 4; the map readout and the line
 * drill moved to W5d.)
 */
import type { Locator, Page } from '@playwright/test';
import type { GenieAnswer } from '../../../src/types';
import { expectAxeClean, KNOWN_VIOLATIONS } from './axe';
import { genieRevealSectionsFixture, registerGenieJob, type GenieJobStep } from './data/genieJobs';
import { GENIE_QUESTION, genieAnswerFixture, registerGenieTurn } from './data/genieTurn';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import type { MockApi } from './mockApi';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const STAGE_WAIT = { timeout: 20_000 };
const THREE = genieRevealSectionsFixture(3);
const REVEAL_STEPS: readonly GenieJobStep[] = [
  { stage: 'queued' },
  { stage: 'researching', parts: [1, 7], reveal: { verified: 1, rev: 1, sections: null } },
  { stage: 'researching', parts: [4, 7], reveal: { verified: 3, rev: 2, sections: THREE } },
  { stage: 'synthesizing', parts: [7, 7], reveal: { verified: 3, rev: 2, sections: THREE } },
  'succeeded',
];

function main(page: Page): Locator {
  return page.locator('#main-content');
}

async function askOnRoute(page: Page): Promise<void> {
  await main(page).getByRole('textbox', { name: 'Ask Genie — question' }).fill(GENIE_QUESTION);
  await main(page).getByRole('button', { name: 'Ask Genie', exact: true }).click();
}

function revealGroup(scope: Locator): Locator {
  return scope.getByRole('group', { name: 'Partial research' });
}

function forbiddenCalls(mockApi: MockApi, since: number): string[] {
  return mockApi.calls
    .slice(since)
    .map((call) => call.path)
    .filter((path) => /\/api(\/v1)?\/genie\/(actions|export-receipt)/.test(path));
}

test.describe('verified sections as Partial research (genie-01 phase 1b)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`(1) ${theme}: count line, then three previewed sections kept across a same-revision poll, then the answer`, async ({ app, page, mockApi }) => {
      const job = registerGenieJob(mockApi, { deep: true, steps: REVEAL_STEPS });
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie');
      const natural = markNaturalLoad(mockApi);
      await askOnRoute(page);
      await expect(main(page).locator('.genie-progress')).toBeVisible(STAGE_WAIT);

      job.next();
      await expect(main(page).locator('.genie-reveal__count')).toHaveText(
        'Partial research · 1 of 7 sub-analyses verified so far',
        STAGE_WAIT,
      );
      await expect(main(page).locator('.genie-answer__section')).toHaveCount(0);

      job.next();
      const group = revealGroup(main(page));
      await expect(group).toBeVisible(STAGE_WAIT);
      await expect(group.locator('h3')).toHaveText(THREE.map((section) => section.title));
      await expect(group.locator('a')).toHaveCount(0);
      await expect(group).not.toContainText(/Show all|CSV/);
      await expect(group).toContainText('Preview: the first 3 of 40 rows.');
      await expectAxeClean(page, { key: { route: 'ask-genie', state: 'genie-partial-research' }, theme, known: KNOWN_VIOLATIONS, include: '#main-content' });

      job.next();
      const polls = job.statusPolls;
      await expect.poll(() => job.statusPolls, STAGE_WAIT).toBeGreaterThan(polls + 1);
      await expect(group.locator('h3')).toHaveCount(3);
      const revs = job.statusBodies.map((body) => (body as { sections_rev?: number | null }).sections_rev);
      expect(revs).toContain(2);
      expect(revs.every((rev) => rev === null || typeof rev === 'number')).toBe(true);

      job.next();
      await expect(main(page).locator('.genie-thread .genie-answer')).toHaveCount(1, STAGE_WAIT);
      await expect(revealGroup(main(page))).toHaveCount(0);
      expectNoAuditedReadSince(mockApi, natural, 'the reveal');
      expect(forbiddenCalls(mockApi, natural)).toEqual([]);
    });
  }

  test('(2) a job that expires after revealing withdraws the sections and shows its hint', async ({ app, page, mockApi }) => {
    const job = registerGenieJob(mockApi, {
      deep: true,
      steps: [{ stage: 'queued' }, { stage: 'researching', parts: [3, 7], reveal: { verified: 3, rev: 5, sections: THREE } }, 'expired'],
    });
    await app.gotoRoute('/ask-genie');
    const natural = markNaturalLoad(mockApi);
    await askOnRoute(page);
    job.next();
    await expect(revealGroup(main(page))).toBeVisible(STAGE_WAIT);

    job.next();
    await expect(main(page)).toContainText('This answer is no longer available here.', STAGE_WAIT);
    await expect(revealGroup(main(page))).toHaveCount(0);
    await expect(main(page).locator('.genie-reveal__count')).toHaveCount(0);
    expectNoAuditedReadSince(mockApi, natural, 'a withdrawn reveal');
    expect(forbiddenCalls(mockApi, natural)).toEqual([]);
  });

  test('(3) the floating panel on Home renders the reveal dense, with no horizontal overflow', async ({ app, mockApi }) => {
    const job = registerGenieJob(mockApi, { deep: true, steps: REVEAL_STEPS });
    await app.gotoRoute('/');
    const natural = markNaturalLoad(mockApi);
    const dialog = await app.askGenie(GENIE_QUESTION);
    job.next();
    job.next();
    const group = revealGroup(dialog);
    await expect(group).toBeVisible(STAGE_WAIT);
    await expect(group).toHaveClass(/genie-reveal--dense/);
    const overflow = await dialog.evaluate((root) =>
      Array.from(root.querySelectorAll<HTMLElement>('.genie-reveal, .genie-reveal *'))
        .filter((el) => el.scrollWidth > el.clientWidth + 1 && getComputedStyle(el).overflowX === 'visible')
        .map((el) => el.className),
    );
    expect(overflow).toEqual([]);
    expectNoAuditedReadSince(mockApi, natural, 'the panel reveal');
  });
});

const CLAIMS = {
  verified: 3,
  total: 3,
  items: [
    { token: '48,396', kind: 'number' as const, derivation: 'returned_value' as const, section: null },
    { token: '59,310', kind: 'number' as const, derivation: 'derived_from_rows' as const, section: null },
    { token: '40,000', kind: 'number' as const, derivation: 'bound' as const, section: null },
  ],
};

function withClaims(claims: typeof CLAIMS | null): GenieAnswer {
  const answer = genieAnswerFixture();
  return { ...answer, proof: { ...answer.proof, claims } } as GenieAnswer;
}

async function openProof(page: Page): Promise<Locator> {
  const opener = main(page).getByRole('button', { name: 'Show proof' }).first();
  await expect(opener).toBeVisible(STAGE_WAIT);
  await opener.click();
  const drawer = page.locator('dialog.genie-proof-drawer');
  await expect(drawer).toHaveClass(/is-open/);
  return drawer;
}

test.describe('figures verified against the rows (genie-10 phase 1)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`(4) ${theme}: "3 of 3 verified" with the figure list; claims null shows no Figures metric`, async ({ app, page, mockApi }) => {
      registerGenieTurn(mockApi, { holdProgress: false, answer: withClaims(CLAIMS) });
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie');
      await askOnRoute(page);
      const drawer = await openProof(page);

      await expect(drawer).toContainText('3 of 3 verified against the returned rows');
      await expect(drawer.locator('.genie-claims__item')).toHaveCount(3);
      await expect(drawer.locator('.genie-claims__item').nth(2)).toContainText('A threshold the returned values satisfy');
      await expectAxeClean(page, { key: { route: 'ask-genie', state: 'genie-proof-claims' }, theme, known: KNOWN_VIOLATIONS, include: 'dialog.genie-proof-drawer' });
    });
  }

  test('(4b) an answer with claims null shows no Figures metric', async ({ app, page, mockApi }) => {
    registerGenieTurn(mockApi, { holdProgress: false, answer: withClaims(null) });
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    const drawer = await openProof(page);

    await expect(drawer.locator('.genie-proof__metric .eyebrow', { hasText: 'Figures' })).toHaveCount(0);
    await expect(drawer).not.toContainText('verified against the returned rows');
  });
});

const BALANCE_ROWS = [
  { state: 'IL', total_balance: 1_250_000 },
  { state: 'TX', total_balance: 640_500 },
  { state: 'FL', total_balance: 98_000 },
];
const OPEN_COHORT = {
  id: 'open-cohort',
  label: 'Open this cohort in the Lead Queue',
  action_type: 'open_cohort',
  description: 'Opens the Lead Queue filtered to this answer.',
  requires_confirmation: false,
  route: '/lead-queue',
  borrower_ids: [],
  criteria: { result_filters: { segment_codes: ['itm'], segment_mode: 'any' } },
};

function barAnswer(): GenieAnswer {
  return genieAnswerFixture({
    table_rows: BALANCE_ROWS,
    row_count: BALANCE_ROWS.length,
    visualization: { kind: 'bar', title: 'Balance by state', x: 'state', y: 'total_balance', reason: 'One measure across states.' },
    actions: [OPEN_COHORT],
  } as Partial<GenieAnswer>);
}

function lineAnswer(): GenieAnswer {
  const rows = Array.from({ length: 24 }, (_, i) => ({ week: `2026-W${String(i + 1).padStart(2, '0')}`, total_balance: 100_000 + i * 12_500 }));
  return genieAnswerFixture({
    table_rows: rows,
    row_count: rows.length,
    visualization: { kind: 'line', title: 'Balance by week', x: 'week', y: 'total_balance', reason: 'A weekly series.' },
  } as Partial<GenieAnswer>);
}

test.describe('Genie charts on the kit (dataviz-05 / stack-06)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`(5a) ${theme}: bar values keep "$", labels drill with the answer cohort, the disclosure shows, ink survives forced colors`, async ({ app, page, mockApi }) => {
      registerGenieTurn(mockApi, { holdProgress: false, answer: barAnswer() });
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie');
      const natural = markNaturalLoad(mockApi);
      await askOnRoute(page);
      const chart = main(page).locator('.genie-chart').first();
      await expect(chart.locator('.genie-bars__row')).toHaveCount(3, STAGE_WAIT);

      await expect(chart.locator('.genie-bars__value')).toHaveText([/^\$/, /^\$/, /^\$/]);
      const barHrefs = await chart.locator('.genie-bars__link').evaluateAll((links) => links.map((a) => a.getAttribute('href')));
      const cellHrefs = await main(page)
        .locator('.genie-answer__table tbody tr td:first-child a')
        .evaluateAll((links) => links.map((a) => a.getAttribute('href')));
      expect(barHrefs).toEqual(cellHrefs);
      expect(barHrefs.every((href) => href?.startsWith('/lead-queue') && href.includes('itm'))).toBe(true);
      await expect(main(page).locator('.genie-chart__drill-note')).toContainText('contact-eligible borrowers only');
      await chart.locator('.genie-bars__row').first().hover();
      expectNoAuditedReadSince(mockApi, natural, 'bar hover');
      await expectAxeClean(page, { key: { route: 'ask-genie', state: 'genie-chart-bar' }, theme, known: KNOWN_VIOLATIONS, include: '#main-content' });

      await page.emulateMedia({ forcedColors: 'active' });
      const ink = await chart.locator('.genie-bars__bar').first().evaluate((bar) => getComputedStyle(bar).backgroundColor);
      expect(ink).not.toMatch(/rgba\(0, 0, 0, 0\)|transparent/);
      await page.emulateMedia({ forcedColors: 'none' });
    });
  }

  test('(5b) the line chart has nice "$" ticks, more than two x labels, a keyboard readout, and tick text that never scales with the plot', async ({ app, page, mockApi }) => {
    registerGenieTurn(mockApi, { holdProgress: false, answer: lineAnswer() });
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    const plot = main(page).locator('.genie-chart .chart-frame__plot').first();
    await expect(plot).toBeVisible(STAGE_WAIT);

    const yTicks = main(page).locator('.genie-chart .analytics-chart__tick--y');
    expect(await yTicks.count()).toBeGreaterThanOrEqual(3);
    for (const text of await yTicks.allTextContents()) expect(text).toMatch(/^\$/);
    expect(await main(page).locator('.genie-chart .analytics-chart__tick--x').count()).toBeGreaterThanOrEqual(3);
    await plot.focus();
    await page.keyboard.press('ArrowRight');
    await expect(main(page).locator('.genie-chart [aria-live="polite"]').first()).toHaveText(/^2026-W02: \$/);
    const wide = await yTicks.first().evaluate((tick) => ({
      size: getComputedStyle(tick).fontSize,
      plot: (tick.closest('.analytics-chart') as HTMLElement).getBoundingClientRect().width,
    }));

    // The floating panel prints the rows as a table only, so the narrow plot
    // is the same route at a panel-like width: HTML ticks never scale.
    await page.setViewportSize({ width: 1180, height: 900 });
    const narrow = await yTicks.first().evaluate((tick) => ({
      size: getComputedStyle(tick).fontSize,
      plot: (tick.closest('.analytics-chart') as HTMLElement).getBoundingClientRect().width,
    }));
    expect(Math.abs(narrow.plot - wide.plot), 'the plot width really changed').toBeGreaterThan(8);
    expect(narrow.size).toBe(wide.size);
    await page.setViewportSize({ width: 1440, height: 900 });
    await expectAxeClean(page, { key: { route: 'ask-genie', state: 'genie-chart-line' }, theme: 'dark', known: KNOWN_VIOLATIONS, include: '#main-content' });
  });
});
