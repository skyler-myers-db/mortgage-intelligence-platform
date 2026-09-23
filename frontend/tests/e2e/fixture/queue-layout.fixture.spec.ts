/**
 * Rendered-layer proofs for the wave-1b Lead Queue layout lane (audit
 * visual-01, tables-01, flow-01, tables-04, tables-05), at the
 * contracted 1440x900 viewport.
 *
 * "On screen" is proven twice for every control that matters: Playwright's
 * toBeInViewport (ratio 1) AND the TOPMOST element at the control's centre
 * (document.elementFromPoint) is the control, so a control painted under the
 * pinned Approval column, under the Console, or clipped by the table's
 * scrollport does not pass.
 */
import type { Locator, Page } from '@playwright/test';
import { PRIMARY_BORROWER } from './data/borrowers';
import { DNC_LEAD, UNRESOLVED_OWNER_LEAD, registerQueueLayoutLeads } from './data/queueLayout';
import { FIXTURE_THEMES } from './routes';
import { expect, test } from './test';

const ROWS = 'table.lead-table__table tbody tr[aria-rowindex]:not(.tbl__expand)';
const WORKFLOW_HEADERS = ['Relationship', 'Assigned to', 'Outreach', 'Last touch'] as const;

/** The topmost element at the locator's centre is the element itself (or inside it). */
async function isTopmostAtCentre(target: Locator): Promise<boolean> {
  return target.evaluate((el) => {
    const rect = el.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return hit !== null && (hit === el || el.contains(hit));
  });
}

async function expectReachable(target: Locator, what: string): Promise<void> {
  await expect(target, `${what} is fully inside the viewport`).toBeInViewport({ ratio: 1 });
  expect(await isTopmostAtCentre(target), `${what} is the topmost element at its centre`).toBe(true);
}

async function wrapOverflow(page: Page): Promise<{ scrollWidth: number; clientWidth: number }> {
  return page.locator('.tbl-wrap').evaluate((wrap) => ({ scrollWidth: wrap.scrollWidth, clientWidth: wrap.clientWidth }));
}

async function rowHeight(row: Locator): Promise<number> {
  return row.evaluate((el) => el.getBoundingClientRect().height);
}

test.describe('the ranked-borrower table fits 1440x900 with the Console closed', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`Score and Approve of the first 8 rows are on screen, unoccluded, with no horizontal scroll (${theme})`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      const rows = page.locator(ROWS);
      await expect(rows.nth(7)).toBeVisible();
      // The 16-pill filter wall still sits above the table in this slice:
      // scroll the table to the top of the page before sampling.
      await page.locator('.main').evaluate((main) => {
        const wrap = document.querySelector('.tbl-wrap');
        if (wrap) main.scrollTop += wrap.getBoundingClientRect().top - 140;
      });

      const overflow = await wrapOverflow(page);
      expect(overflow.scrollWidth, '.tbl-wrap has no horizontal overflow').toBeLessThanOrEqual(overflow.clientWidth);

      let approveButtons = 0;
      for (let index = 0; index < 8; index += 1) {
        const row = rows.nth(index);
        await expectReachable(row.locator('.score'), `row ${index + 1} Score`);
        const approve = row.locator('[data-testid^="lead-approve-"]');
        if ((await approve.count()) === 0) {
          // An already-decided row shows its chip in the same column.
          await expectReachable(row.locator('[data-testid^="lead-approval-cell-"] .chip'), `row ${index + 1} approval chip`);
          continue;
        }
        approveButtons += 1;
        await expectReachable(approve, `row ${index + 1} Approve`);
      }
      expect(approveButtons, 'the fixture queue has pending rows to approve').toBeGreaterThanOrEqual(7);
    });
  }
});

test.describe('one-line rows and the merged Status cell', () => {
  test('a default row reads an em dash, never a default-value chip, and the compliance flags stay in-row', async ({ app, page, mockApi }) => {
    registerQueueLayoutLeads(mockApi);
    await app.gotoRoute('/lead-queue');

    const quiet = page.getByTestId(`lead-status-${PRIMARY_BORROWER.borrower_id}`);
    await expect(quiet).toBeVisible();
    await expect(quiet.locator('[aria-hidden="true"]')).toHaveText('—');
    await expect(quiet.locator('.chip')).toHaveCount(0);
    for (const word of ['Unassigned', 'Untouched', 'Other', 'None']) {
      await expect(quiet, `no "${word}" chip`).not.toContainText(word);
    }

    const dncStatus = page.getByTestId(`lead-status-${DNC_LEAD.borrower_id}`);
    const dnc = dncStatus.locator('.chip', { hasText: /^DNC$/ });
    await expectReachable(dnc, 'the DNC chip');
    // The workflow states fold into +n; the compliance flag never does.
    const more = dncStatus.locator('.lead-table__more');
    await expect(more).toHaveText('+3');
    const spoken = (await more.getAttribute('aria-label')) ?? '';
    expect(spoken).toMatch(/^3 more statuses: /);
    expect(spoken).toContain('Assigned to: Summit LO 01');
    expect(spoken).not.toContain('DNC');
    // DNC plus workflow state still fits one row at the density token.
    expect(await rowHeight(page.locator(ROWS).nth(2))).toBe(44);

    const unresolved = page.getByTestId(`lead-status-${UNRESOLVED_OWNER_LEAD.borrower_id}`);
    await expectReachable(unresolved.locator('.chip', { hasText: 'Owner unresolved' }), 'the Owner unresolved chip');

    // The timestamps, the lifecycle control and Log live in the expanded row.
    const dncRow = page.locator(ROWS).nth(2);
    await expect(dncRow.getByRole('button', { name: /Log call disposition/ })).toHaveCount(0);
    await more.click();
    const workflow = page.getByTestId(`lead-workflow-${DNC_LEAD.borrower_id}`);
    await expect(workflow).toBeVisible();
    await expect(workflow.getByRole('button', { name: `Log call disposition for ${DNC_LEAD.borrower_id}` })).toBeVisible();
    await expect(workflow).toContainText('Summit LO 01');
    await expect(workflow).toContainText('12d aging');
  });

  test('the Console density toggle changes the queue: 44px comfortable rows, 36px compact', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    const row = page.locator(ROWS).nth(2);
    expect(await rowHeight(row), 'comfortable row at --row-h').toBe(44);
    const panel = await app.openConsole();
    await panel.getByRole('button', { name: 'Compact' }).click();
    await expect.poll(() => rowHeight(row), 'compact row at --row-h').toBe(36);
  });
});

test.describe('the pinned Approval column', () => {
  test('with the Console open the table scrolls, and Approve stays reachable at the scrollport edge', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    await app.openConsole();
    const overflow = await wrapOverflow(page);
    expect(overflow.scrollWidth, 'precondition: the Console squeezes the table into horizontal scroll').toBeGreaterThan(overflow.clientWidth);

    const approve = page.getByTestId(`lead-approve-${PRIMARY_BORROWER.borrower_id}`);
    await expectReachable(approve, 'Approve at scrollLeft 0');
    const header = page.locator('.lead-table__approval-header');
    await expectReachable(header, 'the Approval header');

    const wrap = page.locator('.tbl-wrap');
    await wrap.evaluate((el) => { el.scrollLeft = el.scrollWidth; });
    await expect.poll(() => wrap.evaluate((el) => el.scrollLeft)).toBeGreaterThan(0);
    await expectReachable(approve, 'Approve at the end of the horizontal scroll');
  });
});

test.describe('column presets', () => {
  test('?view=sales-ops adds the four workflow columns and keeps Approve pinned; the View control writes the URL', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue?view=sales-ops');
    for (const name of WORKFLOW_HEADERS) {
      await expect(page.getByRole('columnheader', { name: new RegExp(name) }), `${name} column`).toBeVisible();
    }
    await expect(page.getByTestId('lead-status-sort'), 'no merged Status column in Sales ops').toHaveCount(0);
    const overflow = await wrapOverflow(page);
    expect(overflow.scrollWidth, 'Sales ops accepts horizontal scroll').toBeGreaterThan(overflow.clientWidth);
    await expectReachable(page.getByTestId(`lead-approve-${PRIMARY_BORROWER.borrower_id}`), 'Approve in Sales ops');

    const menu = await app.openFilterMenu('VIEW');
    await menu.getByRole('option', { name: 'Default' }).click();
    await expect.poll(() => new URL(page.url()).searchParams.has('view')).toBe(false);
    await expect(page.getByTestId('lead-status-sort')).toBeVisible();
    for (const name of WORKFLOW_HEADERS) {
      await expect(page.getByRole('columnheader', { name: new RegExp(name) })).toHaveCount(0);
    }

    const again = await app.openFilterMenu('VIEW');
    await again.getByRole('option', { name: 'Sales ops' }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get('view')).toBe('sales-ops');
  });
});

