/**
 * Rendered-layer proofs for W5d w5-genie-provenance-tiles (1440x900,
 * production build, dark and light): genie-10 phase 2 and owner ruling O2.
 *
 *  (a) single turn: the cell figure carries .genie-md-figure, the derived
 *      total and the stated threshold do not; hovering the figure shows the
 *      aria-hidden evidence card (title, column, where it is listed); the
 *      proof drawer reads "3 of 3 answer figures verified" with the
 *      provenance labels;
 *  (b) O2: a cross-check note appended after the claims check is disclosed
 *      in the answer and in Known data gaps, never counted ("2 of 2"), never
 *      marked, and the prose's figure that coincides with the note's is not
 *      marked either (its full-text count no longer matches);
 *  (c) sweep: the summary and each section mark their own figures; a
 *      withheld section and the Partial research preview mark none (a first
 *      turn loads the lazy mark chunk, so the preview check cannot pass on
 *      the Suspense fallback);
 *  (d) an answer recorded before provenance: no mark, derivation labels;
 *  (e) axe on the answer with a card open and on the proof drawer;
 *  (f) forced colours: the figure paints the surrounding ink and canvas,
 *      never the system Mark highlighter;
 *  (g) no audited read after the natural load, no /api/genie/actions call
 *      and no export receipt.
 *
 * The delivery-04 events stream and the wow-ai-5 living tiles were not built
 * (their verdicts were pending at W5d branch start), so this spec has no
 * stream or tile case.
 */
import type { Locator, Page } from '@playwright/test';
import { expectAxeClean, KNOWN_VIOLATIONS } from './axe';
import { genieRevealSectionsFixture, registerGenieJob, type GenieJobStep } from './data/genieJobs';
import {
  DIVERGENCE_COINCIDENT,
  DIVERGENCE_NOTE,
  DIVERGENCE_SCORE,
  SINGLE_BOUND,
  SINGLE_CELL,
  SINGLE_DERIVED,
  SWEEP_CONTACTABLE_CELL,
  SWEEP_SECTION_CELL,
  SWEEP_SUMMARY_CELL,
  SWEEP_SUMMARY_SUM,
  WITHHELD_TITLE,
  provenanceDivergenceAnswer,
  provenanceLegacyAnswer,
  provenanceSingleAnswer,
  provenanceSweepAnswer,
} from './data/genieProvenance';
import { GENIE_QUESTION, registerGenieTurn } from './data/genieTurn';
import type { MockApi } from './mockApi';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const STAGE_WAIT = { timeout: 20_000 };

function main(page: Page): Locator {
  return page.locator('#main-content');
}

function answer(page: Page): Locator {
  return main(page).locator('.genie-thread .genie-answer').first();
}

/** The newest answer in the thread (case (c) asks twice). */
function lastAnswer(page: Page): Locator {
  return main(page).locator('.genie-thread .genie-answer').last();
}

async function askOnRoute(page: Page): Promise<void> {
  await main(page).getByRole('textbox', { name: 'Ask Genie — question' }).fill(GENIE_QUESTION);
  await main(page).getByRole('button', { name: 'Ask Genie', exact: true }).click();
}

async function openProof(page: Page): Promise<Locator> {
  const opener = main(page).getByRole('button', { name: 'Show proof' }).first();
  await expect(opener).toBeVisible(STAGE_WAIT);
  await opener.click();
  const drawer = page.locator('dialog.genie-proof-drawer');
  await expect(drawer).toHaveClass(/is-open/);
  return drawer;
}

/** The marks in `scope` (the mark loads lazily, so assertions retry). */
function marks(scope: Locator): Locator {
  return scope.locator('mark.genie-md-figure');
}

function forbiddenCalls(mockApi: MockApi, since: number): string[] {
  return mockApi.calls
    .slice(since)
    .map((call) => call.path)
    .filter((path) => /\/api(\/v1)?\/genie\/(actions|export-receipt)/.test(path));
}

test.describe('genie-10 phase 2: figures that match one returned cell', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`(a)(e)(g) ${theme}: the cell figure is marked, the card shows on hover, the proof reads 3 of 3`, async ({ app, page, mockApi }) => {
      registerGenieTurn(mockApi, { holdProgress: false, answer: provenanceSingleAnswer() });
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie');
      const natural = markNaturalLoad(mockApi);
      await askOnRoute(page);
      const body = answer(page);
      await expect(body).toContainText(SINGLE_DERIVED, STAGE_WAIT);

      await expect(marks(body)).toHaveText([SINGLE_CELL]);
      await expect(body).toContainText(`more than ${SINGLE_BOUND}`);
      const mark = marks(body);
      await expect(mark).not.toHaveAttribute('tabindex', /.*/);
      await expect(mark).not.toHaveAttribute('title', /.*/);

      await mark.hover();
      const card = page.locator('.evidence-hovercard:not(.is-closing)');
      await expect(card).toBeVisible();
      await expect(card).toHaveAttribute('aria-hidden', 'true');
      await expect(card.locator('.evidence-hovercard__title')).toHaveText('Matches a returned value');
      await expect(card.locator('.evidence-hovercard__signal')).toContainText('Column');
      await expect(card.locator('.evidence-hovercard__signal-value')).toHaveText('In the Money');
      await expect(card.locator('.evidence-hovercard__cta')).toHaveText('Listed under Verified figures in the proof');
      await expectAxeClean(page, { key: { route: 'ask-genie', state: 'genie-figure-hover' }, theme, known: KNOWN_VIOLATIONS, include: '#main-content' });

      const drawer = await openProof(page);
      await expect(drawer).toContainText('3 of 3 answer figures verified');
      const labels = drawer.locator('.genie-claims__derivation');
      await expect(labels).toHaveText([
        'Matches a returned value in In the Money',
        'Derived from the returned rows (an average, range, change or running total)',
        'A threshold the returned values satisfy',
      ]);
      await expectAxeClean(page, { key: { route: 'ask-genie', state: 'genie-proof-provenance' }, theme, known: KNOWN_VIOLATIONS, include: 'dialog.genie-proof-drawer' });
      expectNoAuditedReadSince(mockApi, natural, 'the figure highlight');
      expect(forbiddenCalls(mockApi, natural)).toEqual([]);
    });
  }

  for (const theme of FIXTURE_THEMES) {
    test(`(b) ${theme}: O2: the cross-check note is disclosed, never counted and never marked`, async ({ app, page, mockApi }) => {
      registerGenieTurn(mockApi, { holdProgress: false, answer: provenanceDivergenceAnswer() });
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie');
      const natural = markNaturalLoad(mockApi);
      await askOnRoute(page);
      const body = answer(page);
      await expect(body).toContainText('overlaps 3 of 10 borrowers', STAGE_WAIT);

      // The note's "10" coincides with the prose's: neither is marked.
      await expect(marks(body)).toHaveText([DIVERGENCE_SCORE]);
      const noteParagraph = body.locator('p', { hasText: 'Governed cross-check' });
      await expect(noteParagraph.locator('mark')).toHaveCount(0);
      await expect(body.locator('p', { hasText: `${DIVERGENCE_COINCIDENT} of the ranked borrowers` }).locator('mark')).toHaveText([
        DIVERGENCE_SCORE,
      ]);

      const drawer = await openProof(page);
      await expect(drawer).toContainText('2 of 2 answer figures verified');
      await expect(drawer.locator('.genie-claims__token')).toHaveText([DIVERGENCE_SCORE, DIVERGENCE_COINCIDENT]);
      await expect(drawer).toContainText(DIVERGENCE_NOTE);
      expectNoAuditedReadSince(mockApi, natural, 'the divergence note');
      expect(forbiddenCalls(mockApi, natural)).toEqual([]);
    });

    test(`(c) ${theme}: sweep: summary and sections mark their own figures; a withheld section and the preview mark none`, async ({
      app,
      page,
      mockApi,
    }) => {
      registerGenieTurn(mockApi, { holdProgress: false, answer: provenanceSingleAnswer() });
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie');
      const natural = markNaturalLoad(mockApi);

      // Turn 1 loads the lazy mark chunk: its cell figure is marked. Without
      // this, the preview check below would pass on the Suspense fallback
      // (the plain token) even if a provider reached the preview.
      await askOnRoute(page);
      await expect(marks(answer(page))).toHaveText([SINGLE_CELL], STAGE_WAIT);

      // Turn 2, a sweep whose Partial research preview shows the SAME
      // section text the final answer then marks.
      const sweep = provenanceSweepAnswer();
      const previewSections = genieRevealSectionsFixture(3).map((section, index) => ({
        ...section,
        answer: (sweep.sections ?? [])[index]?.answer ?? section.answer,
      }));
      const steps: readonly GenieJobStep[] = [
        { stage: 'queued' },
        { stage: 'researching', parts: [3, 7], reveal: { verified: 3, rev: 1, sections: previewSections } },
        'succeeded',
      ];
      const job = registerGenieJob(mockApi, { deep: true, steps, answer: sweep });
      await askOnRoute(page);

      job.next();
      const preview = main(page).getByRole('group', { name: 'Partial research' });
      await expect(preview).toBeVisible(STAGE_WAIT);
      await expect(preview).toContainText(SWEEP_SECTION_CELL);
      // The chunk is loaded, so a mark would render synchronously here.
      await expect(preview.locator('mark')).toHaveCount(0);

      job.next();
      const body = lastAnswer(page);
      await expect(body.locator('.genie-answer__section')).toHaveCount(3, STAGE_WAIT);
      const summary = body.locator('.genie-answer__sections > .genie-md-p--heading + p');
      await expect(summary).toContainText(SWEEP_SUMMARY_SUM);
      await expect(marks(summary)).toHaveText([SWEEP_SUMMARY_CELL]);
      const sections = body.locator('.genie-answer__section');
      await expect(marks(sections.nth(0))).toHaveText([SWEEP_SECTION_CELL]);
      await expect(marks(sections.nth(1))).toHaveText([SWEEP_CONTACTABLE_CELL]);
      await expect(sections.nth(2)).toContainText(WITHHELD_TITLE);
      await expect(marks(sections.nth(2))).toHaveCount(0);
      expectNoAuditedReadSince(mockApi, natural, 'the sweep highlight');
      expect(forbiddenCalls(mockApi, natural)).toEqual([]);
    });

    test(`(d) ${theme}: an answer recorded before provenance marks nothing and keeps the derivation labels`, async ({ app, page, mockApi }) => {
      registerGenieTurn(mockApi, { holdProgress: false, answer: provenanceLegacyAnswer() });
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie');
      await askOnRoute(page);
      const body = answer(page);
      await expect(body).toContainText(SINGLE_CELL, STAGE_WAIT);
      const drawer = await openProof(page);
      await expect(body.locator('mark')).toHaveCount(0);
      await expect(drawer).toContainText('3 of 3 answer figures verified');
      await expect(drawer.locator('.genie-claims__derivation')).toHaveText([
        'Matches a returned value',
        'Derived from the returned rows (a total, average, share or change)',
        'A threshold the returned values satisfy',
      ]);
    });

    test(`(f) ${theme}: forced colours: the figure paints the surrounding ink, never the Mark highlighter`, async ({ app, page, mockApi }) => {
      registerGenieTurn(mockApi, { holdProgress: false, answer: provenanceSingleAnswer() });
      await app.setTheme(theme);
      await app.gotoRoute('/ask-genie');
      await askOnRoute(page);
      const mark = marks(answer(page));
      await expect(mark).toBeVisible(STAGE_WAIT);
      await page.emulateMedia({ forcedColors: 'active' });
      const paint = await mark.evaluate((el) => {
        const probe = (value: string) => {
          const node = document.createElement('span');
          node.style.backgroundColor = value;
          node.style.color = value;
          document.body.appendChild(node);
          const resolved = getComputedStyle(node).backgroundColor;
          node.remove();
          return resolved;
        };
        const own = getComputedStyle(el);
        const parent = getComputedStyle(el.parentElement as HTMLElement);
        return {
          color: own.color,
          parentColor: parent.color,
          background: own.backgroundColor,
          canvas: probe('Canvas'),
          mark: probe('Mark'),
          markText: probe('MarkText'),
        };
      });
      expect(paint.color).toBe(paint.parentColor);
      expect(paint.background).toBe(paint.canvas);
      expect(paint.background).not.toBe(paint.mark);
      await page.emulateMedia({ forcedColors: 'none' });
    });
  }
});
