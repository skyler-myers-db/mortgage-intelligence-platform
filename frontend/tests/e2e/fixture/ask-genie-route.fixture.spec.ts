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
    // Each tab opens with h2 sections, as Workflows and Saved monitors do.
    const main = page.locator('#main-content');
    await expect(main.getByRole('heading', { name: 'Conversation', level: 2 })).toBeVisible();
    await expect(main.getByRole('heading', { name: 'Trusted sources', level: 2 })).toBeVisible();
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
    await expect(page).toHaveURL(/\/ask-genie$/);
    await expect(composer(page)).toBeVisible();
  });

  test('one keyboard pass over the tabs is one Back step', async ({ app, page }) => {
    const historyLength = () => page.evaluate(() => window.history.length);
    // Wait for the selection to render, not just the URL: the router writes
    // the URL at once and renders the new tab in a transition, and a key
    // pressed before that render would move from the previous tab.
    const expectSelected = async (name: string, url: RegExp) => {
      await expect(page).toHaveURL(url);
      await expect(tab(page, name)).toHaveAttribute('aria-selected', 'true');
    };
    await app.gotoRoute('/ask-genie');
    const start = await historyLength();
    await tab(page, 'Ask').focus();
    await page.keyboard.press('ArrowRight');
    await expectSelected('Workflows', /\?tab=workflows$/);
    await page.keyboard.press('ArrowRight');
    await expectSelected('Saved monitors', /\?tab=monitors$/);
    expect(await historyLength(), 'the pass wrote one entry').toBe(start + 1);
    await page.goBack();
    await expectSelected('Ask', /\/ask-genie$/);

    // A click is its own step; a keyboard pass after it undoes to it.
    await tab(page, 'Workflows').click();
    await expectSelected('Workflows', /\?tab=workflows$/);
    await page.keyboard.press('ArrowRight');
    await expectSelected('Saved monitors', /\?tab=monitors$/);
    await page.keyboard.press('ArrowLeft');
    await expectSelected('Workflows', /\?tab=workflows$/);
    await expect(tab(page, 'Workflows')).toBeFocused();
    await page.goBack();
    await expectSelected('Ask', /\/ask-genie$/);
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

  test('on a phone the empty composer keeps to two lines and the empty state scrolls clear of it', async ({ app, page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await app.gotoRoute('/ask-genie');
    const input = composer(page);
    await expect(input).toHaveAttribute('placeholder', /prime refi candidates/);
    // field-sizing sizes an empty box to its placeholder: the placeholder
    // must fit the two-line minimum, not grow the docked composer.
    const sizing = await input.evaluate((node) => ({
      height: node.getBoundingClientRect().height,
      minHeight: Number.parseFloat(getComputedStyle(node).minHeight),
    }));
    expect(sizing.height, 'empty composer height vs its two-line minimum').toBeLessThanOrEqual(sizing.minHeight + 1);
    // The shell's route nav and the docked composer leave a band between
    // them; the empty state fits it once scrolled there.
    const title = page.locator('#ask-genie-panel-ask .genie-empty__title');
    await title.evaluate((node) => node.scrollIntoView({ block: 'nearest' }));
    const [titleBox, nav, dock] = await Promise.all([
      mustBox(title, 'empty state title'),
      mustBox(page.locator('.route-nav'), 'route nav'),
      mustBox(page.locator('form.genie-composer'), 'composer'),
    ]);
    expect(titleBox.y, 'the empty state title clears the route nav').toBeGreaterThanOrEqual(nav.y + nav.height - 1);
    expect(titleBox.y + titleBox.height, 'the empty state title clears the composer').toBeLessThanOrEqual(dock.y + 1);
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

test.describe('arriving with a thread already stored (visual-07)', () => {
  /** Where an element sits against the band between the route nav and the docked composer. */
  const band = (page: Page, locator: ReturnType<Page['locator']>) =>
    locator.evaluate((node) => {
      const nav = document.querySelector<HTMLElement>('.route-nav')!.getBoundingClientRect();
      const dock = document.querySelector<HTMLElement>('form.genie-composer')!.getBoundingClientRect();
      const box = node.getBoundingClientRect();
      return { clearsNav: box.top >= nav.bottom - 1, clearsComposer: box.bottom <= dock.top + 1 };
    });
  const scrollTop = (page: Page) => page.locator('.main').evaluate((main) => main.scrollTop);

  test('a link opens on the latest exchange, and Back keeps the offset the reader left', async ({ app, mockApi, page }) => {
    registerGenieTurn(mockApi, { answer: genieDeepAnswerFixture(), holdProgress: false });
    await app.gotoRoute('/ask-genie');
    const ask = page.locator('form.genie-composer').getByRole('button', { name: 'Ask Genie', exact: true });
    const followUp = 'How many of them are current customers?';
    for (const [count, question] of [[1, GENIE_QUESTION], [2, followUp]] as const) {
      await composer(page).fill(question);
      await ask.click();
      await expect(page.locator('.genie-thread .genie-answer')).toHaveCount(count);
    }

    const h1 = page.locator('#main-content h1');
    const nav = page.getByRole('navigation', { name: 'Main navigation' });
    const bubbles = page.locator('.genie-thread > .genie__msg--user');
    await nav.getByRole('link', { name: 'Glossary' }).click();
    await expect(h1).toHaveText('Mortgage intelligence glossary');
    // Arrive by a link (a PUSH), as from the nav or the floating panel.
    await nav.getByRole('link', { name: 'Ask Genie' }).click();
    await expect(h1).toHaveText('Ask Genie');
    await expect(bubbles).toHaveText([GENIE_QUESTION, followUp]);
    await expect.poll(() => band(page, bubbles.last()), { message: 'the latest question opens between the bars' }).toEqual({
      clearsNav: true,
      clearsComposer: true,
    });
    // Non-vacuity: the thread opened scrolled; the oldest turn is above the view.
    expect(await scrollTop(page), 'the route opened scrolled to the latest turn').toBeGreaterThan(0);
    await expect(bubbles.first()).not.toBeInViewport();

    // Back / Forward (POP) belong to the shell's scroll restoration: leave
    // at the top, come Back, and the top is where the route reopens.
    await page.locator('.main').evaluate((main) => {
      main.scrollTop = 0;
    });
    await expect(bubbles.first()).toBeInViewport();
    await nav.getByRole('link', { name: 'Glossary' }).click();
    await expect(h1).toHaveText('Mortgage intelligence glossary');
    await page.goBack();
    await expect(h1).toHaveText('Ask Genie');
    await expect(bubbles).toHaveCount(2);
    await expect(bubbles.first()).toBeInViewport();
    expect(await scrollTop(page), 'Back restored the offset the reader left').toBe(0);
    await expect(bubbles.last()).not.toBeInViewport();
  });

  test('a one-turn thread opens with the page head in view', async ({ app, mockApi, page }) => {
    registerGenieTurn(mockApi, { answer: genieDeepAnswerFixture(), holdProgress: false });
    await app.gotoRoute('/ask-genie');
    await askDeepQuestion(page);
    const h1 = page.locator('#main-content h1');
    const nav = page.getByRole('navigation', { name: 'Main navigation' });
    await nav.getByRole('link', { name: 'Glossary' }).click();
    await expect(h1).toHaveText('Mortgage intelligence glossary');
    await nav.getByRole('link', { name: 'Ask Genie' }).click();
    await expect(h1).toHaveText('Ask Genie');
    const bubble = page.locator('.genie-thread > .genie__msg--user');
    await expect(bubble).toHaveCount(1);
    // The latest question is already in view, so nothing moves.
    expect(await band(page, bubble)).toEqual({ clearsNav: true, clearsComposer: true });
    expect(await scrollTop(page)).toBe(0);
    await expect(tab(page, 'Ask')).toBeInViewport();
  });
});

/** What the focus walk records for one stop. */
interface FocusStop {
  name: string;
  /** Focus is inside the docked composer (the forward walk's end). */
  inComposer: boolean;
  /** Focus is in the conversation card's header (the backward walk's end). */
  inHeader: boolean;
  /** The sticky bar the focused element lies entirely behind, if any. */
  hiddenBy: 'composer' | 'route nav' | null;
}

async function focusStop(page: Page): Promise<FocusStop> {
  return page.evaluate(() => {
    const active = document.activeElement as HTMLElement | null;
    const form = document.querySelector<HTMLElement>('form.genie-composer');
    const nav = document.querySelector<HTMLElement>('.route-nav');
    if (!active || !form) return { name: '(none)', inComposer: false, inHeader: false, hiddenBy: null };
    const name = (active.getAttribute('aria-label') ?? active.textContent ?? active.tagName).trim().slice(0, 48);
    const header = form.closest('.surface')?.querySelector(':scope > .surface__hdr');
    const inComposer = form.contains(active);
    const inHeader = Boolean(header?.contains(active));
    const a = active.getBoundingClientRect();
    const within = (bar: HTMLElement) => {
      const b = bar.getBoundingClientRect();
      return a.width > 0 && a.height > 0 && a.top >= b.top && a.bottom <= b.bottom && a.left >= b.left && a.right <= b.right;
    };
    let hiddenBy: 'composer' | 'route nav' | null = null;
    if (!inComposer && within(form)) hiddenBy = 'composer';
    else if (nav && !nav.contains(active) && within(nav)) hiddenBy = 'route nav';
    return { name, inComposer, inHeader, hiddenBy };
  });
}

/** Press `key` until `done(stop)`, recording every stop on the way. */
async function walkFocus(page: Page, key: 'Tab' | 'Shift+Tab', done: (stop: FocusStop) => boolean) {
  const visited: string[] = [];
  const hidden: string[] = [];
  for (let press = 0; press < 150; press += 1) {
    await page.keyboard.press(key);
    const stop = await focusStop(page);
    if (done(stop)) return { reached: true, visited, hidden };
    visited.push(stop.name);
    if (stop.hiddenBy) hidden.push(`'${stop.name}' is obscured by the ${stop.hiddenBy}`);
  }
  return { reached: false, visited, hidden };
}

/**
 * How far the scroller's scroll-padding falls short of each sticky bar's
 * size, in px (0 when it clears the bar). Read from the browser's computed
 * style, so it measures what focus scrolling actually uses.
 */
async function clearanceShortfall(page: Page) {
  return page.evaluate(() => {
    const main = document.querySelector<HTMLElement>('.main')!;
    const style = getComputedStyle(main);
    const composerHeight = document.querySelector<HTMLElement>('form.genie-composer')!.offsetHeight;
    const navHeight = document.querySelector<HTMLElement>('.route-nav')!.offsetHeight;
    return {
      composer: Math.max(0, composerHeight - Number.parseFloat(style.scrollPaddingBlockEnd)),
      routeNav: Math.max(0, navHeight - Number.parseFloat(style.scrollPaddingBlockStart)),
    };
  });
}

async function askDeepQuestion(page: Page) {
  await composer(page).fill(GENIE_QUESTION);
  await page.locator('form.genie-composer').getByRole('button', { name: 'Ask Genie', exact: true }).click();
  await expect(page.locator('.genie-thread .genie-answer')).toHaveCount(1);
  await expect(composer(page)).toHaveValue('');
}

test.describe('focus is never hidden behind a sticky bar (WCAG 2.2 SC 2.4.11)', () => {
  // The composer grows with its draft (two lines to eight), so the clearance
  // must follow its measured size, not a fixed one.
  const drafts = [
    { what: 'an empty composer', draft: '' },
    {
      what: 'an eight-line draft',
      draft: Array.from({ length: 8 }, (_, line) => `Line ${line + 1} of a follow-up about prime refi candidates`).join('\n'),
    },
  ];
  for (const { what, draft } of drafts) {
    test(`tabbing down through an answer keeps every focus stop clear of the composer · ${what}`, async ({ app, mockApi, page }) => {
      registerGenieTurn(mockApi, { answer: genieDeepAnswerFixture(), holdProgress: false });
      await app.gotoRoute('/ask-genie');
      await askDeepQuestion(page);
      if (draft) {
        // The stylesheet's pre-measurement fallback clearance (ask-genie.css).
        const fallback = await page.evaluate(() => {
          const root = getComputedStyle(document.documentElement);
          const px = (name: string) => Number.parseFloat(root.getPropertyValue(name));
          return px('--sp-16') * 2 + px('--sp-8') + px('--focus-ring-width') + px('--focus-ring-offset');
        });
        await composer(page).fill(draft);
        // Non-vacuity: the draft grew the composer past what the fixed
        // fallback would clear, so only the measured size keeps focus clear.
        await expect
          .poll(async () => (await mustBox(page.locator('form.genie-composer'), 'composer')).height)
          .toBeGreaterThan(fallback);
      }
      // The scroller's clearance covers both bars at their current size.
      await expect
        .poll(() => clearanceShortfall(page), { message: 'scroll-padding short of the sticky bars (px)' })
        .toEqual({ composer: 0, routeNav: 0 });

      // From the top of the conversation, walk forward to the composer. The
      // thread runs past the fold, so the stops below it start under the dock.
      await page.locator('.main').evaluate((main) => {
        main.scrollTop = 0;
      });
      const newThread = page.locator('#main-content .surface__hdr').getByRole('button', { name: 'New thread' });
      await newThread.focus();
      await expect(newThread).toBeFocused();

      const walk = await walkFocus(page, 'Tab', (stop) => stop.inComposer);
      expect(walk.reached, 'Tab reaches the composer').toBe(true);
      // Non-vacuity: the walk went through the answer's own controls and the
      // suggestions, which sit under the dock until focus brings them up.
      expect(walk.visited, 'the walk crossed the answer feedback').toContain('Mark this answer helpful');
      expect(walk.visited.some((name) => name.startsWith('Which states')), 'the walk crossed the suggestions').toBe(true);
      expect(walk.hidden, 'focus stops entirely behind a sticky bar').toEqual([]);
    });
  }

  test('the clearance applies only while the Ask tab shows', async ({ app, page }) => {
    const padding = () =>
      page.locator('.main').evaluate((main) => {
        const style = getComputedStyle(main);
        return { start: style.scrollPaddingBlockStart, end: style.scrollPaddingBlockEnd };
      });
    await app.gotoRoute('/ask-genie');
    await expect.poll(clearanceShortfall.bind(null, page)).toEqual({ composer: 0, routeNav: 0 });
    await tab(page, 'Workflows').click();
    await expect(agentPrompt(page)).toBeVisible();
    expect(await padding(), 'no clearance on the Workflows tab').toEqual({ start: 'auto', end: 'auto' });
    await page.goBack();
    await expect(composer(page)).toBeVisible();
    await expect.poll(clearanceShortfall.bind(null, page)).toEqual({ composer: 0, routeNav: 0 });
    // Leaving the route drops the rule and the measured sizes with it.
    await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Glossary' }).click();
    await expect(page.locator('#main-content h1')).toHaveText('Mortgage intelligence glossary');
    expect(await padding(), 'no clearance after leaving the route').toEqual({ start: 'auto', end: 'auto' });
    expect(
      await page.locator('.main').evaluate((main) => main.style.getPropertyValue('--genie-composer-block-size')),
      'the measured size is removed on unmount',
    ).toBe('');
  });

  test('Shift+Tab up through an answer keeps every focus stop clear of the route nav', async ({ app, mockApi, page }) => {
    registerGenieTurn(mockApi, { answer: genieDeepAnswerFixture(), holdProgress: false });
    await app.gotoRoute('/ask-genie');
    await askDeepQuestion(page);

    // From the end of the conversation, walk back up to its header. The
    // thread's top starts above the view, under the sticky route nav.
    const main = page.locator('.main');
    await main.evaluate((node) => {
      node.scrollTop = node.scrollHeight;
    });
    const startTop = await main.evaluate((node) => node.scrollTop);
    await composer(page).focus();

    const walk = await walkFocus(page, 'Shift+Tab', (stop) => stop.inHeader);
    expect(walk.reached, 'Shift+Tab reaches the conversation header').toBe(true);
    // Non-vacuity: the walk scrolled up through the answer to its question.
    expect(walk.visited, 'the walk crossed the answer table').toContain('Genie answer table');
    expect(walk.visited, 'the walk crossed the question').toContain('Edit question');
    expect(await main.evaluate((node) => node.scrollTop), 'focus scrolled the thread up').toBeLessThan(startTop);
    expect(walk.hidden, 'focus stops entirely behind a sticky bar').toEqual([]);
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
