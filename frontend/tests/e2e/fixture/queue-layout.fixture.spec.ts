/**
 * Rendered-layer proofs for the wave-1b Lead Queue layout lane (audit
 * visual-01, tables-01, flow-01, tables-04, tables-05, tables-06), at the
 * contracted 1440x900 viewport.
 *
 * "On screen" is proven twice for every control that matters: Playwright's
 * toBeInViewport (ratio 1) AND the TOPMOST element at the control's centre
 * (document.elementFromPoint) is the control, so a control painted under the
 * pinned Approval column, under the Console, or clipped by the table's
 * scrollport does not pass.
 */
import AxeBuilder from '@axe-core/playwright';
import type { Locator, Page } from '@playwright/test';
import { PRIMARY_BORROWER } from './data/borrowers';
import type { LeadSummary } from '../../../src/types';
import { DNC_LEAD, QUEUE_LAYOUT_LEADS, UNRESOLVED_OWNER_LEAD, registerQueueLayoutLeads } from './data/queueLayout';
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

async function bottomOf(target: Locator): Promise<number> {
  return target.evaluate((el) => el.getBoundingClientRect().bottom);
}

/** Six non-core filters: more hero chips than one line of the hero's action slot holds. */
const SIX_FILTERS = '/lead-queue?owner_link=Portfolio+investor+%285%2B%29&purchase_intent=HELOC+intent'
  + '&recency=Untouched+30d&outreach_status=sent&aged_days=14&zip=60601';

test.describe('the ranked-borrower table fits 1440x900 with the Console closed', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`Score and Approve of the first 8 rows are on screen, unoccluded, with no horizontal scroll (${theme})`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      const rows = page.locator(ROWS);
      await expect(rows.nth(7)).toBeVisible();

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

      const fold = page.viewportSize()?.height ?? 900;
      const aboveTheFold = await rows.evaluateAll(
        (elements, bottom) => elements.filter((el) => {
          const rect = el.getBoundingClientRect();
          return rect.top >= 0 && rect.bottom <= bottom;
        }).length,
        fold,
      );
      expect(aboveTheFold, 'ranked rows fully visible above the fold').toBeGreaterThanOrEqual(8);
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
    // The `+n` button wears the prototype's compact chip type, not the UA button font.
    const type = (el: Element) => {
      const style = getComputedStyle(el);
      return `${style.fontFamily} ${style.fontSize} ${style.fontWeight} ${style.letterSpacing}`;
    };
    expect(await more.evaluate(type)).toBe(await dncStatus.locator('.lead-table__line > .chip').first().evaluate(type));
    const spoken = (await more.getAttribute('aria-label')) ?? '';
    expect(spoken).toMatch(/^3 more statuses: /);
    expect(spoken).toContain('Assigned to: Summit LO 01');
    expect(spoken).not.toContain('DNC');
    // Its hover card says what activating it does: it expands the row, it
    // opens no evidence drawer.
    await more.hover();
    const card = page.locator('.evidence-hovercard');
    await expect(card).toBeVisible();
    await expect(card.locator('.evidence-hovercard__cta')).toHaveText('Click to expand the row →');
    await page.mouse.move(0, 0);
    await expect(card).toHaveCount(0);
    // DNC plus workflow state still fits one row at the density token.
    expect(await rowHeight(page.locator(ROWS).nth(2))).toBe(44);

    // An unresolved owner is suppressed from marketing, and the row says so
    // in its visible text, next to the cause (the CONTACTABILITY "Suppressed
    // only" filter returns exactly these rows).
    const unresolved = page.getByTestId(`lead-status-${UNRESOLVED_OWNER_LEAD.borrower_id}`);
    await expectReachable(unresolved.locator('.chip', { hasText: 'Owner unresolved' }), 'the Owner unresolved chip');
    await expectReachable(unresolved.locator('.chip', { hasText: /^Suppressed$/ }), 'the Suppressed chip of the unresolved-owner row');
    await expect(unresolved.locator('.lead-table__more'), 'compliance flags never fold into +n').toHaveCount(0);

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

test.describe('the assignment lifecycle stage is named in the row', () => {
  // The same locators as the live regression (loan_officer_assignment.spec.ts):
  // the Sales ops Assigned-to cell's +n names the stage, and the expanded
  // row's workflow strip shows the stage chip. Proven here on the rendered
  // DOM so the live spec cannot drift from the markup.
  test('Sales ops names the stage on the Assigned-to +n and the workflow strip shows it; both follow an advance', async ({ app, page, mockApi }) => {
    let stage: NonNullable<LeadSummary['assignment_status']> = 'assigned';
    registerQueueLayoutLeads(mockApi, () => QUEUE_LAYOUT_LEADS.map((lead) => (
      lead.borrower_id === DNC_LEAD.borrower_id ? { ...lead, assignment_status: stage } : lead
    )));
    const id = DNC_LEAD.borrower_id;
    for (const [status, label] of [['assigned', 'Assigned'], ['contact_drafted', 'Contact drafted']] as const) {
      stage = status;
      await app.gotoRoute(`/lead-queue?view=sales-ops&borrower_ids=${encodeURIComponent(id)}`);
      const row = page.locator('tr', { has: page.getByTestId(`lead-select-${id}`) });
      await expect(row).toBeVisible();
      const assignment = row.getByTestId(`lead-assignment-${id}`);
      await expect(assignment.locator('.chip__label').first()).toHaveText('Summit LO 01');
      const more = assignment.getByRole('button', { name: new RegExp(`Stage: ${label}$`) });
      await expectReachable(more, `the Assigned-to +n naming ${label}`);
      await expect(more).toHaveText('+1');

      await page.getByRole('button', { name: `Toggle preview for lead ${id}` }).click();
      const workflow = page.getByTestId(`lead-workflow-${id}`);
      await expect(workflow).toBeVisible();
      await expect(workflow.locator('.chip__label', { hasText: new RegExp(`^${label}$`) })).toBeVisible();
    }

    // Default view: the assignment folds behind the newer call outcome, and
    // its +n still speaks the stage with the assignee.
    await app.gotoRoute('/lead-queue');
    const spoken = (await page.getByTestId(`lead-status-${id}`).locator('.lead-table__more').getAttribute('aria-label')) ?? '';
    expect(spoken).toContain('Assigned to: Summit LO 01 (Contact drafted)');
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

  for (const view of ['default', 'sales-ops'] as const) {
    test(`every chip and button of the first 6 rows stays inside its own cell, compliance rows included (${view} view)`, async ({ app, page, mockApi }) => {
      registerQueueLayoutLeads(mockApi);
      await app.gotoRoute(view === 'sales-ops' ? '/lead-queue?view=sales-ops' : '/lead-queue');
      await expect(page.locator(ROWS).nth(5)).toBeVisible();

      const layout = await page.locator(ROWS).evaluateAll((rows) => {
        const escapes: string[] = [];
        let checked = 0;
        rows.slice(0, 6).forEach((row, index) => {
          row.querySelectorAll<HTMLElement>('.chip, button').forEach((el) => {
            const cell = el.closest('td');
            if (!cell) return;
            checked += 1;
            const box = el.getBoundingClientRect();
            const td = cell.getBoundingClientRect();
            if (box.right > td.right + 0.5 || box.left < td.left - 0.5) {
              escapes.push(`row ${index + 1} "${(el.textContent ?? '').trim()}" x ${box.left}-${box.right} leaves its cell x ${td.left}-${td.right}`);
            }
          });
        });
        return { checked, escapes };
      });
      expect(layout.checked, 'the check saw the rows\' chips and buttons').toBeGreaterThanOrEqual(24);
      expect(layout.escapes).toEqual([]);

      // The unresolved-owner row carries both compliance flags in full.
      const flags = page.getByTestId(`lead-compliance-${UNRESOLVED_OWNER_LEAD.borrower_id}`);
      await expect(flags.locator('.lead-table__flag')).toHaveText(['Owner unresolved', 'Suppressed']);
      await expectReachable(flags.locator('.chip', { hasText: /^Suppressed$/ }), `the Suppressed chip (${view} view)`);
      await expectReachable(flags.locator('.chip', { hasText: 'Owner unresolved' }), `the Owner unresolved chip (${view} view)`);
    });
  }

  test('the VIEW pill keeps "Sales ops" on one line when the Console narrows the header', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue?view=sales-ops');
    const pill = page.locator('.lead-table__header-actions button[aria-haspopup="listbox"][aria-label^="VIEW:"]');
    await expect(pill).toContainText('Sales ops');
    const closed = await pill.evaluate((el) => el.getBoundingClientRect().height);
    await app.openConsole();
    await expect.poll(() => pill.evaluate((el) => el.getBoundingClientRect().height), 'one line with the Console open').toBe(closed);
    await expectReachable(pill, 'the VIEW pill with the Console open');
  });
});

test.describe('the table scroller fills to the fold from its own top edge', () => {
  test('its surface footer ends on the fold at 1440x1100, again after More filters opens, and the 480px floor holds at 1440x900', async ({ app, page }) => {
    await page.setViewportSize({ width: 1440, height: 1100 });
    await app.gotoRoute('/lead-queue');
    const wrap = page.locator('.tbl-wrap');
    const footer = page.locator('.surface:has(> .tbl-wrap) > .surface__ft');
    const onTheFold = async (what: string) => {
      const fold = page.viewportSize()?.height ?? 0;
      await expect.poll(() => bottomOf(footer), `${what}: the footer is not below the fold`).toBeLessThanOrEqual(fold + 0.5);
      expect(await bottomOf(footer), `${what}: the footer sits on the fold, the scroller fills the space`).toBeGreaterThanOrEqual(fold - 4);
    };
    await onTheFold('1440x1100');

    // Content above the table grows: the scroller shrinks to keep its footer on the fold.
    const before = await wrap.evaluate((el) => el.getBoundingClientRect().height);
    await page.getByTestId('lead-queue-more-filters').click();
    await expect(page.getByRole('group', { name: 'More queue filters' })).toBeVisible();
    await onTheFold('More filters open');
    expect(await wrap.evaluate((el) => el.getBoundingClientRect().height)).toBeLessThan(before);

    // 900 tall leaves less than 480px under the table top: the floor wins,
    // and the scroller (with its horizontal scrollbar) still ends above the fold.
    await page.getByTestId('lead-queue-more-filters').click();
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect.poll(() => wrap.evaluate((el) => el.getBoundingClientRect().height), 'the 480px floor').toBe(480);
    expect(await bottomOf(wrap), 'the scroller ends above the fold at 1440x900').toBeLessThanOrEqual(900);
  });
});

test.describe('the collapsed filter wall', () => {
  test('More filters expands inline; an applied non-core filter is a removable hero chip; Clear all resets the URL', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    for (const label of ['STATE', 'SEGMENT', 'RELATIONSHIP', 'PRODUCT', 'APPROVAL']) {
      await expect(page.locator(`button[aria-haspopup="listbox"][aria-label^="${label}:"]`), `${label} is a core pill`).toBeVisible();
    }
    const ownerLink = page.locator('button[aria-haspopup="listbox"][aria-label^="OWNER LINK:"]');
    await expect(ownerLink, 'non-core pills start collapsed').toBeHidden();
    const clearAll = page.getByTestId('lead-queue-clear-all');
    await expect(clearAll).toBeDisabled();

    const toggle = page.getByTestId('lead-queue-more-filters');
    await expect(toggle).toHaveAttribute('aria-expanded', 'false');
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    await expect(page.getByRole('group', { name: 'More queue filters' })).toBeVisible();
    const menu = await app.openFilterMenu('OWNER LINK');
    await menu.getByRole('option', { name: 'Multi-property (2-4)' }).click();
    await expect.poll(() => new URL(page.url()).searchParams.get('owner_link')).toBe('Multi-property (2-4)');
    await app.settle();

    const hero = page.getByRole('group', { name: 'Active filters' });
    const remove = hero.getByRole('button', { name: 'Remove OWNER LINK: Multi-property (2-4) filter' });
    await expectReachable(remove, 'the removable OWNER LINK chip');
    await expect(toggle).toHaveAccessibleName('More filters (1 active)');
    await remove.click();
    await expect.poll(() => new URL(page.url()).searchParams.has('owner_link')).toBe(false);
    await expect(hero).toHaveCount(0);
    // The last chip is gone: focus lands on the More filters toggle, never on <body>.
    await expect(toggle).toBeFocused();

    const again = await app.openFilterMenu('OWNER LINK');
    await again.getByRole('option', { name: 'Portfolio investor (5+)' }).click();
    const state = await app.openFilterMenu('STATE');
    await state.getByRole('option', { name: 'IL', exact: true }).click();
    await expect.poll(() => new URL(page.url()).search).toContain('state=IL');
    await expect(clearAll).toBeEnabled();
    await clearAll.click();
    await expect.poll(() => new URL(page.url()).search, 'Clear all resets the URL').toBe('');
    await expect(clearAll).toBeDisabled();
  });

  test('Clear all keeps the column preset: ?view= is not a filter', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue?view=sales-ops&owner_link=Portfolio+investor+%285%2B%29');
    await page.getByTestId('lead-queue-clear-all').click();
    await expect.poll(() => new URL(page.url()).search).toBe('?view=sales-ops');
    await expect(page.getByTestId('lead-queue-clear-all')).toBeDisabled();
  });

  for (const theme of FIXTURE_THEMES) {
    test(`six active filters: every hero chip's Remove button is reachable, Console closed and open (${theme})`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute(SIX_FILTERS);
      const hero = page.getByRole('group', { name: 'Active filters' });
      const removes = hero.getByRole('button', { name: /^Remove .+ filter$/ });
      await expect(removes).toHaveCount(6);
      for (const phase of ['Console closed', 'Console open'] as const) {
        if (phase === 'Console open') await app.openConsole();
        for (let index = 0; index < 6; index += 1) {
          const remove = removes.nth(index);
          await expectReachable(remove, `${phase}: ${(await remove.getAttribute('aria-label')) ?? `Remove button ${index + 1}`}`);
        }
      }
      // The first chip, the one the one-line row clipped, removes only its own filter.
      await removes.first().click();
      await expect.poll(() => new URL(page.url()).searchParams.has('owner_link')).toBe(false);
      expect(new URL(page.url()).searchParams.get('zip')).toBe('60601');
      await expect(removes).toHaveCount(5);
      // Focus is handed to the Remove button that took its place, not dropped on <body>.
      await expect(removes.first()).toBeFocused();
      await page.keyboard.press('Enter');
      await expect(removes).toHaveCount(4);
      await expect(removes.first()).toBeFocused();
    });
  }

  test('the hero lede is one line and claims no sending', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    const lede = page.locator('.proto-hero .lede');
    await expect(lede).toHaveText('Expand a row, then approve or reject. Decisions are audited; nothing is sent automatically.');
    const lines = await lede.evaluate((el) => {
      const range = document.createRange();
      range.selectNodeContents(el);
      return new Set([...range.getClientRects()].map((rect) => Math.round(rect.top))).size;
    });
    expect(lines, 'the lede renders on one line at 1440').toBe(1);
  });
});

test.describe('axe stays clean on the new queue states', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`hero chip + More filters open + Status sort menu open (${theme})`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue?owner_link=Portfolio+investor+%285%2B%29');
      await page.getByTestId('lead-queue-more-filters').click();
      await page.getByTestId('lead-status-sort').click();
      await expect(page.getByRole('menu', { name: 'Sort the Status column by' })).toBeVisible();
      const results = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
        .analyze();
      const found = results.violations.map((violation) => `${violation.id}: ${violation.nodes.map((node) => node.target.join(' ')).join(' ; ')}`);
      expect(found).toEqual([]);
    });
  }
});
