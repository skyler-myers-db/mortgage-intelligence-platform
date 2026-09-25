/**
 * Rendered-layer proofs for w3-genie-jobs (audit 2026-09-21 `genie-01`,
 * `delivery-04`): the governed completion runs as a server-side job. The
 * complete call answers 202, the browser polls `/message/status`, the rail
 * names the server's own stages, a reload resumes the job without a second
 * submit (and without a second complete once the job id is known), and the
 * actor boundary fails closed.
 *
 * The job is scripted through data/genieJobs.ts (registerGenieJob, stepped by
 * the test); counters read the mock's own call log. 1440x900, production build.
 */
import type { Page } from '@playwright/test';
import type { HealthPayload } from '../../../src/lib/apiTypes';
import { expectAxeClean } from './axe';
import { GENIE_JOB_EXPIRED_HINT, GENIE_JOB_ID, registerGenieJob } from './data/genieJobs';
import { GENIE_QUESTION, registerGenieTurn } from './data/genieTurn';
import { HEALTH_OK } from './data/shell';
import { json } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

/** The client's job-status poll cadence (lib/genieAsk.ts JOB_POLL_MS). */
const POLL_MS = 1_500;
const STAGE_WAIT = { timeout: 15_000 };
const IN_FLIGHT_KEY = 'mip.genie.inFlightTurn';
const ANSWER_TEXT = /leads the footprint with/;

function main(page: Page) {
  return page.locator('#main-content');
}

function thread(page: Page) {
  return main(page).locator('.genie-thread');
}

function cardLabel(page: Page) {
  return thread(page).locator('.genie-progress__label');
}

function routeRegion(page: Page) {
  return page.locator('[data-genie-announcer="route"]');
}

async function askOnRoute(page: Page, { waitForCard = true }: { waitForCard?: boolean } = {}): Promise<void> {
  await main(page).getByRole('textbox', { name: 'Ask Genie — question' }).fill(GENIE_QUESTION);
  await main(page).getByRole('button', { name: 'Ask Genie', exact: true }).click();
  if (waitForCard) await expect(thread(page).locator('.genie-progress')).toBeVisible();
}

async function inFlightRecord(page: Page): Promise<Record<string, unknown> | null> {
  const raw = await page.evaluate((key) => window.sessionStorage.getItem(key), IN_FLIGHT_KEY);
  return raw ? (JSON.parse(raw) as Record<string, unknown>) : null;
}

/** Log every text the progress card's label ever shows, from page load on. */
async function recordCardLabels(page: Page): Promise<() => Promise<string[]>> {
  await page.evaluate(() => {
    const win = window as Window & { __cardLabels?: string[] };
    const seen: string[] = [];
    win.__cardLabels = seen;
    new MutationObserver(() => {
      for (const label of document.querySelectorAll('.genie-progress__label')) {
        const text = label.textContent ?? '';
        if (text && seen[seen.length - 1] !== text) seen.push(text);
      }
    }).observe(document.body, { childList: true, subtree: true, characterData: true });
  });
  return () => page.evaluate(() => (window as Window & { __cardLabels?: string[] }).__cardLabels ?? []);
}

test.describe('the completion job on /ask-genie', () => {
  test('(a) the 202 is followed by the server stages in order, never "Answer ready" in the card, one submit, one complete, one answer', async ({ app, page, mockApi }) => {
    const job = registerGenieJob(mockApi);
    await app.gotoRoute('/ask-genie');
    const labels = await recordCardLabels(page);
    await askOnRoute(page);

    await expect(cardLabel(page)).toHaveText('Queued for governed completion', STAGE_WAIT);
    expect(job.completeBodies[0]).toMatchObject({ respond_async: true, question: GENIE_QUESTION });
    for (const stage of [
      'Verifying the answer against its rows',
      'Cross-checking against the governed metric framing',
      'Applying the output policy and recording the answer',
    ]) {
      job.next();
      await expect(cardLabel(page)).toHaveText(stage, STAGE_WAIT);
    }
    job.next();
    await expect(thread(page).locator('.genie-answer')).toHaveCount(1, STAGE_WAIT);
    await expect(thread(page)).toContainText(ANSWER_TEXT);

    const shown = await labels();
    expect(shown.slice(-4)).toEqual([
      'Queued for governed completion',
      'Verifying the answer against its rows',
      'Cross-checking against the governed metric framing',
      'Applying the output policy and recording the answer',
    ]);
    for (const text of shown) expect(text, 'the card never claims the answer is ready').not.toMatch(/Answer ready/);
    expect(job.submits).toBe(1);
    expect(job.completes).toBe(1);
    expect(job.statusBodies.every((body) => (body as { job_id?: string }).job_id === GENIE_JOB_ID)).toBe(true);
    expect(await inFlightRecord(page)).toBeNull();
  });

  test('(b) deep: "3 of 7 sub-analyses finished" is shown on the card, and the announcer never carries a count', async ({ app, page, mockApi }) => {
    const job = registerGenieJob(mockApi, {
      deep: true,
      steps: [
        { stage: 'queued' },
        { stage: 'researching', parts: [3, 7] },
        { stage: 'researching', parts: [4, 7] },
        'succeeded',
      ],
    });
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    job.next();

    await expect(cardLabel(page)).toHaveText('Running governed sub-analyses · 3 of 7 sub-analyses finished', STAGE_WAIT);
    await expect(routeRegion(page)).toHaveText('Running governed sub-analyses');
    await expect(thread(page).locator('.genie-progress__stage[aria-current="step"]')).toHaveText('Deep research');
    job.next();
    await expect(cardLabel(page)).toHaveText('Running governed sub-analyses · 4 of 7 sub-analyses finished', STAGE_WAIT);
    await expect(routeRegion(page)).toHaveText('Running governed sub-analyses');
    await expect(routeRegion(page)).not.toContainText('of 7');

    job.next();
    await expect(thread(page).locator('.genie-answer')).toHaveCount(1, STAGE_WAIT);
  });
});

test.describe('reload and identity', () => {
  test('(c) a reload mid-job resumes the status polls: no second submit or complete, hidden until the first 200, one exchange', async ({ app, page, mockApi }) => {
    const job = registerGenieJob(mockApi);
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    job.next();
    await expect(cardLabel(page)).toHaveText('Verifying the answer against its rows', STAGE_WAIT);
    expect(await inFlightRecord(page)).toMatchObject({ v: 2, phase: 'completing', asyncComplete: true, jobId: GENIE_JOB_ID });

    const release = job.holdStatus();
    const pollsBefore = job.statusPolls;
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect.poll(() => job.statusPolls, STAGE_WAIT).toBeGreaterThan(pollsBefore);
    // The held poll has not answered: the persisted question is not shown.
    await expect(main(page).getByText('Resuming your last question…')).toBeVisible();
    await expect(thread(page).locator('.genie__msg--user')).toHaveCount(0);
    release();
    await expect(thread(page).locator('.genie__msg--user')).toHaveText([GENIE_QUESTION], STAGE_WAIT);

    job.next();
    job.next();
    job.next();
    await expect(thread(page).locator('.genie-answer')).toHaveCount(1, STAGE_WAIT);
    await expect(thread(page).locator('.genie__msg--user')).toHaveText([GENIE_QUESTION]);
    expect(job.submits).toBe(1);
    expect(job.completes).toBe(1);
    expect(await inFlightRecord(page)).toBeNull();
  });

  test('(d) a reload while the complete is held sends exactly one more complete with the same ids: the same job, one answer', async ({ app, page, mockApi }) => {
    const job = registerGenieJob(mockApi, { holdComplete: true, steps: [{ stage: 'queued' }, 'succeeded'] });
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    await expect.poll(() => job.completes, STAGE_WAIT).toBe(1);
    expect(await inFlightRecord(page)).toMatchObject({ phase: 'completing', asyncComplete: true });
    expect(await inFlightRecord(page)).not.toHaveProperty('jobId');

    await page.reload({ waitUntil: 'domcontentloaded' });
    job.releaseComplete();
    await expect.poll(() => job.completes, STAGE_WAIT).toBe(2);
    expect(job.completeBodies[1]).toEqual(job.completeBodies[0]);
    job.next();
    await expect(thread(page).locator('.genie-answer')).toHaveCount(1, STAGE_WAIT);
    await page.clock.runFor(POLL_MS * 3);
    await app.settle();

    expect(job.completes, 'no third complete').toBe(2);
    expect(job.submits).toBe(1);
    expect(job.statusBodies.every((body) => (body as { job_id?: string }).job_id === GENIE_JOB_ID)).toBe(true);
    await expect(thread(page).locator('.genie-answer')).toHaveCount(1);
  });

  test('(e) an actor change removes the record and stops the polls; a reload resumes nothing', async ({ app, page, mockApi }) => {
    let actor = 'fixture-actor-a';
    mockApi.register('GET', '/api/health', () => json<HealthPayload>({ ...HEALTH_OK, actor_cache_key: actor }));
    const job = registerGenieJob(mockApi);
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    await expect(cardLabel(page)).toHaveText('Queued for governed completion', STAGE_WAIT);
    expect(await inFlightRecord(page)).not.toBeNull();

    actor = 'fixture-actor-b';
    const healthCalls = () => mockApi.calls.filter((call) => /\/api(\/v1)?\/health$/.test(call.path)).length;
    const healthBefore = healthCalls();
    await page.clock.runFor(8_000);
    await expect.poll(healthCalls, STAGE_WAIT).toBeGreaterThan(healthBefore);
    await expect.poll(() => inFlightRecord(page)).toBeNull();
    await expect(thread(page).locator('.genie-progress')).toHaveCount(0);
    await expect(main(page).locator('.genie__msg--user')).toHaveCount(0);

    const polls = job.statusPolls;
    await page.clock.runFor(POLL_MS * 4);
    await app.settle();
    expect(job.statusPolls, 'no poll after the actor change').toBe(polls);

    await page.reload({ waitUntil: 'domcontentloaded' });
    await app.settle();
    await page.clock.runFor(POLL_MS * 4);
    await app.settle();
    expect(job.statusPolls, 'a reload resumes nothing').toBe(polls);
    expect(job.submits).toBe(1);
    expect(job.completes).toBe(1);
  });

  test('(h) a 403 on a resumed poll fails closed silently: no question, no note, no further request', async ({ app, page, mockApi }) => {
    const job = registerGenieJob(mockApi);
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    job.next();
    await expect(cardLabel(page)).toHaveText('Verifying the answer against its rows', STAGE_WAIT);

    mockApi.degrade('/api/genie/message/status', {
      status: 403,
      method: 'POST',
      body: { detail: 'Genie progress token does not authorize this message' },
    });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect.poll(() => inFlightRecord(page), STAGE_WAIT).toBeNull();
    await app.settle();
    const calls = mockApi.calls.length;
    await page.clock.runFor(POLL_MS * 4);
    await app.settle();

    // Nothing of the turn renders: no question, no answer, no note, no card.
    // (The route's starter chips may still offer the same sample question.)
    await expect(thread(page).locator('.genie__msg--user')).toHaveCount(0);
    await expect(thread(page).locator('.genie__msg--ai')).toHaveCount(0);
    await expect(thread(page).locator('.genie__msg--stopped')).toHaveCount(0);
    await expect(thread(page).locator('.genie-progress')).toHaveCount(0);
    expect(mockApi.calls.slice(calls).filter((call) => call.path.includes('/genie/message/'))).toEqual([]);
    expect(job.submits).toBe(1);
    expect(job.completes).toBe(1);
  });
});

test.describe('outcomes', () => {
  test('(f) a legacy 200 (no completion_jobs) never polls a job status and renders the answer', async ({ app, page, mockApi }) => {
    const turn = registerGenieTurn(mockApi, { holdProgress: false });
    await app.gotoRoute('/ask-genie');
    // Nothing is held, so the card may come and go before a check could see it.
    await askOnRoute(page, { waitForCard: false });

    await expect(thread(page).locator('.genie-answer')).toHaveCount(1, STAGE_WAIT);
    await expect(thread(page)).toContainText(ANSWER_TEXT);
    expect(turn.completes).toBe(1);
    expect(mockApi.calls.some((call) => call.path.endsWith('/genie/message/status'))).toBe(false);
  });

  test('(g) an expired job shows the server hint and sends nothing more', async ({ app, page, mockApi }) => {
    const job = registerGenieJob(mockApi, { steps: [{ stage: 'queued' }, 'expired'] });
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    job.next();

    await expect(thread(page)).toContainText(GENIE_JOB_EXPIRED_HINT, STAGE_WAIT);
    await expect(thread(page).locator('.genie-progress')).toHaveCount(0);
    const polls = job.statusPolls;
    await page.clock.runFor(POLL_MS * 4);
    await app.settle();
    expect(job.statusPolls).toBe(polls);
    expect(job.completes).toBe(1);
    expect(await inFlightRecord(page)).toBeNull();
  });
});

test.describe('axe', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`(i) ${theme}: mid-job /ask-genie and the panel have no WCAG violations`, async ({ app, page, mockApi }) => {
      const job = registerGenieJob(mockApi, {
        deep: true,
        steps: [{ stage: 'queued' }, { stage: 'researching', parts: [3, 7] }],
      });
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie');
      await askOnRoute(page);
      job.next();
      await expect(cardLabel(page)).toContainText('3 of 7 sub-analyses finished', STAGE_WAIT);
      await expectAxeClean(page, { key: { route: 'ask-genie', state: 'genie-job' }, theme, known: {} });

      const dialog = await app.openGenie();
      await expect(dialog.locator('.genie-progress__label')).toContainText('3 of 7 sub-analyses finished');
      await expectAxeClean(page, { key: { route: 'ask-genie', state: 'genie-job-panel' }, theme, known: {} });
    });
  }
});
