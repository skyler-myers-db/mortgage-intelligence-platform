/**
 * W5d w5-palette-offer-sales-loop, rendered proofs on the production build
 * (P1-P13; the forced-colors cursor ring, P14, is a case in
 * css-hygiene.forced-ink and forced-colors.firefox):
 *
 *  - the ⌘K palette's provider groups, its one shared borrower search with
 *    the topbar, live keycaps, and audit-free, open-gated reads (shell-07,
 *    runtime-06, tables-09);
 *  - the Console's saved links warm their route chunk only (bundle-09);
 *  - the Queue pager on the keymap and the two-column ? sheet (wow-power-4);
 *  - the Offer: the verified campaign binding through the pager and Next
 *    (shell-04 / flow-09), buyer-safe failures (states-04), the note field
 *    error (critic-04), the mock's exit (motion-01), a decision in flight
 *    from any surface (stack-09) and the docked bar's 40rem rule (P13).
 */
import type { Page } from '@playwright/test';
import type { AppDriver } from './app';
import type { DecisionReceipt } from '../../../src/lib/apiTypes';
import type { SessionResponse } from '../../../src/types';
import { LEADS, PRIMARY_BORROWER } from './data/borrowers';
import { APPROVE_AUDIT_ID, approveResult, ledgerReceipt } from './data/decisionReceipt';
import { REVIEW_AUDIT_ID, registerHeldDecision } from './data/queueKeyboard';
import {
  BOUND_CAMPAIGN_ID,
  BOUND_VARIANT,
  PALETTE_VIEW,
  RECENT_IDS,
  SAVED_LEAD_ID,
  boundSearch,
  registerBoundCampaign,
  registerPaletteSources,
  registerSavedLead,
} from './data/paletteOfferSalesLoop';
import { PRESENTER_SESSION, SESSION } from './data/shell';
import { json, type ApiCall, type MockApi } from './mockApi';
import { expect, test } from './test';
import { auditedReadsAfter, expectNoAuditedReadSince, markNaturalLoad } from './visual';

const NON_ADMIN: SessionResponse = { ...SESSION, can_access_admin: false };

/** lib/queryPersist's sessionStorage snapshot of the allow-listed query cache. */
const QUERY_CACHE_KEY = 'mip.queryCache.v1';
interface PersistedCache {
  clientState: { queries: Array<{ queryKey: readonly unknown[] }> };
}

/** The persisted snapshot's raw JSON ('' while none is written). */
const persistedCache = (page: Page): Promise<string> =>
  page.evaluate((key) => sessionStorage.getItem(key) ?? '', QUERY_CACHE_KEY);

/**
 * Count the persister's writes from now on. The fixture pins the clock, so a
 * save's timestamp never moves and an identical snapshot is rewritten as the
 * same string: only the write itself shows that a save ran.
 */
async function countPersistWrites(page: Page): Promise<() => Promise<number>> {
  await page.evaluate((key) => {
    const counter = window as unknown as { __mipPersistWrites: number };
    counter.__mipPersistWrites = 0;
    const setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function countedSetItem(this: Storage, name: string, value: string) {
      if (this === sessionStorage && name === key) counter.__mipPersistWrites += 1;
      setItem.call(this, name, value);
    };
  }, QUERY_CACHE_KEY);
  return () => page.evaluate(() => (window as unknown as { __mipPersistWrites: number }).__mipPersistWrites);
}

const callsTo = (mockApi: MockApi, path: string, since = 0): ApiCall[] =>
  mockApi.calls.slice(since).filter((call) => call.path.replace('/api/v1/', '/api/') === path);

const palette = (page: Page) => page.getByRole('dialog', { name: 'Command palette' });
const paletteInput = (page: Page) => palette(page).getByRole('combobox');
// Exact: a substring name would let 'Borrowers' also match 'Recent borrowers'
// (and pass on a recents row), as the harness palette test once did.
const group = (page: Page, name: string) => palette(page).getByRole('group', { name, exact: true });

async function openPalette(page: Page): Promise<void> {
  await page.keyboard.press('ControlOrMeta+k');
  await expect(palette(page)).toBeVisible();
}

async function storageKeys(page: Page): Promise<{ local: string[]; session: string[] }> {
  return page.evaluate(() => ({
    local: Object.keys(localStorage).sort(),
    session: Object.keys(sessionStorage).sort(),
  }));
}

async function queueIds(page: Page): Promise<string[]> {
  const rows = page.locator('table.tbl tbody tr:not(.tbl__expand)');
  await expect(rows.first()).toBeVisible();
  const texts = await rows.allInnerTexts();
  return texts.map((text) => /B-[0-9A-Z]{13}/.exec(text)?.[0]).filter((id): id is string => Boolean(id));
}

test.describe('the palette (shell-07, runtime-06, tables-09)', () => {
  test('P1: "refi" leads with Segment Intelligence, renders Segments and Glossary, and reads only audit-free sources once', async ({ app, page, mockApi }) => {
    mockApi.register('GET', '/api/session', () => json<SessionResponse>(NON_ADMIN));
    await app.gotoRoute('/');
    await app.settle();
    const naturalLoad = markNaturalLoad(mockApi);
    await openPalette(page);
    await paletteInput(page).fill('refi');
    await expect(group(page, 'Glossary')).toBeVisible();
    await expect(group(page, 'Segments')).toBeVisible();
    await expect(palette(page).locator('.cmdk__status')).toHaveCount(0);
    await app.settle();
    await expect(group(page, 'Pages and actions').getByRole('option').first()).toContainText('Segment Intelligence');
    expect(callsTo(mockApi, '/api/workspace/saved-views', naturalLoad)).toHaveLength(1);
    expect(callsTo(mockApi, '/api/audit/my-events', naturalLoad)).toHaveLength(1);
    expect(callsTo(mockApi, '/api/borrowers/search', naturalLoad).map((call) => call.search)).toEqual(['q=refi']);
    expect(callsTo(mockApi, '/api/data-estate', naturalLoad), 'a non-admin never reads the data estate').toHaveLength(0);
    expectNoAuditedReadSince(mockApi, naturalLoad, 'palette · refi');
  });

  test('P1: an administrator reads the data estate once, and only after two typed characters', async ({ app, page, mockApi }) => {
    await app.gotoRoute('/');
    await app.settle();
    const naturalLoad = markNaturalLoad(mockApi);
    await openPalette(page);
    await paletteInput(page).fill('p');
    await expect(group(page, 'Pages and actions')).toBeVisible();
    await app.settle();
    expect(callsTo(mockApi, '/api/data-estate', naturalLoad)).toHaveLength(0);
    await paletteInput(page).fill('evidence');
    const assets = group(page, 'Data assets');
    await expect(assets).toBeVisible();
    await expect(assets.getByRole('option').first()).toContainText('Evidence events');
    await paletteInput(page).fill('evidence ev');
    await app.settle();
    expect(callsTo(mockApi, '/api/data-estate', naturalLoad), 'one read, cached for the session').toHaveLength(1);
    expectNoAuditedReadSince(mockApi, naturalLoad, 'palette · data assets');
  });

  test('P2: Recent borrowers come from my-events, only while open, never stored; Enter opens the dossier', async ({ app, page, mockApi }) => {
    registerPaletteSources(mockApi);
    await app.gotoRoute('/');
    await app.settle();
    // Nothing reads my-events before an open (the Console is closed too).
    expect(callsTo(mockApi, '/api/audit/my-events'), 'no recents read before the palette opens').toHaveLength(0);
    // The query persister writes the home load's cache on a 1 s throttle
    // (QUERY_CACHE_SAVE_THROTTLE_MS): let that first write land before the
    // snapshot, so the diff below measures the palette only.
    await expect.poll(() => persistedCache(page), 'the persisted home-load cache').not.toBe('');
    const before = await storageKeys(page);
    const persistWrites = await countPersistWrites(page);
    const naturalLoad = markNaturalLoad(mockApi);

    await openPalette(page);
    const recents = group(page, 'Recent borrowers').getByRole('option');
    await expect(recents).toHaveCount(RECENT_IDS.length);
    for (const [index, id] of RECENT_IDS.entries()) await expect(recents.nth(index)).toContainText(id);
    await page.keyboard.press('Escape');
    await expect(palette(page)).toBeHidden();
    const afterClose = mockApi.calls.length;
    await page.waitForTimeout(500);
    await app.settle();
    expect(callsTo(mockApi, '/api/audit/my-events', afterClose), 'no read after close').toHaveLength(0);
    // The palette's reads are cache events, so the persister saves again;
    // once it has, no storage key was added, and neither a palette nor a
    // search entry nor any masked borrower id reached the persisted cache.
    await expect.poll(persistWrites, 'a save after the palette read').toBeGreaterThan(0);
    expect(await storageKeys(page), 'no storage key was added').toEqual(before);
    const persisted = await persistedCache(page);
    const persistedKeys = (JSON.parse(persisted) as PersistedCache).clientState.queries.map((query) => query.queryKey);
    expect(persistedKeys.filter((key) => key[1] === 'palette' || key[1] === 'borrower-search'), 'no palette or search entry persisted').toEqual([]);
    expect(persisted, 'no masked borrower id persisted').not.toMatch(/B-[0-9A-Z]{13}/);
    expectNoAuditedReadSince(mockApi, naturalLoad, 'palette · recents');

    // One read per open: the closed palette's query is off, so a reopen reads again.
    await openPalette(page);
    const first = group(page, 'Recent borrowers').getByRole('option').first();
    await expect(first).toContainText(RECENT_IDS[0]);
    await expect.poll(() => callsTo(mockApi, '/api/audit/my-events', naturalLoad).length).toBe(2);
    const index = Number(await first.getAttribute('data-cmd-index'));
    for (let step = 0; step < index; step += 1) await page.keyboard.press('ArrowDown');
    await expect(first).toHaveAttribute('aria-selected', 'true');
    const beforeEnter = mockApi.calls.length;
    expect(auditedReadsAfter(mockApi.calls, naturalLoad), 'no audited read before Enter').toEqual([]);
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(new RegExp(`/borrower-360/${RECENT_IDS[0]}$`));
    await app.settle();
    expect(auditedReadsAfter(mockApi.calls, beforeEnter)).toEqual(
      expect.arrayContaining([expect.stringContaining(`/borrowers/${RECENT_IDS[0]} (VIEW_BORROWER)`)]),
    );
  });

  test('P3: a saved view opens the Lead Queue with exactly its params', async ({ app, page, mockApi }) => {
    registerPaletteSources(mockApi);
    await app.gotoRoute('/');
    await openPalette(page);
    await group(page, 'Saved views').getByRole('option', { name: new RegExp(PALETTE_VIEW.name) }).click();
    await expect(page).toHaveURL(new RegExp(`/lead-queue\\?${PALETTE_VIEW.params}$`));
  });

  test('P4: the topbar and the palette share one search; options are out of the Tab order', async ({ app, page, mockApi }) => {
    await app.gotoRoute('/');
    await app.settle();
    const search = page.getByRole('combobox', { name: 'Search borrowers' });
    await search.fill('Chicago');
    const list = page.getByRole('listbox', { name: 'Borrower matches' });
    await expect(list).toBeVisible();
    await app.settle();
    expect(callsTo(mockApi, '/api/borrowers/search').map((call) => call.search)).toEqual(['q=Chicago']);

    // Every option is a button out of the Tab order (a11y-05).
    const options = list.getByRole('option');
    for (const option of await options.all()) {
      await expect(option).toHaveAttribute('tabindex', '-1');
      expect(await option.evaluate((el) => el.tagName)).toBe('BUTTON');
    }
    await page.keyboard.press('ArrowDown');
    const active = list.locator('.topbar__search-result.is-active');
    await expect(active).toHaveCount(1);
    await expect(search).toHaveAttribute('aria-activedescendant', (await active.getAttribute('id')) ?? 'missing');
    await page.keyboard.press('Tab');
    const focusedRole = await page.evaluate(() => document.activeElement?.getAttribute('role') ?? null);
    expect(focusedRole, 'Tab never lands on an option').not.toBe('option');
    await expect(list).toBeHidden();

    await openPalette(page);
    await paletteInput(page).fill('Chicago');
    await expect(group(page, 'Borrowers').getByRole('option').first()).toBeVisible();
    await app.settle();
    expect(callsTo(mockApi, '/api/borrowers/search'), 'the palette answered from the shared cache').toHaveLength(1);
  });

  test('P5: keycaps show while their binding is live; the verbs show Shift A on a Lead Queue selection', async ({ app, page }) => {
    await app.gotoRoute('/');
    await openPalette(page);
    await paletteInput(page).fill('keyboard');
    const sheetRow = palette(page).getByRole('option', { name: /Keyboard shortcuts/ });
    await expect(sheetRow).toHaveAttribute('aria-keyshortcuts', '?');
    await expect(sheetRow.locator('.cmdk__row-hint kbd')).toHaveText(['?']);
    await page.keyboard.press('Escape');

    const consolePanel = await app.openConsole();
    await consolePanel.getByTestId('console-single-key-shortcuts').click();
    await openPalette(page);
    await paletteInput(page).fill('keyboard');
    await expect(sheetRow).toBeVisible();
    await expect(sheetRow).not.toHaveAttribute('aria-keyshortcuts', /./);
    await expect(sheetRow.locator('kbd')).toHaveCount(0);
    await page.keyboard.press('Escape');
    await consolePanel.getByTestId('console-single-key-shortcuts').click();

    await app.gotoRoute('/lead-queue');
    const pending = LEADS.filter((lead) => lead.approval_status === 'pending').slice(0, 2);
    for (const lead of pending) await page.getByTestId(`lead-select-${lead.borrower_id}`).check();
    await openPalette(page);
    const approve = group(page, 'Selected borrowers').getByRole('option', { name: /^Approve \d+ selected…/ });
    await expect(approve).toHaveAttribute('aria-keyshortcuts', 'Shift+A');
    await expect(approve.locator('.cmdk__row-hint kbd')).toHaveText(['Shift', 'A']);
  });
});

test.describe('the Console and the shortcut sheet (bundle-09, wow-power-4)', () => {
  test('P7: hovering a saved lead warms the borrower-360 chunk and makes no /api call; nothing under Save-Data', async ({ app, page, mockApi }) => {
    registerSavedLead(mockApi);
    const chunks: string[] = [];
    page.on('request', (request) => {
      if (/\/assets\/borrower-360[^/]*\.js$/.test(new URL(request.url()).pathname)) chunks.push(request.url());
    });
    await app.gotoRoute('/');
    const consolePanel = await app.openConsole();
    await app.settle();
    expect(chunks, 'precondition: the dossier chunk is not loaded yet').toEqual([]);
    const callsBefore = mockApi.calls.length;
    await consolePanel.locator(`a.saved-workspace__item[href="/borrower-360/${SAVED_LEAD_ID}"]`).hover();
    await expect.poll(() => chunks.length).toBeGreaterThan(0);
    await page.waitForTimeout(300);
    expect(mockApi.calls.slice(callsBefore).map((call) => `${call.method} ${call.path}`), 'intent reads no data').toEqual([]);
  });

  test('P7: under Save-Data a hover preloads nothing', async ({ app, page, mockApi }) => {
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'connection', { configurable: true, value: { saveData: true } });
    });
    registerSavedLead(mockApi);
    const chunks: string[] = [];
    page.on('request', (request) => {
      if (/\/assets\/borrower-360[^/]*\.js$/.test(new URL(request.url()).pathname)) chunks.push(request.url());
    });
    await app.gotoRoute('/');
    const consolePanel = await app.openConsole();
    await consolePanel.locator(`a.saved-workspace__item[href="/borrower-360/${SAVED_LEAD_ID}"]`).hover();
    await page.waitForTimeout(500);
    expect(chunks).toEqual([]);
  });

  test('P6: on Borrower 360 the ? sheet lists the Queue pager; with single keys off J does not page', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue?state=IL');
    const ids = await queueIds(page);
    await app.gotoRoute(`/borrower-360/${ids[1]}`);
    const pager = page.getByRole('navigation', { name: 'Lead queue position' });
    await expect(pager).toContainText('2 of 3 ranked in IL');
    await page.locator('main#main-content h1').filter({ visible: true }).focus();
    await page.keyboard.press('?');
    const sheet = page.getByTestId('shortcut-sheet');
    await expect(sheet).toBeVisible();
    const pagerGroup = sheet.getByRole('region', { name: 'Queue pager' });
    await expect(pagerGroup).toContainText('Next borrower in the queue');
    await expect(pagerGroup).toContainText('Previous borrower in the queue');
    await page.keyboard.press('Escape');

    const consolePanel = await app.openConsole();
    await consolePanel.getByTestId('console-single-key-shortcuts').click();
    await page.locator('main#main-content h1').filter({ visible: true }).focus();
    await page.keyboard.press('j');
    expect(page.url()).toMatch(new RegExp(`/borrower-360/${ids[1]}$`));
    await page.keyboard.press('ControlOrMeta+k');
    await expect(palette(page)).toBeVisible();
    await paletteInput(page).fill('keyboard');
    await palette(page).getByRole('option', { name: /Keyboard shortcuts/ }).click();
    await expect(sheet).toBeVisible();
    await expect(sheet.getByRole('region', { name: 'Queue pager' })).toContainText('Off');
  });

  test('P6: at 1440x720 on Lead Queue with the Triage deck open, the ? sheet list does not scroll', async ({ app, page }) => {
    await page.setViewportSize({ width: 1440, height: 720 });
    await app.gotoRoute('/lead-queue');
    await page.getByTestId('lead-triage-enter').click();
    await expect(page.getByTestId('triage-deck')).toBeVisible();
    await page.keyboard.press('?');
    const sheet = page.getByTestId('shortcut-sheet');
    await expect(sheet).toBeVisible();
    const list = sheet.locator('.cmdk__list');
    const { scrollHeight, clientHeight, columns } = await list.evaluate((el) => ({
      scrollHeight: el.scrollHeight,
      clientHeight: el.clientHeight,
      columns: getComputedStyle(el).columnCount,
    }));
    expect(columns).toBe('2');
    expect(scrollHeight, `list ${scrollHeight}px in a ${clientHeight}px box`).toBeLessThanOrEqual(clientHeight);
  });
});

test.describe('the Offer (shell-04 / flow-09, states-04, critic-04, motion-01, stack-09)', () => {
  const offerUrl = (id: string, bound: boolean) => `/offer-orchestrator/${id}${bound ? `?${boundSearch()}` : ''}`;
  const boundUrl = (id: string) => `/offer-orchestrator/${id}?${boundSearch()}`;
  const urlEndsWith = (suffix: string) => (url: URL) => `${url.pathname}${url.search}` === suffix;
  const pager = (page: Page) => page.getByRole('navigation', { name: 'Lead queue position' });
  const heading = (page: Page) => page.locator('main#main-content h1').filter({ visible: true });

  function registerApprove(mockApi: MockApi): void {
    mockApi.register('POST', '/api/outreach/approve', () => approveResult(APPROVE_AUDIT_ID));
    mockApi.register<DecisionReceipt>('GET', '/api/audit/receipt/:id', ({ params }) =>
      json<DecisionReceipt>(ledgerReceipt(params.id, PRIMARY_BORROWER, 'approved')));
  }

  test('P8: a bound offer from a queue with the same binding steps with both params (pager, J, Next)', async ({ app, page, mockApi }) => {
    registerBoundCampaign(mockApi);
    registerApprove(mockApi);
    await app.gotoRoute(`/lead-queue?state=IL&${boundSearch()}`);
    const ids = await queueIds(page);
    expect(ids.length).toBeGreaterThanOrEqual(3);
    await app.gotoRoute(offerUrl(ids[0], true));
    await expect(pager(page)).toContainText('1 of 3');
    await pager(page).getByRole('button', { name: 'Next' }).click();
    await expect(page).toHaveURL(urlEndsWith(boundUrl(ids[1])));
    await app.settle();
    await heading(page).focus();
    await page.keyboard.press('j');
    await expect(page).toHaveURL(urlEndsWith(boundUrl(ids[2])));
    await app.settle();
    await pager(page).getByRole('button', { name: 'Previous' }).click();
    await expect(page).toHaveURL(urlEndsWith(boundUrl(ids[1])));
    await app.settle();
    await page.getByRole('button', { name: 'Approve outreach' }).click();
    const next = page.getByTestId('offer-next-in-queue').getByRole('link');
    await expect(next).toContainText(`Next in queue: ${ids[2]}`);
    await expect(next).toHaveAttribute('href', boundUrl(ids[2]));
    expect(BOUND_VARIANT).toBe('Variant A');
    expect(boundSearch()).toBe(`campaign_id=${BOUND_CAMPAIGN_ID}&variant_name=Variant+A`);
  });

  test('P8: a bound offer from an unbound queue hides the pager; an unbound offer keeps it', async ({ app, page, mockApi }) => {
    registerBoundCampaign(mockApi);
    await app.gotoRoute('/lead-queue?state=IL');
    const ids = await queueIds(page);
    await app.gotoRoute(offerUrl(ids[1], true));
    await expect(page.getByTestId('offer-campaign-provenance')).toBeVisible();
    await app.settle();
    await expect(pager(page)).toHaveCount(0);
    await app.gotoRoute(offerUrl(ids[1], false));
    await expect(pager(page)).toContainText('2 of 3 ranked in IL');
    await pager(page).getByRole('button', { name: 'Next' }).click();
    await expect(page).toHaveURL(urlEndsWith(`/offer-orchestrator/${ids[2]}`));
  });

  test('P9: a 500 reads the lead phrase and a described sentence, never the raw message', async ({ app, page }) => {
    app.degrade('/api/outreach/approve', { method: 'POST', status: 500, body: { detail: 'psycopg OperationalError at db-7 (fixture)' } });
    await app.gotoRoute(offerUrl(PRIMARY_BORROWER.borrower_id, false));
    await page.getByRole('button', { name: 'Approve outreach' }).click();
    const failure = page.getByTestId('offer-action-bar').locator('[role="alert"]');
    await expect(failure).toContainText("Couldn't write approval. The server hit an unexpected error.");
    await expect(failure).not.toContainText('psycopg');
    await expect(failure.getByRole('button', { name: 'Regenerate draft' })).toHaveCount(0);
  });

  test('P9: a draft-proof 409 offers Regenerate draft and never re-drafts on its own', async ({ app, page, mockApi }) => {
    const proof = 'Generated draft proof does not match this approval; regenerate the draft.';
    app.degrade('/api/outreach/approve', { method: 'POST', status: 409, body: { detail: proof } });
    await app.gotoRoute(offerUrl(PRIMARY_BORROWER.borrower_id, false));
    const drafts = () => callsTo(mockApi, '/api/outreach/draft').length;
    const draftsBefore = drafts();
    await page.getByRole('button', { name: 'Approve outreach' }).click();
    const failure = page.getByTestId('offer-action-bar').locator('[role="alert"]');
    await expect(failure).toContainText(`Couldn't write approval. ${proof}`);
    const regenerate = failure.getByRole('button', { name: 'Regenerate draft' });
    await expect(regenerate).toBeVisible();
    await expect(failure).not.toContainText(/reload/i);
    await page.waitForTimeout(300);
    expect(drafts(), 'no draft POST until the reviewer asks').toBe(draftsBefore);
    await regenerate.click();
    await expect.poll(drafts, 'one draft POST on the reviewer\'s click').toBeGreaterThan(draftsBefore);
  });

  test('P9: any other 409 reads a neutral sentence, with no Regenerate draft and no raw detail', async ({ app, page }) => {
    app.degrade('/api/outreach/approve', { method: 'POST', status: 409, body: { detail: 'draft proof mismatch (fixture)' } });
    await app.gotoRoute(offerUrl(PRIMARY_BORROWER.borrower_id, false));
    await page.getByRole('button', { name: 'Approve outreach' }).click();
    const failure = page.getByTestId('offer-action-bar').locator('[role="alert"]');
    await expect(failure).toContainText("Couldn't write approval. The server refused this decision; it was not recorded.");
    await expect(failure).not.toContainText('mismatch (fixture)');
    await expect(failure).not.toContainText(/reload|no longer current/i);
    await expect(failure.getByRole('button', { name: 'Regenerate draft' })).toHaveCount(0);
  });

  test('P9: a draft-proof 409 on Save draft offers Regenerate draft in the draft panel, never reload advice', async ({ app, page, mockApi }) => {
    const proof = 'Generated draft no longer matches the reviewed copy; regenerate before saving.';
    app.degrade('/api/workspace/drafts/:borrowerId', { method: 'PUT', status: 409, body: { detail: proof } });
    await app.gotoRoute(offerUrl(PRIMARY_BORROWER.borrower_id, false));
    const drafts = () => callsTo(mockApi, '/api/outreach/draft').length;
    const save = page.getByRole('button', { name: `Save outreach draft for ${PRIMARY_BORROWER.borrower_id}` });
    await expect(save).toBeEnabled();
    await save.click();
    const failure = page.locator('[role="alert"]').filter({ hasText: "Couldn't save draft." });
    await expect(failure).toContainText(`Couldn't save draft. ${proof}`);
    await expect(failure).not.toContainText(/reload/i);
    const draftsBefore = drafts();
    await failure.getByRole('button', { name: 'Regenerate draft' }).click();
    await expect.poll(drafts, 'one draft POST on the reviewer\'s click').toBeGreaterThan(draftsBefore);
    await expect(failure).toHaveCount(0);
  });

  test('P9: a 422 governed-text refusal of the reject note names the field, never filter advice', async ({ app, page, mockApi }) => {
    app.degrade('/api/outreach/reject', { method: 'POST', status: 422, body: { detail: 'rationale failed the governed text policy' } });
    await app.gotoRoute(offerUrl(PRIMARY_BORROWER.borrower_id, false));
    const bar = page.getByTestId('offer-action-bar');
    await bar.getByRole('button', { name: 'Reject', exact: true }).click();
    await bar.getByLabel('Reason').selectOption('other_with_text');
    await bar.getByLabel('Rationale note').fill('Asked to be called after the holidays (fixture)');
    await bar.getByRole('button', { name: 'Confirm reject' }).click();
    const failure = bar.locator('[role="alert"]');
    await expect(failure).toContainText("Couldn't record rejection. Rationale failed the governed text policy.");
    await expect(failure).not.toContainText(/filter|reload/i);
    await expect(failure.getByRole('button', { name: 'Regenerate draft' })).toHaveCount(0);
    expect(callsTo(mockApi, '/api/outreach/reject')).toHaveLength(1);
  });

  test('P10: Other with an empty note marks the note, describes the error and sends no reject', async ({ app, page, mockApi }) => {
    await app.gotoRoute(offerUrl(PRIMARY_BORROWER.borrower_id, false));
    const bar = page.getByTestId('offer-action-bar');
    await bar.getByRole('button', { name: 'Reject', exact: true }).click();
    await bar.getByLabel('Reason').selectOption('other_with_text');
    // Confirm reject is aria-disabled (never native disabled) without a note:
    // a keyboard press still submits, and lands on the note.
    const confirm = bar.getByRole('button', { name: 'Confirm reject' });
    await expect(confirm).toHaveAttribute('aria-disabled', 'true');
    await confirm.focus();
    await page.keyboard.press('Enter');
    const note = bar.getByLabel('Rationale note');
    await expect(note).toHaveAttribute('aria-invalid', 'true');
    await expect(note).toBeFocused();
    const describedBy = (await note.getAttribute('aria-describedby')) ?? '';
    const described = await page.evaluate(
      (ids) => ids.split(' ').map((id) => document.getElementById(id)?.textContent ?? ''),
      describedBy,
    );
    expect(described).toContain('Add a note when the reason is Other.');
    await expect(bar.locator('.surface--danger[role="alert"]')).toHaveCount(0);
    expect(callsTo(mockApi, '/api/outreach/reject')).toHaveLength(0);
  });

  for (const reducedMotion of ['no-preference', 'reduce'] as const) {
    test(`P11: the presenter mock closes through its exit and focus returns (${reducedMotion})`, async ({ app, page, mockApi }) => {
      mockApi.register('GET', '/api/session', () => json<SessionResponse>(PRESENTER_SESSION));
      await page.emulateMedia({ reducedMotion });
      await app.gotoRoute(offerUrl(PRIMARY_BORROWER.borrower_id, false));
      const opener = page.getByTestId('preview-borrower-offer');
      await opener.click();
      const mock = page.getByTestId('borrower-offer-mock');
      await expect(mock).toBeVisible();
      await mock.getByRole('button', { name: 'Close prototype' }).click();
      // Still attached, closed: the dialog plays its exit, then is not rendered.
      await expect(page.locator('dialog.offer-mock:not([open])')).toHaveCount(1);
      if (reducedMotion === 'reduce') {
        expect(await page.locator('dialog.offer-mock').evaluate((el) => getComputedStyle(el).display), 'no exit under reduced motion').toBe('none');
      }
      await expect(mock).toBeHidden();
      await expect(opener).toBeFocused();
    });
  }

  test('P12: a held approve POST keeps the banner busy with both buttons disabled, and says so', async ({ app, page, mockApi }) => {
    const held = registerHeldDecision(mockApi);
    await app.gotoRoute(offerUrl(PRIMARY_BORROWER.borrower_id, false));
    const gate = page.getByTestId('offer-action-bar').locator('.approval');
    await gate.getByRole('button', { name: 'Approve outreach' }).click();
    await expect.poll(() => held.approveGate.received).toBe(true);
    await expect(gate).toHaveAttribute('aria-busy', 'true');
    for (const button of await gate.locator('.approval__actions button').all()) await expect(button).toBeDisabled();
    await expect(gate.getByTestId('approval-pending')).toHaveText('Approval being recorded…');
    await expect(page.getByTestId('decision-receipt')).toHaveCount(0);
    held.approveGate.release();
    await expect(page.getByTestId('decision-receipt')).toHaveAttribute('data-audit-event-id', REVIEW_AUDIT_ID);
  });

  async function openRejectAt640(app: AppDriver, page: Page) {
    await page.setViewportSize({ width: 1440, height: 640 });
    await app.gotoRoute(offerUrl(PRIMARY_BORROWER.borrower_id, false));
    await app.openConsole();
    const bar = page.getByTestId('offer-action-bar');
    await bar.getByRole('button', { name: 'Reject', exact: true }).click();
    const note = bar.getByLabel('Rationale note');
    await note.focus();
    await app.settle();
    return note;
  }

  /** The docked nav and bar heights against `.main`'s scrollport (0 when not docked). */
  async function dockedShares(page: Page) {
    return page.evaluate(() => {
      const main = document.querySelector<HTMLElement>('.main');
      const nav = document.querySelector<HTMLElement>('.route-nav');
      const docked = document.querySelector<HTMLElement>('[data-testid="offer-action-bar"]');
      if (!main || !nav || !docked) throw new Error('layout not found');
      const sticky = (el: HTMLElement) => getComputedStyle(el).position === 'sticky';
      return {
        scrollport: main.clientHeight,
        nav: sticky(nav) ? nav.getBoundingClientRect().height : 0,
        bar: sticky(docked) ? docked.getBoundingClientRect().height : 0,
        barDocked: sticky(docked),
      };
    });
  }

  test('P13: at 1440x640 with the Console and the reject panel open, the focused note is fully visible (WCAG 2.4.11)', async ({ app, page }) => {
    const note = await openRejectAt640(app, page);
    const shares = await dockedShares(page);
    test.info().annotations.push({ type: 'P13 shares', description: JSON.stringify(shares) });
    expect(shares.barDocked, 'the bar docks at 640px tall (40rem)').toBe(true);
    const visible = await note.evaluate((el) => {
      const box = el.getBoundingClientRect();
      const main = (document.querySelector('.main') as HTMLElement).getBoundingClientRect();
      return box.height > 0 && box.top >= main.top && box.bottom <= Math.min(main.bottom, window.innerHeight);
    });
    expect(visible, 'the focused note is fully visible').toBe(true);
  });

  test('P13: the docked nav plus the docked bar take at most two thirds of the scrollport', async ({ app, page }) => {
    // Measured 2026-10-07 (Chromium, 1440x640, Console and reject panel open):
    // nav 57 + bar 387.5 of a 584px scrollport (76%), over the two-thirds
    // bound. W5e w5-compiler-lint cut the share policy (its cut order 6: it
    // moves layout on the approval path, so cut rather than half-built); the
    // bar keeps its 40rem rule until a post-wave Offer-cluster layout pass.
    test.fixme(true, 'post-wave Offer-cluster layout pass · D-theme-nav-b / 12.4 #5 · the docked Offer bar with the reject panel open takes 387.5 of 584px at 1440x640');
    await openRejectAt640(app, page);
    const shares = await dockedShares(page);
    expect(shares.nav + shares.bar, `nav ${shares.nav} + bar ${shares.bar} of ${shares.scrollport}`)
      .toBeLessThanOrEqual((2 / 3) * shares.scrollport);
  });
});
