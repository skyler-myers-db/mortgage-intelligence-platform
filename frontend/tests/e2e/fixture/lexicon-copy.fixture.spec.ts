/**
 * W5e w5-lexicon-copy, rendered against the production build at 1440x900 in
 * both themes:
 *
 *  - critic-11 / D-platform-process-g: Home's KPI reads "In the Money" and its
 *    Ask Genie entry prefills the reviewed in-the-money prompt; Home's WHY NOW
 *    "in-the-money borrowers" trigger opens a drawer (title, Delta Explainer
 *    sentence and caption) with the same name, never "refi candidates"
 *    (the W5e VRT review's one-name miss); Segments names
 *    the itm card "In the Money"; the Ask composer and its sample chip speak
 *    the prenominal form; a Genie answer's own "in-the-money" renders verbatim
 *    (the vocabulary rewrite is gone, live-first); /glossary shows only the
 *    current forms (the retired names are search-only); and a DOM scan of
 *    every registry route, reading adjacent elements apart, finds no retired
 *    variant (/analytics* and /lead-queue included since the integrator
 *    emptied eslint LEXICON_PENDING at the W5e merge).
 *  - flow-10: the Segments lede is ONE line with the Console open, and the
 *    hero's height is logged Console closed and open for the W5e hand-off;
 *    the Portfolio save guard speaks buyer words.
 *  - W5c hand-off 15b: a non-admin's Lineage tab says who sees observed
 *    relationships, never loads forever, and reads no /api/admin endpoint.
 *  - bundle-09 item 1: hovering a Genie borrower cell fetches the Borrower 360
 *    chunk with ZERO /api calls.
 *  - W5d lq deferral 9: the request-scoped Lead Queue whose borrowers are all
 *    decided says so, offers "Clear the request" and no Genie Ask; beside
 *    another filter its zero may be that filter's, so it is the filtered
 *    state (Clear filters), still with no Genie Ask.
 * Each new surface is axe-clean (WCAG A/AA) in both themes.
 */
import type { GenieResult, GenieSubmitResult } from '../../../src/lib/apiTypes';
import type { LeadSummary } from '../../../src/types';
import type { FixtureTheme } from './app';
import { expectAxeClean } from './axe';
import { NON_APPROVER_SESSION, REQUEST_BATCH_ID } from './data/approvalRequests';
import { PRIMARY_BORROWER } from './data/borrowers';
import { json, type MockApi } from './mockApi';
import { FIXTURE_ROUTES } from './routes';
import { expect, test } from './test';

const THEMES: readonly FixtureTheme[] = ['dark', 'light'];
const RETIRED = /\bprime\b|\bprime[- ]refi\w*|\brefi(?:nance)?[- ]economics\b|\brefi screen\b|\brefi(?:nance)?[- ]candidates?\b/i;
const ITM_KPI_PROMPT =
  'How many borrowers across the current Cotality data coverage are currently in-the-money, and what is the average rate spread?';
const CHIP = 'Which states have the most in-the-money borrowers?';
/** The lexicon's prenominal population: WHY NOW's trigger and the server's highlight label. */
const ITM_POPULATION = 'in-the-money borrowers';
/**
 * Linux Chromium sets text wider than macOS (charts.fixture.spec.ts's
 * LINUX_TEXT_EMULATION): off Linux the drawer title is widened +0.5px a glyph,
 * so its one-line fit holds on the CI renderer too.
 */
const LINUX_TEXT_EMULATION = '#evidence-drawer-title { letter-spacing: 0.5px; }';
/** The example passes the real prompt guards (tests/unit/test_genie_context_templates.py). */
const COMPOSER_PLACEHOLDER = 'Ask about your book, e.g. in-the-money count by state';

/** GET /api/leads measures zero (the request-scoped empty states). */
function registerEmptyLeads(mockApi: MockApi): void {
  mockApi.register<LeadSummary[]>('GET', '/api/leads', () =>
    json<LeadSummary[]>([], { headers: { 'X-Total-Matching': '0', 'X-Returned-Rows': '0' } }),
  );
}

function callsSince(mockApi: MockApi, from: number): string[] {
  return mockApi.calls.slice(from).map((call) => `${call.method} ${call.path}`);
}

/**
 * The text a registry scan reads (runs in the page): every text node under
 * `root`, one per line. textContent would join adjacent elements with no
 * separator ("ITM" + "refi economics" = "ITMrefi economics"), so a retired
 * name inside a chip, a cell or an alias span would lose its word boundary
 * and slip past RETIRED. The campaign recommendation's audience line is
 * GOVERNED text: the fair-lending screen accepts only its reviewed wording
 * ("borrowers with refinance economics and usable home equity"; see
 * tests/unit/test_segment_label_parity.py), so it is read separately.
 */
function scanText(root: Element): string {
  const copy = root.cloneNode(true) as Element;
  copy.querySelectorAll('.campaign-recommendation__audience').forEach((node) => node.remove());
  const walker = document.createTreeWalker(copy, NodeFilter.SHOW_TEXT);
  const lines: string[] = [];
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) lines.push(node.nodeValue ?? '');
  return lines.join('\n');
}

function borrowerAnswer(): GenieResult {
  return {
    answer: 'There are 3 in-the-money borrowers in the shortlist; Illinois leads.',
    question: CHIP,
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'fixture-conversation-lexicon',
    message_id: 'fixture-message-lexicon',
    genie_status: 'COMPLETED',
    question_hash: 'fixture-hash-lexicon',
    row_count: 1,
    follow_up_questions: [],
    table_rows: [{ borrower_id: PRIMARY_BORROWER.borrower_id, state: 'IL', opportunity_score: 91 }],
    proof: {
      sql_query: 'SELECT borrower_id, state, opportunity_score FROM mip.gold.borrower_360 LIMIT 1',
      source_assets: ['mip.gold.borrower_360'],
      row_count: 1,
      trusted: true,
      filters: [],
      known_data_gaps: [],
    },
  } as GenieResult;
}

function registerBorrowerAnswer(mockApi: MockApi): void {
  const payload = borrowerAnswer();
  mockApi.register<GenieSubmitResult>('POST', '/api/genie/message/submit', () =>
    json<GenieSubmitResult>({
      completed: true,
      conversation_id: payload.conversation_id ?? null,
      message_id: payload.message_id ?? null,
      progress_token: null,
      question_hash: payload.question_hash ?? null,
      response: payload,
    }),
  );
}

for (const theme of THEMES) {
  test.describe(`lexicon and buyer copy · ${theme}`, () => {
    test.beforeEach(async ({ app }) => {
      await app.setTheme(theme);
    });

    test('Home: the In the Money KPI and its reviewed Ask prefill', async ({ app, page }) => {
      await app.gotoRoute('/');
      const kpi = page.locator('.kpi', { has: page.locator('.kpi__label', { hasText: /^In the Money$/ }) });
      await expect(kpi).toHaveCount(1);
      await kpi.getByRole('button', { name: 'Ask Genie about this KPI: In the Money' }).click();
      const dialog = page.getByRole('dialog', { name: 'Genie chat' });
      await expect(dialog.getByRole('textbox', { name: 'Ask Genie' })).toHaveValue(ITM_KPI_PROMPT);
      expect(await page.locator('#main-content').evaluate(scanText)).not.toMatch(RETIRED);
    });

    test('Home: the WHY NOW in-the-money trigger opens a drawer that keeps its name', async ({ app, page }) => {
      await app.gotoRoute('/');
      const trigger = page.locator('.home-answer .login-summary .home-answer__trigger', { hasText: ITM_POPULATION });
      await expect(trigger.getByRole('link', { name: ITM_POPULATION, exact: true })).toBeVisible();
      if (process.platform !== 'linux') await page.addStyleTag({ content: LINUX_TEXT_EMULATION });
      const drawer = await app.openEvidenceDrawer(trigger.locator('.evidence-chip'));
      const title = drawer.locator('#evidence-drawer-title');
      await expect(title).toHaveText(`Since your last login — ${ITM_POPULATION}`);
      // The new name is longer than the old one: the header title stays one
      // line. Slack is the free space before the close button, whose
      // margin-left: auto takes whatever the title leaves.
      const fit = await title.evaluate((element) => {
        const range = document.createRange();
        range.selectNodeContents(element);
        const rects = [...range.getClientRects()];
        const header = element.closest('.drawer__hdr');
        const close = header?.querySelector('.drawer__close');
        const gap = header ? Number.parseFloat(getComputedStyle(header).columnGap) || 0 : 0;
        const textRight = Math.max(...rects.map((rect) => rect.right));
        return {
          lines: new Set(rects.map((rect) => Math.round(rect.top))).size,
          slack: close ? Math.round(close.getBoundingClientRect().left - gap - textRight) : Number.NaN,
        };
      });
      console.log(`[delta-explainer-title] ${theme}: ${fit.lines} line, ${fit.slack}px slack`);
      expect(fit.lines, 'the drawer title stays one line').toBe(1);
      const explainer = drawer.getByTestId('delta-explainer');
      await expect(explainer.locator('table tbody tr').first()).toBeVisible();
      await expect(explainer).toContainText(new RegExp(`: \\+[\\d,]+ ${ITM_POPULATION}\\.`));
      await expect(explainer.locator('table caption')).toHaveText(`Change in ${ITM_POPULATION} by state`);
      // The drawer sits outside #main-content, so the registry scan never read it.
      expect(await drawer.evaluate(scanText)).not.toMatch(RETIRED);
    });

    test('Segments: the In the Money card, a one-line lede and the mode glossary links', async ({ app, page }) => {
      await app.gotoRoute('/segment-intelligence');
      await expect(page.locator('.seg-card__title', { hasText: /^In the Money$/ })).toHaveCount(1);
      const hero = page.locator('#main-content .proto-hero').first();
      const closed = (await hero.boundingBox())?.height ?? 0;
      await app.openConsole();
      await app.settle();
      const open = (await hero.boundingBox())?.height ?? 0;
      const lede = hero.locator('.lede');
      // Lines = distinct line boxes of the lede's text (line-height may be 'normal').
      const lines = await lede.evaluate((element) => {
        const range = document.createRange();
        range.selectNodeContents(element);
        return new Set([...range.getClientRects()].map((rect) => Math.round(rect.top))).size;
      });
      test.info().annotations.push({ type: 'segments-hero-height', description: `${theme}: closed ${closed}px, open ${open}px` });
      console.log(`[segments-hero] ${theme} console closed ${closed}px, open ${open}px, lede lines ${lines}`);
      expect(lines, 'the Segments lede stays one line with the Console open').toBe(1);
      const help = page.locator('.segment-mode-control');
      await expect(help.getByRole('link', { name: /^Any selected\. Segment match mode:/ })).toHaveAttribute('href', '/glossary#segment-match-mode');
      await expect(help.getByRole('link', { name: /^All selected\. Segment match mode:/ })).toBeVisible();
      // The open Console's .audit-panel is a known residual (w5-compiler-lint), so scan the page itself.
      await expectAxeClean(page, {
        key: { route: 'segment-intelligence', state: 'lexicon-mode-help' }, theme, known: {}, include: '#main-content',
      });
    });

    test('Portfolio: the save guard speaks buyer words', async ({ app, page }) => {
      await app.gotoRoute('/portfolio-builder');
      const guard = page.getByTestId('campaign-build-guard');
      await expect(guard).toContainText(/a saved campaign can hold/);
      await expect(guard).not.toContainText(/preflight|treatment/i);
    });

    test('Ask: the composer placeholder and sample chip use in-the-money', async ({ app, page }) => {
      await app.gotoRoute('/ask-genie');
      await expect(page.getByRole('textbox', { name: 'Ask Genie — question' })).toHaveAttribute('placeholder', COMPOSER_PLACEHOLDER);
      await expect(page.locator('#main-content').getByRole('button', { name: CHIP })).toBeVisible();
    });

    test('a non-admin Lineage tab names who sees observed relationships, with no admin read', async ({ app, mockApi, page }) => {
      mockApi.register('GET', '/api/session', () => json(NON_APPROVER_SESSION));
      await app.gotoRoute('/');
      const drawer = await app.openEvidenceDrawer(
        page.locator('.kpi .kpi__source .evidence-chip', { hasText: 'Rate + equity screen' }),
      );
      await drawer.getByRole('tab', { name: 'Lineage' }).click();
      await expect(drawer.getByText('Observed Unity Catalog relationships are shown to administrators.')).toBeVisible();
      await expect(drawer.getByText('Read top to bottom in the documented order.', { exact: false })).toBeVisible();
      await app.settle();
      await expect(drawer).not.toContainText('Loading observed Unity Catalog relationships');
      expect(mockApi.calls.filter((call) => call.path.startsWith('/api/admin/')), 'no admin read for a non-admin').toEqual([]);
      await expectAxeClean(page, {
        key: { route: 'home', state: 'lexicon-lineage-non-admin' }, theme, known: {}, include: '.drawer.is-open',
      });
    });

    test('a request-scoped queue whose borrowers are all decided says so, with Clear the request', async ({ app, mockApi, page }) => {
      registerEmptyLeads(mockApi);
      await app.gotoRoute(`/lead-queue?approval_request_batch=${REQUEST_BATCH_ID}`);
      const empty = page.locator('#main-content .empty');
      await expect(empty.locator('.empty__title')).toHaveText('No open borrowers left in this approval request.');
      await expect(empty.locator('.empty__copy')).toHaveText('Each one was decided, withdrawn or has expired.');
      await expect(empty.getByRole('button')).toHaveText(['Clear the request']);
      await expect(empty.getByRole('button', { name: /Ask Genie/ })).toHaveCount(0);
      await expectAxeClean(page, { key: { route: 'lead-queue', state: 'lexicon-request-scoped-empty' }, theme, known: {} });
    });

    test('a request-scoped queue that a state filter empties is the filtered state, never "request done"', async ({ app, mockApi, page }) => {
      registerEmptyLeads(mockApi);
      // GET /api/leads intersects the request's open borrowers with the state.
      await app.gotoRoute(`/lead-queue?approval_request_batch=${REQUEST_BATCH_ID}&states=TX`);
      const empty = page.locator('#main-content .empty');
      await expect(empty.locator('.empty__title')).toHaveText('No leads match this filter.');
      await expect(page.locator('#main-content')).not.toContainText('No open borrowers left in this approval request.');
      await expect(empty.getByRole('button')).toHaveText(['Clear filters']);
      await expect(empty.getByRole('button', { name: /Ask Genie/ })).toHaveCount(0);
      await expectAxeClean(page, { key: { route: 'lead-queue', state: 'lexicon-request-scoped-filtered' }, theme, known: {} });
    });
  });
}

test.describe('Genie cells and the registry scan', () => {
  test('a Genie answer keeps its own words, and a borrower cell warms only the Borrower 360 chunk', async ({ app, mockApi, page }) => {
    registerBorrowerAnswer(mockApi);
    await app.gotoRoute('/');
    const dialog = await app.askGenie(CHIP);
    await expect(dialog).toContainText('There are 3 in-the-money borrowers in the shortlist');
    const cell = dialog.locator(`.genie-answer__table a.mono[href="/borrower-360/${PRIMARY_BORROWER.borrower_id}"]`).first();
    await expect(cell).toBeVisible();
    await app.settle();
    const before = mockApi.calls.length;
    const chunk = page.waitForRequest((request) => /\/assets\/borrower-360-[^/]+\.js$/.test(request.url()));
    await cell.hover();
    await chunk;
    await page.waitForTimeout(300);
    expect(callsSince(mockApi, before), 'the chunk only: no dossier read, no audit row').toEqual([]);
  });

  test('the scan reads adjacent elements apart, and /glossary shows no retired name', async ({ app, page }) => {
    await app.gotoRoute('/glossary');
    // Non-vacuity: two adjacent chips. textContent fuses them ("ITMrefi
    // economics"), which hides the \b-anchored retired name; scanText must not.
    await page.evaluate(() => {
      document.body.insertAdjacentHTML('beforeend', '<div id="lexicon-scan-probe" hidden><span>ITM</span><span>refi economics</span></div>');
    });
    const probe = page.locator('#lexicon-scan-probe');
    expect(await probe.evaluate((node) => node.textContent ?? ''), 'the fused reading this scan replaces').not.toMatch(RETIRED);
    expect(await probe.evaluate(scanText), 'adjacent elements stay apart').toMatch(RETIRED);
    await probe.evaluate((node) => node.remove());

    // critic-11: the term shows only its current forms; the retired names
    // are search-only (GlossaryEntry.searchAliases), so typing one still finds it.
    const entry = page.locator('#main-content article#in-the-money');
    await expect(entry.locator('.glossary-entry__aliases > span')).toHaveText(['ITM', 'in-the-money']);
    expect(await page.locator('#main-content').evaluate(scanText)).not.toMatch(RETIRED);
    await page.getByLabel('Search the glossary').fill('refi economics');
    await expect(entry.locator('.glossary-entry__term')).toHaveText('In the Money');
    await expect(entry.locator('.glossary-entry__aliases > span')).toHaveText(['ITM', 'in-the-money']);
  });

  test('no registry route shows a retired In the Money variant', async ({ app, page }) => {
    // One test walks every registry route, so it gets the slow budget.
    test.slow();
    for (const route of FIXTURE_ROUTES) {
      await app.gotoRoute(route.path);
      expect(await page.locator('#main-content').evaluate(scanText), route.name).not.toMatch(RETIRED);
    }
  });
});
