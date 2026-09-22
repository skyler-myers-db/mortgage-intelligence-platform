/**
 * Rendered-layer proofs for the wave-0 Genie survivability work (audit
 * runtime-01 / genie-02 mounted panel, runtime-v2 Escape stack, genie-v2
 * held second ask, genie-01 / a11y-06 honest completion wait).
 *
 * The turn is scripted through data/genieTurn.ts (submit → progress →
 * complete), so the test holds Genie's own turn open, counts the polls the
 * browser really sends, and releases each step when it is ready to assert.
 */
import type { Page } from '@playwright/test';
import { genieAnswerFixture, genieDeepAnswerFixture, GENIE_QUESTION, registerGenieTurn } from './data/genieTurn';
import { expect, test } from './test';

const LAUNCHER_STATUS_ID = 'genie-launcher-status';

function panelAnnouncer(page: Page) {
  return page.locator('[data-genie-announcer="panel"]');
}

test.describe('a turn survives closing the panel', () => {
  test('keeps polling behind the closed panel, badges the launcher, and reopens with the answer at its start', async ({ app, page, mockApi }) => {
    const turn = registerGenieTurn(mockApi, { answer: genieDeepAnswerFixture() });
    await app.gotoRoute('/lead-queue');
    const dialog = await app.askGenie(GENIE_QUESTION);
    await expect(dialog.locator('.genie-progress')).toBeVisible();
    await expect(dialog.locator('.genie-progress__label')).toHaveText('Running the governed query');

    await dialog.getByRole('button', { name: 'Close Genie' }).click();
    await expect(app.geniePanel()).toHaveAttribute('aria-hidden', 'true');
    const toggle = app.genieToggle();
    await expect(toggle).toHaveClass(/is-genie-running/);
    await expect(toggle).toHaveAttribute('aria-describedby', LAUNCHER_STATUS_ID);
    await expect(page.locator(`#${LAUNCHER_STATUS_ID}`)).toHaveText('Genie is still working on your question.');

    // Progress polls keep arriving while the panel is closed (1.5 s cadence).
    const pollsAtClose = turn.progressPolls;
    await expect.poll(() => turn.progressPolls, { timeout: 15_000 }).toBeGreaterThanOrEqual(pollsAtClose + 2);
    expect(turn.submits).toBe(1);

    turn.finishGenieTurn();
    await expect.poll(() => turn.completes, { timeout: 15_000 }).toBe(1);
    await expect(toggle).toHaveClass(/is-genie-ready/);
    await expect(toggle).not.toHaveClass(/is-genie-running/);
    await expect(page.locator(`#${LAUNCHER_STATUS_ID}`)).toHaveText('Genie answer ready. Open Genie to read it.');

    await toggle.click();
    await expect(app.geniePanel()).toHaveClass(/is-open/);
    await expect(toggle).not.toHaveClass(/is-genie-ready|is-genie-running/);
    await expect(toggle).not.toHaveAttribute('aria-describedby', LAUNCHER_STATUS_ID);
    const body = dialog.locator('.genie__body');
    await expect(body.locator('.genie__msg--user')).toHaveText(GENIE_QUESTION);
    const answer = body.locator('.genie__msg--ai .genie-answer');
    await expect(answer).toHaveCount(1);
    await expect(answer.locator('.genie-md-p--heading.genie-md-p--first')).toHaveText('Summary');
    // The landed answer is anchored to its START inside the transcript.
    const geometry = await page.evaluate(() => {
      const scroller = document.querySelector<HTMLElement>('.genie .genie__body')!;
      const bubble = document.querySelector<HTMLElement>('.genie .genie__msg--ai')!;
      const heading = document.querySelector<HTMLElement>('.genie .genie-md-p--heading.genie-md-p--first')!;
      const box = scroller.getBoundingClientRect();
      return {
        scrollTop: scroller.scrollTop,
        bubbleTop: bubble.getBoundingClientRect().top - box.top,
        headingTop: heading.getBoundingClientRect().top - box.top,
        height: box.height,
      };
    });
    expect(geometry.bubbleTop).toBeGreaterThanOrEqual(-2);
    expect(geometry.bubbleTop).toBeLessThan(geometry.height / 2);
    expect(geometry.headingTop).toBeGreaterThanOrEqual(-2);
    expect(geometry.headingTop).toBeLessThan(geometry.height / 2);
    await expect(panelAnnouncer(page)).toHaveText('Answer ready');
  });
});

test.describe('Escape layering', () => {
  test('one Escape closes only the topmost layer, and Genie closes only when focus is inside it', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    await app.openGenie();
    const panel = app.geniePanel();

    // Genie open + evidence drawer open: Escape closes the drawer, Genie stays.
    await app.expandFirstLeadRow();
    const drawer = await app.openEvidenceDrawer(page.locator('table.tbl tbody tr.tbl__expand .evidence-chip').first());
    await page.keyboard.press('Escape');
    await expect(drawer).not.toHaveClass(/is-open/);
    await expect(panel).toHaveClass(/is-open/);

    // Escape with focus on a page control: Genie stays.
    const stateFilter = page.locator('button[aria-haspopup="listbox"][aria-label^="STATE:"]').first();
    await stateFilter.focus();
    await page.keyboard.press('Escape');
    await expect(panel).toHaveClass(/is-open/);

    // FilterSelect menu open + Genie open: Escape closes the menu, Genie stays.
    const menu = await app.openFilterMenu('STATE');
    await page.keyboard.press('Escape');
    await expect(menu).toHaveCount(0);
    await expect(panel).toHaveClass(/is-open/);

    // Focus in Genie's input: Escape closes Genie and returns focus to the toggle.
    await panel.getByRole('textbox', { name: 'Ask Genie' }).focus();
    await page.keyboard.press('Escape');
    await expect(panel).not.toHaveClass(/is-open/);
    await expect(app.genieToggle()).toBeFocused();
  });
});

test.describe('mid-turn composer', () => {
  test('holds a second ask while a turn is in flight and keeps the draft', async ({ app, page, mockApi }) => {
    // First turn lands an answer with follow-up chips.
    const first = registerGenieTurn(mockApi, { holdProgress: false, answer: genieAnswerFixture() });
    await app.gotoRoute('/lead-queue');
    const dialog = await app.askGenie(GENIE_QUESTION);
    await expect(dialog.locator('.genie__msg--ai .genie-answer')).toHaveCount(1);
    expect(first.submits).toBe(1);
    const chips = dialog.locator('.genie-answer__followups button');
    await expect(chips).not.toHaveCount(0);
    await expect(chips.first()).toBeEnabled();

    // Second turn, held open.
    const second = registerGenieTurn(mockApi, { answer: genieAnswerFixture() });
    const input = dialog.getByRole('textbox', { name: 'Ask Genie' });
    await input.fill('And how many are current customers?');
    await dialog.getByRole('button', { name: 'Ask', exact: true }).click();
    await expect(dialog.locator('.genie-progress')).toBeVisible();
    expect(second.submits).toBe(1);

    const ask = dialog.getByRole('button', { name: 'Ask', exact: true });
    await expect(ask).toBeDisabled();
    await expect(ask).toHaveAttribute('title', /^Genie is still answering/);
    await expect(dialog.locator('#genie-composer-busy')).toContainText(/^Genie is still answering/);
    for (const chip of await chips.all()) {
      await expect(chip).toBeDisabled();
      await expect(chip).toHaveAttribute('title', /^Genie is still answering/);
    }

    // Typing still works; Enter does not submit a second turn.
    await input.fill('Which counties carry the most?');
    await expect(input).toHaveValue('Which counties carry the most?');
    await input.press('Enter');
    await expect(input).toHaveValue('Which counties carry the most?');
    expect(second.submits).toBe(1);
    expect(first.submits).toBe(1);
    await expect(dialog.locator('.genie__msg--user')).toHaveCount(2);
  });
});

test.describe('deep research', () => {
  test('names the completion wait honestly and never says "Answer ready" before the answer renders', async ({ app, page, mockApi }) => {
    const turn = registerGenieTurn(mockApi, { deep: true, holdComplete: true, answer: genieDeepAnswerFixture() });
    await app.gotoRoute('/lead-queue');
    const dialog = await app.askGenie(GENIE_QUESTION);
    await expect(dialog.locator('.genie-progress')).toBeVisible();
    // Record every announcer change together with whether an answer was rendered.
    await page.evaluate(() => {
      const win = window as Window & { __announced?: Array<{ text: string; answered: boolean }> };
      win.__announced = [];
      const node = document.querySelector('[data-genie-announcer="panel"]')!;
      new MutationObserver(() => {
        win.__announced!.push({
          text: node.textContent ?? '',
          answered: document.querySelector('.genie .genie__msg--ai .genie-answer') !== null,
        });
      }).observe(node, { childList: true, subtree: true, characterData: true });
    });

    turn.finishGenieTurn();
    await expect.poll(() => turn.completes, { timeout: 15_000 }).toBe(1);
    const rail = dialog.locator('.genie-progress');
    await expect(rail.locator('.genie-progress__label')).toHaveText('Deep research: running governed sub-analyses');
    await expect(rail.locator('.genie-progress__stage[aria-current="step"]')).toHaveText('Deep research');
    await expect(panelAnnouncer(page)).toHaveText('Deep research: running governed sub-analyses');
    await expect(panelAnnouncer(page)).not.toHaveText(/Answer ready/);
    await expect(dialog.locator('.genie__msg--ai .genie-answer')).toHaveCount(0);

    turn.releaseComplete();
    await expect(dialog.locator('.genie__msg--ai .genie-answer')).toHaveCount(1);
    await expect(panelAnnouncer(page)).toHaveText('Answer ready');
    const announced = await page.evaluate(
      () => (window as Window & { __announced?: Array<{ text: string; answered: boolean }> }).__announced ?? [],
    );
    const ready = announced.filter((entry) => /Answer ready/.test(entry.text));
    expect(ready.length).toBeGreaterThan(0);
    for (const entry of ready) expect(entry.answered, '"Answer ready" was spoken only with a rendered answer').toBe(true);
  });
});
