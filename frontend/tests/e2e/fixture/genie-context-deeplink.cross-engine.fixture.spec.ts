/**
 * The w5-genie-context-deeplink lane's cross-engine proofs (W5d): collected
 * by fixture-chromium on every fixture run and by fixture-webkit under
 * MIP_CROSS_ENGINE=1. Production build, 1440x900, mocked API.
 *
 *   (b) shell-03: a History load in the floating panel moves a mounted
 *       /ask-genie/<A> to /ask-genie/<B> with exactly one read of B, and the
 *       route's next follow-up targets B.
 *   (c) a settled turn whose persisted conversation differs moves the URL.
 *   (d) a follow-up in A, a leave and a Back show the follow-up with no
 *       second read of A (the link cache follows every settled turn).
 *   (h) motion-01 item 2e: the History menu's exiting class (inert,
 *       aria-hidden) holds until transitionend, then the menu leaves the
 *       DOM, also when the timeline runs the exit past the hook's wall-clock
 *       deadline; under reduced motion it leaves at once; a reopen mid-exit reads
 *       the sessions again; keys pressed while the floating panel's menu
 *       fades over the Lead Queue (A, R) still reach the queue; and a
 *       keyboard pick hands focus back to the History toggle, never to
 *       <body> (the picked row turns disabled while it loads), with and
 *       without reduced motion.
 *
 * The rest of the lane is genie-context-deeplink.fixture.spec.ts.
 */
import type { Locator, Page } from '@playwright/test';
import {
  LINK_A_ANSWER,
  LINK_A_ID,
  LINK_B_ANSWER,
  LINK_B_ID,
  registerInlineSubmit,
  registerSessions,
} from './data/genieContextDeeplink';
import { registerDraftEcho, registerHeldDecision } from './data/queueKeyboard';
import { describeExit, observeExit, type ExitObservation } from './exitObservation';
import type { MockApi } from './mockApi';
import { expect, test } from './test';

const STAGE_WAIT = { timeout: 15_000 };

function askPanel(page: Page): Locator {
  return page.locator('#main-content section[role="tabpanel"][id$="ask"]');
}

async function askOnRoute(page: Page, question: string): Promise<void> {
  await askPanel(page).getByRole('textbox', { name: 'Ask Genie — question' }).fill(question);
  await askPanel(page).getByRole('button', { name: 'Ask Genie', exact: true }).click();
}

test.describe('the conversation follows across the panel and the link (shell-03)', () => {
  test('(b) a panel History load moves /ask-genie/<A> to <B> with one read, and the route follows up in B', async ({ app, mockApi, page }) => {
    const reads = registerSessions(mockApi);
    const turns = registerInlineSubmit(mockApi);
    await app.gotoRoute(`/ask-genie/${LINK_A_ID}`);
    await expect(askPanel(page)).toContainText(LINK_A_ANSWER);

    const dialog = await app.openGenie();
    await dialog.getByRole('button', { name: 'Genie conversation history' }).click();
    await dialog.getByRole('menuitem', { name: /Equity spread/ }).click();
    await expect(page).toHaveURL(new RegExp(`/ask-genie/${LINK_B_ID}$`), STAGE_WAIT);
    await expect(askPanel(page)).toContainText(LINK_B_ANSWER);
    expect(reads.readsOf(LINK_B_ID), 'the link hydrates from the panel read').toBe(1);
    await dialog.getByRole('button', { name: 'Close Genie' }).click();

    await askOnRoute(page, 'Which counties lead?');
    await expect.poll(() => turns.conversations.length, STAGE_WAIT).toBe(1);
    expect(turns.conversations[0]).toBe(LINK_B_ID);
  });

  test('(c) a settled turn whose persisted conversation differs moves the URL with it', async ({ app, mockApi, page }) => {
    const reads = registerSessions(mockApi);
    registerInlineSubmit(mockApi, { persistTo: LINK_B_ID });
    await app.gotoRoute(`/ask-genie/${LINK_A_ID}`);
    await expect(askPanel(page)).toContainText(LINK_A_ANSWER);

    await askOnRoute(page, 'Start over by county?');
    await expect(page).toHaveURL(new RegExp(`/ask-genie/${LINK_B_ID}$`), STAGE_WAIT);
    await expect(askPanel(page)).toContainText('Answer to: Start over by county?');
    expect(reads.readsOf(LINK_B_ID), 'the cache is primed from the thread').toBe(0);
  });

  test('(d) a follow-up, a leave and a Back: the follow-up stays, and A is read once', async ({ app, mockApi, page }) => {
    const reads = registerSessions(mockApi);
    registerInlineSubmit(mockApi);
    await app.gotoRoute(`/ask-genie/${LINK_A_ID}`);
    await expect(askPanel(page)).toContainText(LINK_A_ANSWER);
    await askOnRoute(page, 'Which counties lead?');
    await expect(askPanel(page)).toContainText('Answer to: Which counties lead?', STAGE_WAIT);

    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Leads', exact: true }).click();
    await app.settle();
    await page.goBack();
    await expect(page).toHaveURL(new RegExp(`/ask-genie/${LINK_A_ID}$`));
    await expect(askPanel(page)).toContainText(LINK_A_ANSWER);
    await expect(askPanel(page)).toContainText('Answer to: Which counties lead?');
    expect(reads.readsOf(LINK_A_ID)).toBe(1);
  });
});

/** Stretch the menu's exit so a step can land inside it (inline style: CSSOM, not CSP-gated markup). */
async function holdExit(menu: Locator, ms: number): Promise<void> {
  await menu.evaluate((element, duration) => {
    (element as HTMLElement).style.transitionDuration = `${duration}ms`;
  }, ms);
}

function sessionListReads(mockApi: MockApi): number {
  return mockApi.calls.filter((call) => call.method === 'GET' && call.path === '/api/genie/sessions').length;
}

test.describe('(h) the History menu exit (motion-01 item 2e)', () => {
  // The harness pins reduced motion; these cases need the exit to run.
  test.use({ contextOptions: { reducedMotion: 'no-preference' } });
  const toggle = (page: Page) => askPanel(page).getByRole('button', { name: 'Genie conversation history' });
  const menu = (page: Page) => askPanel(page).locator('.genie-history__menu');

  const HISTORY_EXIT = {
    trigger: '#main-content button[aria-label="Genie conversation history"]',
    exitingClass: 'genie-history__menu--exiting',
  };

  /** The exit held (exiting, inert, aria-hidden, mounted) through its own transitionend, then the node left. */
  function expectHeldUntilTransitionEnd(exit: ExitObservation): void {
    const story = describeExit(exit);
    expect(exit.during, `mid-exit; ${story}`).toEqual({ exiting: true, inert: true, hidden: 'true', connected: true });
    expect(exit.end && { exiting: exit.end.exiting, inert: exit.end.inert, connected: exit.end.connected }, story).toEqual({
      exiting: true,
      inert: true,
      connected: true,
    });
    expect(exit.removedAfterMs, story).not.toBeNull();
    expect(exit.removedAfterMs ?? -1, story).toBeGreaterThanOrEqual(exit.end?.afterMs ?? Number.POSITIVE_INFINITY);
  }

  test('the exiting class holds until transitionend, then the menu leaves the DOM', async ({ app, mockApi, page }) => {
    registerSessions(mockApi);
    await app.gotoRoute('/ask-genie');
    await toggle(page).click();
    await expect(menu(page).getByRole('menuitem')).toHaveCount(1);
    expectHeldUntilTransitionEnd(await observeExit(menu(page), HISTORY_EXIT));
    await expect(menu(page)).toHaveCount(0);
    await expect(toggle(page)).toHaveAttribute('aria-expanded', 'false');
  });

  test('an exit the timeline runs late still holds until its own transitionend: the deadline waits for it', async ({ app, mockApi, page }) => {
    // Linux WebKit on CI (run 38565698599) painted no frame for ~430 ms, so
    // the exit started late on the document timeline while the hook's wall-
    // clock deadline (computed exit + 120 ms) ran on: the menu was cut mid-
    // exit and its transitionend never came. Slowing the menu's own
    // transitions to 0.3x replays that on any engine: they need ~400 ms
    // against a 240 ms first deadline.
    registerSessions(mockApi);
    await app.gotoRoute('/ask-genie');
    await toggle(page).click();
    await expect(menu(page).getByRole('menuitem')).toHaveCount(1);
    const exit = await observeExit(menu(page), { ...HISTORY_EXIT, slowTo: 0.3 });
    expectHeldUntilTransitionEnd(exit);
    expect(exit.pastDeadline, `non-vacuity: the exit outlived the first deadline; ${describeExit(exit)}`).toEqual({
      connected: true,
      exiting: true,
      running: true,
    });
    await expect(menu(page)).toHaveCount(0);
  });

  test('under reduced motion it leaves at once', async ({ app, mockApi, page }) => {
    registerSessions(mockApi);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await app.gotoRoute('/ask-genie');
    await toggle(page).click();
    await expect(menu(page).getByRole('menuitem')).toHaveCount(1);
    const goneNextFrame = await page.evaluate(async () => {
      document.querySelector<HTMLButtonElement>('#main-content button[aria-label="Genie conversation history"]')?.click();
      await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve(null))));
      return document.querySelector('#main-content .genie-history__menu') === null;
    });
    expect(goneNextFrame).toBe(true);
  });

  test('a reopen during the exit reads /api/genie/sessions again', async ({ app, mockApi, page }) => {
    registerSessions(mockApi);
    await app.gotoRoute('/ask-genie');
    await toggle(page).click();
    await expect(menu(page).getByRole('menuitem')).toHaveCount(1);
    expect(sessionListReads(mockApi)).toBe(1);
    await holdExit(menu(page), 4_000);

    await toggle(page).click();
    await expect(menu(page)).toHaveClass(/genie-history__menu--exiting/);
    await toggle(page).click();
    await expect(menu(page)).not.toHaveClass(/genie-history__menu--exiting/);
    await expect(menu(page).getByRole('menuitem')).toHaveCount(1);
    await expect.poll(() => sessionListReads(mockApi), STAGE_WAIT).toBe(2);
  });

  for (const reducedMotion of ['no-preference', 'reduce'] as const) {
    test(`a keyboard pick returns focus to the History toggle as the menu leaves · ${reducedMotion}`, async ({ app, mockApi, page }) => {
      registerSessions(mockApi);
      await page.emulateMedia({ reducedMotion });
      await app.gotoRoute('/ask-genie');
      await toggle(page).click();
      const row = menu(page).getByRole('menuitem', { name: /Equity spread/ });
      await expect(row).toHaveCount(1);
      await row.focus();
      await expect(row).toBeFocused();
      await page.keyboard.press('Enter');
      await expect(page).toHaveURL(new RegExp(`/ask-genie/${LINK_B_ID}$`), STAGE_WAIT);
      await expect(askPanel(page)).toContainText(LINK_B_ANSWER);
      await expect(menu(page)).toHaveCount(0, STAGE_WAIT);
      await expect(toggle(page)).toHaveAttribute('aria-expanded', 'false');
      await expect(toggle(page)).toBeFocused();
    });
  }

  test('keys during the exit: with the panel’s History menu fading over the Lead Queue, A and R reach the queue', async ({ app, mockApi, page }) => {
    // The Lead Queue, the panel and two dialogs in one test: slow under load.
    test.slow();
    registerSessions(mockApi);
    registerDraftEcho(mockApi);
    registerHeldDecision(mockApi);
    await app.gotoRoute('/lead-queue');
    const dialog = await app.openGenie();
    await dialog.getByRole('button', { name: 'Genie conversation history' }).click();
    const panelMenu = dialog.locator('.genie-history__menu');
    await expect(panelMenu.getByRole('menuitem')).toHaveCount(1);
    await holdExit(panelMenu, 4_000);

    // Closing the panel closes its History menu, which fades out inert.
    await page.getByRole('banner').getByRole('button', { name: 'Toggle Genie chat' }).click();
    await expect(page.locator('.genie-history__menu--exiting')).toHaveCount(1);
    const region = page.getByRole('region', { name: 'Ranked borrowers table scroll region' });
    await region.focus();
    await page.keyboard.press('j');
    await page.keyboard.press('a');
    await expect(page.locator('dialog.lead-approve-dialog').getByTestId('lead-approve-review-confirm')).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(page.locator('dialog.lead-approve-dialog')).toHaveCount(0);
    await region.focus();
    await page.keyboard.press('r');
    await expect(page.locator('.decision-panel').getByRole('combobox', { name: 'Reason' })).toBeFocused();
  });
});
