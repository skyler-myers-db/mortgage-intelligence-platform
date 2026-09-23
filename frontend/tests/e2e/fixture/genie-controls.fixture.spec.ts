/**
 * Genie conversational controls, page-context entry points and answer-data
 * honesty (audit 2026-09-21 wave 1: `genie-03`, `genie-04`, `genie-06`,
 * `shell-07`), proven at the rendered layer against the production build.
 *
 * The default Genie fixture registers no answer turn, so each test registers
 * the submit / progress handlers it needs with `mockApi.register()` and owns
 * the timing (a Stop mid-turn needs a turn that never finishes).
 */
import type { Locator, Page } from '@playwright/test';
import type { GenieLiveProgress, GenieResult, GenieSubmitResult } from '../../../src/lib/apiTypes';
import type { AppDriver } from './app';
import { json, type MockApi } from './mockApi';
import { expect, test } from './test';

const ADDRESSABLE_KPI_PROMPT =
  'What is the addressable market size — how many eligible borrowers across the current Cotality data coverage?';
const SQL = 'SELECT state, count(*) AS borrowers FROM mip.gold.borrower_360 GROUP BY state ORDER BY borrowers DESC';
/** The client's progress poll cadence (lib/genieAsk.ts PROGRESS_POLL_MS). */
const PROGRESS_POLL_MS = 1_500;

const LIVE_SUBMIT: GenieSubmitResult = {
  completed: false,
  conversation_id: 'fixture-conversation-0001',
  message_id: 'fixture-message-0001',
  progress_token: 'fixture-progress-token',
  question_hash: 'fixture-hash',
  deep: false,
};

const IN_PROGRESS: GenieLiveProgress = {
  status: 'EXECUTING_QUERY',
  stage: 'executing',
  stage_label: 'Genie is running the generated query',
  terminal: false,
  failed: false,
  reasoning_trace: [],
  sql_preview: null,
  error_hint: null,
};

/** A governed answer in the submit route's wire type. */
function answer(overrides: Partial<GenieResult> = {}): GenieResult {
  return {
    answer: 'Illinois leads with 3,080 in-the-money borrowers.',
    source: 'genie',
    trusted_assets: ['mip.gold.borrower_360'],
    conversation_id: 'fixture-conversation-0001',
    message_id: 'fixture-message-0001',
    genie_status: 'COMPLETED',
    question_hash: 'fixture-hash',
    row_count: 8,
    follow_up_questions: [],
    proof: {
      sql_query: SQL,
      source_assets: ['mip.gold.borrower_360'],
      row_count: 8,
      trusted: true,
      filters: [],
      known_data_gaps: [],
    },
    ...overrides,
  };
}

/** A submit that resolves inline with `payload` (the deterministic contract). */
function completedSubmit(payload: GenieResult): GenieSubmitResult {
  return {
    completed: true,
    conversation_id: payload.conversation_id ?? null,
    message_id: payload.message_id ?? null,
    progress_token: null,
    question_hash: payload.question_hash ?? null,
    response: payload,
  };
}

const genieCalls = (mockApi: MockApi) => mockApi.calls.filter((call) => call.path.startsWith('/api/genie/message'));

test.describe('Genie page context (genie-04, shell-07)', () => {
  test("a KPI's Ask Genie entry opens the panel with the reviewed prompt prefilled, not submitted", async ({ app, mockApi, page }) => {
    await app.gotoRoute('/');
    const entry = page.getByRole('button', { name: 'Ask Genie about this KPI: Addressable population' });
    await expect(entry).toBeVisible();
    await entry.click();

    const dialog = page.getByRole('dialog', { name: 'Genie chat' });
    await expect(dialog).toBeVisible();
    const composer = dialog.getByRole('textbox', { name: 'Ask Genie' });
    await expect(composer).toHaveValue(ADDRESSABLE_KPI_PROMPT);
    await expect(composer).toBeFocused();
    await app.settle();

    // A prefill is never a submit: no question bubble, no answer-path call.
    await expect(dialog.locator('.genie__msg--user')).toHaveCount(0);
    expect(genieCalls(mockApi), 'the guarded ask path was not called by the prefill').toEqual([]);
  });

  test('the palette offers "Ask Genie: <text>" and prefills the panel without submitting', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/');
    const palette = await app.openCommandPalette();
    await palette.getByRole('combobox').fill('refi');
    // The mortgage synonym resolves to the page that owns refi segments...
    await expect(palette.getByRole('option').first()).toContainText('Segment Intelligence');
    // ...and the typed text can go to Genie as a prefill.
    await palette.getByRole('option', { name: /^Ask Genie: refi/ }).click();
    await expect(palette).toBeHidden();

    const dialog = page.getByRole('dialog', { name: 'Genie chat' });
    await expect(dialog.getByRole('textbox', { name: 'Ask Genie' })).toHaveValue('refi');
    await app.settle();
    await expect(dialog.locator('.genie__msg--user')).toHaveCount(0);
    expect(genieCalls(mockApi)).toEqual([]);
  });

  test('the empty panel shows the starters curated for the route', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    const dialog = await app.openGenie();
    const chips = dialog.locator('.genie-chat__sample');
    await expect(chips.first()).toBeVisible();
    await expect(chips.first()).toHaveText(
      'Show the top 10 borrowers by lead score across the current Cotality data coverage.',
    );
    // Not the server's identical global list.
    await expect(dialog).not.toContainText('Which states have the most prime refi candidates?');
    await expect(page.locator('.genie__msg--user')).toHaveCount(0);
  });
});

test.describe('Genie conversational controls (genie-03)', () => {
  test('Stop mid-turn marks the bubble Stopped, restores the composer, and stops polling', async ({ app, mockApi, page }) => {
    let progressPolls = 0;
    mockApi.register<GenieSubmitResult>('POST', '/api/genie/message/submit', () => json(LIVE_SUBMIT));
    mockApi.register<GenieLiveProgress>('POST', '/api/genie/message/progress', () => {
      progressPolls += 1;
      return json(IN_PROGRESS);
    });

    await app.gotoRoute('/');
    const dialog = await app.openGenie();
    const composer = dialog.getByRole('textbox', { name: 'Ask Genie' });
    await composer.fill('How many borrowers are in the money?');
    await dialog.getByRole('button', { name: 'Ask', exact: true }).click();

    await expect(dialog.locator('.genie-progress')).toBeVisible();
    await expect(dialog.getByRole('button', { name: 'Start a new Genie thread' })).toBeDisabled();
    await expect.poll(() => progressPolls).toBeGreaterThan(0);

    await dialog.getByRole('button', { name: 'Stop this Genie turn' }).click();

    await expect(dialog.locator('.genie__msg--stopped')).toBeVisible();
    await expect(dialog.locator('.genie__msg--stopped')).toContainText('Stopped');
    await expect(composer).toHaveValue('How many borrowers are in the money?');
    await expect(dialog.locator('.genie-progress')).toHaveCount(0);
    await expect(dialog.getByRole('button', { name: 'Start a new Genie thread' })).toBeEnabled();
    await expect(dialog.getByRole('button', { name: 'Genie conversation history' })).toBeEnabled();

    // No further polls: fast-forward the page clock well past several poll
    // intervals (deterministic, no wall-clock sleep). A turn that was not
    // aborted would poll again on every interval.
    const pollsAtStop = progressPolls;
    await page.clock.runFor(PROGRESS_POLL_MS * 4);
    await app.settle();
    expect(progressPolls, 'no progress poll after Stop').toBe(pollsAtStop);
    expect(mockApi.calls.filter((call) => call.path === '/api/genie/message/complete')).toEqual([]);
    await expect(dialog.locator('.genie__msg--stopped')).toHaveCount(1);
  });

  test('Regenerate re-asks the same question as a second submit and keeps the first answer', async ({ app, mockApi, page }) => {
    const questions: string[] = [];
    mockApi.register<GenieSubmitResult>('POST', '/api/genie/message/submit', ({ body }) => {
      const question = String((body as { question?: string } | null)?.question ?? '');
      questions.push(question);
      return json(
        completedSubmit(
          answer({
            answer: questions.length === 1 ? 'First answer: 3,080 borrowers.' : 'Second answer: 3,081 borrowers.',
            message_id: `fixture-message-000${questions.length}`,
          }),
        ),
      );
    });

    await app.gotoRoute('/');
    const dialog = await app.openGenie();
    await dialog.getByRole('textbox', { name: 'Ask Genie' }).fill('How many borrowers are in the money?');
    await dialog.getByRole('button', { name: 'Ask', exact: true }).click();
    await expect(dialog).toContainText('First answer: 3,080 borrowers.');

    const regenerate = dialog.getByRole('button', { name: 'Regenerate answer' });
    await expect(regenerate).toHaveAttribute('title', /cannot rewrite its history/);
    await regenerate.click();
    await expect(dialog).toContainText('Second answer: 3,081 borrowers.');
    await expect(dialog).toContainText('First answer: 3,080 borrowers.');
    expect(questions).toEqual(['How many borrowers are in the money?', 'How many borrowers are in the money?']);
    await expect(page.locator('.genie__msg--user', { hasText: 'How many borrowers are in the money?' })).toHaveCount(2);
  });
});

test.describe('Genie answer data honesty (genie-06)', () => {
  test('columns the compact table hides are named under it', async ({ app, mockApi, page }) => {
    const rows = ['IL', 'TX', 'CA'].map((state, i) => ({
      state,
      borrowers: 3080 - i * 500,
      avg_rate_spread_bps: 112 - i,
      avg_equity_pct: 46 + i,
      contactable_borrowers: 897 - i * 100,
      top_segment: 'itm',
      refreshed_at: '2026-07-14T12:00:00Z',
    }));
    const dialog = await askInline(app, mockApi, page, answer({ table_rows: rows, row_count: rows.length }));
    await expect(dialog.locator('.genie-answer__table th')).toHaveCount(4);
    await expect(dialog.locator('.genie-answer__hidden-columns')).toHaveText(
      '3 columns not shown: Contactable Borrowers, Top Cohort, Refreshed At',
    );
  });

  test('Copy SQL writes the governed SQL to the clipboard and confirms', async ({ app, context, mockApi, page }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    const dialog = await askInline(app, mockApi, page, answer());
    await dialog.getByRole('button', { name: 'Copy SQL' }).click();
    await expect(dialog.locator('.genie-answer__toolbar-status')).toHaveText('SQL copied');
    const clipboard = await page.evaluate(() => navigator.clipboard.readText());
    expect(clipboard).toBe(SQL);
  });

  test('Copy answer writes the plain-text answer with every row', async ({ app, context, mockApi, page }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    const rows = [
      { state: 'IL', borrowers: 3080 },
      { state: 'TX', borrowers: 2570 },
    ];
    const dialog = await askInline(app, mockApi, page, answer({ table_rows: rows, row_count: 2 }));
    await dialog.getByRole('button', { name: 'Copy answer' }).click();
    await expect(dialog.locator('.genie-answer__toolbar-status')).toHaveText('Answer copied');
    const clipboard = await page.evaluate(() => navigator.clipboard.readText());
    // Exactly the wording the bubble displays.
    const shown = (await dialog.locator('.genie__msg--ai .genie-md-p').first().textContent())?.trim() ?? '';
    expect(shown).toBe('Illinois leads with 3,080 borrowers passing the refinance-economics screen.');
    expect(clipboard).toContain(shown);
    expect(clipboard).toContain('State\tBorrowers');
    expect(clipboard).toContain('IL\t3,080');
    expect(clipboard).toContain('TX\t2,570');
  });
});

/** Open the panel on Home and ask one question that resolves inline with `payload`. */
async function askInline(app: AppDriver, mockApi: MockApi, page: Page, payload: GenieResult): Promise<Locator> {
  mockApi.register<GenieSubmitResult>('POST', '/api/genie/message/submit', () => json(completedSubmit(payload)));
  await app.gotoRoute('/');
  const dialog = await app.openGenie();
  await dialog
    .getByRole('textbox', { name: 'Ask Genie' })
    .fill('Break down in-the-money borrowers by current coverage state; which state leads?');
  await dialog.getByRole('button', { name: 'Ask', exact: true }).click();
  // The answer bubble has landed once its per-turn Regenerate is offered.
  await expect(dialog.getByRole('button', { name: 'Regenerate answer' })).toBeVisible();
  return page.getByRole('dialog', { name: 'Genie chat' });
}
