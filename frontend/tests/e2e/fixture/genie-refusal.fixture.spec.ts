/**
 * Refusal card in the floating Genie panel and on /ask-genie (audit
 * 2026-09-21 `genie-05`).
 *
 * For every coarse refusal family the panel receives an inline governed
 * refusal and must render, under the refusal sentence, the card: the
 * family's plain-language sentence, the pre-validated rephrase chips (the
 * SAME texts tests/unit/test_genie_refusal_chips.py runs through the real
 * guard battery, read here from the shared JSON), "Edit question" that puts
 * the ORIGINAL prompt back in the composer, the reviewed-vocabulary link, and
 * "This was legitimate", which POSTs exactly once with a 64-hex
 * `question_hash` and no question text anywhere in the body, then moves
 * focus to its confirmation.
 *
 * Rendered-layer proofs the unit tests cannot give: the report button's
 * accessible name (WCAG 2.5.3), text contrast of the card's small text in
 * both themes (WCAG 1.4.3, computed against the composited background), the
 * visible focus ring on the report confirmation after a keyboard report
 * (WCAG 2.4.7), and the deep-dive route's own "Edit question" wiring.
 */
import fs from 'node:fs';
import path from 'node:path';
import type { Locator, Page } from '@playwright/test';
import type { GenieRefusalReason } from '../../../src/types';
import type { GenieRefusalReportResult, GenieSubmitResult } from '../../../src/lib/apiTypes';
import type { AppDriver, FixtureTheme } from './app';
import {
  REFUSAL_FAMILIES,
  REFUSAL_REPORT_ACCEPTED,
  REFUSED_QUESTIONS,
  refusalReportHash,
  refusedSubmit,
} from './data/genieRefusal';
import { json } from './mockApi';
import { expect, test } from './test';

const REPORT_PATH = '/api/genie/refusal-report';
const SUBMIT_PATH = '/api/genie/message/submit';
/** Visible label first, so voice-control users can say what they see. */
const REPORT_BUTTON_NAME = /^This was legitimate\b/;
/** WCAG 1.4.3 AA for the card's 11px text (not large text). */
const AA_NORMAL_TEXT = 4.5;

/** A distinctive fragment of each family's card sentence (GenieRefusalCard copy). */
const SENTENCE_FRAGMENT: Record<GenieRefusalReason, string> = {
  protected_class: 'not on protected-class attributes',
  unreviewed_criterion: 'not in the reviewed Module 0 vocabulary',
  pii_request: 'masked borrower IDs',
  instruction_override: 'Ask the analytics question directly',
  outreach_instruction: 'Offer Orchestrator',
  scope_bypass: 'reads governed Module 0 assets only',
  out_of_scope: 'book of business',
  output_policy: 'did not pass the governed checks before display',
  unknown: 'stopped before a live result',
};

function readValidatedChips(configFile: string | undefined): Record<GenieRefusalReason, string[]> {
  if (!configFile) throw new Error('Fixture harness needs frontend/playwright.config.ts to locate the chips JSON.');
  const chipsPath = path.resolve(
    path.dirname(configFile),
    'src/components/mortgage/genieRefusalChips.json',
  );
  return JSON.parse(fs.readFileSync(chipsPath, 'utf8')) as Record<GenieRefusalReason, string[]>;
}

interface ContrastSample {
  ratio: number;
  text: string;
  background: string;
}

/**
 * WCAG contrast of an element's text against the background it is actually
 * painted on: every translucent background-color from the element up to the
 * first opaque ancestor is composited (the card is a warning tint over the
 * answer bubble), then the text colour is composited over that.
 */
async function textContrast(locator: Locator): Promise<ContrastSample> {
  return locator.evaluate((element) => {
    type Rgba = [number, number, number, number];
    const parse = (value: string): Rgba => {
      const match = /^rgba?\(([^)]+)\)$/.exec(value.trim());
      if (!match) throw new Error(`Unparseable computed colour "${value}"`);
      const parts = match[1].split(/[\s,/]+/).filter(Boolean).map(Number);
      return [parts[0], parts[1], parts[2], parts.length > 3 ? parts[3] : 1];
    };
    const over = (top: Rgba, base: [number, number, number]): [number, number, number] => [
      top[0] * top[3] + base[0] * (1 - top[3]),
      top[1] * top[3] + base[1] * (1 - top[3]),
      top[2] * top[3] + base[2] * (1 - top[3]),
    ];
    const layers: Rgba[] = [];
    for (let node: Element | null = element; node; node = node.parentElement) {
      const layer = parse(getComputedStyle(node).backgroundColor);
      if (layer[3] === 0) continue;
      layers.push(layer);
      if (layer[3] >= 1) break;
    }
    let background: [number, number, number] = [255, 255, 255];
    for (const layer of layers.reverse()) background = over(layer, background);
    const text = over(parse(getComputedStyle(element).color), background);
    const luminance = (rgb: [number, number, number]) => {
      const [r, g, b] = rgb.map((channel) => {
        const c = channel / 255;
        return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const [high, low] = [luminance(text), luminance(background)].sort((a, b) => b - a);
    const fmt = (rgb: [number, number, number]) => `rgb(${rgb.map((c) => Math.round(c)).join(',')})`;
    return { ratio: (high + 0.05) / (low + 0.05), text: fmt(text), background: fmt(background) };
  });
}

async function askInPanel(page: Page, app: AppDriver, question: string): Promise<Locator> {
  const dialog = await app.openGenie();
  const composer = dialog.getByRole('textbox', { name: 'Ask Genie' });
  await composer.fill(question);
  await composer.press('Enter');
  const card = dialog.getByTestId('genie-refusal-card');
  await expect(card).toBeVisible();
  await expect(page.getByRole('dialog', { name: 'Genie chat' })).toBeVisible();
  return card;
}

test.describe('Genie refusal card', () => {
  for (const reason of REFUSAL_FAMILIES) {
    test(`${reason}: sentence, validated chips, Edit question and a hash-only report`, async ({ app, page, mockApi }, testInfo) => {
      const question = REFUSED_QUESTIONS[reason];
      const chips = readValidatedChips(testInfo.config.configFile);
      const reports: unknown[] = [];
      mockApi.register('POST', SUBMIT_PATH, () => json<GenieSubmitResult>(refusedSubmit(reason, question)));
      mockApi.register('POST', REPORT_PATH, (request) => {
        reports.push(request.body);
        return json<GenieRefusalReportResult>(REFUSAL_REPORT_ACCEPTED);
      });

      await app.setTheme('light');
      await app.gotoRoute('/glossary');
      const card = await askInPanel(page, app, question);
      const dialog = page.getByRole('dialog', { name: 'Genie chat' });
      const composer = dialog.getByRole('textbox', { name: 'Ask Genie' });
      await expect(card).toHaveAttribute('data-refusal-reason', reason);
      // Named for everything it holds, not only the rewordings.
      await expect(card).toHaveAccessibleName('Refusal options');
      await expect(card).toContainText(SENTENCE_FRAGMENT[reason]);
      await expect(card.getByTestId('genie-refusal-chip')).toHaveCount(chips[reason].length);
      expect(await card.getByTestId('genie-refusal-chip').locator('.filter__value').allTextContents()).toEqual(
        chips[reason],
      );
      // One "Ask" row only: the card's chips, never a second row of samples.
      await expect(dialog.locator('.genie-answer__followups')).toHaveCount(0);
      await expect(card.getByRole('link', { name: 'Reviewed vocabulary' })).toHaveAttribute(
        'href',
        '/glossary#reviewed-vocabulary',
      );

      // Sending cleared the composer; Edit question restores the ORIGINAL prompt.
      await expect(composer).toHaveValue('');
      await card.getByTestId('genie-refusal-edit').click();
      await expect(composer).toHaveValue(question);
      await expect(composer).toBeFocused();

      // "This was legitimate": found by its visible label, one POST, hash-only.
      await card.getByRole('button', { name: REPORT_BUTTON_NAME }).click();
      const confirmation = card.locator('.genie-answer__refusal-reported');
      await expect(confirmation).toHaveText('Reported for review');
      await expect(confirmation).toBeFocused();
      await app.settle();
      expect(reports).toHaveLength(1);
      const body = reports[0] as Record<string, unknown>;
      expect(body.question_hash).toMatch(/^[0-9a-f]{64}$/);
      expect(body.question_hash).toBe(refusalReportHash(question));
      expect(body.refusal_reason).toBe(reason);
      expect(Object.keys(body).sort()).toEqual(['conversation_id', 'message_id', 'question_hash', 'refusal_reason']);
      const serialized = JSON.stringify(body).toLowerCase();
      expect(serialized).not.toContain(question.toLowerCase());
      for (const word of question.toLowerCase().split(/\W+/).filter((w) => w.length > 6)) {
        expect(serialized).not.toContain(word);
      }
      expect(mockApi.calls.filter((call) => call.path === REPORT_PATH)).toHaveLength(1);
      // The card stays; the report control is replaced by its confirmation.
      await expect(card.getByTestId('genie-refusal-report')).toHaveCount(0);
      await expect(dialog).toBeVisible();
    });
  }

  for (const theme of ['light', 'dark'] as const satisfies readonly FixtureTheme[]) {
    test(`${theme} theme: the card's small text clears WCAG AA contrast`, async ({ app, page, mockApi }) => {
      const reason: GenieRefusalReason = 'protected_class';
      const question = REFUSED_QUESTIONS[reason];
      mockApi.register('POST', SUBMIT_PATH, () => json<GenieSubmitResult>(refusedSubmit(reason, question)));
      // A non-retryable rejection puts the report-failure line on screen too.
      app.degrade(REPORT_PATH, { status: 422, method: 'POST', body: { detail: 'fixture rejection' } });

      await app.setTheme(theme);
      await app.gotoRoute('/glossary');
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      const card = await askInPanel(page, app, question);
      await card.getByRole('button', { name: REPORT_BUTTON_NAME }).click();
      await expect(card.getByRole('alert')).toHaveText('This refusal could not be reported from this answer.');

      for (const selector of [
        '.genie-answer__refusal-title',
        '.genie-answer__refusal-sentence',
        '.genie-answer__refusal-error',
      ]) {
        const sample = await textContrast(card.locator(selector));
        const measured = `${selector} in ${theme}: ${sample.text} on ${sample.background} = ${sample.ratio.toFixed(2)}:1`;
        test.info().annotations.push({ type: 'contrast', description: measured });
        // Soft, so one run reports every element under AA, not only the first.
        expect.soft(sample.ratio, measured).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
      }
    });
  }

  for (const theme of ['light', 'dark'] as const satisfies readonly FixtureTheme[]) {
    test(`${theme} theme: a keyboard report lands focus on a ringed, AA-contrast confirmation`, async ({ app, page, mockApi }) => {
      const reason: GenieRefusalReason = 'output_policy';
      const question = REFUSED_QUESTIONS[reason];
      mockApi.register('POST', SUBMIT_PATH, () => json<GenieSubmitResult>(refusedSubmit(reason, question)));
      mockApi.register('POST', REPORT_PATH, () => json<GenieRefusalReportResult>(REFUSAL_REPORT_ACCEPTED));

      await app.setTheme(theme);
      await app.gotoRoute('/glossary');
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      const card = await askInPanel(page, app, question);
      // Keyboard activation: the confirmation takes focus programmatically,
      // and only a keyboard-led focus matches :focus-visible. The ring is the
      // global :focus-visible rule (tokens.css); this pins that nothing on
      // the card suppresses it. After a pointer click the outline is none,
      // by design of :focus-visible.
      await card.getByRole('button', { name: REPORT_BUTTON_NAME }).focus();
      await page.keyboard.press('Enter');
      const confirmation = card.locator('.genie-answer__refusal-reported');
      await expect(confirmation).toHaveText('Reported for review');
      await expect(confirmation).toBeFocused();

      const ring = await confirmation.evaluate((element) => {
        const style = getComputedStyle(element);
        return {
          focusVisible: element.matches(':focus-visible'),
          outlineStyle: style.outlineStyle,
          outlineWidth: Number.parseFloat(style.outlineWidth),
        };
      });
      expect(ring.focusVisible).toBe(true);
      expect(ring.outlineStyle, 'the focused confirmation needs a visible ring').not.toBe('none');
      expect(ring.outlineWidth).toBeGreaterThan(0);

      const sample = await textContrast(confirmation);
      const measured = `.genie-answer__refusal-reported in ${theme}: ${sample.text} on ${sample.background} = ${sample.ratio.toFixed(2)}:1`;
      test.info().annotations.push({ type: 'contrast', description: measured });
      expect(sample.ratio, measured).toBeGreaterThanOrEqual(AA_NORMAL_TEXT);
    });
  }

  test('/ask-genie: Edit question restores the refused prompt to the route composer', async ({ app, page, mockApi }) => {
    const reason: GenieRefusalReason = 'unreviewed_criterion';
    const question = REFUSED_QUESTIONS[reason];
    mockApi.register('POST', SUBMIT_PATH, () => json<GenieSubmitResult>(refusedSubmit(reason, question)));

    await app.gotoRoute('/ask-genie');
    const main = page.getByRole('main');
    const composer = main.getByRole('textbox', { name: 'Ask Genie — question' });
    await composer.fill(question);
    await composer.press('Enter');

    const card = main.getByTestId('genie-refusal-card');
    await expect(card).toBeVisible();
    await expect(card).toHaveAttribute('data-refusal-reason', reason);
    // The settled turn cleared the composer; the refused text lives only in the thread.
    await expect(composer).toHaveValue('');
    await card.getByRole('button', { name: 'Edit question' }).click();
    await expect(composer).toHaveValue(question);
    await expect(composer).toBeFocused();
  });

  test('a trusted answer renders no refusal card', async ({ app, mockApi }) => {
    const question = 'How many borrowers are currently in the money?';
    mockApi.register('POST', SUBMIT_PATH, () =>
      json<GenieSubmitResult>({
        completed: true,
        conversation_id: 'fixture-conversation-0001',
        message_id: 'fixture-message-0001',
        question_hash: refusalReportHash(question).slice(0, 16),
        response: {
          conversation_id: 'fixture-conversation-0001',
          message_id: 'fixture-message-0001',
          answer: 'There are 124,946 borrowers in the money.',
          source: 'trusted_sql',
          trusted_assets: ['mip.gold.borrower_360'],
          question_hash: refusalReportHash(question).slice(0, 16),
          metric_value: '124,946',
          table_rows: [],
          follow_up_questions: [],
        },
      }),
    );
    await app.gotoRoute('/glossary');
    const dialog = await app.openGenie();
    const composer = dialog.getByRole('textbox', { name: 'Ask Genie' });
    await composer.fill(question);
    await composer.press('Enter');
    await expect(dialog.getByText('124,946').first()).toBeVisible();
    await expect(dialog.getByTestId('genie-refusal-card')).toHaveCount(0);
  });
});
