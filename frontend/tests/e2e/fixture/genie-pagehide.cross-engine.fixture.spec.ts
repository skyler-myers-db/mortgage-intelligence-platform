/**
 * The Genie turn across a pagehide, in every engine (wave-3 remainder of
 * audit runtime-01 / critic fix 13). genie-turn.fixture.spec.ts proves these
 * in Chromium; this spec runs in fixture-chromium on every fixture run and in
 * fixture-webkit and fixture-firefox-forced in the e2e-cross-engine CI job,
 * where unload, bfcache and Web Lock release differ:
 *
 *  (a) a reload while polling resumes the same turn: no second submit, one
 *      complete, one answer;
 *  (b) a reload during the complete call never completes again and shows the
 *      interrupted note;
 *  (c) the Web Lock keeps a turn single-owner: a second page in the same
 *      context, seeded with a wholesale copy of the first page's
 *      sessionStorage (every key, so a renamed key cannot break the proof),
 *      does not resume while the first holds `mip-genie-turn:<id>`
 *      (lib/genieTurnLock.ts); once the first page closes, a fresh load of
 *      the second resumes once.
 *
 * Each test branches on `typeof navigator.locks?.request === 'function'`:
 * without Web Locks a resume never proceeds (fail closed), so that path is
 * asserted instead and annotated with the engine that took it. The turn is
 * scripted by data/genieTurn.ts (read-only reuse); only counts are logged.
 */
import type { Page, TestInfo } from '@playwright/test';
import { GENIE_QUESTION, registerGenieTurn } from './data/genieTurn';
import type { Hygiene } from './hygiene';
import { expect, test } from './test';

/** The client's progress poll cadence (lib/genieAsk.ts PROGRESS_POLL_MS). */
const PROGRESS_POLL_MS = 1_500;
const ANSWER_TEXT = /leads the footprint with/;
const INTERRUPTED =
  'Interrupted by a reload while the answer was being verified. It may still be recorded: check History, or Ask again.';
/*
 * History of (b): found by this spec on WebKit 26.6 (local, 2/3 runs; WebKit
 * rejects the reload-cancelled complete fetch after beforeunload but before
 * pagehide, so the turn settled as unreachable and the reloaded page showed
 * "Genie session reset: The app could not be reached") and in Firefox on the
 * first W5a CI cross-engine run (the interrupted note never showed). It was
 * a named fixme until W5b w5-identity-reset fixed lib/genieInFlightTurn
 * (lib/genieTurnUnload: a failure soon after a beforeunload heard while
 * completing waits for pagehide).
 */

function thread(page: Page) {
  return page.locator('#main-content .genie-thread');
}

async function askOnRoute(page: Page): Promise<void> {
  const main = page.locator('#main-content');
  await main.getByRole('textbox', { name: 'Ask Genie — question' }).fill(GENIE_QUESTION);
  await main.getByRole('button', { name: 'Ask Genie', exact: true }).click();
  await expect(thread(page).locator('.genie-progress')).toBeVisible();
}

/**
 * A reload or close cancels the page's in-flight requests on purpose. The
 * harness ignores only Chromium's name for that (ERR_ABORTED): WebKit reports
 * "cancelled" and Firefox NS_BINDING_ABORTED. WebKit also surfaces the
 * cancelled first health poll (`/api/v1/health?idle_s=0`) of the unloading
 * document as a page error; it is recorded as a follow-up for the health
 * transport's owner, not hidden: the allowance matches only that message.
 */
function allowNavigationCancel(hygiene: Hygiene, browserName: string): void {
  hygiene.allow('request-failed', /\/api\/v1\/genie\/message\/[a-z]+ failed: (cancelled|NS_BINDING_ABORTED)$/);
  hygiene.allow('request-failed', /\/api\/v1\/health\?idle_s=\d+ failed: (cancelled|NS_BINDING_ABORTED)$/);
  if (browserName === 'webkit') {
    hygiene.allow('pageerror', /^Fetch API cannot load \S+\/api\/v1\/health\?idle_s=0 due to access control checks/);
  }
}

/** Whether this engine exposes Web Locks; the unsupported path is annotated. */
async function webLocks(page: Page, testInfo: TestInfo, browserName: string): Promise<boolean> {
  const supported = await page.evaluate(() => typeof navigator.locks?.request === 'function');
  if (!supported) {
    testInfo.annotations.push({ type: 'web-locks', description: `${browserName}: no navigator.locks, asserting the fail-closed path` });
  }
  return supported;
}

test.describe('a Genie turn across a pagehide (runtime-01, every engine)', () => {
  test('(a) a reload while polling resumes the same turn: no second submit, one complete, one answer', async ({ app, browserName, hygiene, mockApi, page }, testInfo) => {
    allowNavigationCancel(hygiene, browserName);
    const turn = registerGenieTurn(mockApi);
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    await expect.poll(() => turn.progressPolls).toBeGreaterThan(0);
    const locks = await webLocks(page, testInfo, browserName);

    await page.reload({ waitUntil: 'domcontentloaded' });
    await app.settle();
    const pollsAfterReload = turn.progressPolls;
    if (!locks) {
      await page.clock.runFor(PROGRESS_POLL_MS * 4);
      await app.settle();
      expect(turn.progressPolls, 'fail closed: no resume without Web Locks').toBe(pollsAfterReload);
      expect({ submits: turn.submits, completes: turn.completes }).toEqual({ submits: 1, completes: 0 });
      return;
    }
    await expect(thread(page).locator('.genie__msg--user')).toHaveText([GENIE_QUESTION]);
    await expect.poll(() => turn.progressPolls, { timeout: 15_000 }).toBeGreaterThan(pollsAfterReload);
    expect(turn.submits).toBe(1);

    turn.finishGenieTurn();
    await expect(thread(page).locator('.genie-answer')).toHaveCount(1);
    await expect(thread(page)).toContainText(ANSWER_TEXT);
    expect({ submits: turn.submits, completes: turn.completes }).toEqual({ submits: 1, completes: 1 });
  });

  test('(b) a reload during the complete call never completes again and shows the interrupted note', async ({ app, browserName, hygiene, mockApi, page }, testInfo) => {
    allowNavigationCancel(hygiene, browserName);
    const turn = registerGenieTurn(mockApi, { holdComplete: true });
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    await webLocks(page, testInfo, browserName);
    turn.finishGenieTurn();
    await expect.poll(() => turn.completes, { timeout: 15_000 }).toBe(1);

    await page.reload({ waitUntil: 'domcontentloaded' });
    // The held complete belonged to the unloaded page; let its handler end.
    turn.releaseComplete();
    await app.settle();
    await expect(thread(page).locator('.genie__msg--stopped')).toContainText(INTERRUPTED);

    const polls = turn.progressPolls;
    await page.clock.runFor(PROGRESS_POLL_MS * 4);
    await app.settle();
    expect(turn.progressPolls, 'no poll after the reload').toBe(polls);
    expect({ submits: turn.submits, completes: turn.completes }, 'the complete is never sent twice').toEqual({ submits: 1, completes: 1 });
  });

  test('(c) a second page seeded with the first page\'s sessionStorage does not resume while the first holds the Web Lock', async ({ app, browserName, hygiene, mockApi, page }, testInfo) => {
    allowNavigationCancel(hygiene, browserName);
    const turn = registerGenieTurn(mockApi);
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    await expect.poll(() => turn.progressPolls).toBeGreaterThan(0);
    const locks = await webLocks(page, testInfo, browserName);
    const storage = await page.evaluate(() => JSON.stringify(Object.entries(window.sessionStorage)));
    expect(JSON.parse(storage).length, 'non-vacuity: the first page stored its in-flight turn').toBeGreaterThan(0);

    const second = await page.context().newPage();
    await mockApi.install(second);
    const secondCalls = { progress: 0, complete: 0, submit: 0 };
    await second.route('**/api/**', (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith('/genie/message/progress')) secondCalls.progress += 1;
      if (path.endsWith('/genie/message/complete')) secondCalls.complete += 1;
      if (path.endsWith('/genie/message/submit')) secondCalls.submit += 1;
      return route.fallback();
    });
    await second.addInitScript((entries: string) => {
      for (const [key, value] of JSON.parse(entries) as Array<[string, string]>) window.sessionStorage.setItem(key, value);
    }, storage);
    await second.goto('/ask-genie');
    await expect(second.locator('#main-content h1')).toBeVisible();

    // The first page keeps polling; the second, finding the lock taken, never does.
    const pollsBefore = turn.progressPolls;
    await expect.poll(() => turn.progressPolls, { timeout: 15_000 }).toBeGreaterThanOrEqual(pollsBefore + 2);
    expect(secondCalls, 'the second page does not resume a turn the first still owns').toEqual({ progress: 0, complete: 0, submit: 0 });

    await page.close();
    await second.reload();
    await expect(second.locator('#main-content h1')).toBeVisible();
    if (!locks) {
      await second.clock.runFor(PROGRESS_POLL_MS * 4);
      expect(secondCalls, 'fail closed: no resume without Web Locks').toEqual({ progress: 0, complete: 0, submit: 0 });
      await second.close();
      return;
    }
    await expect.poll(() => secondCalls.progress, { timeout: 15_000 }).toBeGreaterThan(0);
    turn.finishGenieTurn();
    await expect(thread(second).locator('.genie-answer')).toHaveCount(1);
    await expect.poll(() => secondCalls.complete).toBe(1);
    expect({ submits: turn.submits, completes: turn.completes }, 'one turn, completed once').toEqual({ submits: 1, completes: 1 });
    await second.close();
  });
});
