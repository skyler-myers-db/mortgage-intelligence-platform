/**
 * Rendered-layer proofs for the Growth Agent trust lane (audit 2026-09-21
 * `critic-01`, `genie-09` part 1): Run posts exactly the plan the card shows
 * with its server digest, never composes again, renders nothing as run until
 * the server answers, a 409 runs nothing and Compose again brings the current
 * plan to review, and a plan the deployment cannot sign cannot be run.
 *
 * 1440x900 (the harness default). The data module records every request
 * body the browser posts, so the proofs read the wire, not the component.
 */
import type { Page } from '@playwright/test';
import { expectAxeClean } from './axe';
import {
  COMPOSE_PATTERN,
  DIGEST_RECOMPOSED,
  EXECUTE_PATTERN,
  RECOMPOSED_PLAN,
  REVIEWED_PLAN,
  RUNS_PATTERN,
  composeReply,
  registerGrowthAgentTrust,
} from './data/growthAgentTrust';
import type { MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const CARD = '[aria-label="Composed Growth Agent plan"]';
/** The browser logs the 409 it was served on purpose; nothing else may log. */
const EXPECTED_409 = /status of 409 \(Conflict\).*\/growth-agent\/agent\/plan\/execute/;

function card(page: Page) {
  return page.locator(CARD);
}

function tab(page: Page, name: string) {
  return page.getByRole('tablist', { name: 'Ask Genie views' }).getByRole('tab', { name, exact: true });
}

function commandBar(page: Page) {
  return page.getByRole('region', { name: 'Mortgage Growth Agent command center' });
}

function posts(mockApi: MockApi, pattern: string): number {
  return mockApi.calls.filter((call) => call.method === 'POST' && call.path === pattern).length;
}

async function compose(page: Page) {
  await commandBar(page).getByRole('button', { name: 'Compose plan', exact: true }).click();
  await expect(card(page)).toBeVisible();
}

test.describe('the plan the user reviewed is the plan that runs', () => {
  test('Run posts the displayed plan and digest once, and the trace stops at the gate', async ({ app, page, mockApi }) => {
    const recorder = registerGrowthAgentTrust(mockApi, { replies: [composeReply()], execute: 'ran' });
    await app.gotoRoute('/ask-genie');
    await tab(page, 'Workflows').click();
    await expect(commandBar(page)).toBeVisible();
    await tab(page, 'Ask').click();
    await tab(page, 'Workflows').click();
    // Loading the route and switching tabs posts nothing.
    expect(posts(mockApi, COMPOSE_PATTERN)).toBe(0);
    expect(posts(mockApi, EXECUTE_PATTERN)).toBe(0);

    const actions = commandBar(page).getByRole('button');
    await expect(actions).toHaveText(['Plan reviewed workflow', 'Save reviewed watchlist', 'Compose plan']);
    await expect(commandBar(page).locator('.btn--primary')).toHaveCount(1);

    await compose(page);
    const run = card(page).getByRole('button', { name: 'Run this plan' });
    await expect(run).toBeEnabled();
    await expect(card(page)).toContainText('Segments: Prime Refi Candidates, Listed for Sale · Match: any segment');
    await expect(card(page)).toContainText('States: current coverage');
    expect(recorder.composeBodies).toHaveLength(1);
    expect(recorder.composeBodies[0]).not.toHaveProperty('execute');

    await run.click();
    await expect(card(page).getByText('Execution trace')).toBeVisible();

    expect(posts(mockApi, COMPOSE_PATTERN)).toBe(1);
    expect(posts(mockApi, EXECUTE_PATTERN)).toBe(1);
    const posted = recorder.executeBodies[0];
    expect(posted.plan).toEqual(REVIEWED_PLAN);
    expect(posted.plan_digest).toBe(composeReply().plan_digest);
    expect(posted.objective).toBe((recorder.composeBodies[0] as { objective: string }).objective);
    expect(posted.states).toEqual([]);

    await expect(card(page).locator('.growth-agent-step--review_required')).toBeVisible();
    await expect(card(page).locator('.chip', { hasText: 'Approval gate' })).toBeVisible();
    await expect(card(page).getByRole('button', { name: /Run this plan|Running/ })).toHaveCount(0);
    // The runs list has no consumer this wave: nothing reads it.
    expect(mockApi.calls.filter((call) => call.path === RUNS_PATTERN)).toEqual([]);
  });

  test('a 409 runs nothing, says so in view, and Compose again brings the current plan', async ({ app, page, mockApi, hygiene }) => {
    hygiene.allow('console.error', EXPECTED_409);
    const recorder = registerGrowthAgentTrust(mockApi, {
      replies: [composeReply(), composeReply(RECOMPOSED_PLAN, DIGEST_RECOMPOSED)],
      execute: 'conflict',
    });
    await app.gotoRoute('/ask-genie?tab=workflows');
    await compose(page);
    await card(page).getByRole('button', { name: 'Run this plan' }).click();

    const alert = card(page).getByRole('alert');
    await expect(alert).toHaveText(/^This plan was not run\./);
    await expect(alert).toBeInViewport();
    await expect(card(page).getByRole('button', { name: 'Run this plan' })).toBeDisabled();
    await expect(card(page)).not.toContainText('Execution trace');

    await card(page).getByRole('button', { name: 'Compose again' }).click();
    await expect(card(page)).toContainText('fn_offer_compare');
    await expect(card(page).getByRole('alert')).toHaveCount(0);
    await expect(card(page).getByRole('button', { name: 'Run this plan' })).toBeEnabled();
    expect(recorder.composeBodies).toHaveLength(2);
    expect(recorder.executeBodies).toHaveLength(1);
  });

  test('a plan the deployment cannot sign is shown with its reason and cannot run', async ({ app, page, mockApi }) => {
    const recorder = registerGrowthAgentTrust(mockApi, { replies: [composeReply(REVIEWED_PLAN, null)], execute: 'ran' });
    await app.gotoRoute('/ask-genie?tab=workflows');
    await compose(page);
    const run = card(page).getByRole('button', { name: 'Run this plan' });
    await expect(run).toBeDisabled();
    await expect(run).toHaveAccessibleDescription(/missing a required security setting/);
    expect(recorder.executeBodies).toHaveLength(0);
  });
});

test.describe('axe', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: the composed, conflict and executed plan card have no WCAG violations`, async ({ app, page, mockApi, hygiene }) => {
      hygiene.allow('console.error', EXPECTED_409);
      registerGrowthAgentTrust(mockApi, { replies: [composeReply()], execute: 'conflict' });
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie?tab=workflows');
      await compose(page);
      await expectAxeClean(page, { key: { route: 'ask-genie', state: 'plan-composed' }, theme, known: {}, include: CARD });

      await card(page).getByRole('button', { name: 'Run this plan' }).click();
      await expect(card(page).getByRole('alert')).toBeVisible();
      await expectAxeClean(page, { key: { route: 'ask-genie', state: 'plan-conflict' }, theme, known: {}, include: CARD });

      registerGrowthAgentTrust(mockApi, { replies: [composeReply(RECOMPOSED_PLAN, DIGEST_RECOMPOSED)], execute: 'ran' });
      await card(page).getByRole('button', { name: 'Compose again' }).click();
      await expect(card(page)).toContainText('fn_offer_compare');
      await card(page).getByRole('button', { name: 'Run this plan' }).click();
      await expect(card(page).getByText('Execution trace')).toBeVisible();
      await expectAxeClean(page, { key: { route: 'ask-genie', state: 'plan-executed' }, theme, known: {}, include: CARD });
    });
  }
});
