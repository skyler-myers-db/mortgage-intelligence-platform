/**
 * Rendered-layer proofs for the `/ask-genie` route lane (audit 2026-09-21
 * `visual-07`, `genie-09`, `flow-10`): the route is conversation-first, its
 * Ask | Workflows | Saved monitors tabs live in the URL, and a Growth Agent
 * run shows its progress on the tab it runs from.
 *
 * 1440x900 (the harness default) unless a test says otherwise.
 */
import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { registerHeldWorkflowRun } from './data/askGenieRoute';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

function tab(page: Page, name: string) {
  return page.getByRole('tablist', { name: 'Ask Genie views' }).getByRole('tab', { name, exact: true });
}

function composer(page: Page) {
  return page.locator('#main-content').getByRole('textbox', { name: 'Ask Genie — question' });
}

function agentPrompt(page: Page) {
  return page.locator('#main-content').getByRole('textbox', { name: 'Mortgage Growth Agent prompt' });
}

test.describe('conversation first', () => {
  test('opens on the Ask tab, named what the nav calls it', async ({ app, page }) => {
    await app.gotoRoute('/ask-genie');
    await expect(page.locator('#main-content h1')).toHaveText('Ask Genie');
    await expect(page).toHaveTitle(/^Ask Genie · /);
    await expect(page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Ask Genie' })).toBeVisible();
    await expect(tab(page, 'Ask')).toHaveAttribute('aria-selected', 'true');
    await expect(tab(page, 'Workflows')).toHaveAttribute('aria-selected', 'false');
    await expect(composer(page)).toBeVisible();
    // The Growth Agent no longer leads the page.
    await expect(agentPrompt(page)).toBeHidden();
    await expect(page.getByText('Mortgage growth co-pilot')).toBeHidden();
  });

  test('?tab=workflows deep-links the Growth Agent, and Back returns to Ask', async ({ app, page }) => {
    await app.gotoRoute('/ask-genie?tab=workflows');
    await expect(tab(page, 'Workflows')).toHaveAttribute('aria-selected', 'true');
    await expect(agentPrompt(page)).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Mortgage growth co-pilot', level: 2 })).toBeVisible();
    await expect(page.locator('.growth-agent-card').first()).toBeVisible();
    await expect(composer(page)).toBeHidden();

    await app.gotoRoute('/ask-genie');
    await tab(page, 'Workflows').click();
    await expect(page).toHaveURL(/\/ask-genie\?tab=workflows$/);
    await expect(agentPrompt(page)).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(/\/ask-genie$/);
    await expect(tab(page, 'Ask')).toHaveAttribute('aria-selected', 'true');
    await expect(composer(page)).toBeVisible();
    await expect(agentPrompt(page)).toBeHidden();
  });

  test('arrow keys move between the tabs and write the URL', async ({ app, page }) => {
    await app.gotoRoute('/ask-genie');
    await tab(page, 'Ask').focus();
    await page.keyboard.press('ArrowRight');
    await expect(tab(page, 'Workflows')).toBeFocused();
    await expect(page).toHaveURL(/\?tab=workflows$/);
    await page.keyboard.press('End');
    await expect(tab(page, 'Saved monitors')).toBeFocused();
    await expect(page).toHaveURL(/\?tab=monitors$/);
    await expect(page.getByText('No saved monitors yet.')).toBeVisible();
    await page.keyboard.press('Home');
    await expect(tab(page, 'Ask')).toBeFocused();
    await expect(composer(page)).toBeVisible();
  });
});

test.describe('Growth Agent runs report where they run (genie-09)', () => {
  test('a workflow run shows an in-progress card in view, then its result in the same slot', async ({ app, mockApi, page }) => {
    const run = registerHeldWorkflowRun(mockApi);
    await app.gotoRoute('/ask-genie?tab=workflows');
    const card = page.locator('.growth-agent-card').filter({ hasText: 'Daily refi brief' });
    await card.getByRole('button', { name: 'Run', exact: true }).click();

    const pending = page.getByRole('status', { name: 'Growth Agent run in progress' });
    await expect(pending).toBeVisible();
    await expect(pending).toContainText('Running Daily refi brief');
    await expect(pending).toBeInViewport();
    await expect(card.getByRole('button', { name: 'Running…' })).toBeDisabled();
    expect(run.posts).toBe(1);

    run.release();
    const result = page.getByRole('region', { name: 'Latest Growth Agent run' });
    await expect(result).toBeVisible();
    await expect(pending).toHaveCount(0);
    // The result sits above the workflow cards, where the progress was.
    const [resultBox, cardsBox] = await Promise.all([
      result.boundingBox(),
      page.locator('.growth-agent__cards').boundingBox(),
    ]);
    expect(resultBox && cardsBox && resultBox.y + resultBox.height <= cardsBox.y + 1).toBe(true);
    // Its feedback is not repeated on the Saved monitors tab.
    await tab(page, 'Saved monitors').click();
    await expect(page.getByRole('region', { name: 'Latest Growth Agent run' })).toBeHidden();
  });
});

test.describe('axe on the Workflows and Saved monitors tabs', () => {
  for (const theme of FIXTURE_THEMES) {
    for (const view of ['workflows', 'monitors'] as const) {
      test(`?tab=${view} · ${theme} has no WCAG A/AA violation`, async ({ app, page }) => {
        await app.setTheme(theme);
        await app.gotoRoute(`/ask-genie?tab=${view}`);
        const results = await new AxeBuilder({ page }).withTags(WCAG_TAGS).analyze();
        expect(results.violations.map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(' ')).join(' ; ')}`)).toEqual([]);
        expect(results.passes.length).toBeGreaterThan(0);
      });
    }
  }
});
