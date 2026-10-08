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
