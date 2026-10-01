/**
 * The Lead Queue's lifecycle stepper and its one roster read (lane
 * w5-lead-triage-export), proven on the production build at 1440x900:
 *
 *  - critic-06 item (d): an expanded row with an assignment shows the
 *    five-step stepper on one line with the Console open (nothing clipped
 *    or spilling out of its cell, at Linux text widths), the current step
 *    carries aria-current="step", and a recorded outcome moves it;
 *  - runtime-06: Lead Queue then Offer reads GET /api/sales/team once, and
 *    Offer's reviewer list is filled from that one cached roster.
 *
 * Not here: column visibility (?hide=, the HIDE pill, "Review") moved to
 * W5c with tables-05 step 2; the own-advance "Queue updated" pin lives at
 * the rendered layer in lead-queue.freshness.test.tsx (the real hook and
 * control under one QueryClient), since proving it in a browser would need
 * a server-cache window of real polling.
 */
import type { Page } from '@playwright/test';
import { LEADS } from './data/borrowers';
import { ACTIONED_LEAD, registerAssignmentOutcome, registerLeadQueue } from './data/leadQueue';
import type { MockApi } from './mockApi';
import { expect, test } from './test';

/** Off Linux, widen the stepper's chips to Linux Chromium's wider Geist metrics, so a one-line check here holds on CI. */
const LINUX_TEXT_EMULATION = '.lead-row-workflow__steps .chip { letter-spacing: 0.5px; }';

async function expand(page: Page, borrowerId: string): Promise<void> {
  const toggle = page.getByRole('button', { name: `Toggle preview for lead ${borrowerId}` });
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
}

function rosterReads(mockApi: MockApi): number {
  return mockApi.calls.filter((call) => call.method === 'GET' && call.path === '/api/sales/team').length;
}

test.describe('the lifecycle stepper (critic-06 item d)', () => {
  const id = ACTIONED_LEAD.borrower_id;

  test('sits on one line with the Console open; a recorded outcome moves aria-current', async ({ app, mockApi, page }) => {
    registerLeadQueue(mockApi, LEADS.map((lead, index) => (index === 4 ? ACTIONED_LEAD : lead)));
    const recorder = registerAssignmentOutcome(mockApi);
    await app.gotoRoute('/lead-queue');
    if (process.platform !== 'linux') await page.addStyleTag({ content: LINUX_TEXT_EMULATION });
    await app.openConsole();
    await expand(page, id);

    const steps = page.getByTestId(`lead-workflow-${id}`).getByTestId('assignment-lifecycle-steps');
    await expect(steps).toBeVisible();
    await expect(steps.locator('li')).toHaveCount(5);
    await expect(steps.locator('li[aria-current="step"]')).toHaveText('Actioned');
    const layout = await steps.evaluate((list) => {
      const boxes = [...list.querySelectorAll('li')].map((item) => item.getBoundingClientRect());
      const cell = list.closest('td')?.getBoundingClientRect();
      const last = boxes[boxes.length - 1];
      return {
        lines: new Set(boxes.map((box) => Math.round(box.top))).size,
        overflow: list.scrollWidth - list.clientWidth,
        insideCell: cell !== undefined && last.right <= cell.right + 1,
      };
    });
    expect(layout, 'one line, nothing clipped, inside its cell').toEqual({ lines: 1, overflow: 0, insideCell: true });

    const control = page.getByTestId(`lifecycle-advance-${id}`);
    await control.getByRole('button', { name: `Record outcome for ${id}` }).click();
    await control.getByRole('button', { name: 'Success' }).click();
    await expect(steps.locator('li[aria-current="step"]'), 'nothing moves before Record').toHaveText('Actioned');
    await control.getByRole('button', { name: 'Record', exact: true }).click();
    await expect.poll(() => recorder.bodies.length).toBe(1);
    await expect(steps.locator('li[aria-current="step"]')).toHaveText('Outcome recorded');
    await expect(steps.locator('li').nth(3).locator('.chip')).toHaveClass(/chip--success/);
  });
});

test.describe('one roster read (runtime-06)', () => {
  test('Lead Queue then Offer reads GET /api/sales/team once; Offer lists the reviewers from it', async ({ app, mockApi, page }) => {
    const borrowerId = LEADS[0].borrower_id;
    await app.gotoRoute('/lead-queue');
    await expect.poll(() => rosterReads(mockApi), 'the queue reads the roster').toBe(1);
    await expand(page, borrowerId);
    await page.getByTestId(`lead-build-offer-${borrowerId}`).click();
    await expect(page).toHaveURL(/\/offer-orchestrator/);
    // Non-vacuous: Offer really consumed the roster (more than "Unassigned").
    await expect.poll(() => page.locator('#lo-assign option').count(), 'Offer lists reviewers').toBeGreaterThan(1);
    expect(rosterReads(mockApi), 'one shared roster entry: no second read').toBe(1);
    await app.settle();
  });
});
