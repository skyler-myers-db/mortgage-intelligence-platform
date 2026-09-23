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
import { GENIE_QUESTION, genieDeepAnswerFixture, registerGenieTurn } from './data/genieTurn';
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

interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

function overlaps(a: Box, b: Box): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

async function mustBox(locator: ReturnType<Page['locator']>, what: string): Promise<Box> {
  const box = await locator.boundingBox();
  if (!box) throw new Error(`${what} has no layout box`);
  return box;
}

test.describe('docked composer (visual-07)', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`on load the composer is fully above the fold with a real placeholder · ${theme}`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie');
      const input = composer(page);
      await expect(input).toBeVisible();
      await expect(input).toHaveAttribute('placeholder', /prime refi candidates/);
      const box = await mustBox(input, 'composer textarea');
      const viewport = page.viewportSize()!;
      expect(box.y, 'composer top inside the viewport').toBeGreaterThanOrEqual(0);
      expect(box.y + box.height, 'composer bottom above the 900px fold').toBeLessThanOrEqual(viewport.height);
      const ask = page.locator('form.genie-composer').getByRole('button', { name: 'Ask Genie', exact: true });
      await expect(ask).toHaveClass(/btn--primary/);
      await expect(ask).toBeDisabled();
      await input.fill('Which states have the most prime refi candidates?');
      await expect(ask).toBeEnabled();
    });
  }

  test('an answer lands above the composer, and the composer stays docked in view', async ({ app, mockApi, page }) => {
    const turn = registerGenieTurn(mockApi, { answer: genieDeepAnswerFixture(), holdProgress: false });
    await app.gotoRoute('/ask-genie');
    await composer(page).fill(GENIE_QUESTION);
    await page.locator('form.genie-composer').getByRole('button', { name: 'Ask Genie', exact: true }).click();

    const answer = page.locator('.genie-thread .genie-answer').last();
    await expect(answer).toBeVisible();
    await expect(answer.locator('.genie-md-p--heading.genie-md-p--first')).toHaveText('Summary');
    expect(turn.submits).toBe(1);
    await expect(composer(page)).toHaveValue('');

    const geometry = await page.evaluate(() => {
      const main = document.querySelector<HTMLElement>('.main')!;
      const thread = document.querySelector<HTMLElement>('.genie-thread')!;
      const answers = thread.querySelectorAll<HTMLElement>('.genie-answer');
      const lastAnswer = answers[answers.length - 1];
      const form = document.querySelector<HTMLElement>('form.genie-composer')!;
      const textarea = form.querySelector<HTMLElement>('textarea')!;
      return {
        answerBeforeComposer: Boolean(lastAnswer.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING),
        answerTop: lastAnswer.getBoundingClientRect().top,
        threadBottom: thread.getBoundingClientRect().bottom,
        textareaTop: textarea.getBoundingClientRect().top,
        textareaBottom: textarea.getBoundingClientRect().bottom,
        formBottom: form.getBoundingClientRect().bottom,
        mainBottom: main.getBoundingClientRect().bottom,
        viewportHeight: window.innerHeight,
      };
    });
    expect(geometry.answerBeforeComposer, 'the answer precedes the composer').toBe(true);
    expect(geometry.answerTop, 'the answer starts above the composer').toBeLessThan(geometry.textareaTop);
    // Non-vacuity: the thread runs past the fold, so in normal flow the
    // composer would sit below it.
    expect(geometry.threadBottom, 'the thread is taller than the view').toBeGreaterThan(geometry.viewportHeight);
    expect(geometry.textareaTop, 'composer top in view').toBeGreaterThanOrEqual(0);
    expect(geometry.textareaBottom, 'composer bottom in view').toBeLessThanOrEqual(geometry.viewportHeight);
    expect(Math.abs(geometry.formBottom - geometry.mainBottom), 'composer docked to the bottom of the scroller').toBeLessThanOrEqual(1);

    // Scrolled to the end, the whole answer sits above the composer.
    await page.locator('.main').evaluate((main) => {
      main.scrollTop = main.scrollHeight;
    });
    const [answerBox, formBox] = await Promise.all([
      mustBox(answer, 'answer'),
      mustBox(page.locator('form.genie-composer'), 'composer'),
    ]);
    expect(answerBox.y + answerBox.height).toBeLessThanOrEqual(formBox.y + 1);
  });

  test('a follow-up lands at the end of the thread and is brought into view', async ({ app, mockApi, page }) => {
    registerGenieTurn(mockApi, { answer: genieDeepAnswerFixture(), holdProgress: false });
    await app.gotoRoute('/ask-genie');
    const ask = page.locator('form.genie-composer').getByRole('button', { name: 'Ask Genie', exact: true });
    await composer(page).fill(GENIE_QUESTION);
    await ask.click();
    await expect(page.locator('.genie-thread .genie-answer')).toHaveCount(1);

    const followUp = 'How many of them are current customers?';
    await composer(page).fill(followUp);
    await ask.click();
    await expect(page.locator('.genie-thread .genie-answer')).toHaveCount(2);
    const bubbles = page.locator('.genie-thread > .genie__msg--user');
    await expect(bubbles).toHaveText([GENIE_QUESTION, followUp]);
    // The new exchange starts in view, under the sticky route nav and above
    // the docked composer, although it was appended below the first answer.
    const [bubble, nav, dock] = await Promise.all([
      mustBox(bubbles.last(), 'follow-up question'),
      mustBox(page.locator('.route-nav'), 'route nav'),
      mustBox(page.locator('form.genie-composer'), 'composer'),
    ]);
    expect(bubble.y).toBeGreaterThanOrEqual(nav.y + nav.height - 1);
    expect(bubble.y + bubble.height).toBeLessThanOrEqual(dock.y);
    await expect(composer(page)).toBeInViewport({ ratio: 1 });
  });

  test('the Genie launcher never covers the composer controls', async ({ app, page }) => {
    const launcher = page.locator('.genie__fab');
    const controls = [
      { what: 'composer textarea', locator: composer(page) },
      {
        what: 'composer submit button',
        locator: page.locator('form.genie-composer').getByRole('button', { name: 'Ask Genie', exact: true }),
      },
    ];

    // Desktop: the launcher is the topbar toggle; the floating button is not shown.
    await app.gotoRoute('/ask-genie');
    if (await launcher.isVisible()) {
      const fab = await mustBox(launcher, 'launcher');
      for (const control of controls) expect(overlaps(fab, await mustBox(control.locator, control.what)), control.what).toBe(false);
    }

    // Narrow: the floating launcher is fixed at the inline end. Grow the
    // question to several lines so the composer reaches up into its band.
    await page.setViewportSize({ width: 390, height: 844 });
    await app.gotoRoute('/ask-genie');
    await expect(launcher).toBeVisible();
    await composer(page).fill(
      'Which states have the most prime refi candidates, and how many of them are current customers with at least forty percent equity and a recent listing?',
    );
    const fab = await mustBox(launcher, 'launcher');
    const dock = await mustBox(page.locator('form.genie-composer'), 'composer');
    // The composer is docked at the bottom of the view, in the launcher's band.
    expect(dock.y + dock.height).toBeGreaterThan(fab.y);
    for (const control of controls) {
      const box = await mustBox(control.locator, control.what);
      expect(overlaps(fab, box), `launcher overlaps the ${control.what}`).toBe(false);
    }
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
