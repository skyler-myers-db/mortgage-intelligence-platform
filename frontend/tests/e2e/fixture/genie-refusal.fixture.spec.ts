/**
 * Refusal card in the floating Genie panel (audit 2026-09-21 `genie-05`).
 *
 * For every coarse refusal family the panel receives an inline governed
 * refusal and must render, under the refusal sentence, the card: the
 * family's plain-language sentence, the pre-validated rephrase chips (the
 * SAME texts tests/unit/test_genie_refusal_chips.py runs through the real
 * guard battery, read here from the shared JSON), "Edit question" that puts
 * the ORIGINAL prompt back in the composer, the reviewed-vocabulary link, and
 * "This was legitimate", which POSTs exactly once with a 64-hex
 * `question_hash` and no question text anywhere in the body.
 */
import fs from 'node:fs';
import path from 'node:path';
import type { GenieRefusalReason } from '../../../src/types';
import type { GenieRefusalReportResult, GenieSubmitResult } from '../../../src/lib/apiTypes';
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

/** A distinctive fragment of each family's card sentence (GenieRefusalCard copy). */
const SENTENCE_FRAGMENT: Record<GenieRefusalReason, string> = {
  protected_class: 'not on protected-class attributes',
  unreviewed_criterion: 'not in the reviewed Module 0 vocabulary',
  pii_request: 'masked borrower IDs',
  instruction_override: 'Ask the analytics question directly',
  outreach_instruction: 'Offer Orchestrator',
  scope_bypass: 'reads governed Module 0 assets only',
  out_of_scope: 'book of business',
  output_policy: 'could not verify against trusted SQL',
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

test.describe('Genie refusal card', () => {
  for (const reason of REFUSAL_FAMILIES) {
    test(`${reason}: sentence, validated chips, Edit question and a hash-only report`, async ({ app, page, mockApi }, testInfo) => {
      const question = REFUSED_QUESTIONS[reason];
      const chips = readValidatedChips(testInfo.config.configFile);
      const reports: unknown[] = [];
      mockApi.register('POST', '/api/genie/message/submit', () =>
        json<GenieSubmitResult>(refusedSubmit(reason, question)),
      );
      mockApi.register('POST', REPORT_PATH, (request) => {
        reports.push(request.body);
        return json<GenieRefusalReportResult>(REFUSAL_REPORT_ACCEPTED);
      });

      await app.setTheme('light');
      await app.gotoRoute('/glossary');
      const dialog = await app.openGenie();
      const composer = dialog.getByRole('textbox', { name: 'Ask Genie' });
      await composer.fill(question);
      await composer.press('Enter');

      const card = dialog.getByTestId('genie-refusal-card');
      await expect(card).toBeVisible();
      await expect(card).toHaveAttribute('data-refusal-reason', reason);
      await expect(card).toContainText(SENTENCE_FRAGMENT[reason]);
      await expect(card.getByTestId('genie-refusal-chip')).toHaveCount(chips[reason].length);
      expect(await card.getByTestId('genie-refusal-chip').locator('.filter__value').allTextContents()).toEqual(
        chips[reason],
      );
      await expect(card.getByRole('link', { name: 'Reviewed vocabulary' })).toHaveAttribute(
        'href',
        '/glossary#reviewed-vocabulary',
      );

      // Sending cleared the composer; Edit question restores the ORIGINAL prompt.
      await expect(composer).toHaveValue('');
      await card.getByTestId('genie-refusal-edit').click();
      await expect(composer).toHaveValue(question);
      await expect(composer).toBeFocused();

      // "This was legitimate": one POST, hash-only.
      await card.getByTestId('genie-refusal-report').click();
      await expect(card.getByText('Reported for review')).toBeVisible();
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
      await expect(page.getByRole('dialog', { name: 'Genie chat' })).toBeVisible();
    });
  }

  test('a trusted answer renders no refusal card', async ({ app, mockApi }) => {
    const question = 'How many borrowers are currently in the money?';
    mockApi.register('POST', '/api/genie/message/submit', () =>
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
