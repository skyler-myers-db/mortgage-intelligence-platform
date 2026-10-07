/**
 * W5c lane w5-refusal-capture-sales on the rendered production build at
 * 1440x900, both themes, axe-clean:
 *
 *  (a) the consented-capture step of "This was legitimate" (D-audit-reads-d):
 *      focus moves in, "Report with my question" posts the question exactly
 *      as asked, "Report without it" posts the hash-only body (both asserted
 *      through mockApi.calls), and a hash-only answer reads "Reported without
 *      your question";
 *  (b) the refusal reports on /audit-ledger, as an administrator and as an
 *      auditor: nothing loads with the route; "Show refusal reports" makes one
 *      list read; "Show question" makes exactly one question read per click
 *      and none on hover;
 *  (c) the disposition form has no free-text Notes field and posts no notes
 *      key (D-shell-deviations-g2);
 *  (d) Confirm reject wears the Button loading state while the held reject is
 *      on the wire: aria-busy, the spinner centred, the box unchanged
 *      (motion-08 slice 2; no more-specific .btn display rule defeats
 *      .btn--loading's grid stack in .decision-panel__actions, where the flex
 *      container blockifies inline-grid to grid).
 */
import type { Locator, Page } from '@playwright/test';
import type { GenieRefusalReportResult, GenieSubmitResult } from '../../../src/lib/apiTypes';
import type { RefusalReportQuestionResponse } from '../../../src/lib/apiClients/refusalReports';
import type { SessionResponse } from '../../../src/types';
import { expectAxeClean } from './axe';
import { REFUSAL_REPORT_WITH_TEXT_ID } from './data/admin';
import { PRIMARY_BORROWER } from './data/borrowers';
import { REFUSAL_REPORT_ACCEPTED, REFUSED_QUESTIONS, refusalReportHash, refusedSubmit } from './data/genieRefusal';
import { registerRejectRecorder } from './data/leadQueue';
import { SESSION } from './data/shell';
import { json, normalizeApiPath, type MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const REPORT_PATH = '/api/genie/refusal-report';
const SUBMIT_PATH = '/api/genie/message/submit';
const QUESTION_PATH = /^\/api\/audit\/refusal-reports\/[^/]+\/question$/;
const LIST_PATH = /^\/api\/audit\/refusal-reports$/;
const AUDITOR: SessionResponse = {
  ...SESSION,
  can_access_admin: false,
  can_approve: false,
  can_read_audit: true,
  role_labels: ['Auditor'],
};
const KEPT_QUESTION: RefusalReportQuestionResponse = {
  question_text: 'Which zyrplax borrowers are eligible for a HELOC? Call me at [PHONE-REDACTED].',
  redacted: true,
  captured_at: '2026-07-14T15:20:00Z',
  expires_at: '2026-10-12T15:20:00Z',
};

const callsTo = (mockApi: MockApi, method: string, pattern: RegExp) =>
  mockApi.calls.filter((call) => call.method === method && pattern.test(normalizeApiPath(call.path)));
/** Register the report fixture; its bodies are recorded, its calls counted in mockApi.calls. */
function recordReports(mockApi: MockApi, captured: boolean): Array<Record<string, unknown>> {
  const bodies: Array<Record<string, unknown>> = [];
  mockApi.register('POST', REPORT_PATH, ({ body }) => {
    bodies.push(body as Record<string, unknown>);
    return json<GenieRefusalReportResult>({ ...REFUSAL_REPORT_ACCEPTED, question_captured: captured });
  });
  return bodies;
}
const reportCalls = (mockApi: MockApi) => callsTo(mockApi, 'POST', /^\/api\/genie\/refusal-report$/);

async function refuseInPanel(page: Page, app: { openGenie(): Promise<Locator> }, question: string): Promise<Locator> {
  const dialog = await app.openGenie();
  const composer = dialog.getByRole('textbox', { name: 'Ask Genie' });
  await composer.fill(question);
  await composer.press('Enter');
  const card = dialog.getByTestId('genie-refusal-card');
  await expect(card).toBeVisible();
  return card;
}

for (const theme of FIXTURE_THEMES) {
  test.describe(`refusal capture and the sales loop (${theme})`, () => {
    test.beforeEach(async ({ app }) => {
      // Full-page axe scans of the Lead Queue and the ledger, per theme.
      test.slow();
      await app.setTheme(theme);
    });

    test('(a) the consented step posts the question only on "Report with my question"', async ({ app, page, mockApi }) => {
      const reason = 'unreviewed_criterion' as const;
      const question = REFUSED_QUESTIONS[reason];
      mockApi.register('POST', SUBMIT_PATH, () => json<GenieSubmitResult>(refusedSubmit(reason, question)));
      const bodies = recordReports(mockApi, true);
      await app.gotoRoute('/glossary');
      const card = await refuseInPanel(page, app, question);
      const legit = card.getByRole('button', { name: /^This was legitimate\b/ });
      await expect(legit).toHaveAttribute('aria-expanded', 'false');

      await legit.click();
      const step = card.getByTestId('genie-refusal-confirm');
      await expect(step).toBeVisible();
      await expect(legit).toHaveAttribute('aria-expanded', 'true');
      await expect(legit).toHaveAttribute('aria-controls', (await step.getAttribute('id')) ?? 'missing');
      await expect(step.getByRole('button', { name: 'Report with my question' })).toBeFocused();
      await expect(step.getByTestId('genie-refusal-question')).toHaveText(question);
      await expect(step).toContainText('questions that appear to name a person or a borrower are not kept');
      await expect(step).toContainText('the report itself stays in the audit record');
      expect(reportCalls(mockApi), 'opening the step files nothing').toEqual([]);
      await expectAxeClean(page, { key: { route: 'genie-panel', state: 'refusal-confirm' }, theme, known: {} });

      // Esc closes the step only (not the panel) and returns focus.
      await page.keyboard.press('Escape');
      await expect(step).toHaveCount(0);
      await expect(legit).toBeFocused();
      await expect(page.getByRole('dialog', { name: 'Genie chat' })).toBeVisible();

      await legit.click();
      await card.getByRole('button', { name: 'Report with my question' }).click();
      await expect(card.locator('.genie-answer__refusal-reported')).toHaveText('Reported for review');
      await app.settle();
      expect(reportCalls(mockApi)).toHaveLength(1);
      expect(bodies).toEqual([
        {
          question_hash: refusalReportHash(question),
          refusal_reason: reason,
          conversation_id: expect.anything(),
          message_id: null,
          question_text: question,
        },
      ]);
    });

    test('(a) "Report without it" posts the hash-only body; a hash-only answer to a text post says so', async ({ app, page, mockApi }) => {
      const reason = 'protected_class' as const;
      const question = REFUSED_QUESTIONS[reason];
      mockApi.register('POST', SUBMIT_PATH, () => json<GenieSubmitResult>(refusedSubmit(reason, question)));
      const bodies = recordReports(mockApi, false);
      await app.gotoRoute('/glossary');
      const card = await refuseInPanel(page, app, question);
      await card.getByRole('button', { name: /^This was legitimate\b/ }).click();
      await card.getByRole('button', { name: 'Report without it' }).click();
      await expect(card.locator('.genie-answer__refusal-reported')).toHaveText('Reported for review');
      await app.settle();
      expect(reportCalls(mockApi)).toHaveLength(1);
      const [body] = bodies;
      expect(Object.keys(body).sort()).toEqual(['conversation_id', 'message_id', 'question_hash', 'refusal_reason']);
      expect(JSON.stringify(body).toLowerCase()).not.toContain(question.toLowerCase());

      // A second refusal, reported with the question but kept hash-only.
      const second = REFUSED_QUESTIONS.unreviewed_criterion;
      mockApi.register('POST', SUBMIT_PATH, () => json<GenieSubmitResult>(refusedSubmit('unreviewed_criterion', second)));
      const dialog = page.getByRole('dialog', { name: 'Genie chat' });
      const composer = dialog.getByRole('textbox', { name: 'Ask Genie' });
      await composer.fill(second);
      await composer.press('Enter');
      const latest = dialog.getByTestId('genie-refusal-card').last();
      await latest.getByRole('button', { name: /^This was legitimate\b/ }).click();
      await latest.getByRole('button', { name: 'Report with my question' }).click();
      await expect(latest.locator('.genie-answer__refusal-reported')).toHaveText('Reported without your question');
    });

    for (const [who, session] of [['an administrator', SESSION], ['an auditor', AUDITOR]] as const) {
      test(`(b) ${who} opens the refusal reports explicitly and reads one question per click`, async ({ app, page, mockApi }) => {
        mockApi.register<SessionResponse>('GET', '/api/session', () => json(session));
        mockApi.register<RefusalReportQuestionResponse>(
          'GET',
          '/api/audit/refusal-reports/:id/question',
          () => json(KEPT_QUESTION),
        );
        await app.gotoRoute('/audit-ledger');
        await expect(page.locator('table[aria-label="Audit events"] tbody tr').first()).toBeVisible();
        await app.settle();
        expect(callsTo(mockApi, 'GET', LIST_PATH), 'nothing loads with the route').toEqual([]);

        const toggle = page.getByTestId('refusal-reports-toggle');
        await expect(toggle).toHaveAttribute('aria-expanded', 'false');
        await toggle.click();
        const panel = page.getByTestId('refusal-reports-panel');
        await expect(panel.getByTestId('refusal-report-row')).toHaveCount(2);
        await expect(toggle).toHaveAttribute('aria-expanded', 'true');
        expect(callsTo(mockApi, 'GET', LIST_PATH), 'one list read on the explicit open').toHaveLength(1);
        await expect(panel.locator('a.mono').first()).toHaveAttribute('href', /^\/audit-ledger\?audit_event_id=/);

        const show = panel.getByTestId('refusal-report-show-question');
        await expect(show).toHaveCount(1);
        await show.hover();
        await show.focus();
        await page.waitForTimeout(300);
        expect(callsTo(mockApi, 'GET', QUESTION_PATH), 'no question read on hover or focus').toEqual([]);
        await show.click();
        const kept = panel.getByTestId('refusal-report-question');
        await expect(kept).toContainText('[PHONE-REDACTED]');
        await expect(kept.locator('.chip')).toHaveText('Redacted');
        const reads = callsTo(mockApi, 'GET', QUESTION_PATH);
        expect(reads).toHaveLength(1);
        expect(normalizeApiPath(reads[0].path)).toBe(`/api/audit/refusal-reports/${REFUSAL_REPORT_WITH_TEXT_ID}/question`);
        await expectAxeClean(page, { key: { route: 'audit-ledger', state: 'refusal-reports' }, theme, known: {} });

        await show.click();
        await expect(kept).toHaveCount(0);
        await show.click();
        await expect(panel.getByTestId('refusal-report-question')).toBeVisible();
        expect(callsTo(mockApi, 'GET', QUESTION_PATH), 'exactly one read per click').toHaveLength(2);
        expect(callsTo(mockApi, 'GET', LIST_PATH)).toHaveLength(1);
      });
    }

    test('(c) the disposition form has no Notes field and posts no notes key', async ({ app, page, mockApi }) => {
      const posted: Array<Record<string, unknown>> = [];
      mockApi.register('POST', '/api/leads/:id/disposition', ({ body, params }) => {
        posted.push(body as Record<string, unknown>);
        return json({
          disposition: {
            disposition_id: '4b3a2c1d-0e9f-48a7-b6c5-d4e3f2a1b0c9',
            borrower_id: params.id,
            lo_email: 'lo01@summit-mortgage.example',
            outcome: 'called_left_voicemail',
            attempt_number: 1,
            occurred_at: '2026-07-14T15:20:00Z',
            callback_at: null,
            audit_event_id: '5c4b3a2d-1e0f-49a8-b7c6-d5e4f3a2b1c0',
          },
          audit_event_id: '5c4b3a2d-1e0f-49a8-b7c6-d5e4f3a2b1c0',
        });
      });
      await app.gotoRoute('/lead-queue');
      const id = PRIMARY_BORROWER.borrower_id;
      await page.locator(`tr[data-borrower-row="${id}"] [aria-expanded]`).first().click();
      await page.getByRole('button', { name: `Log call disposition for ${id}` }).click();
      const form = page.locator('form.decision-panel', { hasText: 'Call disposition' });
      await expect(form).toBeVisible();
      await expect(form.locator('textarea')).toHaveCount(0);
      await expect(form.getByText('Notes', { exact: true })).toHaveCount(0);
      await expectAxeClean(page, { key: { route: 'lead-queue', state: 'disposition-panel' }, theme, known: {} });
      await form.getByRole('button', { name: 'Log disposition' }).click();
      await expect.poll(() => posted.length).toBe(1);
      expect(Object.keys(posted[0]).sort()).toEqual(['callback_at', 'lo_email', 'outcome', 'request_id']);
    });

    test('(d) Confirm reject wears the loading state while the held reject is on the wire', async ({ app, page, mockApi }) => {
      const recorder = registerRejectRecorder(mockApi, { holdFirst: 1 });
      const id = PRIMARY_BORROWER.borrower_id;
      await app.gotoRoute('/lead-queue');
      await page.getByTestId(`lead-reject-${id}`).click();
      await page.getByTestId('lead-reject-reason').selectOption('low_intent');
      const confirm = page.getByTestId('lead-reject-confirm');
      const before = await confirm.boundingBox();
      await expect(confirm).not.toHaveAttribute('aria-busy', 'true');

      await confirm.click();
      await expect.poll(() => recorder.bodies.length).toBe(1);
      await expect(confirm).toHaveAttribute('aria-busy', 'true');
      await expect(confirm).toHaveAttribute('aria-disabled', 'true');
      await expect(confirm).toHaveClass(/\bbtn--loading\b/);
      await expect(confirm).toHaveAccessibleName('Confirm reject');
      const during = await confirm.boundingBox();
      expect(during?.width).toBe(before?.width);
      expect(during?.height).toBe(before?.height);
      const layout = await confirm.evaluate((button) => {
        const spinner = button.querySelector('.btn__spinner');
        const box = button.getBoundingClientRect();
        const dot = spinner?.getBoundingClientRect();
        return {
          display: getComputedStyle(button).display,
          dx: dot ? Math.abs(dot.left + dot.width / 2 - (box.left + box.width / 2)) : Number.NaN,
          dy: dot ? Math.abs(dot.top + dot.height / 2 - (box.top + box.height / 2)) : Number.NaN,
        };
      });
      // .btn--loading sets inline-grid; as a flex item of .decision-panel__actions
      // it is blockified to grid. Either way it is the grid stack, never the
      // .btn inline-flex a more-specific display rule would leave.
      expect(layout.display, 'no more-specific rule defeats the grid stack').toMatch(/^(inline-)?grid$/);
      expect(layout.dx, 'the spinner is centred horizontally').toBeLessThanOrEqual(1);
      expect(layout.dy, 'the spinner is centred vertically').toBeLessThanOrEqual(1);
      // A second click while it is on the wire sends nothing more (force:
      // Playwright itself treats aria-disabled as not actionable).
      await confirm.click({ force: true });
      expect(recorder.bodies).toHaveLength(1);
      await expectAxeClean(page, { key: { route: 'lead-queue', state: 'reject-pending' }, theme, known: {} });

      recorder.gate.release();
      await expect(page.getByTestId(`lead-approval-cell-${id}`)).toContainText('Rejected');
    });
  });
}
