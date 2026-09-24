/**
 * Rendered-layer proofs for wave 1c lane queue-keyboard-review (audit
 * tables-03 row cursor, wow-power-4 keymap / `?` sheet / Cmd-K verbs,
 * flow-03 + states-06 approve review), at 1440x900.
 *
 *  - J / K move one cursor row through a VIRTUALIZED queue (160 rows) and
 *    scroll an unrendered row into the scrollport, also after the pointer
 *    scrolled the cursor row out of the virtual window; the cursor is drawn
 *    with the focus-ring token in both themes.
 *  - A opens the review for the cursor row: the subject and message TEXT are
 *    on screen in a modal dialog; Enter confirms; nothing claims approval and
 *    no "View receipt" exists until the approve response returns; then the
 *    cursor advances and "View receipt" opens the ledger read-back.
 *  - No /outreach/draft request on expand or cursor movement (each draft
 *    writes a DRAFT_OUTREACH audit row): asserted on the request log.
 *  - Bulk keeps its rationale gate, shows count by offer and drafts samples
 *    only on "Preview 3 sample drafts"; the Cmd-K verb opens that same gate.
 *  - `?` lists the page's shortcuts; the Console switch turns single keys off.
 *  - Review round 1: a review whose row is rejected through its own Reject
 *    panel closes (no approve after the reject); Cmd-K never mounts the
 *    palette under the review dialog; the Cmd-K verb's review hands focus
 *    back to the table; "Skip table" lands on a visible, ringed target.
 *  - Review round 2: an evidence chip in the review dialog opens the drawer
 *    WITH focus in it (Enter there approves nothing; Escape returns to the
 *    inline review); R moves focus into the reject panel, and a returned
 *    reject or Cancel hands it back to the table so J keeps working.
 *
 * Holds are RequestGates, never wall-clock waits, so the in-flight
 * assertions hold under any machine load.
 */
import AxeBuilder from '@axe-core/playwright';
import type { Locator, Page } from '@playwright/test';
import type { FixtureTheme } from './app';
import { LEADS } from './data/borrowers';
import {
  REVIEW_AUDIT_ID,
  VIRTUAL_QUEUE,
  registerDraftEcho,
  registerHeldDecision,
  registerHeldReject,
  registerVirtualQueue,
  reviewSubject,
} from './data/queueKeyboard';
import type { MockApi } from './mockApi';
import { expect, test } from './test';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];

function draftRequests(mockApi: MockApi): number {
  return mockApi.calls.filter((call) => call.method === 'POST' && call.path.endsWith('/outreach/draft')).length;
}

/**
 * Count the approve POSTs the page itself issues, read in-page: the client
 * calls fetch synchronously from the Confirm handler, so a read after a key
 * press has returned sees any POST that press started (the mock's own log
 * only sees it once the request reaches the route handler).
 */
async function installApproveFetchProbe(page: Page): Promise<void> {
  await page.evaluate(() => {
    const probe = window as Window & { __mipApprovePosts?: number };
    probe.__mipApprovePosts = 0;
    const original = window.fetch.bind(window);
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
      if (url.endsWith('/outreach/approve')) probe.__mipApprovePosts = (probe.__mipApprovePosts ?? 0) + 1;
      return original(input, init);
    };
  });
}

function approvePostsSent(page: Page): Promise<number> {
  return page.evaluate(() => (window as Window & { __mipApprovePosts?: number }).__mipApprovePosts ?? -1);
}

function scrollRegion(page: Page): Locator {
  return page.getByRole('region', { name: 'Ranked borrowers table scroll region' });
}

function cursorRow(page: Page): Locator {
  return page.locator('table.tbl tr.is-cursor');
}

function focusInOpenDrawer(page: Page): Promise<boolean> {
  return page.evaluate(() => document.querySelector('aside.drawer.is-open')?.contains(document.activeElement) ?? false);
}

/** Let `count` animation frames commit (focus handoffs run on frames). */
function frames(page: Page, count: number): Promise<void> {
  return page.evaluate((remaining) => new Promise<void>((resolve) => {
    const step = (left: number) => {
      if (left <= 0) resolve();
      else requestAnimationFrame(() => step(left - 1));
    };
    step(remaining);
  }), count);
}

/** The cursor row's box sits inside the scrollport, below the sticky header. */
async function expectInScrollport(page: Page, row: Locator): Promise<void> {
  const boxes = await row.evaluate((element) => {
    const port = element.closest('.tbl-wrap');
    const head = port?.querySelector('thead');
    if (!port || !head) return null;
    const portBox = port.getBoundingClientRect();
    return {
      rowTop: element.getBoundingClientRect().top,
      rowBottom: element.getBoundingClientRect().bottom,
      headBottom: head.getBoundingClientRect().bottom,
      portBottom: portBox.top + port.clientHeight,
    };
  });
  expect(boxes, 'the cursor row is inside the table scrollport').not.toBeNull();
  if (!boxes) return;
  expect(boxes.rowTop, 'not under the sticky header').toBeGreaterThanOrEqual(boxes.headBottom - 1);
  expect(boxes.rowBottom, 'not below the scrollport').toBeLessThanOrEqual(boxes.portBottom + 1);
}

test.describe('keyboard row cursor on a virtualized queue', () => {
  for (const theme of ['dark', 'light'] as const satisfies readonly FixtureTheme[]) {
    test(`J / K walk a 160-row queue, scroll unrendered rows in and draw the focus-ring cursor (${theme})`, async ({ app, mockApi, page }) => {
      // A few dozen real key presses, each re-rendering the virtual window.
      test.slow();
      const echo = registerDraftEcho(mockApi);
      registerVirtualQueue(mockApi);
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      const rendered = page.locator('table.tbl tbody tr[data-borrower-row]');
      await expect(rendered.first()).toBeVisible();
      // The first row past the rendered window (the virtualizer renders a
      // contiguous window from the top): the walk has to bring it in.
      const renderedAtRest = await rendered.count();
      expect(renderedAtRest, 'precondition: the queue is virtualized').toBeLessThan(VIRTUAL_QUEUE.length);
      const targetIndex = renderedAtRest + 1;
      const target = VIRTUAL_QUEUE[targetIndex].borrower_id;
      expect(await page.locator(`tr[data-borrower-row="${target}"]`).count(), 'precondition: the target row is not rendered').toBe(0);

      await scrollRegion(page).focus();
      for (let step = 0; step <= targetIndex; step += 1) await page.keyboard.press(step % 2 === 0 ? 'j' : 'ArrowDown');
      await expect(cursorRow(page)).toHaveAttribute('data-borrower-row', target);
      await expect(cursorRow(page)).toHaveAttribute('aria-current', 'true');
      await expectInScrollport(page, cursorRow(page));
      await expect(page.getByTestId('lead-cursor-status')).toContainText(`Row ${targetIndex + 1} of ${VIRTUAL_QUEUE.length}: ${target}`);
      expect(await rendered.count(), 'still virtualized, not all 160 rows').toBeLessThan(VIRTUAL_QUEUE.length);

      // The ring is the focus-ring token, not a hue of its own.
      const colors = await cursorRow(page).evaluate((row) => {
        const probe = document.createElement('span');
        probe.style.color = 'var(--focus-ring-color)';
        document.body.appendChild(probe);
        const ring = getComputedStyle(probe).color;
        probe.remove();
        return { ring, shadow: getComputedStyle(row.querySelector('td') as Element).boxShadow };
      });
      expect(colors.shadow).toContain(colors.ring);

      await page.keyboard.press('k');
      await page.keyboard.press('ArrowUp');
      await page.keyboard.press('k');
      const back = targetIndex - 3;
      await expect(cursorRow(page)).toHaveAttribute('data-borrower-row', VIRTUAL_QUEUE[back].borrower_id);
      await expectInScrollport(page, cursorRow(page));

      // Scrolled far away by pointer, the cursor row leaves the virtual
      // window; the next J must bring its neighbour back through the
      // virtualizer's scrollToIndex (a step walk alone never needs it).
      await scrollRegion(page).evaluate((element) => {
        element.scrollTop = element.scrollHeight;
      });
      await expect(cursorRow(page), 'precondition: the cursor row is no longer rendered').toHaveCount(0);
      await page.keyboard.press('j');
      await expect(cursorRow(page)).toHaveAttribute('data-borrower-row', VIRTUAL_QUEUE[back + 1].borrower_id);
      await expectInScrollport(page, cursorRow(page));

      // Opening and closing the cursor row with Enter drafts nothing.
      await page.keyboard.press('Enter');
      await expect(page.locator('table.tbl tr.tbl__expand')).toHaveCount(1);
      await page.keyboard.press('Enter');
      await expect(page.locator('table.tbl tr.tbl__expand')).toHaveCount(0);
      expect(draftRequests(mockApi), 'no draft on cursor movement or expand').toBe(0);
      expect(echo.calls).toEqual([]);
    });
  }
});

test.describe('approve review', () => {
  test('A opens the review with the copy, Enter confirms, the receipt link appears only after the approve response, the cursor advances', async ({ app, mockApi, page }) => {
    const echo = registerDraftEcho(mockApi);
    const held = registerHeldDecision(mockApi);
    registerVirtualQueue(mockApi);
    await app.gotoRoute('/lead-queue');
    await installApproveFetchProbe(page);
    const reviewed = VIRTUAL_QUEUE[2];
    const next = VIRTUAL_QUEUE[3];

    await scrollRegion(page).focus();
    for (let step = 0; step < 3; step += 1) await page.keyboard.press('j');
    await expect(cursorRow(page)).toHaveAttribute('data-borrower-row', reviewed.borrower_id);
    expect(draftRequests(mockApi)).toBe(0);

    await page.keyboard.press('a');
    const dialog = page.locator('dialog.lead-approve-dialog');
    await expect(dialog).toBeVisible();
    const review = dialog.getByTestId('lead-approve-review');
    await expect(review.getByTestId('lead-approve-review-subject')).toHaveText(reviewSubject(reviewed.borrower_id));
    await expect(review.getByTestId('lead-approve-review-body')).toContainText('a mortgage review may lower your monthly cost');
    await expect(review.getByTestId('lead-approve-review-channel')).toHaveText('Email');
    await expect(review).toContainText('Disclosure fixture-2026-07');
    await expect(review.locator('.evidence-chip')).toHaveCount(2);
    expect(echo.calls, 'exactly one draft, on the explicit A').toEqual([reviewed.borrower_id]);

    const confirm = review.getByTestId('lead-approve-review-confirm');
    await expect(confirm).toBeFocused();
    await page.keyboard.press('Enter');
    await expect.poll(() => held.approveGate.received, 'the approve POST left the browser').toBe(true);
    await expect(confirm).toHaveText('Approving…');
    // Held: nothing claims approval, and there is no receipt link yet.
    await expect(page.getByTestId('lead-decision-toast')).toHaveCount(0);
    await expect(page.getByTestId('lead-decision-view-receipt')).toHaveCount(0);
    await expect(page.getByTestId(`lead-approval-cell-${reviewed.borrower_id}`).locator('.chip--success')).toHaveCount(0);
    // Enter again while the write is in flight is not a second POST: the
    // in-page probe is read after the press's handlers ran.
    expect(await approvePostsSent(page)).toBe(1);
    await page.keyboard.press('Enter');
    expect(await approvePostsSent(page), 'no second approve POST was started').toBe(1);
    expect(held.approvals).toHaveLength(1);
    expect(held.approvals[0]).toEqual(expect.objectContaining({
      borrower_id: reviewed.borrower_id,
      draft_generation_id: `gen-${reviewed.borrower_id}`,
      draft_subject: reviewSubject(reviewed.borrower_id),
    }));

    held.approveGate.release();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByTestId('lead-decision-toast')).toContainText(`Approved ${reviewed.borrower_id}`);
    const viewReceipt = page.getByTestId('lead-decision-view-receipt');
    await expect(viewReceipt).toBeVisible();
    await expect(page.getByTestId(`lead-approval-cell-${reviewed.borrower_id}`).locator('.chip--success')).toHaveText(/Approved/);
    await expect(cursorRow(page), 'the cursor advanced to the next pending row').toHaveAttribute('data-borrower-row', next.borrower_id);
    await expect(scrollRegion(page)).toBeFocused();
    expect(held.approvals).toHaveLength(1);
    expect(echo.calls).toEqual([reviewed.borrower_id]);

    await viewReceipt.click();
    const receipt = page.locator(`tr:has([data-testid="lead-approval-cell-${reviewed.borrower_id}"]) + tr.tbl__expand`)
      .getByTestId('decision-receipt');
    await expect(receipt).toHaveAttribute('data-audit-event-id', REVIEW_AUDIT_ID);
    await expect(page.locator(`#lead-receipt-${reviewed.borrower_id}`)).toBeFocused();
    expect(echo.calls, 'opening the receipt drafts nothing').toEqual([reviewed.borrower_id]);
  });

  test('Escape abandons the review: no approval, the one draft stays the only one', async ({ app, mockApi, page }) => {
    const echo = registerDraftEcho(mockApi);
    const held = registerHeldDecision(mockApi);
    await app.gotoRoute('/lead-queue');
    await scrollRegion(page).focus();
    await page.keyboard.press('j');
    await page.keyboard.press('a');
    const dialog = page.locator('dialog.lead-approve-dialog');
    await expect(dialog.getByTestId('lead-approve-review-confirm')).toBeFocused();

    // Cmd-K under the modal review mounts no inert, unseen palette (a DOM
    // query, not a role query: inert content leaves the accessibility tree),
    // so the next Escape still belongs to the review.
    await page.keyboard.press('Control+k');
    await expect(page.locator('[role="dialog"][aria-label="Command palette"]')).toHaveCount(0);
    await expect(dialog.getByTestId('lead-approve-review-confirm')).toBeFocused();

    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(scrollRegion(page), 'focus returns to the table').toBeFocused();
    expect(held.approveGate.received).toBe(false);
    expect(echo.calls).toEqual([LEADS[0].borrower_id]);
  });

  test('a review left open on a row rejected through its own Reject panel closes: no approve after the reject', async ({ app, mockApi, page }) => {
    const echo = registerDraftEcho(mockApi);
    const held = registerHeldReject(mockApi);
    await app.gotoRoute('/lead-queue');
    const target = LEADS[0];
    expect(target.approval_status, 'precondition: the first row is pending').toBe('pending');

    await scrollRegion(page).focus();
    await page.keyboard.press('j');
    await page.keyboard.press('Enter');
    await page.keyboard.press('a');
    const review = page.locator('table.tbl tr.tbl__expand').getByTestId('lead-approve-review');
    const confirm = review.getByTestId('lead-approve-review-confirm');
    await expect(confirm).toBeFocused();

    await page.getByTestId(`lead-reject-${target.borrower_id}`).click();
    await page.locator('.decision-panel').getByRole('button', { name: 'Confirm reject' }).click();
    await expect.poll(() => held.rejectGate.received, 'the reject POST left the browser').toBe(true);

    // The reject is on the wire: Confirm on the still-open review is not a
    // second decision, and says why.
    await confirm.click();
    await expect(review.getByRole('alert')).toHaveText(
      'Not approved yet: another decision for this borrower is still being recorded. Wait for it to finish, then check the row.',
    );

    held.rejectGate.release();
    await expect(review, 'the review closed once its row was rejected').toHaveCount(0);
    await expect(page.getByTestId(`lead-approval-cell-${target.borrower_id}`)).toContainText('Rejected');
    await expect(page.getByTestId('lead-approve-review-confirm')).toHaveCount(0);
    expect(held.approvals, 'no approve after the reject').toEqual([]);
    expect(echo.calls).toEqual([target.borrower_id]);
  });

  for (const how of ['keyboard', 'pointer'] as const) {
    test(`an evidence chip in the review dialog (${how}) opens the drawer holding focus: Enter there approves nothing, Escape returns into the inline review`, async ({ app, mockApi, page }) => {
      const echo = registerDraftEcho(mockApi);
      const held = registerHeldDecision(mockApi);
      await app.gotoRoute('/lead-queue');
      await installApproveFetchProbe(page);
      const target = LEADS[0];

      await scrollRegion(page).focus();
      await page.keyboard.press('j');
      await page.keyboard.press('a');
      const dialog = page.locator('dialog.lead-approve-dialog');
      await expect(dialog.getByTestId('lead-approve-review-confirm')).toBeFocused();
      const chip = dialog.locator('.evidence-chip').first();
      if (how === 'keyboard') {
        await chip.focus();
        await page.keyboard.press('Enter');
      } else {
        await chip.click();
      }

      const drawer = app.evidenceDrawer();
      await expect(drawer).toHaveClass(/is-open/);
      await expect(dialog).toHaveCount(0);
      const inline = page.locator('table.tbl tr.tbl__expand').getByTestId('lead-approve-review');
      await expect(inline, 'the same review moved into its row').toHaveAttribute('data-review-phase', 'ready');
      await expect.poll(() => focusInOpenDrawer(page), 'focus is inside the open drawer').toBe(true);
      // Nothing behind the drawer takes focus back once the handoff settles.
      await frames(page, 4);
      expect(await focusInOpenDrawer(page), 'still inside the drawer').toBe(true);

      // An Enter meant for the drawer (on its selected tab, which keeps it
      // open) certifies nothing: no approve POST leaves the browser.
      await page.keyboard.press('Tab');
      expect(await focusInOpenDrawer(page)).toBe(true);
      await page.keyboard.press('Enter');
      await expect(drawer).toHaveClass(/is-open/);
      expect(await approvePostsSent(page), 'no approve POST under the drawer').toBe(0);
      expect(held.approvals).toEqual([]);

      await page.keyboard.press('Escape');
      await expect(drawer).not.toHaveClass(/is-open/);
      await expect(inline.locator('.evidence-chip').first(), 'focus returns to the chip in the inline review').toBeFocused();
      await expect(inline).toHaveAttribute('data-review-phase', 'ready');
      expect(await approvePostsSent(page)).toBe(0);
      expect(held.approvals).toEqual([]);
      expect(echo.calls, 'the move drafted nothing new').toEqual([target.borrower_id]);
    });
  }

  for (const theme of ['dark', 'light'] as const satisfies readonly FixtureTheme[]) {
    test(`the review dialog and the ? sheet pass axe (${theme})`, async ({ app, mockApi, page }) => {
      registerDraftEcho(mockApi);
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      await scrollRegion(page).focus();
      await page.keyboard.press('j');
      await page.keyboard.press('a');
      await expect(page.locator('dialog.lead-approve-dialog').getByTestId('lead-approve-review-confirm')).toBeFocused();
      const reviewScan = await new AxeBuilder({ page }).include('dialog.lead-approve-dialog').withTags(WCAG_TAGS).analyze();
      expect(reviewScan.violations.map((violation) => violation.id)).toEqual([]);
      await page.keyboard.press('Escape');

      await scrollRegion(page).focus();
      await page.keyboard.press('?');
      const sheet = page.getByTestId('shortcut-sheet');
      await expect(sheet).toBeVisible();
      const sheetScan = await new AxeBuilder({ page }).include('[data-testid="shortcut-sheet"]').withTags(WCAG_TAGS).analyze();
      expect(sheetScan.violations.map((violation) => violation.id)).toEqual([]);
    });
  }
});

test.describe('keyboard reject', () => {
  test('R moves focus into the reject panel; a returned reject or Cancel hands it back to the table and J keeps moving the cursor', async ({ app, mockApi, page }) => {
    registerDraftEcho(mockApi);
    const held = registerHeldReject(mockApi);
    await app.gotoRoute('/lead-queue');
    const [rejected, approvedRow, next, after] = LEADS;
    // Every fixture lead is marketing-eligible; LEADS[1] is already approved,
    // so the next pending row after LEADS[0] is LEADS[2].
    expect([rejected.approval_status, approvedRow.approval_status, next.approval_status]).toEqual(['pending', 'approved', 'pending']);
    const panel = page.locator('.decision-panel');
    const reason = panel.getByRole('combobox', { name: 'Reason' });

    await scrollRegion(page).focus();
    await page.keyboard.press('j');
    await expect(cursorRow(page)).toHaveAttribute('data-borrower-row', rejected.borrower_id);

    // Cancel by keyboard: Reason -> Rationale note -> Cancel.
    await page.keyboard.press('r');
    await expect(reason, 'R puts focus on the panel, not in the table').toBeFocused();
    await page.keyboard.press('Tab');
    await page.keyboard.press('Tab');
    await expect(panel.getByRole('button', { name: 'Cancel' })).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(panel).toHaveCount(0);
    await expect(scrollRegion(page), 'Cancel hands focus back to the table').toBeFocused();
    expect(held.rejectGate.received).toBe(false);

    // Confirm reject by keyboard: Reason -> Rationale note -> Cancel -> Confirm reject.
    await page.keyboard.press('r');
    await expect(reason).toBeFocused();
    for (let step = 0; step < 3; step += 1) await page.keyboard.press('Tab');
    await expect(panel.getByRole('button', { name: 'Confirm reject' })).toBeFocused();
    await page.keyboard.press('Enter');
    await expect.poll(() => held.rejectGate.received, 'the reject POST left the browser').toBe(true);
    held.rejectGate.release();
    await expect(panel).toHaveCount(0);
    await expect(page.getByTestId(`lead-approval-cell-${rejected.borrower_id}`)).toContainText('Rejected');
    await expect(cursorRow(page), 'the cursor advanced to the next pending row').toHaveAttribute('data-borrower-row', next.borrower_id);
    await expect(scrollRegion(page), 'the table region holds focus, not <body>').toBeFocused();

    await page.keyboard.press('j');
    await expect(cursorRow(page), 'J acts from the advanced cursor').toHaveAttribute('data-borrower-row', after.borrower_id);
    expect(held.approvals).toEqual([]);
  });
});

test.describe('bulk gate and Cmd-K verbs', () => {
  test('count by offer, samples only on explicit preview, and the Cmd-K verb opens the same gate', async ({ app, mockApi, page }) => {
    const echo = registerDraftEcho(mockApi);
    const held = registerHeldDecision(mockApi);
    await app.gotoRoute('/lead-queue');
    const pending = LEADS.filter((lead) => lead.approval_status === 'pending').slice(0, 4);
    for (const lead of pending) await page.getByTestId(`lead-select-${lead.borrower_id}`).check();

    // Cmd-K: the verb lists the eligible count and opens the rationale gate.
    const palette = await app.openCommandPalette();
    const verb = palette.getByRole('option', { name: /^Approve 4 selected…/ });
    await expect(verb).toBeVisible();
    await verb.click();
    await expect(palette).toHaveCount(0);
    const rationale = page.locator('.bulk-actions__rationale input');
    await expect(rationale).toBeFocused();

    const review = page.getByTestId('lead-bulk-review');
    await expect(review.getByTestId('lead-bulk-offer-counts')).toContainText('By offer');
    const counted = await review.locator('[data-testid="lead-bulk-offer-counts"] .chip .num').allTextContents();
    expect(counted.map(Number).reduce((sum, count) => sum + count, 0)).toBe(4);
    expect(echo.calls, 'opening the gate drafts nothing').toEqual([]);

    const preview = review.getByTestId('lead-bulk-preview-samples');
    await expect(preview).toHaveText('Preview 3 sample drafts');
    await expect(review).toContainText('Generates 3 audited drafts');
    await preview.click();
    await expect(review.locator('[data-testid="lead-bulk-samples"] li')).toHaveCount(3);
    await expect(review.locator('[data-testid="lead-bulk-samples"] li').first()).toContainText(reviewSubject(pending[0].borrower_id));
    expect(echo.calls).toEqual(pending.slice(0, 3).map((lead) => lead.borrower_id));
    expect(held.approveGate.received, 'nothing approves without the rationale and the button').toBe(false);
  });

  test('the Cmd-K approve verb on one selected row opens its review; Cancel hands focus back to the table', async ({ app, mockApi, page }) => {
    const echo = registerDraftEcho(mockApi);
    const held = registerHeldDecision(mockApi);
    await app.gotoRoute('/lead-queue');
    const [target] = LEADS.filter((lead) => lead.approval_status === 'pending');
    await page.getByTestId(`lead-select-${target.borrower_id}`).check();

    // Cmd-K from inside the table, as a keyboard user would.
    await scrollRegion(page).focus();
    await page.keyboard.press('Control+k');
    const palette = page.getByRole('dialog', { name: 'Command palette' });
    await palette.getByRole('option', { name: /^Approve 1 selected…/ }).click();
    await expect(palette).toHaveCount(0);

    const dialog = page.locator('dialog.lead-approve-dialog');
    await expect(dialog.getByTestId('lead-approve-review-confirm')).toBeFocused();
    expect(echo.calls, 'one draft, on the explicit verb').toEqual([target.borrower_id]);

    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect.poll(
      () => page.evaluate(() => document.querySelector('.tbl-wrap')?.contains(document.activeElement) ?? false),
      'focus is back in the table, not on the page body',
    ).toBe(true);
    expect(held.approveGate.received).toBe(false);
  });
});

/** Decision writes and draft generations (each writes an audit row) the page sent. */
function outreachWrites(mockApi: MockApi): string[] {
  return mockApi.calls
    .filter((call) => call.method === 'POST' && /\/outreach\/(draft|approve|reject)$/.test(call.path))
    .map((call) => call.path);
}

test.describe('shortcut sheet and the single-key switch', () => {
  test('? lists the table keys on /lead-queue; the Console switch turns single keys off and Cmd-K stays on', async ({ app, mockApi, page }) => {
    await app.gotoRoute('/lead-queue');
    await scrollRegion(page).focus();
    await page.keyboard.press('?');
    const sheet = page.getByTestId('shortcut-sheet');
    await expect(sheet.locator('.cmdk__group-label')).toHaveText(['Ranked borrowers table', 'Everywhere']);
    await expect(sheet).toContainText('Review the outreach, then approve (Enter confirms)');
    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);

    const consolePanel = await app.openConsole();
    await consolePanel.getByTestId('console-single-key-shortcuts').click();
    await expect(consolePanel.getByTestId('console-single-key-shortcuts')).toHaveAttribute('aria-pressed', 'false');
    await consolePanel.getByRole('button', { name: 'Close console' }).click();

    await scrollRegion(page).focus();
    await page.keyboard.press('j');
    await expect(cursorRow(page)).toHaveCount(0);
    await expect(page.getByText('Single-key shortcuts are off (Console).')).toBeVisible();

    // tables-v2 / a11y-09: with the switch off a mouse click still puts the
    // cursor on a row (and expands it), and two pending rows are selected,
    // yet A, R and Shift+A are inert from the row control AND from the
    // table region: no review, no reject panel, no bulk gate, and no draft,
    // approve or reject request leaves the browser.
    const [target, second, third] = LEADS.filter((lead) => lead.approval_status === 'pending');
    for (const lead of [second, third]) await page.getByTestId(`lead-select-${lead.borrower_id}`).check();
    await page.locator(`tr[data-borrower-row="${target.borrower_id}"] [aria-expanded]`).click();
    await expect(cursorRow(page)).toHaveAttribute('data-borrower-row', target.borrower_id);
    await expect(page.locator('table.tbl tr.tbl__expand')).toHaveCount(1);
    for (const key of ['a', 'r', 'Shift+A']) await page.keyboard.press(key);
    await scrollRegion(page).focus();
    for (const key of ['a', 'r', 'Shift+A']) await page.keyboard.press(key);

    // Mod accepts Ctrl on every platform; a modifier chord survives the
    // switch. The palette opening through the same dispatcher is also the
    // barrier: every key pressed before it has been handled.
    await page.keyboard.press('Control+k');
    const palette = page.getByRole('dialog', { name: 'Command palette' });
    await expect(palette).toBeVisible();
    await expect(page.getByTestId('lead-approve-review')).toHaveCount(0);
    await expect(page.locator('dialog.lead-approve-dialog')).toHaveCount(0);
    await expect(page.locator('.decision-panel')).toHaveCount(0);
    await expect(page.locator('.bulk-actions__rationale')).toHaveCount(0);
    expect(outreachWrites(mockApi), 'no draft, approve or reject request').toEqual([]);
    await page.keyboard.press('Escape');
    await expect(palette).toHaveCount(0);

    await page.getByTestId('lead-shortcuts-open').click();
    await expect(sheet.getByTestId('shortcut-sheet-off')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(sheet).toHaveCount(0);
    expect(outreachWrites(mockApi)).toEqual([]);
  });
});

test.describe('keyboard hint', () => {
  test('stays one header line at 1440x900, so the 480px scroller still ends above the fold', async ({ app, page }) => {
    await app.gotoRoute('/lead-queue');
    const subtitle = page.locator('.surface:has(> .tbl-wrap) > .surface__hdr .muted.fs-12');
    await expect(subtitle.locator('kbd')).toHaveText(['J', 'K', 'Enter', 'A', 'R']);
    await expect(page.getByTestId('lead-shortcuts-open')).toBeVisible();
    const box = await subtitle.evaluate((element) => {
      const style = getComputedStyle(element);
      const declared = Number.parseFloat(style.lineHeight);
      // `normal` parses to NaN: fall back to a generous single line.
      const lineHeight = Number.isFinite(declared) ? declared : Number.parseFloat(style.fontSize) * 1.6;
      return { height: element.getBoundingClientRect().height, lineHeight };
    });
    expect(box.height, 'the hint wraps to a second line').toBeLessThan(box.lineHeight * 1.5);
    const bottom = await scrollRegion(page).evaluate((element) => element.getBoundingClientRect().bottom);
    expect(bottom, 'the table scroller ends above the fold').toBeLessThanOrEqual(900);
  });
});

test.describe('skip table', () => {
  for (const theme of ['dark', 'light'] as const satisfies readonly FixtureTheme[]) {
    test(`Skip table shows in place on focus and lands on a visible, ringed target past the rows, URL untouched (${theme})`, async ({ app, page }) => {
      await app.setTheme(theme);
      await app.gotoRoute('/lead-queue');
      const before = page.url();
      const skip = page.getByRole('link', { name: 'Skip table' });
      await skip.focus();
      await expect(skip).toBeVisible();
      const box = await skip.boundingBox();
      const regionBox = await scrollRegion(page).boundingBox();
      expect(box && regionBox && box.y + box.height <= regionBox.y + 1, 'shown in place, above the table').toBe(true);
      await page.keyboard.press('Enter');
      const end = page.getByText('End of ranked borrowers table');
      await expect(end).toBeFocused();
      const order = await end.evaluate((element) => {
        const region = document.querySelector('.tbl-wrap');
        return region ? region.compareDocumentPosition(element) & Node.DOCUMENT_POSITION_FOLLOWING : 0;
      });
      expect(order, 'focus landed after the table').toBeTruthy();
      // A sighted keyboard user sees where focus landed: the target is shown
      // in place with the focus-ring token, not a clipped 1px sr-only box.
      const landed = await end.evaluate((element) => {
        const probe = document.createElement('span');
        probe.style.color = 'var(--focus-ring-color)';
        document.body.appendChild(probe);
        const ring = getComputedStyle(probe).color;
        probe.remove();
        const style = getComputedStyle(element);
        const box = element.getBoundingClientRect();
        return {
          ring,
          outlineColor: style.outlineColor,
          outlineStyle: style.outlineStyle,
          outlineWidth: Number.parseFloat(style.outlineWidth),
          width: box.width,
          height: box.height,
        };
      });
      expect(landed.width, 'the target is shown, not clipped').toBeGreaterThan(1);
      expect(landed.height, 'the target is shown, not clipped').toBeGreaterThan(1);
      expect(landed.outlineStyle).toBe('solid');
      expect(landed.outlineWidth).toBeGreaterThan(0);
      expect(landed.outlineColor).toBe(landed.ring);
      expect(page.url()).toBe(before);
    });
  }
});
