/**
 * W5d w5-print-glossary-sales-manager, rendered against the production build
 * at 1440x900 (both themes where colour or layout matters).
 *
 *  - responsive-08: print emulation hides the chrome and overlays, one-column
 *    grids, unclipped tables, the provenance block; a computed-style sweep
 *    under print paints Borrower 360 and the ledger with IDENTICAL colours in
 *    dark and light; Export PDF writes exactly one PRINT_EXPORT receipt
 *    BEFORE exactly one print(), a failed receipt prints nothing, and
 *    printing re-reads nothing. One receipt, one paper: once that dialog's
 *    afterprint has fired, a later Ctrl/Cmd-P prints as unaudited.
 *  - tables-10: the facets read only on an explicit menu open; the exclusion
 *    reaches the page read and stays out of the URL; an auditor gets Export
 *    PDF but no CSV (the admin CSV's receipt order is export-audit.fixture
 *    .spec.ts).
 *  - critic-07: glossary search through ?q=, typed at speed and edited
 *    mid-word without losing a key or the caret (the field is a local draft
 *    the router's transition cannot undo), and Back / Forward still bring
 *    the URL's search back into the field.
 *  - motion-01 2e: a closing filter menu mid-exit (is-closing, aria-hidden,
 *    a running CSSTransition) while the Lead Queue keys already work, gone
 *    afterwards, instant under reduced motion.
 *  - D-platform-process-g / bundle-09: the Borrower 360 In the Money labels,
 *    and intent on 'Build outreach draft' fetching the Offer chunk with zero
 *    /api calls.
 * The new states' axe scans are axe.fixture.spec.ts (EXTRA_STATES).
 */
import type { Page } from '@playwright/test';
import type { SessionResponse } from '../../../src/types';
import type { FixtureTheme } from './app';
import { PRIMARY_BORROWER } from './data/borrowers';
import { PRINT_RECEIPT_ID, printReceiptFor } from './data/printReceipt';
import { AUDITOR_ONLY_SESSION } from './data/shell';
import { json, type MockApi } from './mockApi';
import { expect, test } from './test';

const B360 = `/borrower-360/${PRIMARY_BORROWER.borrower_id}`;
const THEMES: readonly FixtureTheme[] = ['dark', 'light'];

declare global {
  interface Window {
    __mipPrintOrder?: string[];
    __mipPrintedProvenance?: string[];
  }
}

/**
 * Stub window.print and record, in page order, the receipt request, its
 * response and every print(), with what the provenance read at that moment.
 * The stub is shaped like Chromium's blocking print(): beforeprint, the
 * paper, then afterprint before it returns (a listener added after print()
 * returns misses that afterprint).
 */
function installPrintProbe(): void {
  const order: string[] = [];
  const printed: string[] = [];
  window.__mipPrintOrder = order;
  window.__mipPrintedProvenance = printed;
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const isReceipt = url.includes('/print-receipt');
    if (isReceipt) order.push('receipt-request');
    const response = await originalFetch(input, init);
    if (isReceipt) order.push(`receipt-response ${response.status}`);
    return response;
  };
  window.print = () => {
    window.dispatchEvent(new Event('beforeprint'));
    order.push('print');
    printed.push(document.querySelector('[data-testid="print-provenance"]')?.textContent ?? '');
    window.dispatchEvent(new Event('afterprint'));
  };
}

const printOrder = (page: Page) => page.evaluate(() => [...(window.__mipPrintOrder ?? [])]);
const printedPapers = (page: Page) => page.evaluate(() => [...(window.__mipPrintedProvenance ?? [])]);

/** Record every PRINT_EXPORT request body the page sends. */
function recordReceipts(mockApi: MockApi): unknown[] {
  const bodies: unknown[] = [];
  mockApi.register('POST', '/api/print-receipt', ({ body }) => {
    bodies.push(body);
    return json(printReceiptFor(body));
  });
  return bodies;
}

/** The /api calls made after `from`, as `METHOD path`. */
function callsSince(mockApi: MockApi, from: number): string[] {
  return mockApi.calls.slice(from).map((call) => `${call.method} ${call.path}`);
}

/** color / background-color / border colours of every visible element in <main>, keyed by its DOM path. */
async function paperColours(page: Page): Promise<Record<string, string>> {
  return page.evaluate(() => {
    const out: Record<string, string> = {};
    const main = document.querySelector('#main-content');
    if (!main) return out;
    const walk = (element: Element, path: string) => {
      const style = getComputedStyle(element);
      if (style.display === 'none') return;
      if (style.visibility !== 'hidden' && element.getClientRects().length > 0) {
        out[path] = [
          style.color,
          style.backgroundColor,
          style.borderTopColor,
          style.borderRightColor,
          style.borderBottomColor,
          style.borderLeftColor,
        ].join(' | ');
      }
      Array.from(element.children).forEach((child, index) => walk(child, `${path}>${child.tagName}${index}`));
    };
    walk(main, 'main');
    return out;
  });
}

/**
 * The paper once it has settled. The switch to print (and the theme switch
 * before it) starts the shell's colour transitions, 0.01ms under reduced
 * motion but each a frame away, and a loaded runner can start one AFTER a
 * single wait on document.getAnimations() returned: a sweep read then
 * catches the screen ink a transition starts from (or an oklab mid-value).
 * So wait until no animation runs across two frames, and accept a sweep only
 * when the next one reads the same.
 */
async function settledPaperColours(page: Page, label: string): Promise<Record<string, string>> {
  const quiet = () => page.evaluate(async () => {
    await Promise.all(document.getAnimations().map((animation) => animation.finished.catch(() => undefined)));
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    return document.getAnimations().filter((animation) => animation.playState === 'running').length;
  });
  let previous: Record<string, string> | null = null;
  let settled: Record<string, string> | null = null;
  await expect.poll(async () => {
    if ((await quiet()) > 0) return false;
    const sweep = await paperColours(page);
    const stable = previous !== null && JSON.stringify(previous) === JSON.stringify(sweep);
    previous = sweep;
    if (stable) settled = sweep;
    return stable;
  }, { message: `${label}: the paper settles`, timeout: 15_000 }).toBe(true);
  return settled ?? {};
}

/** Switch the theme the way a person does, from the account menu (the app persists it). */
async function chooseTheme(page: Page, theme: FixtureTheme): Promise<void> {
  if ((await page.locator('html').getAttribute('data-theme')) === theme) return;
  await page.getByTestId('identity-menu-trigger').click();
  await page.getByRole('menuitemradio', { name: theme === 'dark' ? 'Dark' : 'Light' }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
}

async function expectIdenticalPaper(page: Page, label: string, open: () => Promise<void>): Promise<void> {
  const sweeps: Array<Record<string, string>> = [];
  for (const theme of THEMES) {
    await page.emulateMedia({ media: 'screen', colorScheme: theme });
    await open();
    await chooseTheme(page, theme);
    await page.emulateMedia({ media: 'print', colorScheme: theme });
    sweeps.push(await settledPaperColours(page, `${label} (${theme})`));
  }
  const [dark, light] = sweeps;
  expect(Object.keys(dark).length, `${label}: the sweep saw the page`).toBeGreaterThan(40);
  expect(Object.keys(light).sort(), `${label}: the same elements are visible on paper`).toEqual(Object.keys(dark).sort());
  const differing = Object.keys(dark).filter((key) => dark[key] !== light[key]).map((key) => `${key}: ${dark[key]} vs ${light[key]}`);
  expect(differing, `${label}: paper ink differs between dark and light`).toEqual([]);
}

test.describe('print path (responsive-08)', () => {
  for (const theme of THEMES) {
    test(`Borrower 360 on paper hides the chrome and overlays, prints one column and the provenance (${theme})`, async ({ app, page }) => {
      await page.addInitScript(installPrintProbe);
      await app.setTheme(theme);
      await app.gotoRoute(B360);
      await app.openConsole();
      const exportPdf = page.getByTestId('export-pdf');
      await expect(exportPdf).toHaveText('Export PDF');
      await exportPdf.click();
      await expect.poll(() => printOrder(page)).toEqual(['receipt-request', 'receipt-response 200', 'print']);
      expect(await printedPapers(page), 'the Export PDF paper named its receipt when print() ran').toEqual([
        expect.stringContaining(`Audit receipt ${PRINT_RECEIPT_ID}`),
      ]);
      await expect(page.locator('.toast-region')).toContainText('Print receipt recorded');
      await app.openCommandPalette();

      await page.emulateMedia({ media: 'print' });
      for (const chrome of ['.rail', '.topbar', '.route-nav', '#workspace-console', '.genie__fab', 'dialog.cmdk', '.toast-region']) {
        await expect(page.locator(chrome).first(), `${chrome} stays off paper`).toBeHidden();
      }
      const columns = await page.locator('.layoutA-grid').first().evaluate((grid) => getComputedStyle(grid).gridTemplateColumns.trim().split(/\s+/).length);
      expect(columns, '.layoutA-grid prints one column').toBe(1);
      const provenance = page.getByTestId('print-provenance');
      await expect(provenance).toBeVisible();
      await expect(provenance).toContainText(`Borrower 360 · ${PRIMARY_BORROWER.borrower_id}`);
      await expect(provenance.locator('img')).toHaveCount(0);
      // The Export PDF dialog has closed (its afterprint): the receipt
      // captioned that one paper, so this page on paper now is unaudited.
      await expect(provenance).toContainText('Unaudited print');
      await expect(provenance).not.toContainText(PRINT_RECEIPT_ID);
      // A Ctrl/Cmd-P of the same page: no receipt asked for, none claimed.
      await page.evaluate(() => window.print());
      const papers = await printedPapers(page);
      expect(papers, 'a later print of the page').toHaveLength(2);
      expect(papers[1]).toContain('Unaudited print');
      expect(papers[1]).not.toContain(PRINT_RECEIPT_ID);
      await page.emulateMedia({ media: 'screen' });
      await expect(provenance).toBeHidden();
    });
  }

  test('the Lead Queue and the ledger tables print unclipped', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    await page.emulateMedia({ media: 'print' });
    const wrap = page.locator('.tbl-wrap').first();
    await expect(wrap).toHaveCSS('max-height', 'none');
    await expect(wrap).toHaveCSS('overflow-y', 'visible');
    await page.emulateMedia({ media: 'screen' });
    await app.gotoRoute('/audit-ledger#audit');
    await page.emulateMedia({ media: 'print' });
    await expect(page.locator('.admin-audit-list').first()).toHaveCSS('overflow-x', 'visible');
  });

  test('Borrower 360 and the ledger print the same ink in dark and light', async ({ app, page }) => {
    await app.setTheme('dark');
    await expectIdenticalPaper(page, 'Borrower 360', async () => {
      await app.gotoRoute(B360);
      // The decision history is the column's lazy last item: sweep both
      // themes with it rendered, so they compare the same DOM.
      await expect(page.getByTestId('borrower-decision-history').locator('li').first()).toBeVisible();
      await app.settle();
    });
    await expectIdenticalPaper(page, 'audit ledger', () => app.gotoRoute('/audit-ledger#audit'));
  });

  test('exactly one receipt precedes exactly one print, and printing re-reads nothing', async ({ app, mockApi, page }) => {
    const receipts = recordReceipts(mockApi);
    await page.addInitScript(installPrintProbe);
    await app.gotoRoute(B360);
    // The natural load ends with the lazy decision history's own read.
    await expect.poll(() => mockApi.calls.some((call) => call.path.endsWith('/decisions'))).toBe(true);
    await app.settle();
    const before = mockApi.calls.length;

    await page.getByTestId('export-pdf').click();
    await expect.poll(() => printOrder(page)).toEqual(['receipt-request', 'receipt-response 200', 'print']);
    const printed = await printedPapers(page);
    expect(printed, 'the page already named its receipt when print() ran').toEqual([expect.stringContaining(`Audit receipt ${PRINT_RECEIPT_ID}`)]);
    const after = callsSince(mockApi, before);
    expect(after).toEqual(['POST /api/print-receipt']);
    expect(receipts).toEqual([{ surface: 'borrower_360', borrower_id: PRIMARY_BORROWER.borrower_id, row_count: 1 }]);
  });

  test('a 503 receipt prints nothing and raises an error toast', async ({ app, mockApi, page, hygiene }) => {
    hygiene.allow('console.error', /status of 503/);
    // Not retryable: the transport hands the 503 straight back, no row was written.
    mockApi.register('POST', '/api/print-receipt', () => json({ detail: 'The audit ledger is unavailable.' }, { status: 503 }));
    await page.addInitScript(installPrintProbe);
    await app.gotoRoute(B360);

    await page.getByTestId('export-pdf').click();
    await expect(page.locator('.toast-region')).toContainText('Print not recorded');
    expect(await printOrder(page)).toEqual(['receipt-request', 'receipt-response 503']);
    await expect(page.getByTestId('print-provenance')).toContainText('Unaudited print');
  });

  test('the ledger prints its page for an auditor, who gets no CSV', async ({ app, mockApi, page }) => {
    mockApi.register('GET', '/api/session', () => json<SessionResponse>(AUDITOR_ONLY_SESSION));
    const receipts = recordReceipts(mockApi);
    await page.addInitScript(installPrintProbe);
    await app.gotoRoute('/audit-ledger#audit');
    const explorer = page.locator('#audit');
    await expect(explorer.locator('table[aria-label="Audit events"] tbody tr[data-audit-event-id]').first()).toBeVisible();
    await expect(explorer.getByRole('button', { name: /as CSV$/ })).toHaveCount(0);
    const before = mockApi.calls.length;

    await explorer.getByTestId('audit-export-pdf').click();
    await expect.poll(() => printOrder(page)).toEqual(['receipt-request', 'receipt-response 200', 'print']);
    expect(callsSince(mockApi, before), 'no /api/audit read on the way to paper').toEqual(['POST /api/print-receipt']);
    expect(receipts).toEqual([{ surface: 'audit_ledger', filters: {}, row_count: await explorer.locator('tbody tr[data-audit-event-id]').count() }]);
  });
});

test.describe('audit explorer (tables-10)', () => {
  test('the facets are read only on an explicit Action menu open', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/audit-ledger#audit');
    const facets = () => mockApi.calls.filter((call) => call.path === '/api/audit/facets').length;
    const action = page.locator('#audit').getByRole('combobox', { name: /^Action: / });
    await action.hover();
    await action.focus();
    await page.waitForTimeout(300);
    expect(facets(), 'no facets on load, hover or focus').toBe(0);

    await action.click();
    const menu = page.getByRole('listbox', { name: 'Action' });
    await expect(menu.locator('.filter-menu__count').first()).toBeVisible();
    expect(facets()).toBe(1);
    await page.keyboard.press('Escape');
    await action.click();
    await expect(menu).toBeVisible();
    expect(facets(), 'a reopen is served from the cache').toBe(1);
  });

  test('Exclude my ledger reads reaches the page read and stays out of the URL', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/audit-ledger?audit_event_type=APPROVE#audit');
    const pages = () => mockApi.calls.filter((call) => call.path === '/api/audit/events/page');
    expect(pages().at(-1)?.search ?? '').not.toContain('exclude_own_ledger_reads');

    await page.getByLabel('Exclude my ledger reads').check();
    await expect.poll(() => pages().at(-1)?.search ?? '').toContain('exclude_own_ledger_reads=true');
    expect(new URL(page.url()).search).toBe('?audit_event_type=APPROVE');
  });
});

test.describe('glossary search (critic-07)', () => {
  test('typing filters the glossary through ?q= without new history entries; Clear search restores it', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/glossary');
    const before = mockApi.calls.length;
    const historyLength = await page.evaluate(() => window.history.length);
    const search = page.getByLabel('Search the glossary');

    await search.fill('automated valuation');
    await expect(page).toHaveURL(/\/glossary\?q=automated(\+|%20)valuation$/);
    await expect(page.locator('.glossary-search__status')).toHaveText('1 term matches');
    await expect(page.locator('article.glossary-entry')).toHaveCount(1);
    await expect(page.locator('#avm')).toBeVisible();

    await search.fill('zzqx');
    await expect(page.locator('.glossary-search__status')).toHaveText('No terms match "zzqx"');
    await page.getByRole('button', { name: 'Clear search' }).click();
    await expect(page).toHaveURL(/\/glossary$/);
    await expect(search).toBeFocused();
    expect(await page.evaluate(() => window.history.length), 'replace, never push').toBe(historyLength);
    expect(callsSince(mockApi, before), 'searching reads nothing').toEqual([]);
  });

  test('typing at speed keeps every key, and a mid-word edit keeps the caret', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/glossary');
    const before = mockApi.calls.length;
    const historyLength = await page.evaluate(() => window.history.length);
    const search = page.getByLabel('Search the glossary');
    const status = page.locator('.glossary-search__status');

    await search.pressSequentially('automated valuation', { delay: 0 });
    await expect(page).toHaveURL(/\/glossary\?q=automated(\+|%20)valuation$/);
    await expect(status).toHaveText('1 term matches');
    await expect(search).toHaveValue('automated valuation');

    await search.fill('');
    await search.pressSequentially('equty', { delay: 0 });
    await expect(status).toHaveText('No terms match "equty"');
    await search.evaluate((input: HTMLInputElement) => input.setSelectionRange(3, 3));
    await page.keyboard.type('i');
    await expect(page).toHaveURL(/\/glossary\?q=equity$/);
    // The list has caught up with 'equity': the render that once threw the caret is done.
    await expect(status).toHaveText(/^\d+ terms? match(es)?$/);
    await expect(search).toHaveValue('equity');
    expect(await search.evaluate((input: HTMLInputElement) => input.selectionStart), 'the caret stays after the typed i').toBe(4);
    expect(await page.evaluate(() => window.history.length), 'replace, never push').toBe(historyLength);
    expect(callsSince(mockApi, before), 'searching reads nothing').toEqual([]);
  });

  test('Back and Forward bring the URL search back into the field', async ({ app, page }) => {
    await app.gotoRoute('/glossary');
    const search = page.getByLabel('Search the glossary');
    await search.pressSequentially('equity', { delay: 0 });
    await expect(page).toHaveURL(/\?q=equity$/);
    // A category chip pushes an entry that keeps ?q=.
    await page.locator('.glossary-index .chip').first().click();
    await expect(page).toHaveURL(/\?q=equity#/);
    await search.selectText();
    await search.pressSequentially('lien', { delay: 0 });
    await expect(page).toHaveURL(/\/glossary\?q=lien$/);

    await page.goBack();
    await expect(page).toHaveURL(/\/glossary\?q=equity$/);
    await expect(search).toHaveValue('equity');
    await page.goForward();
    await expect(page).toHaveURL(/\/glossary\?q=lien$/);
    await expect(search).toHaveValue('lien');
  });
});

test.describe('presence exit (motion-01 item 2e)', () => {
  test.use({ contextOptions: { reducedMotion: 'no-preference' } });

  test('a closing filter menu is is-closing, aria-hidden and running its exit while the queue keys already work', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    // Hold the exit long enough to act inside it on a loaded machine; the
    // hook waits for the element's own transition, whatever its length.
    await page.addStyleTag({ content: '.filter-menu.is-closing { transition-duration: 4s !important; }' });
    const menu = await app.openFilterMenu('STATE');
    // Close after the entry keyframes end: a CSS animation still running on
    // opacity holds the exit transition back until it finishes.
    await menu.evaluate((element) => Promise.all(element.getAnimations().map((animation) => animation.finished)));
    await page.keyboard.press('Escape');

    const closing = page.locator('.filter-menu.is-closing');
    await expect(closing).toHaveCount(1);
    await expect(closing).toHaveAttribute('aria-hidden', 'true');
    expect(await closing.evaluate((menu) => menu.getAnimations().some((animation) => animation instanceof CSSTransition && animation.playState === 'running'))).toBe(true);

    // The table keys work during the fade: J moves the cursor, R opens the reject panel.
    await page.locator('.tbl-wrap').first().focus();
    await page.keyboard.press('j');
    await expect(page.locator('table.tbl tr.is-cursor')).toHaveCount(1);
    await page.keyboard.press('r');
    await expect(page.locator('.decision-panel')).toBeVisible();
    await expect(closing, 'still fading').toHaveCount(1);

    await expect(page.locator('.filter-menu')).toHaveCount(0, { timeout: 15_000 });
  });

  test('under reduced motion the same close removes the menu at once', async ({ app, page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await app.gotoRoute('/lead-queue');
    const menu = await app.openFilterMenu('STATE');
    // 'At once' is proven by the absence of a fade, not by a one-shot count
    // right after Escape (a loaded runner can be a commit behind). Watch the
    // menu from before Escape: no exit transition may ever start on the menu
    // itself (an option's own hover transition bubbles here and is not one).
    await menu.evaluate((element) => {
      const watch = { transitioned: false };
      (window as unknown as { __menuExitWatch: typeof watch }).__menuExitWatch = watch;
      const runningTransition = () => element.getAnimations().some((animation) => animation instanceof CSSTransition);
      element.addEventListener('transitionrun', (event) => {
        if (event.target === element) watch.transitioned = true;
      });
      new MutationObserver(() => {
        if (element.classList.contains('is-closing') && runningTransition()) watch.transitioned = true;
      }).observe(element, { attributes: true, attributeFilter: ['class'] });
    });
    await page.keyboard.press('Escape');
    await expect(page.locator('.filter-menu')).toHaveCount(0, { timeout: 500 });
    const watch = await page.evaluate(() => (window as unknown as { __menuExitWatch: { transitioned: boolean } }).__menuExitWatch);
    expect(watch.transitioned, 'no exit transition under reduced motion').toBe(false);
  });
});

test.describe('Borrower 360 (D-platform-process-g, bundle-09)', () => {
  test('the panel speaks In the Money, and intent on the offer link fetches the Offer chunk with zero /api calls', async ({ app, mockApi, page }) => {
    await app.gotoRoute(B360);
    const panel = page.locator('.surface', { hasText: 'In the Money check' }).first();
    await expect(panel).toBeVisible();
    await expect(panel).toContainText(/In the Money rule\./);
    await expect(page.locator('#main-content')).not.toContainText(/\bprime[- ]refi\w*|\brefi(?:nance)?[- ]economics\b|\brefi screen\b/i);

    const before = mockApi.calls.length;
    const chunk = page.waitForRequest((request) => /\/assets\/offer-orchestrator-[^/]+\.js$/.test(request.url()));
    await page.getByRole('link', { name: /Build outreach draft/ }).hover();
    await chunk;
    await page.waitForTimeout(300);
    expect(callsSince(mockApi, before), 'the chunk only: no Offer read, no audit row').toEqual([]);
  });
});
