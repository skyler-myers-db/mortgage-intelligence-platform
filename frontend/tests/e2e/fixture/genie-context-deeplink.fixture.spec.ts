/**
 * Rendered-layer proofs for the w5-genie-context-deeplink lane (W5d), on the
 * production build at 1440x900 with the mocked API:
 *
 *   (a) genie-04 phase 2: an empty Lead Queue intersection offers "Ask
 *       Genie"; one click sends ONE closed-code POST /api/genie/context-scope
 *       and the panel composer holds the server's prompt verbatim; nothing
 *       is submitted, no audited read opens, axe is clean. The assignee
 *       preset is named in the label; an email assignee offers nothing; a
 *       withheld render shows the fixed line.
 *   (b)-(d) shell-03 (the conversation switch and the link cache) and (h)
 *       live in genie-context-deeplink.cross-engine.fixture.spec.ts, so the
 *       WebKit cross-engine run collects them too.
 *   (e) shell-03: an unavailable link: Retry wears the shared pending style and keeps
 *       focus (axe-clean while pending), and a second failure is announced
 *       in the route announcer. An exiting History menu (inert,
 *       aria-hidden) is axe-clean too.
 *   (f) genie-08 item 1: a five-section answer, on the route and in the
 *       panel, has five 24 px outline targets; a jump opens a collapsed
 *       section, lands its heading at the scroller top and focuses its
 *       toggle; axe (wcag22aa, target-size included) is clean.
 *   (g) a feedback vote that fails keeps its line across a leave and return
 *       (the collapse/expand twin is GenieAnswerFeedback.test.tsx); the
 *       buttons stay enabled and a retry reuses the request id.
 *
 * Every scope reply is the server's real rendering of the closed context the
 * test sends (tests/unit/test_genie_context_model.py pins it).
 */
import type { Locator, Page, Request } from '@playwright/test';
import { expectAxeClean, KNOWN_VIOLATIONS } from './axe';
import {
  ASSIGNEE_PROMPT,
  ASSIGNEE_SCOPE,
  FIVE_SECTION_TITLES,
  INTERSECTION_PROMPT,
  INTERSECTION_SCOPE,
  LINK_A_ID,
  WITHHELD_SCOPE,
  fiveSectionAnswer,
  registerEmptyQueue,
  registerInlineSubmit,
  registerScope,
  registerSessions,
} from './data/genieContextDeeplink';
import type { MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const STAGE_WAIT = { timeout: 15_000 };
const SCOPE_UNAVAILABLE = "Genie can't take this filter set as a question.";
const RETRY_FAILED = "This conversation still can't be loaded. Nothing was changed.";
const FEEDBACK_FAILED = 'Feedback could not be recorded. Please try again.';

function askPanel(page: Page): Locator {
  return page.locator('#main-content section[role="tabpanel"][id$="ask"]');
}

function emptyState(page: Page): Locator {
  return page.locator('#main-content .empty');
}

function geniePanel(page: Page): Locator {
  return page.getByRole('dialog', { name: 'Genie chat' });
}

function submits(mockApi: MockApi): number {
  return mockApi.calls.filter((call) => call.method === 'POST' && call.path === '/api/genie/message/submit').length;
}

async function askOnRoute(page: Page, question: string): Promise<void> {
  await askPanel(page).getByRole('textbox', { name: 'Ask Genie — question' }).fill(question);
  await askPanel(page).getByRole('button', { name: 'Ask Genie', exact: true }).click();
}

test.describe('(a) an empty Lead Queue asks a server-rendered scoped question (genie-04 phase 2)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`an all-mode intersection prefills the server prompt on click · ${theme}`, async ({ app, mockApi, page }) => {
      registerEmptyQueue(mockApi);
      const scope = registerScope(mockApi, INTERSECTION_SCOPE);
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue?segment_codes=itm,equity&segment_mode=all');
      const naturalLoad = markNaturalLoad(mockApi);

      await expect(emptyState(page)).toHaveAttribute('data-empty-cause', 'intersection');
      const ask = emptyState(page).getByRole('button', { name: 'Ask Genie about this filter selection' });
      await expect(ask).toHaveText('Ask Genie');
      await expect(ask).not.toHaveAttribute('title', /.*/);
      await ask.hover();
      await ask.focus();
      expect(scope.bodies, 'no request on render, hover or focus').toHaveLength(0);
      await expectAxeClean(page, { key: { route: 'lead-queue', state: 'genie-scope-ask' }, theme, known: KNOWN_VIOLATIONS });

      await ask.click();
      await expect(geniePanel(page).getByRole('textbox', { name: 'Ask Genie' })).toHaveValue(INTERSECTION_PROMPT);
      expect(scope.bodies).toEqual([{ route: 'lead-queue', segment_codes: ['itm', 'equity'], segment_mode: 'all' }]);
      expect(JSON.stringify(scope.bodies), 'codes only: no borrower id, no email').not.toMatch(/B-[0-9A-Z]{13}|@/);
      expect(submits(mockApi), 'a prefill is never a submit').toBe(0);
      expectNoAuditedReadSince(mockApi, naturalLoad, `scoped ask · ${theme}`);
    });
  }

  test('the assignee preset is named in the label and left out of the question', async ({ app, mockApi, page }) => {
    registerEmptyQueue(mockApi);
    const scope = registerScope(mockApi, ASSIGNEE_SCOPE);
    await app.gotoRoute('/lead-queue?state=TX&assigned_to=me');
    const ask = emptyState(page).getByRole('button', { name: 'Ask Genie about this filter selection without the assignee filter' });
    await expect(ask).toHaveText('Ask Genie without the assignee filter');
    await ask.click();
    await expect(geniePanel(page).getByRole('textbox', { name: 'Ask Genie' })).toHaveValue(ASSIGNEE_PROMPT);
    expect(scope.bodies).toEqual([{ route: 'lead-queue', state: 'TX', assignee: 'me' }]);
  });

  test('an email assignee offers no Ask and sends nothing', async ({ app, mockApi, page }) => {
    registerEmptyQueue(mockApi);
    const scope = registerScope(mockApi, INTERSECTION_SCOPE);
    await app.gotoRoute('/lead-queue?segment_codes=itm,equity&segment_mode=all&assigned_to=lo%40summit.example');
    await expect(emptyState(page)).toBeVisible();
    await expect(emptyState(page).locator('.genie-ask-about')).toHaveCount(0);
    expect(scope.bodies).toHaveLength(0);
  });

  test('a withheld render replaces the button with the fixed line inside the status region, and focus moves to Clear filters', async ({ app, mockApi, page }) => {
    registerEmptyQueue(mockApi);
    registerScope(mockApi, WITHHELD_SCOPE);
    await app.setTheme('dark');
    await app.gotoRoute('/lead-queue?zip=60601&segment_codes=itm,equity&segment_mode=all');
    const ask = emptyState(page).getByRole('button', { name: 'Ask Genie about this filter selection' });
    await ask.focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('#main-content .empty[role="status"] .muted')).toHaveText(SCOPE_UNAVAILABLE);
    await expect(emptyState(page).locator('.genie-ask-about')).toHaveCount(0);
    // The focused Ask left the DOM: focus lands on its sibling, never on <body> (WCAG 2.4.3).
    await expect(emptyState(page).getByRole('button', { name: 'Clear lead queue filters' })).toBeFocused();
    await expect(geniePanel(page)).toBeHidden();
    await expectAxeClean(page, { key: { route: 'lead-queue', state: 'genie-scope-withheld' }, theme: 'dark', known: KNOWN_VIOLATIONS });
  });
});

test.describe('the conversation link (shell-03)', () => {
  test('(e) Retry shows the pending style, keeps focus, and a second failure is announced', async ({ app, mockApi, page, hygiene }) => {
    hygiene.allow('console.error', /status of 503/);
    await app.setTheme('dark');
    let reads = 0;
    let release: () => void = () => undefined;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    mockApi.register<{ detail: string }>('GET', '/api/genie/sessions/:id', async () => {
      reads += 1;
      if (reads > 1) await held;
      return { status: 503, body: { detail: 'unavailable' } };
    });
    await page.goto(`/ask-genie/${LINK_A_ID}`, { waitUntil: 'domcontentloaded' });
    const retry = askPanel(page).locator('[data-genie-link="unavailable"]').getByRole('button', { name: /^Retry/ });
    await expect(retry).toBeVisible(STAGE_WAIT);
    await retry.focus();
    await retry.click();

    await expect(retry).toHaveClass(/btn--loading/);
    await expect(retry).toHaveAttribute('aria-busy', 'true');
    await expect(retry).toHaveAttribute('aria-disabled', 'true');
    await expect(retry).toBeFocused();
    await expectAxeClean(page, { key: { route: 'ask-genie', state: 'genie-link-retry-pending' }, theme: 'dark', known: KNOWN_VIOLATIONS });
    release();
    await expect(page.locator('[data-genie-announcer="route"]')).toHaveText(RETRY_FAILED, STAGE_WAIT);
    await expect(retry).not.toHaveClass(/btn--loading/);
    await expect(retry).toBeFocused();
    expect(reads).toBe(2);
  });
});

test.describe('an exiting History menu (motion-01 item 2e)', () => {
  // The harness pins reduced motion; the exit must run for the scan to see it.
  test.use({ contextOptions: { reducedMotion: 'no-preference' } });

  test('is axe-clean while it fades out inert and aria-hidden', async ({ app, mockApi, page }) => {
    registerSessions(mockApi);
    await app.setTheme('dark');
    await app.gotoRoute('/ask-genie');
    const toggle = askPanel(page).getByRole('button', { name: 'Genie conversation history' });
    const menu = askPanel(page).locator('.genie-history__menu');
    await toggle.click();
    await expect(menu.getByRole('menuitem')).toHaveCount(1);
    // Hold the exit well past one scan (inline CSSOM style, not CSP-gated markup).
    await menu.evaluate((element) => {
      (element as HTMLElement).style.transitionDuration = '60000ms';
    });
    await toggle.click();
    await expect(menu).toHaveClass(/genie-history__menu--exiting/);
    await expectAxeClean(page, { key: { route: 'ask-genie', state: 'genie-history-exiting' }, theme: 'dark', known: KNOWN_VIOLATIONS });
    await expect(menu, 'the scan ran inside the exit').toHaveClass(/genie-history__menu--exiting/);
  });
});

interface Box {
  y: number;
  height: number;
}

async function mustBox(locator: Locator): Promise<Box> {
  const box = await locator.boundingBox();
  if (!box) throw new Error('no layout box');
  return box;
}

/**
 * Collapse section 4, jump to it from the outline, and check the landing:
 * the heading's top settles within 40 px below `bandTop` (the scroller's
 * visible top: below the sticky route nav on the route, the panel body's
 * top edge in the panel).
 */
async function jumpToFourth(surface: Locator, bandTop: () => Promise<number>): Promise<void> {
  const outline = surface.getByRole('navigation', { name: 'Sections in this answer' });
  const links = outline.getByRole('button');
  await expect(links).toHaveText([...FIVE_SECTION_TITLES]);
  for (let i = 0; i < FIVE_SECTION_TITLES.length; i += 1) {
    expect((await mustBox(links.nth(i))).height, `outline target ${i + 1} is at least 24 px`).toBeGreaterThanOrEqual(24);
  }
  const toggle = surface.locator('h3 > button.genie-answer__section-toggle').nth(3);
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await links.nth(3).click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(toggle).toBeFocused();
  const heading = toggle.locator('xpath=..');
  await expect.poll(async () => (await mustBox(heading)).y - (await bandTop()), { timeout: 5_000 }).toBeLessThan(40);
  expect((await mustBox(heading)).y - (await bandTop()), 'never under the scroller edge').toBeGreaterThanOrEqual(-1);
}

async function bottomOf(locator: Locator): Promise<number> {
  const box = await mustBox(locator);
  return box.y + box.height;
}

test.describe('(f) the section outline for long sweeps (genie-08 item 1)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`on the route and in the panel · ${theme}`, async ({ app, mockApi, page }) => {
      // A route sweep, a panel sweep and two full axe scans in one test: on a
      // loaded runner the second scan alone can take 15 s (the W5d review
      // traces), so the budget is tripled rather than the halves split.
      test.slow();
      registerSessions(mockApi);
      registerInlineSubmit(mockApi, {
        answerFor: (question, conversationId) => fiveSectionAnswer(conversationId, `sweep-${question.length}`, question),
      });
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie');
      await askOnRoute(page, 'Run the five-part sweep.');
      await expect(askPanel(page).locator('.genie-answer__outline')).toBeVisible(STAGE_WAIT);
      await jumpToFourth(askPanel(page), () => bottomOf(page.locator('#main-content .route-nav')));
      await expectAxeClean(page, { key: { route: 'ask-genie', state: 'genie-answer-outline' }, theme, known: KNOWN_VIOLATIONS });

      await app.gotoRoute('/segment-intelligence');
      const dialog = await app.openGenie();
      await dialog.getByRole('textbox', { name: 'Ask Genie' }).fill('Run the five-part sweep again.');
      await dialog.getByRole('button', { name: 'Ask', exact: true }).click();
      await expect(dialog.locator('.genie-answer__outline').last()).toBeVisible(STAGE_WAIT);
      const latest = dialog.locator('.genie-answer').last();
      await jumpToFourth(latest, async () => (await mustBox(dialog.locator('.genie__body'))).y);
      await expectAxeClean(page, { key: { route: 'segment-intelligence', state: 'genie-panel-outline' }, theme, known: KNOWN_VIOLATIONS });
    });
  }
});

test('(g) a failed vote keeps its line across a leave and return; a retry reuses its id', async ({ app, mockApi, page, hygiene }) => {
  hygiene.allow('console.error', /status of 500/);
  registerSessions(mockApi);
  registerInlineSubmit(mockApi);
  const keys: string[] = [];
  page.on('request', (request: Request) => {
    if (request.method() === 'POST' && /\/genie\/feedback$/.test(new URL(request.url()).pathname)) {
      keys.push(request.headers()['idempotency-key'] ?? '');
    }
  });
  let votes = 0;
  mockApi.register<{ accepted: boolean; audit_event_id: string } | { detail: string }>('POST', '/api/genie/feedback', () => {
    votes += 1;
    return votes === 1
      ? { status: 500, body: { detail: 'Internal Server Error' } }
      : { body: { accepted: true, audit_event_id: 'fixture-audit-feedback-0002' } };
  });
  await app.gotoRoute(`/ask-genie/${LINK_A_ID}`);
  await askOnRoute(page, 'Which counties lead?');
  const answer = askPanel(page).locator('.genie-answer').filter({ hasText: 'Answer to: Which counties lead?' });
  await expect(answer).toBeVisible(STAGE_WAIT);
  await answer.getByRole('button', { name: 'Mark this answer helpful' }).click();
  const line = answer.locator('.genie-feedback__error');
  await expect(line).toHaveText(FEEDBACK_FAILED);
  await expect(line).toHaveAttribute('role', 'alert');

  // Leave and return: the thread and the line come back; the line is not re-announced.
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Leads', exact: true }).click();
  await app.settle();
  await page.goBack();
  await expect(page).toHaveURL(new RegExp(`/ask-genie/${LINK_A_ID}$`));
  const returned = askPanel(page).locator('.genie-answer').filter({ hasText: 'Answer to: Which counties lead?' });
  await expect(returned.locator('.genie-feedback__error')).toHaveText(FEEDBACK_FAILED, STAGE_WAIT);
  await expect(returned.locator('.genie-feedback__error')).not.toHaveAttribute('role', /.*/);
  const helpful = returned.getByRole('button', { name: 'Mark this answer helpful' });
  await expect(helpful).toBeEnabled();
  await expect(returned.getByRole('button', { name: 'Mark this answer not helpful' })).toBeEnabled();

  await helpful.click();
  await expect(returned.locator('.genie-feedback--done')).toContainText('Feedback recorded', STAGE_WAIT);
  expect(keys).toHaveLength(2);
  expect(keys[1], 'the retry reuses the request id').toBe(keys[0]);
});
