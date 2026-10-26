/**
 * The pinned-focus clearance, in each engine the fixture harness runs
 * (w5-approval-core; manual check 2026-09-30, integrator decision 5).
 *
 * WebKit 26 scrolled the Lead Queue's `.tbl-wrap` to its inline end when a
 * control in the pinned Approval column took focus with the Console open
 * (element.focus(): scrollLeft 0 -> 284), although the column is sticky and
 * the control was already in view; the row's borrower id slid out of sight.
 * Chromium never scrolled there. WebKit reveals against the scroller's
 * inline-end padding, whatever the control's scroll-margin, so LeadTable.css
 * drops that padding while focus is in the pin; useTableScrollClearance's
 * guard, which restores an offset a focus in the pinned cell moved, stays as
 * the fallback. The guard alone lost a race in CI (W5d main run 38692903624,
 * W5e run 40311237233; Linux WebKit, intermittent on macOS): WebKit moved the
 * table after the guard's frame and dispatched the scroll event the guard
 * waits for after this check's two frames ("settled 284").
 *
 * Collected by fixture-chromium (every *.fixture.spec.ts) and by the
 * fixture-webkit project's cross-engine match. Proof: with focus elsewhere
 * first, focus() each pinned control at the middle of the overflow and at
 * scrollLeft 0; two frames later scrollLeft is where it started (+-1), and
 * the table never left it on the way: a window capture-phase scroll listener
 * (it runs before any listener on the scroller, so no restore can hide a
 * move), a frame chain queued before focus() and a timer chain read it for
 * PEAK_WINDOW_MS. The non-vacuity twins, which show an engine does scroll
 * there without the clearance, are the pinned-controls non-vacuity tests in
 * queue-clearance.cross-engine and lead-queue.fixture.spec.ts.
 */
import type { Locator, Page } from '@playwright/test';
import { PRIMARY_BORROWER } from './data/borrowers';
import { registerQueueLayoutLeads } from './data/queueLayout';
import { expect, test } from './test';

const ELIGIBLE = PRIMARY_BORROWER.borrower_id;

/** How long after focus() the observers keep reading scrollLeft: past a late reveal and its late scroll event. */
const PEAK_WINDOW_MS = 300;

function tableWrap(page: Page): Locator {
  return page.getByRole('region', { name: 'Ranked borrowers table scroll region' });
}

/**
 * Focus something else, then focus() the control at `start`. Returns
 * scrollLeft two frames later (`settled`), the offset farthest from `start`
 * any observer read within PEAK_WINDOW_MS (`peak`), and the trail of offsets
 * around the focus (the set, each scroll event, right after focus(), settled)
 * so a failure says which side moved the table.
 */
async function focusAt(control: Locator, start: number): Promise<{ settled: number; peak: number; trail: string }> {
  return control.evaluate(async (element, { left, windowMs }) => {
    const wrap = element.closest<HTMLElement>('.tbl-wrap');
    if (!wrap) throw new Error('the control is not in the table scroller');
    // focus() on the element that already has focus does nothing.
    (document.activeElement as HTMLElement | null)?.blur();
    const trail: string[] = [];
    let peak = left;
    const read = (): number => {
      const offset = wrap.scrollLeft;
      if (Math.abs(offset - left) > Math.abs(peak - left)) peak = offset;
      return offset;
    };
    const onScroll = (event: Event) => {
      if (event.target === wrap) trail.push(`scroll ${read()}`);
    };
    window.addEventListener('scroll', onScroll, { capture: true });
    wrap.scrollLeft = left;
    trail.push(`set ${left} -> ${wrap.scrollLeft} (overflow ${wrap.scrollWidth - wrap.clientWidth})`);
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    trail.push(`before focus ${wrap.scrollLeft}`);
    peak = wrap.scrollLeft;
    // Queued before focus(): in each frame this read runs ahead of any rAF a
    // focus handler queues (the guard's restore).
    let watching = true;
    const frame = () => {
      read();
      if (watching) requestAnimationFrame(frame);
    };
    requestAnimationFrame(frame);
    const tick = () => {
      read();
      if (watching) setTimeout(tick, 0);
    };
    (element as HTMLElement).focus();
    const focusedAt = performance.now();
    trail.push(`after focus() ${read()}`);
    setTimeout(tick, 0);
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const settled = wrap.scrollLeft;
    const rest = windowMs - (performance.now() - focusedAt);
    if (rest > 0) await new Promise((resolve) => setTimeout(resolve, rest));
    watching = false;
    window.removeEventListener('scroll', onScroll, { capture: true });
    const box = element.getBoundingClientRect();
    const port = wrap.getBoundingClientRect();
    trail.push(`settled ${settled}; peak ${peak}; control ${box.left.toFixed(1)}-${box.right.toFixed(1)} in port ${(port.left + wrap.clientLeft).toFixed(1)}-${(port.left + wrap.clientLeft + wrap.clientWidth).toFixed(1)}`);
    return { settled, peak, trail: trail.join(' | ') };
  }, { left: start, windowMs: PEAK_WINDOW_MS });
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
      const { settled, peak, trail } = await focusAt(control, start);
      await expect(control).toBeFocused();
      expect(Math.abs(settled - start), `${testId} focused at scrollLeft ${start}: ${trail}`).toBeLessThanOrEqual(1);
      expect(Math.abs(peak - start), `${testId} focused at scrollLeft ${start} moved the table on the way: ${trail}`).toBeLessThanOrEqual(1);
    }
  }
  // The row's borrower id is still inside the scrollport.
  const idInView = await page.getByRole('button', { name: `Toggle preview for lead ${ELIGIBLE}` }).evaluate((id) => {
    const port = id.closest('.tbl-wrap')?.getBoundingClientRect();
    return port ? id.getBoundingClientRect().left >= port.left - 1 : false;
  });
  expect(idInView).toBe(true);
});
