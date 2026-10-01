/**
 * The pinned-focus guard, in each engine the fixture harness runs
 * (w5-approval-core; manual check 2026-09-30, integrator decision 5).
 *
 * WebKit 26 scrolled the Lead Queue's `.tbl-wrap` to its inline end when a
 * control in the pinned Approval column took focus with the Console open
 * (element.focus(): scrollLeft 0 -> 284), although the column is sticky and
 * the control was already in view; the row's borrower id slid out of sight.
 * Chromium never scrolled there. useTableScrollClearance's guard restores
 * the offset a focus in the pinned cell moved.
 *
 * Collected by fixture-chromium (every *.fixture.spec.ts) and by the
 * fixture-webkit project's cross-engine match. Proof: focus() each pinned
 * control at scrollLeft 0 and at the middle of the overflow; two frames
 * later scrollLeft is where it started (+-1). The non-vacuity twin, which
 * shows an engine does scroll there and the guard puts it back, is the
 * ZERO_PIN_MARGIN test in lead-queue.fixture.spec.ts.
 */
import type { Locator, Page } from '@playwright/test';
import { PRIMARY_BORROWER } from './data/borrowers';
import { registerQueueLayoutLeads } from './data/queueLayout';
import { expect, test } from './test';

const ELIGIBLE = PRIMARY_BORROWER.borrower_id;

function tableWrap(page: Page): Locator {
  return page.getByRole('region', { name: 'Ranked borrowers table scroll region' });
}

/**
 * focus() the control at `start`; scrollLeft two frames later, plus the trail
 * of offsets around the focus (the set, each scroll event, right after focus()
 * and settled) so a failure says which side moved the table.
 */
async function focusAt(control: Locator, start: number): Promise<{ settled: number; trail: string }> {
  return control.evaluate(async (element, left) => {
    const wrap = element.closest<HTMLElement>('.tbl-wrap');
    if (!wrap) throw new Error('the control is not in the table scroller');
    const trail: string[] = [];
    const onScroll = () => trail.push(`scroll ${wrap.scrollLeft}`);
    wrap.addEventListener('scroll', onScroll);
    wrap.scrollLeft = left;
    trail.push(`set ${left} -> ${wrap.scrollLeft} (overflow ${wrap.scrollWidth - wrap.clientWidth})`);
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    trail.push(`before focus ${wrap.scrollLeft}`);
    (element as HTMLElement).focus();
    trail.push(`after focus() ${wrap.scrollLeft}`);
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    wrap.removeEventListener('scroll', onScroll);
    const box = element.getBoundingClientRect();
    const port = wrap.getBoundingClientRect();
    trail.push(`settled ${wrap.scrollLeft}; control ${box.left.toFixed(1)}-${box.right.toFixed(1)} in port ${(port.left + wrap.clientLeft).toFixed(1)}-${(port.left + wrap.clientLeft + wrap.clientWidth).toFixed(1)}`);
    return { settled: wrap.scrollLeft, trail: trail.join(' | ') };
  }, start);
}

test('a focus on a pinned Approve or Reject leaves the table where it was, Console open', async ({ app, mockApi, page }) => {
  registerQueueLayoutLeads(mockApi);
  await app.gotoRoute('/lead-queue');
  await app.openConsole();
  const overflow = await tableWrap(page).evaluate((wrap) => wrap.scrollWidth - wrap.clientWidth);
  expect(overflow, 'precondition: the Console narrows the table into a horizontal scroll').toBeGreaterThan(40);
  const middle = Math.round(overflow / 2);

  for (const testId of [`lead-approve-${ELIGIBLE}`, `lead-reject-${ELIGIBLE}`]) {
    const control = page.getByTestId(testId);
    // The middle first: the last focus is at scrollLeft 0, where the id shows.
    for (const start of [middle, 0]) {
      const { settled, trail } = await focusAt(control, start);
      await expect(control).toBeFocused();
      expect(Math.abs(settled - start), `${testId} focused at scrollLeft ${start}: ${trail}`).toBeLessThanOrEqual(1);
    }
  }
  // The row's borrower id is still inside the scrollport.
  const idInView = await page.getByRole('button', { name: `Toggle preview for lead ${ELIGIBLE}` }).evaluate((id) => {
    const port = id.closest('.tbl-wrap')?.getBoundingClientRect();
    return port ? id.getBoundingClientRect().left >= port.left - 1 : false;
  });
  expect(idInView).toBe(true);
});
