/**
 * The W5d sales loop at the rendered layer, 1440 x 900 (w5-sales-loop):
 *
 *  (a) the sales manager view: a period preset is written to the URL, every
 *      loan officer is listed (one with no calls in the period as '0 calls'),
 *      and neither the load, a period change nor the Board's dry run opens an
 *      audited read;
 *  (b) the Distribution Board, both themes: exactly one dry-run POST
 *      (score_balanced, dry_run, no request id), the per-officer bars and
 *      numbers (the free capacity painted apart from the dialog's fill),
 *      axe clean; Commit carries the fingerprint and a request id;
 *      F8 reaches the toast's Undo, which POSTs the run's release with that
 *      request id and raises its own audit-linked toast;
 *  (c) a 409 distribution_plan_changed re-previews and shows the notice; a
 *      re-preview that places nothing keeps focus on Commit;
 *  (d) a 429 holds Retry behind the countdown, then retries the same intent
 *      with the same request id;
 *  (e) a Lead Queue assign -> Undo POSTs the single release; a second Undo
 *      the server refuses as started shows the client copy, not the server's;
 *  (f) the custom range at the keyboard: an ArrowUp or a typed year stays a
 *      draft (focus kept, URL untouched, Custom kept), an unreadable draft
 *      shows its inline error, and Enter applies a readable one in place;
 *      a preset pressed over a dirty draft writes one history entry, keeps
 *      focus on the preset and never applies the draft (W5e C11; the WebKit
 *      twin is sales-period-preset.webkit.fixture.spec.ts).
 *
 * Every write is registered per test (mockApi.register); the default
 * registry is unchanged.
 */
import fs from 'node:fs';
import path from 'node:path';
import type { Page } from '@playwright/test';
import type { LeadAssignment } from '../../../src/types';
import { expectAxeClean } from './axe';
import { LEADS } from './data/borrowers';
import { SALES_TEAM } from './data/portfolio';
import {
  BOARD_FINGERPRINT,
  BOARD_FINGERPRINT_MOVED,
  UNASSIGN_AUDIT_ID,
  assignmentRow,
  boardPreview,
  commitBody,
  conversionWithCalls,
  distributionReleaseBody,
  dryRunBody,
  registerBoard,
  releaseBody,
} from './data/salesLoop';
import { json, type FixtureReply, type MockApi } from './mockApi';
import { centerPixel, contrastRatio, renderedColors, type Rgb } from './renderedColor';
import { FIXTURE_THEMES } from './routes';
import { expectPresetPressOverDirtyDraft } from './salesPeriodPreset';
import { expect, test } from './test';
import { expectNoAuditedReadSince, expectNoSurfaceOverflow } from './visual';

const SALES_OPS = '/analytics?view=sales-ops';

/**
 * How far apart (WCAG contrast ratio) a load bar's free capacity must paint
 * from the dialog's own fill and from the open book beside it, so the gauge
 * shows its capacity extent (W5d distribution-board VRT review, run
 * 38563619204). A legibility floor, not a 1.4.11 claim: the bar is
 * aria-hidden and every number it draws is in its row. A track on the
 * dialog's own --bg-2 painted the fill exactly, 1:1.
 */
const FREE_TRACK_FLOOR = 1.15;

function rgb([r, g, b]: Rgb): string {
  return `rgb(${r}, ${g}, ${b})`;
}

interface ReleaseBody {
  assignment_id: string;
  request_id: string;
}

/**
 * Whether the transport carries a 409 body's top-level `reason`. On this
 * lane's base only 503 / 429 bodies are parsed for it (_parseRetryableBody's
 * early return). W5d w5-actor-binding KEEPS that early return and carries a
 * 409's string `reason` through apiActorBinding.actorRefusalReason(res) when
 * the transport builds the ApiError, so the capability is that call, or any
 * transport that no longer stops at 503 / 429.
 */
const TRANSPORT_SOURCE = fs.readFileSync(path.join(process.cwd(), 'src', 'lib', 'apiTransport.ts'), 'utf8');
const TRANSPORT_CARRIES_409_REASON = TRANSPORT_SOURCE.includes('actorRefusalReason(res)')
  || !TRANSPORT_SOURCE.includes('if (res.status !== 503 && res.status !== 429) return empty;');
const LOAN_OFFICERS = SALES_TEAM.filter((member) => member.role === 'loan_officer');

async function openBoard(page: Page) {
  await page.getByRole('button', { name: 'Distribute approved leads…' }).click();
  const dialog = page.locator('dialog.distribution-board');
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('tbody tr')).toHaveCount(LOAN_OFFICERS.length);
  return dialog;
}

function toastWithLink(page: Page, auditId: string) {
  return page.locator('.toast').filter({ has: page.locator(`a.toast__link[href*="audit_event_id=${auditId}"]`) });
}

test.describe('(a) the sales manager view', () => {
  test('a preset changes the URL, every officer is listed, and nothing reads an audited row', async ({ app, mockApi, page }) => {
    registerBoard(mockApi);
    // Only the first officer logged calls: the other has no conversion row at all.
    mockApi.register('GET', '/api/sales/conversion', ({ query }) =>
      json(conversionWithCalls(LOAN_OFFICERS.slice(0, 1), query.get('from') ?? undefined, query.get('to') ?? undefined)));
    await app.gotoRoute(SALES_OPS);
    const period = page.locator('.sales-ops-period');
    await expect(period.getByRole('button', { name: 'Week to date' })).toHaveAttribute('aria-pressed', 'true');

    await period.getByRole('button', { name: '30 days' }).click();
    await expect(page).toHaveURL(/[?&]sales_period=30d/);
    await expect(period.getByRole('button', { name: '30 days' })).toHaveAttribute('aria-pressed', 'true');
    await app.settle();
    const conversion = page.locator('.sales-ops-card').filter({ hasText: 'Last 30 days conversion' });
    // Every in-scope loan officer, the one without calls as an explicit zero.
    await expect(conversion.locator('.split-row')).toHaveCount(LOAN_OFFICERS.length);
    await expect(conversion.locator('.split-row').nth(0)).toContainText(LOAN_OFFICERS[0].display_label);
    await expect(conversion.locator('.split-row').nth(1)).toHaveText(`${LOAN_OFFICERS[1].display_label}0 calls`);
    await expect(conversion.locator('[title]')).toHaveCount(0);

    // W5e (critic-10 6b): the team row states its change against the
    // previous 30 days, from the cohort reads.
    const team = page.getByTestId('sales-team-comparison');
    await expect(team.locator('.kpi')).toHaveCount(4);
    await expect(team.locator('.kpi__delta').first()).toContainText('vs previous 30 days');
    // W5e (critic-10 6c): the workload lists every in-scope officer, sorts by
    // a numeric header with aria-sort, links each officer into the Lead
    // Queue's assigned_to filter and never shows an email.
    const workload = page.getByTestId('sales-workload');
    await expect(workload.locator('tbody tr')).toHaveCount(LOAN_OFFICERS.length);
    // A sortable header is named by its button (LeadTableHead's pattern).
    await expect(workload.getByRole('columnheader', { name: 'Sort by Open', exact: true })).toHaveAttribute('aria-sort', 'descending');
    const stale = workload.getByRole('columnheader', { name: 'Sort by Stale', exact: true });
    await stale.getByRole('button').click();
    await expect(stale).toHaveAttribute('aria-sort', 'descending');
    await expect(workload.getByRole('columnheader', { name: 'Sort by Open', exact: true })).toHaveAttribute('aria-sort', 'none');
    const first = workload.locator('tbody tr').first().locator('a');
    await expect(first).toHaveAttribute('href', /^\/lead-queue\?assigned_to=[^&]+%40summit\.example$/);
    await expect(workload).not.toContainText('@summit.example');
    await expect(workload.locator('[title]')).toHaveCount(0);

    await openBoard(page);
    await app.settle();
    expectNoAuditedReadSince(mockApi, 0, 'sales ops: load, period change, Board dry run');
  });
});

test.describe('(b) the Distribution Board', () => {
  for (const theme of FIXTURE_THEMES) {
    test(`${theme}: one dry run, the plan, a fingerprinted commit, and the run's Undo`, async ({ app, mockApi, page }) => {
      await app.setTheme(theme);
      const bodies = registerBoard(mockApi);
      const releases: Array<{ distribution_request_id: string; request_id: string }> = [];
      mockApi.register('POST', '/api/sales/distribute/release', ({ body }) => {
        releases.push(body as { distribution_request_id: string; request_id: string });
        return json(distributionReleaseBody(commitBody(LOAN_OFFICERS, boardPreview(LOAN_OFFICERS)).assignments));
      });
      await app.gotoRoute(SALES_OPS);
      const dialog = await openBoard(page);

      expect(bodies).toHaveLength(1);
      expect(bodies[0]).toMatchObject({ strategy: 'score_balanced', dry_run: true, borrower_ids: [] });
      expect(bodies[0]).not.toHaveProperty('request_id');
      // BOARD_PLANS.default, a plan the planner produces: B's open book is
      // over its capacity, so A takes the whole pool and nothing is unplaced.
      await expect(dialog).toContainText('4 of 4 leads placed · 3 skipped: already assigned · 1 skipped: not approved or not contactable');
      await expect(dialog.locator('.distribution-board__bar')).toHaveCount(LOAN_OFFICERS.length);
      const rows = dialog.locator('tbody tr');
      await expect(rows.first()).toContainText('+4');
      await expect(rows.nth(1)).toContainText('+0');
      // The over-capacity marker on B alone, and never over a planned lead.
      await expect(rows.first().locator('.distribution-board__over')).toHaveCount(0);
      await expect(rows.nth(1).locator('.distribution-board__over')).toHaveCount(1);
      const hidden = await dialog.locator('.distribution-board__bar').evaluateAll((bars) => bars.flatMap((bar) => {
        const over = bar.querySelector('.distribution-board__over')?.getBoundingClientRect();
        const added = bar.querySelector('.distribution-board__seg--added')?.getBoundingClientRect();
        return over && added && added.width > 0 && added.right > over.left
          ? [`planned lead x${added.left}-${added.right} under the marker x${over.left}-${over.right}`]
          : [];
      }));
      expect(hidden, 'a planned lead painted under the over-capacity marker').toEqual([]);
      // The first officer has room left: its free segment paints the track,
      // which must stand apart from the dialog's fill and from the open book.
      const first = rows.first();
      const free = await centerPixel(page, first.locator('.distribution-board__seg--free'));
      const open = await centerPixel(page, first.locator('.distribution-board__seg--open'));
      const fill = (await renderedColors(dialog)).bg;
      expect(contrastRatio(free, fill), `free capacity ${rgb(free)} on the dialog fill ${rgb(fill)}`).toBeGreaterThanOrEqual(FREE_TRACK_FLOOR);
      expect(contrastRatio(free, open), `free capacity ${rgb(free)} beside the open book ${rgb(open)}`).toBeGreaterThanOrEqual(FREE_TRACK_FLOOR);
      // The dialog is a .surface like the page's: nothing in it scrolls sideways
      // (visual.fixture.spec.ts holds its capture to the same check()).
      await expectNoSurfaceOverflow(page, { route: 'analytics-sales-ops', state: 'distribution-board', theme });
      await expectAxeClean(page, { key: { route: 'analytics-sales-ops', state: 'distribution-board' }, theme, known: {}, include: 'dialog.distribution-board' });

      await dialog.getByRole('button', { name: 'Commit distribution' }).click();
      await expect(dialog).toBeHidden();
      expect(bodies).toHaveLength(2);
      expect(bodies[1].expected_plan_fingerprint).toBe(BOARD_FINGERPRINT);
      expect(bodies[1].request_id).toMatch(/\S/);
      const toast = page.locator('.toast').filter({ hasText: '4 leads assigned' });
      await expect(toast).toBeVisible();

      await page.keyboard.press('F8');
      const undo = toast.getByRole('button', { name: 'Undo' });
      await expect(undo).toBeFocused();
      await page.keyboard.press('Enter');
      await expect(page.locator('.toast').filter({ hasText: '3 of 4 assignments undone' })).toBeVisible();
      expect(releases).toHaveLength(1);
      expect(releases[0].distribution_request_id).toBe(bodies[1].request_id);
      const undone = page.locator('.toast').filter({ hasText: '3 of 4 assignments undone' });
      await expect(undone.locator('a.toast__link')).toHaveAttribute('href', /audit_event_id=/);
      await expect(undone.getByRole('button', { name: 'Undo' })).toHaveCount(0);
    });
  }
});

test.describe('(c) a moved plan', () => {
  test('a 409 distribution_plan_changed re-previews and shows the notice', async ({ app, mockApi, page, hygiene }) => {
    hygiene.allow('console.error', /status of 409/);
    let previews = 0;
    const commits: string[] = [];
    mockApi.register<unknown>('POST', '/api/sales/distribute', ({ body }) => {
      const request = body as { dry_run?: boolean; expected_plan_fingerprint?: string };
      if (request.dry_run) {
        previews += 1;
        return json(dryRunBody(previews === 1
          ? boardPreview(LOAN_OFFICERS)
          : boardPreview(LOAN_OFFICERS, 'moved', BOARD_FINGERPRINT_MOVED)));
      }
      commits.push(request.expected_plan_fingerprint ?? '');
      return json({ detail: 'The distribution plan changed since the preview.', reason: 'distribution_plan_changed' }, { status: 409 });
    });
    await app.gotoRoute(SALES_OPS);
    const dialog = await openBoard(page);
    await dialog.getByRole('button', { name: 'Commit distribution' }).click();

    await expect(dialog.getByText('The plan changed since your preview')).toBeVisible();
    await expect(dialog).toContainText(`${LOAN_OFFICERS[0].display_label}: +4 → +3`);
    expect(previews).toBe(2);
    expect(commits).toEqual([BOARD_FINGERPRINT]);
    await expect(dialog).toBeVisible();
  });

  test('a re-preview that places nothing keeps focus on Commit', async ({ app, mockApi, page, hygiene }) => {
    hygiene.allow('console.error', /status of 409/);
    let previews = 0;
    mockApi.register<unknown>('POST', '/api/sales/distribute', ({ body }) => {
      if ((body as { dry_run?: boolean }).dry_run) {
        previews += 1;
        return json(dryRunBody(previews === 1 ? boardPreview(LOAN_OFFICERS) : boardPreview(LOAN_OFFICERS, 'full', BOARD_FINGERPRINT_MOVED)));
      }
      return json({ detail: 'The distribution plan changed since the preview.', reason: 'distribution_plan_changed' }, { status: 409 });
    });
    await app.gotoRoute(SALES_OPS);
    const dialog = await openBoard(page);
    const commit = dialog.getByRole('button', { name: 'Commit distribution' });
    await commit.focus();
    await page.keyboard.press('Enter');

    await expect(dialog).toContainText('0 of 2 leads placed · 2 left unplaced: no capacity');
    // A natively disabled button would have dropped focus to <body> here.
    await expect(commit).toBeFocused();
    await expect(commit).toHaveAttribute('aria-disabled', 'true');
    await expect(commit).toHaveJSProperty('disabled', false);
  });
});

test.describe('(d) a rate-limited commit', () => {
  // The countdown needs a clock that moves: install one and advance it.
  test.use({ fixtureNow: null });

  test('429 holds Retry behind the countdown, then retries with the same request id', async ({ app, mockApi, page, hygiene }) => {
    hygiene.allow('console.error', /status of 429/);
    await page.clock.install();
    const bodies = registerBoard(mockApi, {
      onCommit: (request, index) => (index === 1
        ? json(
          { detail: 'Too many requests.', retryable: true, reason: 'rate_limited', retry_after_seconds: 12 },
          { status: 429, headers: { 'Retry-After': '12' } },
        )
        : json(commitBody(LOAN_OFFICERS, boardPreview(LOAN_OFFICERS)))),
    });
    await app.gotoRoute(SALES_OPS);
    const dialog = await openBoard(page);
    await dialog.getByRole('button', { name: 'Commit distribution' }).click();

    const failure = page.locator('.toast').filter({ hasText: "Couldn't distribute the leads" });
    const retry = failure.getByRole('button', { name: 'Retry' });
    await expect(retry).toHaveAttribute('aria-disabled', 'true');
    await expect(failure).toContainText('Try again in');
    // A press while the wait runs does nothing (Playwright treats
    // aria-disabled as not actionable, so the click is forced through).
    await retry.click({ force: true });
    expect(bodies.filter((body) => !body.dry_run)).toHaveLength(1);
    await page.clock.runFor(12_500);
    await expect(retry).not.toHaveAttribute('aria-disabled', 'true');
    await retry.click();
    await expect(page.locator('.toast').filter({ hasText: '4 leads assigned' })).toBeVisible();

    const commits = bodies.filter((body) => !body.dry_run);
    expect(commits).toHaveLength(2);
    expect(commits[1].request_id).toBe(commits[0].request_id);
    expect(commits[1].expected_plan_fingerprint).toBe(commits[0].expected_plan_fingerprint);
  });
});

test.describe('(e) the Lead Queue assign Undo', () => {
  const officer = LOAN_OFFICERS[0];

  /** Assign answers with a fresh row; release answers `release(index)`. */
  function registerQueueWrites(mockApi: MockApi, release: (index: number, request: ReleaseBody, id: string) => FixtureReply) {
    const releases: Array<ReleaseBody & { id: string }> = [];
    let assigned = 0;
    mockApi.register('POST', '/api/leads/:id/assign', ({ params }) => {
      assigned += 1;
      const row: LeadAssignment = assignmentRow(params.id, officer, { assignment_id: `asg-queue-${assigned}` });
      return json({ assignment: row, audit_event_id: `6f0d6d3e-2c1b-4c5e-9a6f-1d2e3f4a5d0${assigned}` });
    });
    mockApi.register('POST', '/api/leads/:id/assignment/release', ({ params, body }) => {
      const request = body as ReleaseBody;
      releases.push({ id: params.id, ...request });
      return release(releases.length, request, params.id);
    });
    return releases;
  }

  async function assignFirstRow(page: Page) {
    await page.locator('table.tbl tbody [data-testid^="lead-select-B-"]').first().check();
    await page.getByRole('toolbar', { name: 'Bulk actions' })
      .getByRole('button', { name: 'Assign 1 selected leads to selected loan officer' })
      .click();
    return page.locator('.toast').filter({ hasText: '1 lead assigned' });
  }

  test('Undo POSTs one single release with the row id and raises its own audit-linked toast', async ({ app, mockApi, page }) => {
    const releases = registerQueueWrites(mockApi, (_, request, id) =>
      json(releaseBody(assignmentRow(id, officer, { assignment_id: request.assignment_id }), null)));
    await app.gotoRoute('/lead-queue');

    const assigned = await assignFirstRow(page);
    await assigned.getByRole('button', { name: 'Undo' }).click();
    const undone = toastWithLink(page, UNASSIGN_AUDIT_ID).filter({ hasText: 'Assignment undone' });
    await expect(undone).toBeVisible();
    await expect(undone.getByRole('button', { name: 'Undo' })).toHaveCount(0);
    expect(releases).toHaveLength(1);
    expect(releases[0].assignment_id).toBe('asg-queue-1');
    expect(releases[0].request_id).toMatch(/\S/);
    expect(LEADS.map((lead) => lead.borrower_id)).toContain(releases[0].id);
  });

  test('a started lead shows the client copy keyed by the 409 reason, not the server text', async ({ app, mockApi, page, hygiene }) => {
    // The copy is keyed by the 409 body's top-level `reason`. On this lane's
    // base the transport carries `reason` for 503 / 429 bodies only
    // (lib/apiTransport.ts _parseRetryableBody), so a 409 still reads as the
    // generic "Reload to read the current version." until W5d
    // w5-actor-binding's 409 reason parse (apiActorBinding.actorRefusalReason)
    // merges; then TRANSPORT_CARRIES_409_REASON is true and this case runs.
    // The integrator confirms it runs and passes after that merge.
    test.fixme(!TRANSPORT_CARRIES_409_REASON, 'needs the 409 reason parse from W5d w5-actor-binding');
    hygiene.allow('console.error', /status of 409/);
    registerQueueWrites(mockApi, () =>
      json({ detail: 'The loan officer has already started on this lead.', reason: 'assignment_started' }, { status: 409 }));
    await app.gotoRoute('/lead-queue');

    const assigned = await assignFirstRow(page);
    await assigned.getByRole('button', { name: 'Undo' }).click();
    const refused = page.locator('.toast').filter({ hasText: "Couldn't undo the assignment" });
    await expect(refused).toContainText('The loan officer has already started on this lead; reassign instead of undoing.');
    await expect(refused).not.toContainText('already started on this lead.');
  });
});

test.describe('(f) the custom range at the keyboard (Chromium fires change per keyboard edit)', () => {
  test('edits stay a draft until Enter, focus never drops, and an unreadable draft stays in Custom', async ({ app, page }) => {
    await app.gotoRoute(`${SALES_OPS}&sales_period=custom&sales_from=2026-07-01&sales_to=2026-07-10`);
    const period = page.locator('.sales-ops-period');
    const from = period.getByLabel('From');
    await expect(from).toHaveValue('2026-07-01');
    const start = page.url();

    // en-US segments are mm/dd/yyyy: ArrowRight reaches the day, ArrowUp moves it.
    await from.focus();
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('ArrowUp');
    await expect(from).toHaveValue('2026-07-02');
    await expect(from).toBeFocused();
    expect(page.url()).toBe(start);

    // A typed year passes through 0002, 0020 and 0202 on its way: none of
    // them may leave Custom or touch the URL.
    await page.keyboard.press('ArrowRight');
    await page.keyboard.type('2025');
    await expect(from).toHaveValue('2025-07-02');
    await expect(from).toBeFocused();
    await expect(period.getByRole('button', { name: 'Custom' })).toHaveAttribute('aria-pressed', 'true');
    await expect(period.locator('input[type="date"]')).toHaveCount(2);
    expect(page.url()).toBe(start);

    // Enter on a range over 92 days: an inline error, the draft kept, the URL alone.
    await page.keyboard.press('Enter');
    await expect(period.locator('.field__error')).toHaveText('A custom range covers at most 92 days.');
    await expect(from).toHaveAttribute('aria-invalid', 'true');
    await expect(from).toHaveValue('2025-07-02');
    await expect(from).toBeFocused();
    expect(page.url()).toBe(start);

    // A readable draft applies on Enter, and focus stays on the field.
    await page.keyboard.press('ArrowUp');
    await expect(from).toHaveValue('2026-07-02');
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/[?&]sales_from=2026-07-02(&|$)/);
    await expect(page).toHaveURL(/[?&]sales_to=2026-07-10(&|$)/);
    await expect(from).toBeFocused();
    await expect(period.locator('.field__error')).toHaveCount(0);
    await expect(page.locator('.sales-ops-card .eyebrow').filter({ hasText: 'Conversion Jul 2 to Jul 10' })).toBeVisible();
  });

  test('a preset pressed over a dirty draft writes one history entry and keeps focus on the preset', async ({ app, page }) => {
    await expectPresetPressOverDirtyDraft(app, page);
  });
});
