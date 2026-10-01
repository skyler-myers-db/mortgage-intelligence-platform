/**
 * Rendered-layer proofs for the wave-5 Growth Agent trust lane (audit
 * 2026-09-21 `critic-01` residual, `genie-09` part 1, `flow-08` slice 2):
 *
 * 1. a 409 moves focus to Compose again; the recompose says what it is doing
 *    and lands on a step diff of what changed, with focus on its summary;
 * 2. while a reviewed plan runs, Run keeps focus (aria-disabled) and every
 *    input whose edit would drop the answer is locked; focus then moves to
 *    the Execution trace;
 * 3. the command-bar hint sits under the buttons, and the bar stacks at 768px
 *    (both themes);
 * 4. Save as watchlist saves exactly the run shown, with no second plan, and
 *    its confirmation takes focus;
 * 5. a saved watchlist says whether scheduled runs are on, per server state;
 * 7. the new surfaces are axe-clean in both themes.
 * (Case 6, the run history list, moved to W5b with genie-09 part 3.)
 *
 * 1440x900 (the harness default). The data modules record every request
 * body the browser posts, so the proofs read the wire, not the component.
 */
import type { Page } from '@playwright/test';
import { expectAxeClean } from './axe';
import {
  AGENT_RUN_PATTERN,
  COMPOSE_PATTERN,
  DIGEST_RECOMPOSED,
  EXECUTE_PATTERN,
  HOME_PATTERN,
  RECOMPOSED_PLAN,
  composeReply,
  executedReply,
  homeWithScheduler,
  plannedRun,
  registerGrowthAgentTrust,
  registerRunWatchlistSave,
  saveReply,
} from './data/growthAgentTrust';
import type { MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const CARD = '[aria-label="Composed Growth Agent plan"]';
const RUN_CARD = '[aria-label="Latest Growth Agent run"]';
const EXPECTED_409 = /status of 409 \(Conflict\).*\/growth-agent\/(agent\/plan\/execute|runs\/.+\/monitors)/;

function card(page: Page) {
  return page.locator(CARD);
}

function commandBar(page: Page) {
  return page.getByRole('region', { name: 'Mortgage Growth Agent command center' });
}

function posts(mockApi: MockApi, pattern: string): number {
  return mockApi.calls.filter((call) => call.method === 'POST' && call.path === pattern).length;
}

/** A gate a handler awaits until the test opens it. */
function gate() {
  let open: () => void = () => undefined;
  const opened = new Promise<void>((resolve) => {
    open = resolve;
  });
  return { opened, open: () => open() };
}

async function compose(page: Page) {
  await commandBar(page).getByRole('button', { name: 'Compose plan', exact: true }).click();
  await expect(card(page)).toBeVisible();
}

/** [objective, state scope, review interval, segment chips (all), segment logic] disabled. */
async function lockedControls(page: Page): Promise<boolean[]> {
  return page.evaluate(() => {
    const query = (selector: string) => document.querySelector<HTMLInputElement>(selector);
    const chips = Array.from(document.querySelectorAll<HTMLButtonElement>('[aria-label="Custom workflow segments"] button'));
    return [
      Boolean(query('textarea[aria-label="Mortgage Growth Agent prompt"]')?.disabled),
      Boolean(query('input[aria-label="Growth Agent state scope"]')?.disabled),
      Boolean(query('select[aria-label="Growth Agent review interval"]')?.disabled),
      chips.length > 0 && chips.every((chip) => chip.disabled),
      Boolean(query('select[aria-label="Custom Growth Agent segment logic"]')?.disabled),
    ];
  });
}

test.describe('compose, review, run: trust in the plan that runs', () => {
  test('a 409 focuses Compose again; the recompose says so and diffs against the reviewed plan', async ({ app, page, mockApi, hygiene }) => {
    hygiene.allow('console.error', EXPECTED_409);
    const recorder = registerGrowthAgentTrust(mockApi, { replies: [composeReply()], execute: 'conflict' });
    const held = gate();
    let composes = 0;
    mockApi.register('POST', COMPOSE_PATTERN, async () => {
      composes += 1;
      if (composes === 1) return { body: composeReply() };
      await held.opened;
      return { body: composeReply(RECOMPOSED_PLAN, DIGEST_RECOMPOSED) };
    });
    await app.gotoRoute('/ask-genie?tab=workflows');
    await compose(page);
    await card(page).getByRole('button', { name: 'Run this plan' }).click();

    const composeAgain = card(page).getByRole('button', { name: 'Compose again' });
    await expect(composeAgain).toBeFocused();
    expect(recorder.executeBodies).toHaveLength(1);

    await composeAgain.click();
    const pending = page.getByRole('status', { name: 'Growth Agent run in progress' });
    await expect(pending).toContainText('Composing the current plan for your objective');
    await expect(pending).toContainText(
      'Composing the plan again. The new plan is compared step by step with the plan you reviewed.',
    );
    held.open();

    const changes = card(page).getByRole('region', { name: 'Changes since the plan you reviewed' });
    const summary = changes.locator('.growth-agent-diff__summary');
    await expect(summary).toHaveText('2 kept · 1 changed · 1 added');
    await expect(summary).toBeFocused();
    await expect(changes.locator('li .chip')).toHaveText(['Kept', 'Changed', 'Added', 'Kept']);
    await expect(changes.locator('li .growth-agent-step__title')).toHaveText([
      'fn_build_cohort',
      'fn_segment_counts',
      'fn_offer_compare',
      'fn_lead_queue_url',
    ]);
    await expect(changes.locator('li').nth(1)).toContainText('Was: ');
    await expect(changes.locator('li').nth(1)).toContainText(' · Now: ');
    expect(posts(mockApi, COMPOSE_PATTERN)).toBe(2);
  });

  test('a running plan keeps focus on Run and locks every input until the server answers', async ({ app, page, mockApi }) => {
    registerGrowthAgentTrust(mockApi, { replies: [composeReply()], execute: 'ran' });
    const held = gate();
    // Counted on arrival: mockApi.calls records a request only once it is answered.
    let executes = 0;
    mockApi.register('POST', EXECUTE_PATTERN, async (request) => {
      executes += 1;
      await held.opened;
      return { body: executedReply((request.body as { plan_digest: string }).plan_digest) };
    });
    await app.gotoRoute('/ask-genie?tab=workflows');
    await compose(page);
    const run = card(page).getByRole('button', { name: 'Run this plan' });
    await run.click();

    const running = card(page).getByRole('button', { name: 'Running…' });
    await expect(running).toHaveAttribute('aria-disabled', 'true');
    await expect(running).toBeFocused();
    await expect.poll(() => lockedControls(page)).toEqual([true, true, true, true, true]);
    // Enter on the focused, aria-disabled button is ignored: still one execute.
    await page.keyboard.press('Enter');
    await expect(running).toBeFocused();
    expect(executes).toBe(1);

    held.open();
    const trace = card(page).getByRole('region', { name: 'Execution trace' });
    await expect(trace).toBeFocused();
    await expect.poll(() => lockedControls(page)).toEqual([false, false, false, false, false]);
  });

  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: the command-bar hint sits under the buttons, and the bar stacks at 768px`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie?tab=workflows');
      const hint = commandBar(page).locator('.growth-agent-command__hint');
      const actions = commandBar(page).locator('.growth-agent-command__actions');
      const objective = page.getByLabel('Mortgage Growth Agent prompt');
      await expect(hint).toBeVisible();
      const [hintBox, actionsBox, objectiveBox] = await Promise.all([hint.boundingBox(), actions.boundingBox(), objective.boundingBox()]);
      if (!hintBox || !actionsBox || !objectiveBox) throw new Error('command bar not laid out');
      expect(hintBox.y).toBeGreaterThanOrEqual(actionsBox.y + actionsBox.height - 0.5);
      expect(hintBox.x).toBeGreaterThanOrEqual(objectiveBox.x + objectiveBox.width - 0.5);

      await page.setViewportSize({ width: 768, height: 900 });
      await expect(async () => {
        const [h, a, o] = await Promise.all([hint.boundingBox(), actions.boundingBox(), objective.boundingBox()]);
        if (!h || !a || !o) throw new Error('command bar not laid out');
        expect(a.y).toBeGreaterThanOrEqual(o.y + o.height - 0.5);
        expect(h.y).toBeGreaterThanOrEqual(a.y + a.height - 0.5);
      }).toPass();
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
      expect(overflow).toBeLessThanOrEqual(0);
    });
  }
});

test.describe('Save as watchlist and scheduled-run status', () => {
  test('Save as watchlist saves exactly the run shown, with no second plan', async ({ app, page, mockApi }) => {
    const recorder = registerRunWatchlistSave(mockApi, 'saved');
    await app.gotoRoute('/ask-genie?tab=workflows');
    await expect(commandBar(page).getByRole('button')).toHaveText(['Plan reviewed workflow', 'Compose plan']);
    await commandBar(page).getByRole('button', { name: 'Plan reviewed workflow' }).click();
    const runCard = page.locator(RUN_CARD);
    await expect(runCard).toBeVisible();
    expect(recorder.runBodies).toHaveLength(1);
    expect(recorder.runBodies[0].save_monitor).toBe(false);

    const save = runCard.getByRole('button', { name: 'Save as watchlist' });
    await expect(save).toHaveAccessibleDescription(/Nothing runs again and nothing is sent\./);
    await save.click();
    await expect(runCard.getByRole('status')).toHaveText(
      `Saved as watchlist “${saveReply().name}”. Find it under Saved monitors.`,
    );
    // The pressed button is replaced by the confirmation, which takes focus.
    await expect(runCard.getByRole('status')).toBeFocused();
    expect(recorder.saves).toHaveLength(1);
    expect(recorder.saves[0].runId).toBe(plannedRun().run_id);
    const { request_id: requestId, ...saveBody } = recorder.saves[0].body;
    expect(saveBody).toEqual({ tool_result_hash: plannedRun().tool_result_hash, cadence: 'daily' });
    expect(requestId).toBeTruthy();
    expect(posts(mockApi, AGENT_RUN_PATTERN)).toBe(1);
    expect(mockApi.calls.filter((call) => call.method === 'POST' && call.path.endsWith('/monitors'))).toHaveLength(1);

    await page.getByRole('tablist', { name: 'Ask Genie views' }).getByRole('tab', { name: 'Saved monitors', exact: true }).click();
    await expect(page.getByLabel('Saved Growth Agent watchlists')).toContainText(saveReply().name);
    expect(posts(mockApi, AGENT_RUN_PATTERN)).toBe(1);
  });

  for (const [state, text] of [
    ['active', 'scheduled runs on'],
    ['paused', 'scheduled runs off'],
    ['unavailable', 'scheduled-run status unavailable'],
  ] as const) {
    test(`a ${state} scheduler reads as "${text}" on the saved watchlist`, async ({ app, page, mockApi }) => {
      mockApi.register('GET', HOME_PATTERN, () => ({ body: homeWithScheduler(state, [saveReply()]) }));
      await app.gotoRoute('/ask-genie?tab=monitors');
      const list = page.getByLabel('Saved Growth Agent watchlists');
      await expect(list).toContainText(`Daily interval · ${text}`);
      await expect(page.getByRole('main')).not.toContainText(/scheduler/i);
    });
  }
});

test.describe('axe', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: the plan diff, the save status and the scheduled-run row have no WCAG violations`, async ({ app, page, mockApi, hygiene }) => {
      hygiene.allow('console.error', EXPECTED_409);
      registerGrowthAgentTrust(mockApi, {
        replies: [composeReply(), composeReply(RECOMPOSED_PLAN, DIGEST_RECOMPOSED)],
        execute: 'conflict',
      });
      registerRunWatchlistSave(mockApi, 'saved');
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie?tab=workflows');
      await compose(page);
      await card(page).getByRole('button', { name: 'Run this plan' }).click();
      await card(page).getByRole('button', { name: 'Compose again' }).click();
      await expect(card(page).locator('.growth-agent-diff__summary')).toHaveText('2 kept · 1 changed · 1 added');
      await expectAxeClean(page, { key: { route: 'ask-genie', state: 'plan-diff' }, theme, known: {}, include: CARD });

      await commandBar(page).getByRole('button', { name: 'Plan reviewed workflow' }).click();
      await page.locator(RUN_CARD).getByRole('button', { name: 'Save as watchlist' }).click();
      await expect(page.locator(RUN_CARD).getByRole('status')).toBeVisible();
      // The whole run card, its success CTA included (--success-fill, W5b).
      await expectAxeClean(page, {
        key: { route: 'ask-genie', state: 'run-saved' },
        theme,
        known: {},
        include: RUN_CARD,
      });

      await page.getByRole('tablist', { name: 'Ask Genie views' }).getByRole('tab', { name: 'Saved monitors', exact: true }).click();
      await expect(page.getByLabel('Saved Growth Agent watchlists')).toContainText('scheduled runs off');
      await expectAxeClean(page, {
        key: { route: 'ask-genie', state: 'watchlist-scheduled-status' },
        theme,
        known: {},
        include: '[aria-label="Saved Growth Agent watchlists"]',
      });
    });
  }
});
