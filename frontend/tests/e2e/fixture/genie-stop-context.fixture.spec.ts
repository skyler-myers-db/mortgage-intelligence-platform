/**
 * Rendered-layer proofs for W5c w5-genie-stop-context (1440x900, production
 * build):
 *
 *  (a) genie-03 pre-cancel: a Stop while Genie is still answering, and a Stop
 *      while the complete is held before its 202, each send ONE turn-keyed
 *      cancel (job_id null, never the question); the note confirms and no
 *      status poll follows the Stop;
 *  (b) flow-04: a governed action's receipt links its audit event id for an
 *      administrator and an auditor and keeps it mono text for a plain
 *      session, in the floating panel and on /ask-genie, in both themes,
 *      axe-clean;
 *  (c) D-platform-process-d2: the static data-rum-target attributes on the
 *      panel and both composers;
 *  (d) genie-01 fold: the Partial research reveal renders from the in-flight
 *      turn, and a status of another job never leaks into it.
 *
 * Sessions and the action result are registered per test through mockApi.
 */
import type { Locator, Page } from '@playwright/test';
import type { GenieActionResult, GenieActionSuggestion, GenieAnswer, SessionResponse } from '../../../src/types';
import type { GenieLiveProgress } from '../../../src/lib/apiTypes';
import { expectAxeClean, KNOWN_VIOLATIONS } from './axe';
import { GENIE_CONVERSATION_ID } from './data/genie';
import {
  GENIE_JOB_ID,
  genieJobStatusFixture,
  genieJobSubmitFixture,
  genieRevealSectionsFixture,
  registerGenieJob,
  type GenieJobStatusBody,
} from './data/genieJobs';
import { GENIE_MESSAGE_ID, GENIE_PROGRESS_TOKEN, GENIE_QUESTION, genieAnswerFixture, registerGenieTurn } from './data/genieTurn';
import { SESSION } from './data/shell';
import { json, type FixtureRequest, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const POLL_MS = 1_500;
const WAIT = { timeout: 20_000 };
const UNCONFIRMED = 'Genie may still finish this turn on the server';
const CONFIRMED =
  'Stopped. This answer was not recorded and will not appear later. Genie may keep the question as context for the next turn in this thread.';
const EVENT_ID = '0f1e2d3c-4b5a-4968-8776-655443322110';
const SAVED = 'Saved 12 borrowers to the reviewed Lead Queue handoff.';
const SAVE: GenieActionSuggestion = {
  id: 'save-reviewed',
  label: 'Save reviewed cohort',
  action_type: 'save_borrowers',
  description: 'Create the reviewed Lead Queue handoff.',
  requires_confirmation: true,
  route: null,
  borrower_ids: [],
  criteria: {},
} as GenieActionSuggestion;
const AUDITOR: SessionResponse = { ...SESSION, can_access_admin: false, can_approve: false, can_read_audit: true };
const PLAIN: SessionResponse = { ...SESSION, can_access_admin: false, can_approve: true, can_read_audit: false };
const SESSIONS = [
  { who: 'admin', session: SESSION, linked: true },
  { who: 'auditor', session: AUDITOR, linked: true },
  { who: 'plain', session: PLAIN, linked: false },
] as const;
const STILL_ANSWERING: GenieLiveProgress = {
  status: 'EXECUTING_QUERY',
  stage: 'executing',
  stage_label: 'Running the governed query',
  terminal: false,
  failed: false,
  reasoning_trace: [],
  sql_preview: null,
  error_hint: null,
};

function main(page: Page): Locator {
  return page.locator('#main-content');
}

async function askOnRoute(page: Page): Promise<void> {
  await main(page).getByRole('textbox', { name: 'Ask Genie — question' }).fill(GENIE_QUESTION);
  await main(page).getByRole('button', { name: 'Ask Genie', exact: true }).click();
}

async function askInPanel(dialog: Locator): Promise<void> {
  await dialog.getByRole('textbox', { name: 'Ask Genie' }).fill(GENIE_QUESTION);
  await dialog.getByRole('button', { name: 'Ask', exact: true }).click();
}

function expectTurnKeyedCancel(bodies: readonly unknown[]): void {
  expect(bodies).toHaveLength(1);
  const [body] = bodies as Array<Record<string, unknown>>;
  expect(body).toEqual({
    conversation_id: GENIE_CONVERSATION_ID,
    message_id: GENIE_MESSAGE_ID,
    progress_token: GENIE_PROGRESS_TOKEN,
    job_id: null,
    question_hash: genieJobSubmitFixture().question_hash,
  });
  expect(JSON.stringify(body)).not.toContain(GENIE_QUESTION);
}

test.describe('(a) a Stop before the 202 sends ONE turn-keyed cancel', () => {
  test('while Genie is still answering (polling)', async ({ app, page, mockApi }) => {
    const job = registerGenieJob(mockApi, { cancel: { outcome: 'cancelled', jobStatus: 'cancelled' } });
    mockApi.register<GenieLiveProgress>('POST', '/api/genie/message/progress', () => json(STILL_ANSWERING));
    await app.gotoRoute('/ask-genie');
    await askOnRoute(page);
    await expect(main(page).locator('.genie-progress__label')).toContainText('Running the governed query', WAIT);

    await main(page).getByRole('button', { name: 'Stop this Genie turn' }).click();
    const note = main(page).locator('.genie__msg--stopped');
    await expect(note).toContainText(CONFIRMED, WAIT);
    await page.clock.runFor(POLL_MS * 4);
    await app.settle();

    expectTurnKeyedCancel(job.cancelBodies);
    expect(job.completes).toBe(0);
    expect(job.statusPolls, 'no /message/status poll after the Stop').toBe(0);
    await expect(note).not.toContainText(UNCONFIRMED);
  });

  test('while the complete is held before its 202', async ({ app, page, mockApi }) => {
    const job = registerGenieJob(mockApi, { holdComplete: true, cancel: { outcome: 'cancelled', jobStatus: 'cancelled' } });
    await app.gotoRoute('/');
    const dialog = await app.openGenie();
    await askInPanel(dialog);
    await expect.poll(() => job.completes, WAIT).toBe(1);

    await dialog.getByRole('button', { name: 'Stop this Genie turn' }).click();
    const note = dialog.locator('.genie__msg--stopped');
    await expect(note).toContainText(CONFIRMED, WAIT);
    job.releaseComplete();
    await page.clock.runFor(POLL_MS * 4);
    await app.settle();

    expectTurnKeyedCancel(job.cancelBodies);
    expect(job.statusPolls, 'the stopped turn never polls the job').toBe(0);
    expect(job.completes).toBe(1);
    await expect(dialog.locator('.genie-answer'), 'the held complete never lands an answer').toHaveCount(0);
    await expect(note).toHaveCount(1);
  });
});

function actionAnswer(): GenieAnswer {
  return genieAnswerFixture({ actions: [SAVE] } as Partial<GenieAnswer>);
}

function registerAction(mockApi: MockApi): { bodies: unknown[] } {
  const bodies: unknown[] = [];
  mockApi.register<GenieActionResult>('POST', '/api/genie/actions', (request: FixtureRequest) => {
    bodies.push(request.body);
    return json({ ok: true, action_type: 'save_borrowers', audit_event_id: EVENT_ID, saved_count: 12, route: null, message: SAVED });
  });
  return { bodies };
}

async function runSave(scope: Locator): Promise<void> {
  await scope.getByRole('button', { name: `Run ${SAVE.label}` }).last().click();
  await scope.getByRole('button', { name: `Confirm ${SAVE.label}` }).last().click();
}

async function expectReceipt(receipt: Locator, linked: boolean): Promise<void> {
  await expect(receipt).toHaveText(`${SAVED} Audit event ${EVENT_ID}.`, WAIT);
  if (linked) {
    const link = receipt.getByRole('link', { name: EVENT_ID });
    await expect(link).toHaveAttribute('href', `/audit-ledger?audit_event_id=${EVENT_ID}#audit`);
    await expect(link).toHaveClass('mono');
  } else {
    await expect(receipt.getByRole('link')).toHaveCount(0);
    await expect(receipt.locator('span.mono')).toHaveText(EVENT_ID);
  }
}

test.describe('(b) the governed action receipt (flow-04)', () => {
  for (const theme of FIXTURE_THEMES) {
    for (const { who, session, linked } of SESSIONS) {
      test(`${theme} / ${who}: /ask-genie callout`, async ({ app, page, mockApi }) => {
        mockApi.register<SessionResponse>('GET', '/api/session', () => json(session));
        registerGenieTurn(mockApi, { holdProgress: false, answer: actionAnswer() });
        const action = registerAction(mockApi);
        await app.setTheme(theme);
        await app.gotoRoute('/ask-genie');
        await askOnRoute(page);
        await expect(main(page).getByRole('button', { name: `Run ${SAVE.label}` })).toBeVisible(WAIT);

        await runSave(main(page));

        await expectReceipt(main(page).locator('.status-callout'), linked);
        expect(JSON.stringify(action.bodies)).not.toContain(EVENT_ID);
        await expectAxeClean(page, {
          key: { route: 'ask-genie', state: `genie-action-receipt-${who}` },
          theme,
          known: KNOWN_VIOLATIONS,
          include: '#main-content',
        });
      });

      test(`${theme} / ${who}: floating panel bubble`, async ({ app, mockApi }) => {
        mockApi.register<SessionResponse>('GET', '/api/session', () => json(session));
        registerGenieTurn(mockApi, { holdProgress: false, answer: actionAnswer() });
        registerAction(mockApi);
        await app.setTheme(theme);
        await app.gotoRoute('/');
        const dialog = await app.openGenie();
        await askInPanel(dialog);
        await expect(dialog.getByRole('button', { name: `Run ${SAVE.label}` })).toBeVisible(WAIT);

        await runSave(dialog);

        const bubble = dialog.locator('.genie__msg--ai').last();
        const note = bubble.locator('.genie-answer__api-source');
        await expect(note).toContainText('Governed action result', WAIT);
        const receipt = bubble.locator('p.genie-md-p', { hasText: 'Audit event' });
        await expectReceipt(receipt, linked);
        // The receipt sits where an older bubble's in-answer text did: one
        // --sp-2 under the source note and above the follow-up chips.
        const followUps = bubble.locator('.genie-answer__followups');
        await expect(followUps).toBeVisible();
        const [noteBox, receiptBox, followUpsBox] = await Promise.all([
          note.boundingBox(),
          receipt.boundingBox(),
          followUps.boundingBox(),
        ]);
        if (!noteBox || !receiptBox || !followUpsBox) throw new Error('the note, receipt and chips are laid out');
        const sp2 = await receipt.evaluate((p) => parseFloat(getComputedStyle(p).getPropertyValue('--sp-2')));
        const gap = receiptBox.y - (noteBox.y + noteBox.height);
        expect(sp2).toBeGreaterThan(0);
        expect(Math.abs(gap - sp2), `gap ${gap}px, --sp-2 ${sp2}px`).toBeLessThanOrEqual(1);
        expect(receiptBox.y + receiptBox.height).toBeLessThanOrEqual(followUpsBox.y);
        await expectAxeClean(dialog.page(), {
          key: { route: 'home', state: `genie-panel-action-receipt-${who}` },
          theme,
          known: KNOWN_VIOLATIONS,
          include: '.genie',
        });
      });
    }
  }
});

test('(c) the Genie panel and both composers carry their static data-rum-target', async ({ app, page }) => {
  await app.gotoRoute('/ask-genie');
  await expect(main(page).locator('form.genie-composer')).toHaveAttribute('data-rum-target', 'genie-composer');

  const dialog = await app.openGenie();
  await expect(dialog).toHaveAttribute('data-rum-target', 'genie-panel');
  await expect(dialog.locator('form.genie__input')).toHaveAttribute('data-rum-target', 'genie-composer');
});

test('(d) the Partial research reveal renders from the turn, and another job\'s status never leaks into it', async ({ app, page, mockApi }) => {
  const sections = genieRevealSectionsFixture(3);
  const leaked = genieRevealSectionsFixture(4).map((section) => ({ ...section, title: `Leaked ${section.title}` }));
  registerGenieJob(mockApi, { deep: true, steps: [{ stage: 'queued' }, { stage: 'researching' }] });
  const ours = (knownRev: number | null): GenieJobStatusBody =>
    genieJobStatusFixture({ stage: 'researching', parts: [4, 7], reveal: { verified: 3, rev: 2, sections } }, undefined, undefined, knownRev);
  const theirs: GenieJobStatusBody = {
    ...genieJobStatusFixture({ stage: 'synthesizing', parts: [7, 7], reveal: { verified: 4, rev: 9, sections: leaked } }),
    job_id: '00000000-0000-4000-8000-0000000000ff',
  };
  let polls = 0;
  mockApi.register<GenieJobStatusBody>('POST', '/api/genie/message/status', (request: FixtureRequest) => {
    polls += 1;
    const sent = (request.body as { sections_rev?: unknown } | null)?.sections_rev;
    return json(polls === 2 ? theirs : ours(typeof sent === 'number' ? sent : null));
  });
  await app.gotoRoute('/ask-genie');
  await askOnRoute(page);
  const group = main(page).getByRole('group', { name: 'Partial research' });
  await expect(group.locator('h3')).toHaveText(sections.map((s) => s.title), WAIT);

  await expect.poll(() => polls, WAIT).toBeGreaterThanOrEqual(3);
  await app.settle();

  await expect(group.locator('h3')).toHaveText(sections.map((s) => s.title));
  await expect(main(page)).not.toContainText('Leaked');
  await expect(main(page).locator('.genie-progress__label')).toContainText('Running governed sub-analyses');
  expect(GENIE_JOB_ID).not.toBe(theirs.job_id);
});
