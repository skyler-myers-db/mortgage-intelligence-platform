/**
 * Rendered-layer proofs for w4-genie-client (audit 2026-09-21), 1440x900,
 * production build, mocked API:
 *
 *   genie-02 item 2   a turn a reload interrupted resumes from the SHELL on
 *                     any route, before the panel's first open: the launchers
 *                     ring only after the first status 200, badge the answer,
 *                     and no /api/genie/start or panel exists until the open;
 *                     a /api/health blip keeps the ring (w4-delivery-boot).
 *   shell-03          /ask-genie/:conversationId: hex and UUID links hydrate
 *                     the actor's own conversation, a foreign id (404), a 403
 *                     and a malformed id fail closed to one neutral state,
 *                     History load writes the URL, New thread takes it out,
 *                     and the title never holds the id.
 *   responsive-v1 #2  with the Console open, the panel is dragged no further
 *                     than the Console's edge, and opening the Console
 *                     re-clamps an undocked panel.
 *
 * Every natural load is followed by expectNoAuditedReadSince: no state here
 * may open an audited read. Axe runs on the pre-open ring, the hydrated link
 * and the not-found link in both themes.
 */
import type { Locator, Page } from '@playwright/test';
import type { GenieSessionDetail } from '../../../src/types';
import { expectAxeClean, KNOWN_VIOLATIONS } from './axe';
import {
  GENIE_CONVERSATION_ID,
  GENIE_LINK_ANSWER,
  GENIE_LINK_FOREIGN_ID,
  GENIE_LINK_HEX_ID,
  GENIE_LINK_HISTORY_SESSIONS,
  GENIE_LINK_QUESTION,
  GENIE_LINK_UUID_ID,
  genieSessionDetail,
} from './data/genie';
import { GENIE_JOB_ID, registerGenieJob } from './data/genieJobs';
import { GENIE_MESSAGE_ID, GENIE_PROGRESS_TOKEN, GENIE_QUESTION, genieAnswerFixture } from './data/genieTurn';
import { json } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const IN_FLIGHT_KEY = 'mip.genie.inFlightTurn';
const TITLE = 'Ask Genie · Mortgage Intelligence Platform';
const NOT_FOUND_COPY = "This conversation isn't available.";
const STAGE_WAIT = { timeout: 15_000 };
const ANSWER_TEXT = /leads the footprint with/;

function main(page: Page): Locator {
  return page.locator('#main-content');
}

function askPanel(page: Page): Locator {
  return main(page).locator('section[role="tabpanel"][id$="ask"]');
}

function genieStartCalls(mockApi: { calls: ReadonlyArray<{ method: string; path: string }> }): number {
  return mockApi.calls.filter((call) => call.method === 'POST' && /\/genie\/start$/.test(call.path)).length;
}

/** Put a v:2 completion-job record in this tab's session, as a reload mid-job leaves it. */
async function seedJobRecord(page: Page): Promise<void> {
  await page.evaluate(
    ({ key, question, conversationId, messageId, progressToken, jobId }) => {
      window.sessionStorage.setItem(
        key,
        JSON.stringify({
          v: 2,
          question,
          conversationId,
          surface: 'panel',
          startedAt: Date.now(),
          deep: false,
          phase: 'completing',
          ids: { conversationId, messageId, progressToken },
          asyncComplete: true,
          jobId,
        }),
      );
    },
    {
      key: IN_FLIGHT_KEY,
      question: GENIE_QUESTION,
      conversationId: GENIE_CONVERSATION_ID,
      messageId: GENIE_MESSAGE_ID,
      progressToken: GENIE_PROGRESS_TOKEN,
      jobId: GENIE_JOB_ID,
    },
  );
}

/** Serve the actor's own conversations: the shape-valid link ids replay. */
function registerSessions(mockApi: Parameters<typeof registerGenieJob>[0]): void {
  const owned = new Set([GENIE_LINK_HEX_ID, GENIE_LINK_UUID_ID]);
  mockApi.register<GenieSessionDetail | { detail: string }>('GET', '/api/genie/sessions/:id', (request) =>
    owned.has(request.params.id)
      ? { body: genieSessionDetail(request.params.id) }
      : { status: 404, body: { detail: 'genie conversation not found' } },
  );
}

test.describe('the shell resumes a turn before the first open (genie-02 item 2)', () => {
  test('(a) the ring waits for the first 200, the badge follows the answer, and nothing mounts or reads start before the open', async ({ app, page, mockApi }) => {
    const job = registerGenieJob(mockApi, { steps: [{ stage: 'verifying' }, 'succeeded'] });
    await app.gotoRoute('/segment-intelligence');
    await seedJobRecord(page);
    const release = job.holdStatus();
    const polls = job.statusPolls;
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(main(page).locator('h1')).toBeVisible();

    const toggle = app.genieToggle();
    const fab = page.locator('.genie__fab');
    await expect.poll(() => job.statusPolls, STAGE_WAIT).toBeGreaterThan(polls);
    // The held status poll keeps app.settle() from ever going quiet, and the
    // mock records a call when it is fulfilled, not when it starts. The route's
    // natural load (its GET /api/leads included) ends once the held poll is
    // the only request in flight and the API has been quiet for a moment.
    await expect.poll(() => mockApi.inflight === 1 && mockApi.idleMs >= 300, STAGE_WAIT).toBe(true);
    const naturalLoad = markNaturalLoad(mockApi);
    // The held poll has not answered: a resumed turn shows no ring yet.
    await expect(toggle).not.toHaveClass(/is-genie-running/);
    await expect(fab).not.toHaveClass(/is-genie-running/);
    await expect(app.geniePanel()).toHaveCount(0);

    release();
    await expect(toggle).toHaveClass(/is-genie-running/, STAGE_WAIT);
    await expect(fab).toHaveClass(/is-genie-running/);
    // One id among the description ids: the Topbar Tooltip (critic-08) adds its own.
    await expect(toggle).toHaveAttribute('aria-describedby', /(^|\s)genie-launcher-status(\s|$)/);
    await expect(page.locator('#genie-launcher-status')).toHaveText('Genie is still working on your question.');

    job.next();
    await expect(toggle).toHaveClass(/is-genie-ready/, STAGE_WAIT);
    await expect(fab).toHaveClass(/is-genie-ready/);
    await expect(toggle).not.toHaveClass(/is-genie-running/);
    await expect(page.locator('#genie-launcher-status')).toHaveText('Genie answer ready. Open Genie to read it.');
    expect(genieStartCalls(mockApi), 'no /api/genie/start before the open').toBe(0);
    await expect(app.geniePanel(), 'the panel is not mounted before the open').toHaveCount(0);
    expect(job.submits, 'a resume never re-submits').toBe(0);

    await toggle.click();
    const dialog = page.getByRole('dialog', { name: 'Genie chat' });
    await expect(dialog.locator('.genie__msg--user')).toHaveText([GENIE_QUESTION]);
    await expect(dialog.locator('.genie-answer')).toContainText(ANSWER_TEXT);
    await expect(toggle).not.toHaveClass(/is-genie-ready|is-genie-running/);
    expectNoAuditedReadSince(mockApi, naturalLoad, 'shell resume');
  });

  test('(b) a 502 /api/health mid-turn keeps the ring, and the answer still lands', async ({ app, page, mockApi }) => {
    const job = registerGenieJob(mockApi, { steps: [{ stage: 'verifying' }, 'succeeded'] });
    await app.gotoRoute('/segment-intelligence');
    await seedJobRecord(page);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(main(page).locator('h1')).toBeVisible();
    const toggle = app.genieToggle();
    await expect(toggle).toHaveClass(/is-genie-running/, STAGE_WAIT);

    const healthCalls = () => mockApi.calls.filter((call) => /\/health$/.test(call.path)).length;
    const restore = app.degrade('/api/health', { status: 502, body: { detail: 'Bad gateway' } });
    const before = healthCalls();
    await page.clock.runFor(8_000);
    await expect.poll(healthCalls, STAGE_WAIT).toBeGreaterThan(before);
    await expect(toggle, 'unreachable is not an actor change').toHaveClass(/is-genie-running/);
    expect(await page.evaluate((key) => window.sessionStorage.getItem(key), IN_FLIGHT_KEY)).not.toBeNull();

    restore();
    job.next();
    await expect(toggle).toHaveClass(/is-genie-ready/, STAGE_WAIT);
    const dialog = await app.openGenie();
    await expect(dialog.locator('.genie-answer')).toContainText(ANSWER_TEXT);
  });

  for (const theme of FIXTURE_THEMES) {
    test(`(c) ${theme}: the pre-open ring is axe-clean`, async ({ app, page, mockApi }) => {
      registerGenieJob(mockApi, { steps: [{ stage: 'verifying' }] });
      await app.setTheme(theme);
      await app.gotoRoute('/segment-intelligence');
      await seedJobRecord(page);
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(app.genieToggle()).toHaveClass(/is-genie-running/, STAGE_WAIT);
      await expectAxeClean(page, { key: { route: 'segment-intelligence', state: 'genie-pre-open-ring' }, theme, known: KNOWN_VIOLATIONS });
    });
  }
});

test.describe('the conversation deep link (shell-03)', () => {
  test('(d) a reload of /ask-genie/<32 hex> hydrates the thread, and a follow-up carries that conversation', async ({ app, page, mockApi }) => {
    registerSessions(mockApi);
    const submits: unknown[] = [];
    mockApi.register('POST', '/api/genie/message/submit', (request) => {
      submits.push(request.body);
      return json({
        completed: true,
        conversation_id: GENIE_LINK_HEX_ID,
        message_id: 'fixture-message-link-0002',
        progress_token: null,
        question_hash: 'e'.repeat(16),
        response: genieAnswerFixture({ conversation_id: GENIE_LINK_HEX_ID, message_id: 'fixture-message-link-0002' }),
      });
    });
    await app.gotoRoute(`/ask-genie/${GENIE_LINK_HEX_ID}`);
    await page.reload({ waitUntil: 'domcontentloaded' });
    await app.settle();
    const naturalLoad = markNaturalLoad(mockApi);

    await expect(askPanel(page).locator('.genie__msg--user')).toHaveText([GENIE_LINK_QUESTION]);
    await expect(askPanel(page)).toContainText(GENIE_LINK_ANSWER);
    expect(await page.title()).toBe(TITLE);

    await askPanel(page).getByRole('textbox', { name: 'Ask Genie — question' }).fill('Which counties lead?');
    await askPanel(page).getByRole('button', { name: 'Ask Genie', exact: true }).click();
    await expect.poll(() => submits.length, STAGE_WAIT).toBe(1);
    expect(submits[0]).toMatchObject({ conversation_id: GENIE_LINK_HEX_ID });
    await expect(askPanel(page).locator('.genie-answer')).toHaveCount(2, STAGE_WAIT);
    expect(new URL(page.url()).pathname, 'a settled turn never rewrites the URL').toBe(`/ask-genie/${GENIE_LINK_HEX_ID}`);
    expect(await page.title()).toBe(TITLE);
    expectNoAuditedReadSince(mockApi, naturalLoad, 'hex link');
  });

  test('(e) a UUID link hydrates', async ({ app, page, mockApi }) => {
    registerSessions(mockApi);
    await app.gotoRoute(`/ask-genie/${GENIE_LINK_UUID_ID}`);
    await expect(askPanel(page).locator('.genie__msg--user')).toHaveText([GENIE_LINK_QUESTION]);
    expect(await page.title()).toBe(TITLE);
  });

  test('(f) a foreign id (404) and a 403 show the same neutral state and never ask Genie', async ({ app, page, mockApi }) => {
    registerSessions(mockApi);
    const shown: string[] = [];
    for (const status of [404, 403]) {
      const restore = app.degrade(/^\/api\/genie\/sessions\/[^/]+$/, { status, body: { detail: 'not yours' } });
      await app.gotoRoute(`/ask-genie/${GENIE_LINK_FOREIGN_ID}`);
      const state = askPanel(page).locator('[data-genie-link="not-found"]');
      await expect(state).toContainText(NOT_FOUND_COPY);
      await expect(askPanel(page).getByRole('textbox', { name: 'Ask Genie — question' })).toHaveCount(0);
      shown.push(await state.innerText());
      restore();
    }
    expect(shown[0]).toBe(shown[1]);
    expect(mockApi.calls.filter((call) => /\/genie\/message/.test(call.path))).toEqual([]);
    expect(await page.title()).toBe(TITLE);
  });

  test('(g) /ask-genie/not-an-id makes zero session requests', async ({ app, page, mockApi }) => {
    registerSessions(mockApi);
    await app.gotoRoute('/ask-genie/not-an-id');
    await expect(askPanel(page).locator('[data-genie-link="not-found"]')).toContainText(NOT_FOUND_COPY);
    expect(mockApi.calls.filter((call) => /\/genie\/sessions\//.test(call.path))).toEqual([]);

    await askPanel(page).getByRole('button', { name: 'Open Ask Genie' }).click();
    await expect(page).toHaveURL(/\/ask-genie$/);
    await expect(askPanel(page).getByRole('textbox', { name: 'Ask Genie — question' })).toBeFocused();
  });

  test('(h) History load puts the id in the URL, a reload keeps the thread, and New thread takes it out', async ({ app, page, mockApi }) => {
    registerSessions(mockApi);
    mockApi.register('GET', '/api/genie/sessions', () => json({ sessions: GENIE_LINK_HISTORY_SESSIONS }));
    await app.gotoRoute('/ask-genie');
    const naturalLoad = markNaturalLoad(mockApi);
    await askPanel(page).getByRole('button', { name: 'Genie conversation history' }).click();
    await askPanel(page).getByRole('menuitem', { name: /Prime refi by state/ }).click();
    await expect(page).toHaveURL(new RegExp(`/ask-genie/${GENIE_LINK_HEX_ID}$`));
    await expect(askPanel(page).locator('.genie__msg--user')).toHaveText([GENIE_LINK_QUESTION]);
    const detailReads = () => mockApi.calls.filter((call) => call.path.endsWith(`/genie/sessions/${GENIE_LINK_HEX_ID}`)).length;
    expect(detailReads(), 'the link hydrates from the History read').toBe(1);
    expect(await page.title()).toBe(TITLE);

    await page.reload({ waitUntil: 'domcontentloaded' });
    await app.settle();
    await expect(askPanel(page).locator('.genie__msg--user')).toHaveText([GENIE_LINK_QUESTION]);

    await askPanel(page).getByRole('button', { name: 'New thread' }).click();
    await expect(page).toHaveURL(/\/ask-genie$/);
    await expect(askPanel(page).locator('.genie__msg--user')).toHaveCount(0);
    expectNoAuditedReadSince(mockApi, naturalLoad, 'history link');
  });

  for (const theme of FIXTURE_THEMES) {
    test(`(i) ${theme}: the hydrated and the not-found link are axe-clean`, async ({ app, page, mockApi }) => {
      registerSessions(mockApi);
      await app.setTheme(theme);
      await app.gotoRoute(`/ask-genie/${GENIE_LINK_HEX_ID}`);
      await expect(askPanel(page)).toContainText(GENIE_LINK_ANSWER);
      await expectAxeClean(page, { key: { route: 'ask-genie', state: 'conversation-link' }, theme, known: KNOWN_VIOLATIONS });

      await app.gotoRoute('/ask-genie/not-an-id');
      await expect(askPanel(page).locator('[data-genie-link="not-found"]')).toBeVisible();
      await expectAxeClean(page, { key: { route: 'ask-genie', state: 'conversation-link-not-found' }, theme, known: KNOWN_VIOLATIONS });
    });
  }
});

test.describe('the panel keeps clear of the Console (responsive-v1 item 2)', () => {
  interface Box {
    x: number;
    y: number;
    width: number;
    height: number;
  }

  function intersects(a: Box, b: Box, tolerance = 1): boolean {
    return (
      a.x + a.width > b.x + tolerance &&
      b.x + b.width > a.x + tolerance &&
      a.y + a.height > b.y + tolerance &&
      b.y + b.height > a.y + tolerance
    );
  }

  async function box(locator: Locator): Promise<Box> {
    const found = await locator.boundingBox();
    if (!found) throw new Error('element has no box');
    return found;
  }

  /** Drag the panel by its header background by (dx, dy): a point in the
   *  header's bottom padding, clear of its children and the resize edges. */
  async function dragHeader(page: Page, dx: number, dy: number): Promise<void> {
    const header = await box(page.locator('.genie .genie__hdr'));
    const start = { x: header.x + header.width / 2, y: header.y + header.height - 4 };
    await page.mouse.move(start.x, start.y);
    await page.mouse.down();
    for (let step = 1; step <= 10; step += 1) {
      await page.mouse.move(start.x + (dx * step) / 10, start.y + (dy * step) / 10);
    }
    await page.mouse.up();
  }

  test('(j) at 1440x900 a panel dragged into the right zone stops at the Console, and opening the Console re-clamps it', async ({ app, page, mockApi }) => {
    await app.gotoRoute('/segment-intelligence');
    const naturalLoad = markNaturalLoad(mockApi);
    const consolePanel = await app.openConsole();
    await app.openGenie();
    const panel = app.geniePanel();
    const docked = await box(panel);
    const consoleBox = await box(consolePanel);
    expect(intersects(docked, consoleBox), 'the docked panel sits left of the Console').toBe(false);

    await dragHeader(page, 600, -200);
    await expect(panel).toHaveClass(/is-undocked/);
    const dragged = await box(panel);
    expect(intersects(dragged, consoleBox), 'a drag stops at the Console edge').toBe(false);
    expect(dragged.x + dragged.width).toBeLessThanOrEqual(consoleBox.x + 1);

    // Close the Console, drag to the far right, then open it again.
    await page.getByRole('banner').getByRole('button', { name: 'Toggle console' }).click();
    await expect(consolePanel.locator('.tweaks__body')).toBeHidden();
    await dragHeader(page, 600, 0);
    const farRight = await box(panel);
    expect(farRight.x + farRight.width).toBeGreaterThan(consoleBox.x + 1);
    const reopened = await app.openConsole();
    await expect.poll(async () => intersects(await box(panel), await box(reopened)), STAGE_WAIT).toBe(false);
    expectNoAuditedReadSince(mockApi, naturalLoad, 'panel geometry');
  });
});
