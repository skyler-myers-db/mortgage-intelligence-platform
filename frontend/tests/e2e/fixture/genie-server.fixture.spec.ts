/**
 * Rendered-layer proofs for w4-genie-server (audit 2026-09-21 `genie-03`
 * server cancel): Stop on a job-backed turn sends
 * ONE audited cancel carrying the turn's ids, the job and the submit's
 * 16-hex label (never the question); the Stopped note keeps its honest
 * unconfirmed copy until the server replies, then says what really happened;
 * a Stop with no job named sends nothing; and the progress card shows the
 * job's typical duration without overflowing its head.
 *
 * The job is scripted through data/genieJobs.ts (registerGenieJob, its cancel
 * answered with a real delay); counters read the mock's own call log. 1440x900,
 * production build.
 */
import type { Locator, Page } from '@playwright/test';
import type { GenieLiveProgress } from '../../../src/lib/apiTypes';
import { expectAxeClean } from './axe';
import { GENIE_JOB_ID, genieJobSubmitFixture, registerGenieJob, type GenieJobScript } from './data/genieJobs';
import { GENIE_CONVERSATION_ID } from './data/genie';
import { GENIE_MESSAGE_ID, GENIE_PROGRESS_TOKEN, GENIE_QUESTION } from './data/genieTurn';
import { json } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

/** The client's job-status poll cadence (lib/genieAsk.ts JOB_POLL_MS). */
const POLL_MS = 1_500;
const WAIT = { timeout: 15_000 };
const QUEUED_LABEL = 'Queued for governed completion';
/** lib/genieTurnOutcome.ts: the unconfirmed copy, then the server's. */
const UNCONFIRMED = 'Genie may still finish this turn on the server';
const CONFIRMED =
  'Stopped. This answer was not recorded and will not appear later. Genie may keep the question as context for the next turn in this thread.';
const RECORDED = 'Stopped here, but the answer had already been verified and recorded. Find it in History.';
/** A running job that never finishes on its own. */
const RUNNING: GenieJobScript['steps'] = [{ stage: 'queued' }, { stage: 'researching', parts: [1, 7] }];

function main(page: Page): Locator {
  return page.locator('#main-content');
}

function thread(page: Page): Locator {
  return main(page).locator('.genie-thread');
}

async function askOnRoute(page: Page): Promise<void> {
  await main(page).getByRole('textbox', { name: 'Ask Genie — question' }).fill(GENIE_QUESTION);
  await main(page).getByRole('button', { name: 'Ask Genie', exact: true }).click();
  await expect(thread(page).locator('.genie-progress__label')).toContainText(QUEUED_LABEL, WAIT);
}

async function askInPanel(dialog: Locator): Promise<void> {
  await dialog.getByRole('textbox', { name: 'Ask Genie' }).fill(GENIE_QUESTION);
  await dialog.getByRole('button', { name: 'Ask', exact: true }).click();
  await expect(dialog.locator('.genie-progress__label')).toContainText(QUEUED_LABEL, WAIT);
}

/** Cancel POSTs in the mock's own call log (the /api and /api/v1 forms). */
function cancelCalls(calls: ReadonlyArray<{ path: string }>): number {
  return calls.filter((call) => /\/genie\/message\/cancel$/.test(call.path)).length;
}

test.describe('(a) Stop on a job turn sends one cancel and confirms it', () => {
  for (const surface of ['route', 'panel'] as const) {
    test(`${surface}: five fields, the submit label, never the question; unconfirmed until the delayed 200; no poll after`, async ({ app, page, mockApi }) => {
      const job = registerGenieJob(mockApi, { steps: RUNNING, cancel: { outcome: 'cancelled', delayMs: 1_000 } });
      let scope: Locator;
      if (surface === 'route') {
        await app.gotoRoute('/ask-genie');
        await askOnRoute(page);
        scope = thread(page);
      } else {
        await app.gotoRoute('/');
        scope = await app.openGenie();
        await askInPanel(scope);
      }
      await expect.poll(() => job.statusPolls, WAIT).toBeGreaterThan(0);
      await app.settle();

      await scope.getByRole('button', { name: 'Stop this Genie turn' }).click();
      const note = scope.locator('.genie__msg--stopped');
      await expect(note).toContainText(UNCONFIRMED);
      const pollsAtStop = job.statusPolls;
      await expect.poll(() => job.cancels, WAIT).toBe(1);
      await expect(note).toContainText(CONFIRMED, WAIT);
      await expect(note).not.toContainText(UNCONFIRMED);

      const [body] = job.cancelBodies as Array<Record<string, unknown>>;
      expect(Object.keys(body).sort()).toEqual(['conversation_id', 'job_id', 'message_id', 'progress_token', 'question_hash']);
      expect(body).toEqual({
        conversation_id: GENIE_CONVERSATION_ID,
        message_id: GENIE_MESSAGE_ID,
        progress_token: GENIE_PROGRESS_TOKEN,
        job_id: GENIE_JOB_ID,
        question_hash: genieJobSubmitFixture().question_hash,
      });
      expect(JSON.stringify(body)).not.toContain(GENIE_QUESTION);

      await page.clock.runFor(POLL_MS * 4);
      await app.settle();
      expect(job.statusPolls, 'no /message/status poll after Stop').toBe(pollsAtStop);
      expect(cancelCalls(mockApi.calls), 'exactly one cancel').toBe(1);
      await expect(note).toHaveCount(1);
    });
  }
});

test('(b) "recorded" shows the recorded copy and the announcer says it once', async ({ app, page, mockApi }) => {
  const job = registerGenieJob(mockApi, { steps: RUNNING, cancel: { outcome: 'recorded' } });
  await app.gotoRoute('/ask-genie');
  await askOnRoute(page);
  await expect.poll(() => job.statusPolls, WAIT).toBeGreaterThan(0);
  await page.evaluate((recorded) => {
    const win = window as Window & { __recordedSaid?: number };
    win.__recordedSaid = 0;
    const region = document.querySelector('[data-genie-announcer="route"]')!;
    let last = region.textContent ?? '';
    new MutationObserver(() => {
      const text = region.textContent ?? '';
      if (text !== last && text === recorded) win.__recordedSaid = (win.__recordedSaid ?? 0) + 1;
      last = text;
    }).observe(region, { childList: true, subtree: true, characterData: true });
  }, RECORDED);

  await main(page).getByRole('button', { name: 'Stop this Genie turn' }).click();

  await expect(thread(page).locator('.genie__msg--stopped')).toContainText(RECORDED, WAIT);
  await expect(page.locator('[data-genie-announcer="route"]')).toHaveText(RECORDED);
  await page.clock.runFor(POLL_MS * 4);
  await app.settle();
  expect(await page.evaluate(() => (window as Window & { __recordedSaid?: number }).__recordedSaid)).toBe(1);
  expect(job.cancels).toBe(1);
});

test('(c) a cancel that fails (503) leaves the unconfirmed copy', async ({ app, page, mockApi, hygiene }) => {
  // The browser logs the scripted 503 itself; only that resource is allowed.
  hygiene.allow('console.error', /503 \(Service Unavailable\).*\/genie\/message\/cancel/);
  const job = registerGenieJob(mockApi, { steps: RUNNING, cancel: { outcome: 'cancelled', status: 503 } });
  await app.gotoRoute('/ask-genie');
  await askOnRoute(page);
  await expect.poll(() => job.statusPolls, WAIT).toBeGreaterThan(0);

  await main(page).getByRole('button', { name: 'Stop this Genie turn' }).click();
  await expect.poll(() => job.cancels, WAIT).toBe(1);
  await page.clock.runFor(POLL_MS * 2);
  await app.settle();

  const note = thread(page).locator('.genie__msg--stopped');
  await expect(note).toContainText(UNCONFIRMED);
  await expect(note).not.toContainText(CONFIRMED);
  expect(job.cancels, 'a non-retryable 503 is not re-sent').toBe(1);
});

test('(d) a Stop while Genie is still answering (no job yet) sends no cancel', async ({ app, page, mockApi }) => {
  const job = registerGenieJob(mockApi, { steps: RUNNING });
  // Genie's own turn never finishes: the turn stays in its polling phase.
  mockApi.register<GenieLiveProgress>('POST', '/api/genie/message/progress', () =>
    json({
      status: 'EXECUTING_QUERY',
      stage: 'executing',
      stage_label: 'Running the governed query',
      terminal: false,
      failed: false,
      reasoning_trace: [],
      sql_preview: null,
      error_hint: null,
    }),
  );
  await app.gotoRoute('/ask-genie');
  await main(page).getByRole('textbox', { name: 'Ask Genie — question' }).fill(GENIE_QUESTION);
  await main(page).getByRole('button', { name: 'Ask Genie', exact: true }).click();
  await expect(thread(page).locator('.genie-progress__label')).toContainText('Running the governed query', WAIT);

  await main(page).getByRole('button', { name: 'Stop this Genie turn' }).click();
  await expect(thread(page).locator('.genie__msg--stopped')).toContainText(UNCONFIRMED);
  await page.clock.runFor(POLL_MS * 4);
  await app.settle();

  expect(job.completes).toBe(0);
  expect(job.cancels).toBe(0);
  expect(cancelCalls(mockApi.calls)).toBe(0);
});

test.describe('(f) axe', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: the confirmed Stopped note has no WCAG violations`, async ({ app, page, mockApi }) => {
      const job = registerGenieJob(mockApi, { steps: RUNNING, cancel: { outcome: 'cancelled' } });
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie');
      await askOnRoute(page);
      await expect.poll(() => job.statusPolls, WAIT).toBeGreaterThan(0);
      await main(page).getByRole('button', { name: 'Stop this Genie turn' }).click();
      await expect(thread(page).locator('.genie__msg--stopped')).toContainText(CONFIRMED, WAIT);

      await expectAxeClean(page, { key: { route: 'ask-genie', state: 'genie-stop-confirmed' }, theme, known: {} });
    });
  }
});
