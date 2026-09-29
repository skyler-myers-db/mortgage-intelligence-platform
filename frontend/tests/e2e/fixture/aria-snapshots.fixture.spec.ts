/**
 * Accessibility-tree snapshots (audit a11y-05 item 3, test-infra PR-2):
 * Playwright's toMatchAriaSnapshot, INLINE, so a reviewer reads the expected
 * tree in the diff. They pin structure, not pixels: the role, accessible
 * name and state (sort, expanded, selected) of two keyboard-critical Lead
 * Queue surfaces, while classes and wrappers stay free to change.
 *
 * 1440x900 on fixture-chromium, dark theme only: the accessibility tree is
 * theme-independent (no role, name or state reads a colour token).
 *
 *  a. The ranked-borrower table's header row after the natural load: each
 *     columnheader, and each sort control's name and sort state. Only the
 *     header: the body is virtualized and holds truncated text, whose line
 *     breaks follow Linux Geist Mono widths.
 *  b. The STATE filter open: the expanded trigger and the listbox options
 *     (the synthetic STATES), names and selected state. Escape closes it.
 *  c. Opening a filter never re-reads GET /leads, or any other audited read.
 *
 * Update only deliberately, and review the rewritten literals:
 *   E2E_FIXTURE_PORT=<port> npm --prefix frontend run e2e:fixture -- aria-snapshots
 *     --update-snapshots --update-source-method=overwrite
 */
import { expect, test } from './test';
import { expectNoAuditedReadSince, markNaturalLoad } from './visual';

const TABLE = 'table.tbl:not([aria-hidden="true"])';

test.describe('Lead Queue accessibility tree (aria snapshots)', () => {
  test('the header sort controls and the STATE filter listbox', async ({ app, mockApi, page }) => {
    await app.setTheme('dark');
    await app.gotoRoute('/lead-queue');
    const t0 = markNaturalLoad(mockApi);

    await expect(page.locator(`${TABLE} thead`)).toMatchAriaSnapshot(`
      - rowgroup:
        - row "Select all eligible leads Borrower Location Segments Sort by Equity Sort by Rate Δ (bps) Primary offer Sort by Score Sort by Signal Status. Sort options Approval":
          - columnheader "Select all eligible leads":
            - checkbox "Select all eligible leads"
          - columnheader
          - columnheader "Borrower"
          - columnheader "Location"
          - columnheader "Segments"
          - columnheader "Sort by Equity":
            - button "Sort by Equity"
          - columnheader "Sort by Rate Δ (bps)":
            - button "Sort by Rate Δ (bps)"
          - columnheader "Primary offer"
          - columnheader "Sort by Score":
            - button "Sort by Score"
          - columnheader "Sort by Signal":
            - button "Sort by Signal"
          - columnheader "Status. Sort options":
            - button "Status. Sort options"
          - columnheader "Approval"
    `);

    // aria-sort is outside the aria-snapshot vocabulary: pin each sortable
    // header's state beside it (the queue opens idle, ranked by the server).
    const sortState = await page
      .locator(`${TABLE} thead th[aria-sort]`)
      .evaluateAll((cells) => cells.map((cell) => `${cell.querySelector('button')?.getAttribute('aria-label')}: ${cell.getAttribute('aria-sort')}`));
    expect(sortState).toEqual([
      'Sort by Equity: none',
      'Sort by Rate Δ (bps): none',
      'Sort by Score: none',
      'Sort by Signal: none',
      'Status. Sort options: none',
    ]);

    const menu = await app.openFilterMenu('STATE');
    const trigger = page.locator('button[aria-haspopup="listbox"][aria-label^="STATE:"]').first();
    await expect(trigger).toMatchAriaSnapshot(`
      - 'combobox "STATE: All states" [expanded]': STATE All states
    `);
    await expect(menu).toMatchAriaSnapshot(`
      - listbox "STATE":
        - option "All states" [selected]
        - option "AZ"
        - option "CA"
        - option "CO"
        - option "FL"
        - option "GA"
        - option "IL"
        - option "TX"
        - option "WA"
    `);
    await page.keyboard.press('Escape');
    await expect(menu).toBeHidden();
    await expect(trigger).toHaveAttribute('aria-expanded', 'false');

    expectNoAuditedReadSince(mockApi, t0, 'aria snapshots');
  });
});
